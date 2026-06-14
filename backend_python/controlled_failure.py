from __future__ import annotations

from typing import Any, Dict, List

CF_NO_RELEVANT_SOURCE = "no_relevant_source"
CF_NO_PERMITTED_SOURCE = "no_permitted_source"
CF_NO_PRIMARY_EVIDENCE = "no_primary_evidence"
CF_METADATA_ONLY = "metadata_only_access"
CF_AGGREGATE_ONLY = "aggregate_only_access"
CF_GOVERNANCE_DENIED = "governance_denied"
CF_CONTEXTUAL_ONLY = "contextual_only_evidence"
CF_CONTRASTIVE = "contrastive_or_defeated_evidence"
CF_UNSUPPORTED_ACTION = "unsupported_action_item"
CF_CONNECTOR_UNAVAILABLE = "connector_unavailable"

FAILURE_REASONS = {
    CF_NO_RELEVANT_SOURCE,
    CF_NO_PERMITTED_SOURCE,
    CF_NO_PRIMARY_EVIDENCE,
    CF_METADATA_ONLY,
    CF_AGGREGATE_ONLY,
    CF_GOVERNANCE_DENIED,
    CF_CONTEXTUAL_ONLY,
    CF_CONTRASTIVE,
    CF_UNSUPPORTED_ACTION,
    CF_CONNECTOR_UNAVAILABLE,
}


def build_controlled_failure(
    reason: str,
    message: str,
    *,
    evidence_state: Dict[str, Any] | None = None,
    policy_state: Dict[str, Any] | None = None,
    safe_output: str | None = None,
    next_steps: List[str] | None = None,
    trace: Dict[str, Any] | None = None,
    status: str = "controlled_failure",
) -> Dict[str, Any]:
    normalized_reason = reason if reason in FAILURE_REASONS else "controlled_failure"
    return {
        "status": status,
        "reason": normalized_reason,
        "message": message,
        "evidence_state": evidence_state or {},
        "policy_state": policy_state or {},
        "safe_output": safe_output or message,
        "next_steps": next_steps or [],
        "trace": trace or {},
    }


def summarize_source_roles(source_role_summary: Dict[str, Any] | None) -> Dict[str, Any]:
    summary = source_role_summary or {}
    return {
        "primary": int(summary.get("primary", 0) or 0),
        "contextual": int(summary.get("contextual", 0) or 0),
        "contrastive": int(summary.get("contrastive", 0) or 0),
        "aggregate_only": int(summary.get("aggregate-only", summary.get("aggregate_only", 0)) or 0),
        "governance_excluded": int(summary.get("governance-excluded", summary.get("governance_excluded", 0)) or 0),
    }


def scenario_result(identifier: str, name: str, expected: str, observed: str, passed: bool, failure: Dict[str, Any] | None = None) -> Dict[str, Any]:
    return {
        "id": identifier,
        "name": name,
        "expected": expected,
        "observed": observed,
        "pass": bool(passed),
        "controlled_failure": failure,
    }
