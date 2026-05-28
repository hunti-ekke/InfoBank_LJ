from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from sqlalchemy.orm import Session

import browser_history_connector
import gmail_connector
import models
import security
from database import get_db

router = APIRouter(prefix="/api/connectors", tags=["Connectors"])


@router.get("/gmail/status")
def gmail_status(
    user_id: str = Depends(security.get_current_user_id),
    db: Session = Depends(get_db),
):
    account = db.query(models.ConnectorAccount).filter(
        models.ConnectorAccount.user_id == user_id,
        models.ConnectorAccount.provider == gmail_connector.PROVIDER,
    ).first()
    return {
        "status": "success",
        "connected": bool(account and account.status == "connected"),
        "provider": gmail_connector.PROVIDER,
        "account_id": account.id if account else None,
    }


@router.get("/gmail/auth-url")
def gmail_auth_url(user_id: str = Depends(security.get_current_user_id)):
    try:
        return {"status": "success", **gmail_connector.create_authorization_url(user_id)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/gmail/callback")
def gmail_callback(request: Request, db: Session = Depends(get_db)):
    """OAuth callback.

    The OAuth state carries the InfoBank user_id. In production this should be a
    signed short-lived state token; for the prototype it is enough to complete
    the CITDS connector path.
    """

    user_id = request.query_params.get("state")
    if not user_id:
        raise HTTPException(status_code=400, detail="Missing OAuth state.")
    try:
        account = gmail_connector.store_callback_tokens(db, user_id, str(request.url))
        return {
            "status": "success",
            "message": "Gmail connected. You can close this tab and return to InfoBank.",
            "account_id": account.id,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/gmail/sync")
def gmail_sync(
    max_results: int = 25,
    query: str = "",
    user_id: str = Depends(security.get_current_user_id),
    db: Session = Depends(get_db),
):
    try:
        result = gmail_connector.sync_gmail_messages(db, user_id, max_results=max_results, query=query)
        return {"status": "success", **result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/browser-history/upload")
async def browser_history_upload(
    file: UploadFile = File(...),
    user_id: str = Depends(security.get_current_user_id),
    db: Session = Depends(get_db),
):
    try:
        raw = await file.read()
        result = browser_history_connector.import_history_export(db, user_id, file.filename or "history", raw)
        return {"status": "success", **result}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
