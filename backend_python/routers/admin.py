from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
import models
from database import get_db

router = APIRouter(prefix="/api/admin", tags=["Admin"])

@router.get("/logs")
def get_audit_logs(
    action: str = None,
    user_id: str = None,
    limit: int = 50,
    db: Session = Depends(get_db)
):
    query = db.query(models.AuditLog)
    
    if action:
        query = query.filter(models.AuditLog.action == action)
    if user_id:
        query = query.filter(models.AuditLog.user_id == user_id)
        
    logs = query.order_by(models.AuditLog.timestamp.desc()).limit(limit).all()
    
    return {"status": "success", "logs": logs}