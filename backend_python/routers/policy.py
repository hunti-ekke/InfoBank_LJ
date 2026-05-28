from fastapi import APIRouter, Depends, Form, HTTPException
from sqlalchemy.orm import Session

import models
import policy_engine
import security
from database import get_db

router = APIRouter(prefix="/api/policy", tags=["Policy"])


@router.get("/rules")
def list_my_policy_rules(
    user_id: str = Depends(security.get_current_user_id),
    db: Session = Depends(get_db),
):
    rules = db.query(models.PolicyRule).filter(models.PolicyRule.owner_user_id == user_id).order_by(models.PolicyRule.created_at.desc()).all()
    return {
        "status": "success",
        "rules": [
            {
                "id": rule.id,
                "target_type": rule.target_type,
                "target_id": rule.target_id,
                "purpose": rule.purpose,
                "access_mode": rule.access_mode.value if hasattr(rule.access_mode, "value") else str(rule.access_mode),
                "valid_from": rule.valid_from.isoformat() if rule.valid_from else None,
                "valid_until": rule.valid_until.isoformat() if rule.valid_until else None,
                "created_at": rule.created_at.isoformat() if rule.created_at else None,
            }
            for rule in rules
        ],
    }


@router.post("/rules")
def create_policy_rule(
    target_type: str = Form(...),
    target_id: str = Form(...),
    purpose: str = Form("any"),
    access_mode: str = Form(...),
    user_id: str = Depends(security.get_current_user_id),
    db: Session = Depends(get_db),
):
    if target_type not in {policy_engine.TARGET_DOCUMENT, policy_engine.TARGET_EVIDENCE_UNIT}:
        raise HTTPException(status_code=400, detail="target_type must be Document or EvidenceUnit.")
    if access_mode not in {"Full", "Aggregate", "Metadata", "Deny"}:
        raise HTTPException(status_code=400, detail="access_mode must be Full, Aggregate, Metadata, or Deny.")

    if target_type == policy_engine.TARGET_DOCUMENT:
        owner_perm = db.query(models.UserDocumentPermission).filter(
            models.UserDocumentPermission.user_id == user_id,
            models.UserDocumentPermission.document_id == target_id,
            models.UserDocumentPermission.permission_type == models.PermissionType.Owner,
        ).first()
        if not owner_perm:
            raise HTTPException(status_code=403, detail="Only the document owner may create policy rules for this document.")
    else:
        unit = db.query(models.EvidenceUnit).filter(models.EvidenceUnit.id == target_id, models.EvidenceUnit.user_id == user_id).first()
        if not unit:
            raise HTTPException(status_code=403, detail="Only the evidence owner may create policy rules for this evidence unit.")

    try:
        rule = policy_engine.create_policy_rule(
            db=db,
            owner_user_id=user_id,
            target_type=target_type,
            target_id=target_id,
            purpose=purpose,
            access_mode=access_mode,
        )
        return {"status": "success", "rule_id": rule.id}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/rules/{rule_id}")
def delete_policy_rule(
    rule_id: str,
    user_id: str = Depends(security.get_current_user_id),
    db: Session = Depends(get_db),
):
    rule = db.query(models.PolicyRule).filter(models.PolicyRule.id == rule_id, models.PolicyRule.owner_user_id == user_id).first()
    if not rule:
        raise HTTPException(status_code=404, detail="Policy rule not found.")
    db.delete(rule)
    db.commit()
    return {"status": "success", "message": "Policy rule deleted."}


@router.get("/resolve/document/{doc_id}")
def resolve_document_policy(
    doc_id: str,
    purpose: str = "grounded_question_answering",
    user_id: str = Depends(security.get_current_user_id),
    db: Session = Depends(get_db),
):
    return {
        "status": "success",
        "resolution": policy_engine.resolve_document_access(db, user_id, doc_id, purpose),
    }


@router.get("/resolve/evidence-unit/{unit_id}")
def resolve_evidence_unit_policy(
    unit_id: str,
    purpose: str = "action_reconstruction",
    user_id: str = Depends(security.get_current_user_id),
    db: Session = Depends(get_db),
):
    return {
        "status": "success",
        "resolution": policy_engine.resolve_evidence_unit_access(db, user_id, unit_id, purpose),
    }
