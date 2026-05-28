"""Evidence checking and action-list reconstruction for InfoBank.

This module implements the CITDS paper's requirement that retrieved sources are
not passed to generation as undifferentiated context. Evidence is checked for
role, status, conflict, permission, and support strength before an answer is
accepted.
"""

import datetime
import json
import re
import uuid
from typing import Any, Dict, Iterable, List, Optional

from sqlalchemy.orm import Session

import citds_classifier
import models
import relevance


ACTION_ROLE_PRIMARY = "primary"
ACTION_ROLE_CONTEXTUAL = "contextual"
ACTION_ROLE_CONTRASTIVE = "contrastive"
ACTION_ROLE_EXCLUDED = "governance-excluded"


def parse_timestamp(value: Optional[str]) -> Optional[datetime.datetime]:
    if not value:
        return None
    try:
        return datetime.datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
    except Exception:
        return None


def create_evidence_unit(db: Session, user_id: str, payload: Dict[str, Any]) -> models.EvidenceUnit:
    unit = models.EvidenceUnit(
        id=str(uuid.uuid4()),
        user_id=user_id,
        source_type=models.EvidenceSourceType(payload.get("source_type", "Other")),
        title=payload.get("title") or "Untitled evidence unit",
        content=payload.get("content") or "",
        source_timestamp=parse_timestamp(payload.get("source_timestamp")),
        thread_id=payload.get("thread_id"),
        relation_key=payload.get("relation_key"),
        metadata_json=json.dumps(payload.get("metadata", {}), ensure_ascii=False),
    )
    db.add(unit)
    db.commit()
    db.refresh(unit)
    return unit


def import_evidence_units(db: Session, user_id: str, units: Iterable[Dict[str, Any]]) -> List[models.EvidenceUnit]:
    created: List[models.EvidenceUnit] = []
    for payload in units:
        created.append(create_evidence_unit(db, user_id, payload))
    return created


def _source_type_default_use_decision(source_type: str) -> str:
    """Evidence units are owned user data by default.

    BrowserHistory and ActivityTrace are full-access data, but their evidential
    source role remains contextual through the classifier. This distinction is
    important: governance can allow access while evidential logic still prevents
    a task from being created from activity traces alone.
    """

    if source_type in {"BrowserHistory", "ActivityTrace"}:
        return relevance.USE_FULL
    return relevance.USE_FULL


def _citds_role_to_action_role(source_role: str, source_type: str) -> str:
    if source_role == relevance.SOURCE_ROLE_GOVERNANCE_EXCLUDED:
        return ACTION_ROLE_EXCLUDED
    if source_role == relevance.SOURCE_ROLE_CONTRASTIVE:
        return ACTION_ROLE_CONTRASTIVE
    if source_type in {"BrowserHistory", "ActivityTrace"}:
        return ACTION_ROLE_CONTEXTUAL
    if source_role == relevance.SOURCE_ROLE_PRIMARY:
        return ACTION_ROLE_PRIMARY
    if source_role == relevance.SOURCE_ROLE_AGGREGATE_ONLY:
        return ACTION_ROLE_CONTEXTUAL
    return ACTION_ROLE_CONTEXTUAL


