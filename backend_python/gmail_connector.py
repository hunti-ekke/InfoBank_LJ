"""Gmail OAuth connector service for CITDS EvidenceUnit sync.

This module provides the production-oriented path beyond the import contract:
OAuth URL generation, callback token storage, and Gmail thread/message sync into
EvidenceUnit records. The sync is intentionally conservative: imported messages
become owned EvidenceUnits and then flow through the same policy/classifier/action
reconstruction pipeline as all other evidence.
"""

from __future__ import annotations

import json
import os
import uuid
from typing import Any, Dict, List

from sqlalchemy.orm import Session

import evidence_service
import models

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
PROVIDER = "gmail"


def _require_google_libs():
    try:
        from google.oauth2.credentials import Credentials  # noqa: F401
        from google_auth_oauthlib.flow import Flow  # noqa: F401
        from googleapiclient.discovery import build  # noqa: F401
    except Exception as exc:
        raise RuntimeError(
            "Google connector dependencies are missing. Run pip install -r requirements.txt."
        ) from exc


def _client_config() -> Dict[str, Any]:
    client_id = os.getenv("GOOGLE_CLIENT_ID")
    client_secret = os.getenv("GOOGLE_CLIENT_SECRET")
    redirect_uri = os.getenv("GOOGLE_REDIRECT_URI", "http://127.0.0.1:8000/api/connectors/gmail/callback")
    if not client_id or not client_secret:
        raise RuntimeError("GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET must be set for Gmail OAuth.")
    return {
        "web": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [redirect_uri],
        }
    }


def create_authorization_url(user_id: str) -> Dict[str, str]:
    _require_google_libs()
    from google_auth_oauthlib.flow import Flow

    redirect_uri = os.getenv("GOOGLE_REDIRECT_URI", "http://127.0.0.1:8000/api/connectors/gmail/callback")
    flow = Flow.from_client_config(_client_config(), scopes=SCOPES, redirect_uri=redirect_uri)
    auth_url, state = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
        state=user_id,
    )
    return {"authorization_url": auth_url, "state": state}


def store_callback_tokens(db: Session, user_id: str, authorization_response_url: str) -> models.ConnectorAccount:
    _require_google_libs()
    from google_auth_oauthlib.flow import Flow

    redirect_uri = os.getenv("GOOGLE_REDIRECT_URI", "http://127.0.0.1:8000/api/connectors/gmail/callback")
    flow = Flow.from_client_config(_client_config(), scopes=SCOPES, redirect_uri=redirect_uri)
    flow.fetch_token(authorization_response=authorization_response_url)
    credentials = flow.credentials

    existing = db.query(models.ConnectorAccount).filter(
        models.ConnectorAccount.user_id == user_id,
        models.ConnectorAccount.provider == PROVIDER,
    ).first()
    token_json = credentials.to_json()
    metadata_json = json.dumps({"scopes": SCOPES}, ensure_ascii=False)
    if existing:
        existing.token_json = token_json
        existing.metadata_json = metadata_json
        existing.status = "connected"
        db.commit()
        db.refresh(existing)
        return existing

    account = models.ConnectorAccount(
        id=str(uuid.uuid4()),
        user_id=user_id,
        provider=PROVIDER,
        status="connected",
        token_json=token_json,
        metadata_json=metadata_json,
    )
    db.add(account)
    db.commit()
    db.refresh(account)
    return account


def _credentials_from_account(account: models.ConnectorAccount):
    _require_google_libs()
    from google.oauth2.credentials import Credentials

    return Credentials.from_authorized_user_info(json.loads(account.token_json), SCOPES)


def _gmail_service(account: models.ConnectorAccount):
    _require_google_libs()
    from googleapiclient.discovery import build

    return build("gmail", "v1", credentials=_credentials_from_account(account))


def _headers_to_dict(headers: List[Dict[str, str]]) -> Dict[str, str]:
    return {h.get("name", "").lower(): h.get("value", "") for h in headers or []}


def _extract_text_from_payload(payload: Dict[str, Any]) -> str:
    if not payload:
        return ""
    body = payload.get("body", {}) or {}
    data = body.get("data")
    if data:
        try:
            import base64
            return base64.urlsafe_b64decode(data.encode("utf-8")).decode("utf-8", errors="ignore")
        except Exception:
            return ""
    parts = payload.get("parts") or []
    texts = []
    for part in parts:
        mime = part.get("mimeType", "")
        if mime.startswith("text/"):
            texts.append(_extract_text_from_payload(part))
    return "\n".join(t for t in texts if t).strip()


def sync_gmail_messages(db: Session, user_id: str, max_results: int = 25, query: str = "") -> Dict[str, Any]:
    account = db.query(models.ConnectorAccount).filter(
        models.ConnectorAccount.user_id == user_id,
        models.ConnectorAccount.provider == PROVIDER,
        models.ConnectorAccount.status == "connected",
    ).first()
    if not account:
        raise RuntimeError("Gmail is not connected for this user.")

    service = _gmail_service(account)
    list_response = service.users().messages().list(userId="me", maxResults=max_results, q=query).execute()
    messages = list_response.get("messages", [])
    units = []

    for msg_ref in messages:
        message = service.users().messages().get(userId="me", id=msg_ref["id"], format="full").execute()
        payload = message.get("payload", {})
        headers = _headers_to_dict(payload.get("headers", []))
        subject = headers.get("subject") or "Gmail message"
        sender = headers.get("from")
        recipients = headers.get("to")
        sent_at = None
        internal_date = message.get("internalDate")
        if internal_date:
            try:
                import datetime
                sent_at = datetime.datetime.fromtimestamp(int(internal_date) / 1000).isoformat()
            except Exception:
                sent_at = None
        body = _extract_text_from_payload(payload) or message.get("snippet", "")
        labels = message.get("labelIds", [])
        direction = "outbound" if "SENT" in labels else "inbound"
        content = (
            f"Gmail message. Direction: {direction}. Subject: {subject}. "
            f"From: {sender or 'unknown'}. To: {recipients or ''}. "
            f"Labels: {', '.join(labels)}. Body: {body}"
        )
        units.append({
            "source_type": "Email",
            "title": subject,
            "content": content,
            "source_timestamp": sent_at,
            "thread_id": message.get("threadId") or message.get("id"),
            "relation_key": message.get("threadId") or message.get("id"),
            "metadata": {
                "connector": "gmail_oauth",
                "message_id": message.get("id"),
                "thread_id": message.get("threadId"),
                "sender": sender,
                "recipients": recipients,
                "direction": direction,
                "labels": labels,
                "snippet": message.get("snippet"),
            },
        })

    created = evidence_service.import_evidence_units(db, user_id, units)
    return {"imported": len(created), "ids": [unit.id for unit in created], "gmail_count": len(messages)}
