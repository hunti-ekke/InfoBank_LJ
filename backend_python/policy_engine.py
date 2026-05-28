"""Purpose-aware governance policy engine for CITDS retrieval.

The engine resolves each candidate source into one of four use decisions:
Full, Aggregate, Metadata, or Deny. It combines explicit policy rules,
document visibility, and direct user permissions. Retrieval and generation
must consume only the resulting decision, never raw caller-provided user_id.
"""

from __future__ import annotations

import datetime
from typing import Any, Dict, Iterable, List

from sqlalchemy.orm import Session

import models
import relevance


TARGET_DOCUMENT = "Document"
TARGET_EVIDENCE_UNIT = "EvidenceUnit"


def _now() -> datetime.datetime:
    return datetime.datetime.utcnow()


def _mode_value(mode: Any) -> str:
    if hasattr(mode, "value"):
        return mode.value
    return str(mode)


def _is_rule_active(rule: models.PolicyRule, purpose: str) -> bool:
    current = _now()
    if rule.purpose not in {"any", purpose}:
        return False
    if rule.valid_from and rule.valid_from > current:
        return False
    if rule.valid_until and rule.valid_until < current:
        return False
    return True


def _mode_to_use_decision(mode: str) -> str:
    normalized = mode.lower()
    if normalized == "full":
        return relevance.USE_FULL
    if normalized == "aggregate":
        return relevance.USE_AGGREGATE
    if normalized == "metadata":
        return relevance.USE_METADATA
    return relevance.USE_DENY


def _decision_to_role(decision: str) -> str:
    if decision == relevance.USE_FULL:
        return relevance.SOURCE_ROLE_PRIMARY
    if decision == relevance.USE_AGGREGATE:
        return relevance.SOURCE_ROLE_AGGREGATE_ONLY
    if decision == relevance.USE_METADATA:
        return relevance.SOURCE_ROLE_CONTEXTUAL
    return relevance.SOURCE_ROLE_GOVERNANCE_EXCLUDED


def resolve_document_access(db: Session, user_id: str, doc_id: str, purpose: str = "grounded_question_answering") -> Dict[str, Any]:
    """Resolve one document into a CITDS use decision."""

    doc = db.query(models.Document).filter(models.Document.id == doc_id).first()
    if not doc:
        return {
            "target_id": doc_id,
            "target_type": TARGET_DOCUMENT,
            "use_decision": relevance.USE_DENY,
            "source_role": relevance.SOURCE_ROLE_GOVERNANCE_EXCLUDED,
            "reason": "document_not_found",
            "policy_rule_id": None,
        }

    permission = db.query(models.UserDocumentPermission).filter(
        models.UserDocumentPermission.user_id == user_id,
        models.UserDocumentPermission.document_id == doc_id,
    ).first()

    policy_rules = db.query(models.PolicyRule).filter(
        models.PolicyRule.target_type == TARGET_DOCUMENT,
        models.PolicyRule.target_id == doc_id,
    ).all()
    active_rules = [rule for rule in policy_rules if _is_rule_active(rule, purpose)]

    if active_rules:
        deny_rules = [r for r in active_rules if _mode_value(r.access_mode) == models.PolicyAccessMode.Deny.value]
        if deny_rules:
            rule = deny_rules[0]
            decision = relevance.USE_DENY
            return {
                "target_id": doc_id,
                "target_type": TARGET_DOCUMENT,
                "use_decision": decision,
                "source_role": _decision_to_role(decision),
                "reason": "explicit_policy_deny",
                "policy_rule_id": rule.id,
            }
        # Prefer the strongest available non-deny rule for this purpose.
        rank = {"Full": 3, "Aggregate": 2, "Metadata": 1}
        rule = sorted(active_rules, key=lambda r: rank.get(_mode_value(r.access_mode), 0), reverse=True)[0]
        decision = _mode_to_use_decision(_mode_value(rule.access_mode))
        return {
            "target_id": doc_id,
            "target_type": TARGET_DOCUMENT,
            "use_decision": decision,
            "source_role": _decision_to_role(decision),
            "reason": f"explicit_policy_{_mode_value(rule.access_mode).lower()}",
            "policy_rule_id": rule.id,
        }

    if permission:
        perm = _mode_value(permission.permission_type)
        if perm in {"Owner", "Reader"}:
            decision = relevance.USE_FULL
            reason = "direct_permission_full"
        elif perm == "Aggregate":
            decision = relevance.USE_AGGREGATE
            reason = "direct_permission_aggregate"
        elif perm == "Metadata":
            decision = relevance.USE_METADATA
            reason = "direct_permission_metadata"
        else:
            decision = relevance.USE_DENY
            reason = "direct_permission_unknown"
        return {
            "target_id": doc_id,
            "target_type": TARGET_DOCUMENT,
            "use_decision": decision,
            "source_role": _decision_to_role(decision),
            "reason": reason,
            "policy_rule_id": None,
        }

    if doc.visibility == "Aggregate":
        decision = relevance.USE_AGGREGATE
        reason = "document_visibility_aggregate"
    elif doc.visibility == "Metadata":
        decision = relevance.USE_METADATA
        reason = "document_visibility_metadata"
    else:
        decision = relevance.USE_DENY
        reason = "private_no_permission"

    return {
        "target_id": doc_id,
        "target_type": TARGET_DOCUMENT,
        "use_decision": decision,
        "source_role": _decision_to_role(decision),
        "reason": reason,
        "policy_rule_id": None,
    }