def classify_evidence_unit(unit: models.EvidenceUnit) -> Dict[str, Any]:
    text = f"{unit.title}\n{unit.content}"
    source_type = unit.source_type.value if hasattr(unit.source_type, "value") else str(unit.source_type)
    use_decision = _source_type_default_use_decision(source_type)
    classifier_result = citds_classifier.classify_source(
        text=text,
        task_intent="current_action_list",
        use_decision=use_decision,
        use_llm=False,
    )

    action_role = _citds_role_to_action_role(classifier_result.get("source_role"), source_type)
    lowered = text.lower()
    status = "unknown"
    if action_role == ACTION_ROLE_CONTRASTIVE:
        status = "closed"
    elif action_role == ACTION_ROLE_CONTEXTUAL:
        status = "contextual"
    elif action_role == ACTION_ROLE_PRIMARY:
        status = "open"

    due = extract_due_date(text)
    action = extract_action_text(text)
    object_label = extract_object_label(text)

    return {
        "id": unit.id,
        "source_type": source_type,
        "title": unit.title,
        "content_summary": summarize_text(unit.content),
        "timestamp": unit.source_timestamp.isoformat() if unit.source_timestamp else None,
        "thread_id": unit.thread_id,
        "relation_key": unit.relation_key or object_label or unit.thread_id or unit.id,
        "role": action_role,
        "citds_source_role": classifier_result.get("source_role"),
        "status": status,
        "due": due,
        "action": action,
        "object": object_label,
        "classifier": classifier_result,
        "signals": {
            "primary": classifier_result.get("speech_acts", []),
            "contextual": ["activity_trace"] if source_type in {"BrowserHistory", "ActivityTrace"} else [],
            "closure": [s for s in classifier_result.get("speech_acts", []) if s in {"completion", "cancellation"}],
            "genre": classifier_result.get("genre", []),
            "temporal_status": classifier_result.get("temporal_status", []),
        },
    }


def extract_due_date(text: str) -> Optional[str]:
    match = re.search(r"20\d{2}-\d{2}-\d{2}", text)
    if match:
        return match.group(0)
    due_match = re.search(r"(?:deadline|due|by)[:\s]+([^\.\n]+)", text, flags=re.IGNORECASE)
    if due_match:
        return due_match.group(1).strip()[:80]
    return None


def extract_action_text(text: str) -> str:
    # Prefer explicit action fields in synthetic/imported evidence units.
    match = re.search(r"Action[:\s]+([^\n\.]+)", text, flags=re.IGNORECASE)
    if match:
        return match.group(1).strip()
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    for line in lines:
        low = line.lower()
        if any(word in low for word in ["review", "announce", "book", "answer", "reply", "prepare", "submit", "send"]):
            return line[:160]
    return lines[0][:160] if lines else "Unspecified action"


def extract_object_label(text: str) -> Optional[str]:
    candidates = [
        r"candidate\s+([A-Z][a-z]+\s+[A-Z][a-z]+)",
        r"course[:\s]+([^\n\.]+)",
        r"trip[:\s]+([^\n\.]+)",
        r"object[:\s]+([^\n\.]+)",
        r"task[:\s]+([^\n\.]+)",
    ]
    for pattern in candidates:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return match.group(1).strip()[:120]
    return None


def summarize_text(text: str, max_len: int = 220) -> str:
    clean = re.sub(r"\s+", " ", text).strip()
    return clean[:max_len] + ("..." if len(clean) > max_len else "")


