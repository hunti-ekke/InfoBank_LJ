from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

import evidence_service
import models
import security
from database import get_db

router = APIRouter(prefix="/api/evidence", tags=["Evidence"])


class EvidenceUnitIn(BaseModel):
    source_type: str = "Other"
    title: str
    content: str
    source_timestamp: str | None = None
    thread_id: str | None = None
    relation_key: str | None = None
    metadata: Dict[str, Any] | None = None


class EvidenceImportRequest(BaseModel):
    units: List[EvidenceUnitIn]


class GmailMessageIn(BaseModel):
    message_id: str
    thread_id: str | None = None
    subject: str
    sender: str | None = None
    recipients: List[str] | None = None
    snippet: str | None = None
    body: str | None = None
    sent_at: str | None = None
    direction: str | None = None  # inbound, outbound, unknown
    labels: List[str] | None = None
    relation_key: str | None = None


class GmailImportRequest(BaseModel):
    messages: List[GmailMessageIn]


class BrowserHistoryItemIn(BaseModel):
    url: str
    title: str | None = None
    visited_at: str | None = None
    visit_count: int | None = None
    relation_key: str | None = None
    metadata: Dict[str, Any] | None = None


class BrowserHistoryImportRequest(BaseModel):
    items: List[BrowserHistoryItemIn]


@router.post("/import")
def import_evidence_units(
    payload: EvidenceImportRequest,
    user_id: str = Depends(security.get_current_user_id),
    db: Session = Depends(get_db),
):
    try:
        created = evidence_service.import_evidence_units(db, user_id, [unit.model_dump() for unit in payload.units])
        return {"status": "success", "imported": len(created), "ids": [unit.id for unit in created]}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/import/gmail")
def import_gmail_messages(
    payload: GmailImportRequest,
    user_id: str = Depends(security.get_current_user_id),
    db: Session = Depends(get_db),
):
    """Import Gmail connector output into the CITDS EvidenceUnit model.

    This endpoint is connector-ready: a future Gmail OAuth connector only needs to
    map Gmail API thread/message objects into this request schema.
    """

    units = []
    for message in payload.messages:
        labels = message.labels or []
        direction = message.direction or "unknown"
        body = message.body or message.snippet or ""
        content = (
            f"Gmail message. Direction: {direction}. Subject: {message.subject}. "
            f"From: {message.sender or 'unknown'}. To: {', '.join(message.recipients or [])}. "
            f"Labels: {', '.join(labels)}. Body: {body}"
        )
        units.append({
            "source_type": "Email",
            "title": message.subject,
            "content": content,
            "source_timestamp": message.sent_at,
            "thread_id": message.thread_id or message.message_id,
            "relation_key": message.relation_key or message.thread_id or message.message_id,
            "metadata": {
                "connector": "gmail",
                "message_id": message.message_id,
                "thread_id": message.thread_id,
                "sender": message.sender,
                "recipients": message.recipients,
                "direction": direction,
                "labels": labels,
            },
        })
    try:
        created = evidence_service.import_evidence_units(db, user_id, units)
        return {"status": "success", "imported": len(created), "ids": [unit.id for unit in created]}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/import/browser-history")
def import_browser_history(
    payload: BrowserHistoryImportRequest,
    user_id: str = Depends(security.get_current_user_id),
    db: Session = Depends(get_db),
):
    """Import browser-history/export records as contextual evidence only."""

    units = []
    for item in payload.items:
        title = item.title or item.url
        content = (
            f"Browser history: visited {title}. URL: {item.url}. "
            "This is contextual support only and cannot create an obligation alone."
        )
        units.append({
            "source_type": "BrowserHistory",
            "title": title,
            "content": content,
            "source_timestamp": item.visited_at,
            "thread_id": None,
            "relation_key": item.relation_key or item.url,
            "metadata": {
                "connector": "browser_history",
                "url": item.url,
                "visit_count": item.visit_count,
                **(item.metadata or {}),
            },
        })
    try:
        created = evidence_service.import_evidence_units(db, user_id, units)
        return {"status": "success", "imported": len(created), "ids": [unit.id for unit in created]}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/units")
def list_evidence_units(user_id: str = Depends(security.get_current_user_id), db: Session = Depends(get_db)):
    units = db.query(models.EvidenceUnit).filter(models.EvidenceUnit.user_id == user_id).order_by(models.EvidenceUnit.created_at.desc()).all()
    return {
        "status": "success",
        "units": [
            {
                "id": unit.id,
                "source_type": unit.source_type.value if hasattr(unit.source_type, "value") else str(unit.source_type),
                "title": unit.title,
                "content": unit.content,
                "source_timestamp": unit.source_timestamp.isoformat() if unit.source_timestamp else None,
                "thread_id": unit.thread_id,
                "relation_key": unit.relation_key,
                "metadata_json": unit.metadata_json,
            }
            for unit in units
        ],
    }


def _source_type_value(unit: models.EvidenceUnit) -> str:
    value = unit.source_type
    if hasattr(value, "value"):
        return value.value
    return str(value)


def _delete_evidence_unit_ids(db: Session, user_id: str, unit_ids: List[str]) -> int:
    if not unit_ids:
        return 0
    db.query(models.PolicyRule).filter(
        models.PolicyRule.owner_user_id == user_id,
        models.PolicyRule.target_type == "EvidenceUnit",
        models.PolicyRule.target_id.in_(unit_ids),
    ).delete(synchronize_session=False)
    deleted = db.query(models.EvidenceUnit).filter(
        models.EvidenceUnit.user_id == user_id,
        models.EvidenceUnit.id.in_(unit_ids),
    ).delete(synchronize_session=False)
    db.commit()
    return deleted


