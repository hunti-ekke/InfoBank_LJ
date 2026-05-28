"""Evidence checking and action-list reconstruction for InfoBank.

This module implements the CITDS paper's requirement that retrieved sources are
not passed to generation as undifferentiated context. Evidence is checked for
role, status, conflict, permission, and support strength before an answer is
accepted.
"""

import datetime
import hashlib
import json
import re
import uuid
from typing import Any, Dict, Iterable, List, Optional

from sqlalchemy.orm import Session

import citds_classifier
import models
import policy_engine
import relevance


ACTION_ROLE_PRIMARY = "primary"
ACTION_ROLE_CONTEXTUAL = "contextual"
ACTION_ROLE_CONTRASTIVE = "contrastive"
ACTION_ROLE_EXCLUDED = "governance-excluded"

ACTION_VERBS = ["review", "announce", "book", "answer", "reply", "prepare", "submit", "send"]
NON_ACTION_PATTERNS = [
    r"temporary\s+chatgpt\s+login\s+code",
    r"login\s+code",
    r"verification\s+code",
    r"multi-factor\s+authentication",
    r"two-factor\s+authentication",
    r"security\s+alert",
    r"third-party\s+github\s+application\s+has\s+been\s+added",
    r"application\s+has\s+been\s+added\s+to\s+your\s+account",
    r"if\s+you\s+did\s+not\s+make\s+this\s+request",
    r"unsubscribe",
    r"category_promotions",
    r"k[eé]szlet(?:riaszt[aá]s)?",
    r"legn[eé]pszer[uű]bb\s+iphone",
]


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


def _metadata(unit: models.EvidenceUnit) -> Dict[str, Any]:
    try:
        return json.loads(unit.metadata_json or "{}")
    except Exception:
        return {}


def _dedupe_key(unit: models.EvidenceUnit) -> str:
    meta = _metadata(unit)
    connector = meta.get("connector")
    message_id = meta.get("message_id")
    if connector in {"gmail", "gmail_oauth"} and message_id:
        return f"gmail-message:{message_id}"
    fingerprint = hashlib.sha1(
        f"{unit.user_id}|{unit.source_type}|{unit.title}|{unit.content}|{unit.relation_key}".encode("utf-8", errors="ignore")
    ).hexdigest()
    return f"evidence:{fingerprint}"


def dedupe_evidence_units(units: Iterable[models.EvidenceUnit]) -> List[models.EvidenceUnit]:
    """Keep one copy of each imported external evidence item.

    Re-running Gmail sync should not inflate the evidence set or the UI trace with
    duplicate message imports. The newest local row wins so manual re-syncs remain
    visible if metadata changes.
    """

    by_key: Dict[str, models.EvidenceUnit] = {}
    for unit in units:
        key = _dedupe_key(unit)
        current = by_key.get(key)
        if not current or (unit.created_at or datetime.datetime.min) >= (current.created_at or datetime.datetime.min):
            by_key[key] = unit
    return list(by_key.values())


def _citds_role_to_action_role(source_role: str, source_type: str, use_decision: str) -> str:
    if use_decision == relevance.USE_DENY or source_role == relevance.SOURCE_ROLE_GOVERNANCE_EXCLUDED:
        return ACTION_ROLE_EXCLUDED
    if use_decision == relevance.USE_METADATA:
        return ACTION_ROLE_CONTEXTUAL
    if source_role == relevance.SOURCE_ROLE_CONTRASTIVE:
        return ACTION_ROLE_CONTRASTIVE
    if source_type in {"BrowserHistory", "ActivityTrace"}:
        return ACTION_ROLE_CONTEXTUAL
    if source_role == relevance.SOURCE_ROLE_PRIMARY:
        return ACTION_ROLE_PRIMARY
    if source_role == relevance.SOURCE_ROLE_AGGREGATE_ONLY:
        return ACTION_ROLE_CONTEXTUAL
    return ACTION_ROLE_CONTEXTUAL


def is_non_action_notification(text: str) -> bool:
    lowered = text.lower()
    return any(re.search(pattern, lowered, flags=re.IGNORECASE) for pattern in NON_ACTION_PATTERNS)


def is_unowned_outbound_request(text: str) -> bool:
    lowered = text.lower()
    if "direction: outbound" not in lowered:
        return False
    if any(marker in lowered for marker in ["i will", "i'll", "i commit", "committed", "accepted"]):
        return False
    # An outbound "please review" is usually a request to somebody else, not the
    # user's own open task. It may still be contextual support for a thread.
    return any(re.search(rf"\bplease\s+{verb}\b", lowered) for verb in ACTION_VERBS)