def reconstruct_action_list(db: Session, user_id: str) -> Dict[str, Any]:
    units = db.query(models.EvidenceUnit).filter(models.EvidenceUnit.user_id == user_id).all()
    classified = [classify_evidence_unit(unit) for unit in units]

    grouped: Dict[str, Dict[str, Any]] = {}
    for item in classified:
        key = item.get("relation_key") or item["id"]
        if key not in grouped:
            grouped[key] = {
                "relation_key": key,
                "primary_evidence": [],
                "contextual_support": [],
                "contrastive_evidence": [],
                "excluded_evidence": [],
            }
        if item["role"] == ACTION_ROLE_PRIMARY:
            grouped[key]["primary_evidence"].append(item)
        elif item["role"] == ACTION_ROLE_CONTRASTIVE:
            grouped[key]["contrastive_evidence"].append(item)
        elif item["role"] == ACTION_ROLE_EXCLUDED:
            grouped[key]["excluded_evidence"].append(item)
        else:
            grouped[key]["contextual_support"].append(item)

    open_items: List[Dict[str, Any]] = []
    closed_items: List[Dict[str, Any]] = []
    contextual_only: List[Dict[str, Any]] = []
    excluded_only: List[Dict[str, Any]] = []

    for group in grouped.values():
        primary = group["primary_evidence"]
        contextual = group["contextual_support"]
        contrastive = group["contrastive_evidence"]
        excluded = group["excluded_evidence"]
        if primary and not contrastive:
            strongest = sorted(primary, key=lambda x: x.get("timestamp") or "")[-1]
            open_items.append({
                "actor": "user",
                "action": strongest.get("action") or "Unspecified action",
                "object": strongest.get("object"),
                "due": strongest.get("due"),
                "status": "open",
                "E": [e["id"] for e in primary + contextual],
                "R": {"primary": len(primary), "contextual": len(contextual), "contrastive": 0, "excluded": len(excluded)},
                "evidence": {"primary": primary, "contextual": contextual, "contrastive": [], "excluded": excluded},
            })
        elif primary and contrastive:
            strongest = sorted(primary, key=lambda x: x.get("timestamp") or "")[-1]
            closed_items.append({
                "actor": "user",
                "action": strongest.get("action") or "Unspecified action",
                "object": strongest.get("object"),
                "due": strongest.get("due"),
                "status": "closed_or_cancelled",
                "E": [e["id"] for e in primary + contextual + contrastive],
                "R": {"primary": len(primary), "contextual": len(contextual), "contrastive": len(contrastive), "excluded": len(excluded)},
                "evidence": {"primary": primary, "contextual": contextual, "contrastive": contrastive, "excluded": excluded},
            })
        elif contextual:
            contextual_only.append({
                "relation_key": group["relation_key"],
                "status": "contextual_only_not_action",
                "reason": "Contextual/activity evidence cannot create an obligation without primary evidence.",
                "evidence": contextual,
            })
        elif excluded:
            excluded_only.append({
                "relation_key": group["relation_key"],
                "status": "governance_excluded",
                "reason": "Evidence exists but is excluded by governance.",
                "evidence": excluded,
            })

    return {
        "status": "success",
        "open_items": open_items,
        "closed_items": closed_items,
        "contextual_only": contextual_only,
        "excluded_only": excluded_only,
        "counts": {
            "open": len(open_items),
            "closed": len(closed_items),
            "contextual_only": len(contextual_only),
            "excluded_only": len(excluded_only),
            "evidence_units": len(classified),
        },
        "classified_units": classified,
    }


def check_rag_evidence(sources: Iterable[Dict[str, Any]], query_profile: Dict[str, Any], governance: Dict[str, Any]) -> Dict[str, Any]:
    sources = list(sources)
    role_summary = relevance.summarize_source_roles(sources)
    level_summary = relevance.summarize_relevance_levels(sources)
    has_primary = role_summary.get(relevance.SOURCE_ROLE_PRIMARY, 0) > 0
    has_aggregate = role_summary.get(relevance.SOURCE_ROLE_AGGREGATE_ONLY, 0) > 0
    has_contrastive = role_summary.get(relevance.SOURCE_ROLE_CONTRASTIVE, 0) > 0
    has_metadata_only = bool(governance.get("metadata_only_doc_ids"))
    has_denied = bool(governance.get("denied_doc_ids"))

    required = query_profile.get("required_evidence_strength")
    warnings: List[str] = []
    decision = "answer_allowed"

    if has_denied:
        warnings.append("Some candidate sources were denied before generation.")
    if has_metadata_only:
        warnings.append("Some candidate sources are metadata-only; content claims must not rely on them.")
    if required == "primary_required_for_direct_claim" and not has_primary:
        decision = "controlled_failure_or_cautious_answer"
        warnings.append("Primary evidence is required for this task, but no primary source was retrieved.")
    if has_aggregate and not has_primary:
        warnings.append("Only aggregate evidence is available; do not quote individual source text.")
    if has_contrastive:
        warnings.append("Contrastive evidence was retrieved; check whether it closes, cancels, or weakens a candidate claim.")

    for source in sources:
        profile = source.get("usable_relevance", {})
        for warning in profile.get("evidence_warnings", []):
            if warning not in warnings:
                warnings.append(warning)

    return {
        "decision": decision,
        "role_summary": role_summary,
        "relevance_level_summary": level_summary,
        "has_primary": has_primary,
        "has_aggregate": has_aggregate,
        "has_contrastive": has_contrastive,
        "has_metadata_only": has_metadata_only,
        "warnings": warnings,
    }