def _clear_evidence_by_source_type_values(db: Session, user_id: str, source_type_values: List[str]) -> int:
    """Delete connector EvidenceUnits with enum/string-safe matching.

    MySQL + SQLAlchemy Enum columns can be returned as enum objects while manual
    rows or older migrations may behave like strings. Filtering in Python keeps
    this cleanup endpoint reliable for local prototype databases.
    """

    wanted = set(source_type_values)
    units = db.query(models.EvidenceUnit).filter(models.EvidenceUnit.user_id == user_id).all()
    unit_ids = [unit.id for unit in units if _source_type_value(unit) in wanted]
    return _delete_evidence_unit_ids(db, user_id, unit_ids)


@router.delete("/clear/email")
def clear_email_evidence(user_id: str = Depends(security.get_current_user_id), db: Session = Depends(get_db)):
    """Delete imported/synced email EvidenceUnits for the current user.

    This keeps the Gmail OAuth connection itself, and only clears evidence already
    imported into InfoBank.
    """

    try:
        deleted = _clear_evidence_by_source_type_values(db, user_id, ["Email"])
        return {"status": "success", "deleted": deleted, "source_type": "Email"}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/clear/browser-history")
def clear_browser_history_evidence(user_id: str = Depends(security.get_current_user_id), db: Session = Depends(get_db)):
    """Delete imported browser/search-history EvidenceUnits for the current user."""

    try:
        deleted = _clear_evidence_by_source_type_values(db, user_id, ["BrowserHistory", "ActivityTrace"])
        return {"status": "success", "deleted": deleted, "source_type": "BrowserHistory"}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/clear/all")
def clear_all_evidence(user_id: str = Depends(security.get_current_user_id), db: Session = Depends(get_db)):
    """Delete all EvidenceUnits for the current user.

    Intended for automated test cleanup and local connector re-testing. It does
    not disconnect OAuth accounts and does not delete uploaded documents.
    """

    try:
        units = db.query(models.EvidenceUnit).filter(models.EvidenceUnit.user_id == user_id).all()
        deleted = _delete_evidence_unit_ids(db, user_id, [unit.id for unit in units])
        return {"status": "success", "deleted": deleted, "scope": "all_evidence"}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/action-list")
def reconstruct_current_action_list(user_id: str = Depends(security.get_current_user_id), db: Session = Depends(get_db)):
    try:
        return evidence_service.reconstruct_action_list(db, user_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/demo-action-list")
def seed_demo_action_list(user_id: str = Depends(security.get_current_user_id), db: Session = Depends(get_db)):
    """Seed the exact illustrative scenario from the CITDS draft as evidence units."""

    demo_units = [
        {"source_type": "Email", "title": "Official PhD review request - Anna Toth", "content": "Official request. Action: review the PhD dissertation draft for candidate Anna Toth. Deadline: 2026-06-03.", "source_timestamp": "2026-05-20T09:00:00", "thread_id": "phd-anna-toth", "relation_key": "phd-review-anna-toth"},
        {"source_type": "BrowserHistory", "title": "Visited doctoral review template page", "content": "Browser history: search for PhD review template and doctoral rules. This is contextual support only.", "source_timestamp": "2026-05-21T10:00:00", "relation_key": "phd-review-anna-toth"},
        {"source_type": "Calendar", "title": "Data Science exam announcement obligation", "content": "Course obligation. Action: announce the Data Science exam dates. Deadline: 2026-06-05.", "source_timestamp": "2026-05-22T12:00:00", "relation_key": "data-science-exam-announcement"},
        {"source_type": "Email", "title": "Kosice research trip invitation accepted", "content": "Invitation accepted for a three-day research trip. Action: book a hotel in Kosice for the three-day research trip. Deadline: 2026-06-10.", "source_timestamp": "2026-05-23T14:00:00", "relation_key": "kosice-trip"},
        {"source_type": "BrowserHistory", "title": "Kosice hotel and transport searches", "content": "Browser history: searched hotel in Kosice, map, transport, program pages. Contextual support, cannot create obligation alone.", "source_timestamp": "2026-05-24T16:00:00", "relation_key": "kosice-trip"},
        {"source_type": "Email", "title": "Student emails about database systems course", "content": "Five unanswered student emails about the database systems course. Action: answer five unanswered student emails about the database systems course.", "source_timestamp": "2026-05-25T08:30:00", "thread_id": "db-course-students", "relation_key": "database-systems-student-emails"},
        {"source_type": "Email", "title": "Previous trip documentation task completed", "content": "The previous-trip documentation task was completed and closed. This contrastive evidence closes the earlier reporting obligation.", "source_timestamp": "2026-05-18T11:00:00", "relation_key": "previous-trip-documentation"},
        {"source_type": "BrowserHistory", "title": "Previous trip invoice form page", "content": "Browser history: visited reimbursement form and invoice page for previous trip. Contextual only.", "source_timestamp": "2026-05-17T11:00:00", "relation_key": "previous-trip-documentation"},
    ]
    try:
        created = evidence_service.import_evidence_units(db, user_id, demo_units)
        return {"status": "success", "imported": len(created), "ids": [u.id for u in created]}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