def has_obligation_signal(text: str) -> bool:
    lowered = text.lower()
    if is_non_action_notification(text):
        return False
    if "action:" in lowered or "official request" in lowered:
        return True
    if any(re.search(rf"\bplease\s+{verb}\b", lowered) for verb in ACTION_VERBS):
        return True
    if any(re.search(rf"\b{verb}\b", lowered) for verb in ACTION_VERBS) and any(marker in lowered for marker in ["deadline", "due", " by 20"]):
        return True
    if any(marker in lowered for marker in ["must", "required", "assigned", "obligation", "reminder", "overdue"]):
        return True
    return False


def classify_evidence_unit(unit: models.EvidenceUnit, policy_resolution: Dict[str, Any] | None = None) -> Dict[str, Any]:
    text = f"{unit.title}\n{unit.content}"
    source_type = unit.source_type.value if hasattr(unit.source_type, "value") else str(unit.source_type)
    policy_resolution = policy_resolution or {
        "use_decision": relevance.USE_FULL,
        "source_role": relevance.SOURCE_ROLE_PRIMARY,
        "reason": "owned_evidence_full",
        "policy_rule_id": None,
    }
    use_decision = policy_resolution.get("use_decision", relevance.USE_FULL)

    classifier_text = text if use_decision != relevance.USE_METADATA else unit.title
    classifier_result = citds_classifier.classify_source(
        text=classifier_text,
        task_intent="current_action_list",
        use_decision=use_decision,
        use_llm=False,
    )

    action_role = _citds_role_to_action_role(classifier_result.get("source_role"), source_type, use_decision)
    warnings = classifier_result.setdefault("warnings", [])
    if use_decision != relevance.USE_METADATA and action_role == ACTION_ROLE_PRIMARY:
        if is_non_action_notification(text):
            action_role = ACTION_ROLE_CONTEXTUAL
            classifier_result["source_role"] = relevance.SOURCE_ROLE_CONTEXTUAL
            warnings.append("non_action_system_or_marketing_notification")
        elif is_unowned_outbound_request(text):
            action_role = ACTION_ROLE_CONTEXTUAL
            classifier_result["source_role"] = relevance.SOURCE_ROLE_CONTEXTUAL
            warnings.append("outbound_request_contextual_not_user_obligation")
        elif not has_obligation_signal(text):
            action_role = ACTION_ROLE_CONTEXTUAL
            classifier_result["source_role"] = relevance.SOURCE_ROLE_CONTEXTUAL
            warnings.append("no_explicit_user_obligation_signal")

    status = "unknown"
    if action_role == ACTION_ROLE_EXCLUDED:
        status = "governance_excluded"
    elif use_decision == relevance.USE_METADATA:
        status = "metadata_only"
    elif action_role == ACTION_ROLE_CONTRASTIVE:
        status = "closed"
    elif action_role == ACTION_ROLE_CONTEXTUAL:
        status = "contextual"
    elif action_role == ACTION_ROLE_PRIMARY:
        status = "open"

    due = None if use_decision == relevance.USE_METADATA else extract_due_date(text)
    action = "[metadata-only: evidence content withheld]" if use_decision == relevance.USE_METADATA else extract_action_text(text)
    object_label = None if use_decision == relevance.USE_METADATA else extract_object_label(text)
    content_summary = "[metadata-only: evidence content withheld]" if use_decision == relevance.USE_METADATA else summarize_text(unit.content)

    return {
        "id": unit.id,
        "source_type": source_type,
        "title": unit.title,
        "content_summary": content_summary,
        "timestamp": unit.source_timestamp.isoformat() if unit.source_timestamp else None,
        "thread_id": unit.thread_id,
        "relation_key": unit.relation_key or object_label or unit.thread_id or unit.id,
        "role": action_role,
        "citds_source_role": classifier_result.get("source_role"),
        "status": status,
        "due": due,
        "action": action,
        "object": object_label,
        "policy": policy_resolution,
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


def _clean_action(value: str) -> str:
    value = re.sub(r"\s+", " ", value).strip(" .:-")
    value = re.sub(r"^please\s+", "", value, flags=re.IGNORECASE)
    value = re.sub(r"^action\s*[:\-]\s*", "", value, flags=re.IGNORECASE)
    return value[:160]


def extract_action_text(text: str) -> str:
    match = re.search(r"Action[:\s]+([^\n\.]+)", text, flags=re.IGNORECASE)
    if match:
        return _clean_action(match.group(1))

    subject_match = re.search(r"Subject:\s*(please\s+(?:review|announce|book|answer|reply|prepare|submit|send)[^\.\n]*)", text, flags=re.IGNORECASE)
    if subject_match:
        return _clean_action(subject_match.group(1))

    please_match = re.search(r"\bplease\s+(review|announce|book|answer|reply|prepare|submit|send)\b([^\.\n]*)", text, flags=re.IGNORECASE)
    if please_match:
        return _clean_action(" ".join(part for part in please_match.groups() if part))

    lines = [line.strip() for line in text.splitlines() if line.strip()]
    for line in lines:
        low = line.lower()
        if any(word in low for word in ACTION_VERBS):
            return _clean_action(line[:160])
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


def _normalise_action_key(value: str) -> str:
    value = value.lower()
    value = re.sub(r"[^a-z0-9]+", " ", value)
    value = re.sub(r"\b(please|gmail|message|direction|inbound|outbound|subject|body)\b", " ", value)
    return re.sub(r"\s+", " ", value).strip()[:120]


def _group_key(item: Dict[str, Any]) -> str:
    if item.get("role") in {ACTION_ROLE_PRIMARY, ACTION_ROLE_CONTRASTIVE} and item.get("action"):
        action_key = _normalise_action_key(item.get("action") or "")
        if action_key and action_key != "unspecified action":
            return f"action:{action_key}|due:{item.get('due') or ''}"
    return item.get("relation_key") or item["id"]


def reconstruct_action_list(db: Session, user_id: str) -> Dict[str, Any]:
    raw_units = db.query(models.EvidenceUnit).filter(models.EvidenceUnit.user_id == user_id).all()
    units = dedupe_evidence_units(raw_units)
    unit_ids = [unit.id for unit in units]
    policy_context = policy_engine.resolve_evidence_unit_access_bulk(
        db=db,
        user_id=user_id,
        unit_ids=unit_ids,
        purpose="action_reconstruction",
    ) if unit_ids else {
        "use_decisions": {},
        "source_roles": {},
        "policy_reasons": {},
        "policy_rule_ids": {},
        "usable_unit_ids": [],
        "content_unit_ids": [],
        "metadata_only_unit_ids": [],
        "denied_unit_ids": [],
    }
    classified = [
        classify_evidence_unit(
            unit,
            {
                "use_decision": policy_context["use_decisions"].get(unit.id, relevance.USE_DENY),
                "source_role": policy_context["source_roles"].get(unit.id, relevance.SOURCE_ROLE_GOVERNANCE_EXCLUDED),
                "reason": policy_context["policy_reasons"].get(unit.id),
                "policy_rule_id": policy_context["policy_rule_ids"].get(unit.id),
            },
        )
        for unit in units
    ]

    grouped: Dict[str, Dict[str, Any]] = {}
    for item in classified:
        key = _group_key(item)
        if key not in grouped:
            grouped[key] = {
                "relation_key": key,
                "primary_evidence": [],
                "contextual_support": [],
                "contrastive_evidence": [],
                "excluded_evidence": [],
                "metadata_only_evidence": [],
            }
        if item["status"] == "metadata_only":
            grouped[key]["metadata_only_evidence"].append(item)
        elif item["role"] == ACTION_ROLE_PRIMARY:
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
    metadata_only: List[Dict[str, Any]] = []

    for group in grouped.values():
        primary = group["primary_evidence"]
        contextual = group["contextual_support"]
        contrastive = group["contrastive_evidence"]
        excluded = group["excluded_evidence"]
        metadata = group["metadata_only_evidence"]
        if primary and not contrastive:
            strongest = sorted(primary, key=lambda x: x.get("timestamp") or "")[-1]
            open_items.append({
                "actor": "user",
                "action": strongest.get("action") or "Unspecified action",
                "object": strongest.get("object"),
                "due": strongest.get("due"),
                "status": "open",
                "E": [e["id"] for e in primary + contextual],
                "R": {"primary": len(primary), "contextual": len(contextual), "contrastive": 0, "excluded": len(excluded), "metadata_only": len(metadata)},
                "evidence": {"primary": primary, "contextual": contextual, "contrastive": [], "excluded": excluded, "metadata_only": metadata},
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
                "R": {"primary": len(primary), "contextual": len(contextual), "contrastive": len(contrastive), "excluded": len(excluded), "metadata_only": len(metadata)},
                "evidence": {"primary": primary, "contextual": contextual, "contrastive": contrastive, "excluded": excluded, "metadata_only": metadata},
            })
        elif contextual:
            contextual_only.append({
                "relation_key": group["relation_key"],
                "status": "contextual_only_not_action",
                "reason": "Contextual/activity evidence cannot create an obligation without primary evidence.",
                "evidence": contextual,
            })
        elif metadata:
            metadata_only.append({
                "relation_key": group["relation_key"],
                "status": "metadata_only_not_action",
                "reason": "Metadata-only evidence cannot create or describe an action obligation without content access.",
                "evidence": metadata,
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
        "policy": policy_context,
        "open_items": open_items,
        "closed_items": closed_items,
        "contextual_only": contextual_only,
        "metadata_only": metadata_only,
        "excluded_only": excluded_only,
        "counts": {
            "open": len(open_items),
            "closed": len(closed_items),
            "contextual_only": len(contextual_only),
            "metadata_only": len(metadata_only),
            "excluded_only": len(excluded_only),
            "evidence_units": len(classified),
            "raw_evidence_units": len(raw_units),
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
