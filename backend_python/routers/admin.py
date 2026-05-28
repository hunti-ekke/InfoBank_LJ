import json
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

import models
import security
from database import get_db

router = APIRouter(prefix="/api/admin", tags=["Admin"])


def serialize_log(log: models.AuditLog):
    parsed_details = None
    if log.details:
        try:
            parsed_details = json.loads(log.details)
        except Exception:
            parsed_details = {"raw": log.details}
    return {
        "id": log.id,
        "user_id": log.user_id,
        "action": log.action,
        "target_id": log.target_id,
        "details": parsed_details,
        "timestamp": log.timestamp.isoformat() if log.timestamp else None,
    }


@router.get("/logs")
def get_my_audit_logs(
    action: str | None = None,
    limit: int = 50,
    user_id: str = Depends(security.get_current_user_id),
    db: Session = Depends(get_db),
):
    """Return the authenticated user's audit logs.

    Earlier prototype code accepted an arbitrary user_id query parameter. CITDS
    governance requires traceability without cross-user log disclosure, so this
    endpoint now scopes logs to the bearer-token user.
    """

    query = db.query(models.AuditLog).filter(models.AuditLog.user_id == user_id)
    if action:
        query = query.filter(models.AuditLog.action == action)
    logs = query.order_by(models.AuditLog.timestamp.desc()).limit(min(limit, 200)).all()
    return {"status": "success", "logs": [serialize_log(log) for log in logs]}


@router.get("/citds-traces")
def get_my_citds_traces(
    limit: int = 25,
    user_id: str = Depends(security.get_current_user_id),
    db: Session = Depends(get_db),
):
    logs = db.query(models.AuditLog).filter(
        models.AuditLog.user_id == user_id,
        models.AuditLog.action == "CHAT_ASK",
    ).order_by(models.AuditLog.timestamp.desc()).limit(min(limit, 100)).all()

    traces = []
    for log in logs:
        item = serialize_log(log)
        details = item.get("details") or {}
        traces.append({
            "id": item["id"],
            "timestamp": item["timestamp"],
            "question": details.get("question"),
            "answer": details.get("answer"),
            "status": details.get("status"),
            "query_profile": details.get("query_profile", {}),
            "source_roles": details.get("source_roles", {}),
            "relevance_levels": details.get("relevance_levels", {}),
            "governance": details.get("governance", {}),
            "evidence_check": details.get("evidence_check", {}),
        })
    return {"status": "success", "traces": traces}


@router.get("/citds-coverage")
def get_my_citds_coverage(
    user_id: str = Depends(security.get_current_user_id),
    db: Session = Depends(get_db),
):
    logs = db.query(models.AuditLog).filter(
        models.AuditLog.user_id == user_id,
        models.AuditLog.action == "CHAT_ASK",
    ).all()

    coverage = {
        "lexical": False,
        "semantic": False,
        "ontological": False,
        "pragmatic": False,
        "genre": False,
        "perlocutionary": False,
        "temporal_status": False,
        "governance": False,
        "evidential": False,
        "primary": False,
        "aggregate_only": False,
        "metadata_only": False,
        "contrastive": False,
        "controlled_failure": False,
    }

    for log in logs:
        try:
            details = json.loads(log.details or "{}")
        except Exception:
            continue
        levels = details.get("relevance_levels", {}) or {}
        for key in ["lexical", "semantic", "ontological", "pragmatic", "genre", "perlocutionary", "temporal_status", "governance", "evidential"]:
            if float(levels.get(key, 0) or 0) > 0:
                coverage[key] = True
        roles = details.get("source_roles", {}) or {}
        if roles.get("primary", 0) > 0:
            coverage["primary"] = True
        if roles.get("aggregate-only", 0) > 0:
            coverage["aggregate_only"] = True
        if roles.get("contrastive", 0) > 0:
            coverage["contrastive"] = True
        governance = details.get("governance", {}) or {}
        if governance.get("metadata_only_doc_ids"):
            coverage["metadata_only"] = True
        if details.get("status") in {"not_found", "rejected", "metadata_only"}:
            coverage["controlled_failure"] = True

    passed = sum(1 for v in coverage.values() if v)
    return {
        "status": "success",
        "coverage": coverage,
        "summary": {
            "passed": passed,
            "total": len(coverage),
            "score": round(passed / max(1, len(coverage)), 3),
        },
    }