def resolve_document_access_bulk(db: Session, user_id: str, doc_ids: Iterable[str], purpose: str) -> Dict[str, Any]:
    decisions: Dict[str, str] = {}
    roles: Dict[str, str] = {}
    reasons: Dict[str, str] = {}
    policy_rule_ids: Dict[str, str | None] = {}

    for doc_id in set(doc_ids):
        resolved = resolve_document_access(db, user_id, doc_id, purpose)
        decisions[doc_id] = resolved["use_decision"]
        roles[doc_id] = resolved["source_role"]
        reasons[doc_id] = resolved["reason"]
        policy_rule_ids[doc_id] = resolved.get("policy_rule_id")

    usable_doc_ids = [doc_id for doc_id, d in decisions.items() if d in {relevance.USE_FULL, relevance.USE_AGGREGATE, relevance.USE_METADATA}]
    content_doc_ids = [doc_id for doc_id, d in decisions.items() if d in {relevance.USE_FULL, relevance.USE_AGGREGATE}]
    metadata_only_doc_ids = [doc_id for doc_id, d in decisions.items() if d == relevance.USE_METADATA]
    denied_doc_ids = [doc_id for doc_id, d in decisions.items() if d == relevance.USE_DENY]

    return {
        "use_decisions": decisions,
        "source_roles": roles,
        "policy_reasons": reasons,
        "policy_rule_ids": policy_rule_ids,
        "usable_doc_ids": usable_doc_ids,
        "content_doc_ids": content_doc_ids,
        "metadata_only_doc_ids": metadata_only_doc_ids,
        "denied_doc_ids": denied_doc_ids,
        "has_primary_evidence": any(roles.get(doc_id) == relevance.SOURCE_ROLE_PRIMARY for doc_id in content_doc_ids),
        "has_aggregate_evidence": any(roles.get(doc_id) == relevance.SOURCE_ROLE_AGGREGATE_ONLY for doc_id in content_doc_ids),
        "has_metadata_only": bool(metadata_only_doc_ids),
    }


def public_metadata_summary(db: Session, doc_id: str) -> Dict[str, Any]:
    doc = db.query(models.Document).filter(models.Document.id == doc_id).first()
    if not doc:
        return {"document_id": doc_id, "status": "missing"}
    kw_records = db.query(models.Keyword.word).join(
        models.DocumentKeyword,
        models.Keyword.id == models.DocumentKeyword.keyword_id,
    ).filter(models.DocumentKeyword.document_id == doc_id).all()
    return {
        "document_id": doc.id,
        "file_name": doc.file_path,
        "visibility": doc.visibility,
        "keywords": [row[0] for row in kw_records],
        "upload_date": doc.upload_date.isoformat() if doc.upload_date else None,
        "content": "[metadata-only: document content withheld]",
    }


def create_policy_rule(
    db: Session,
    owner_user_id: str,
    target_type: str,
    target_id: str,
    purpose: str,
    access_mode: str,
    valid_from: datetime.datetime | None = None,
    valid_until: datetime.datetime | None = None,
) -> models.PolicyRule:
    import uuid

    mode = models.PolicyAccessMode(access_mode)
    rule = models.PolicyRule(
        id=str(uuid.uuid4()),
        owner_user_id=owner_user_id,
        target_type=target_type,
        target_id=target_id,
        purpose=purpose or "any",
        access_mode=mode,
        valid_from=valid_from,
        valid_until=valid_until,
    )
    db.add(rule)
    db.commit()
    db.refresh(rule)
    return rule
