from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

import evidence_service
import models
import relevance
import security
from database import get_db

router = APIRouter(prefix="/api/citds", tags=["CITDS Evaluation"])


@router.get("/self-test")
def citds_self_test(
    user_id: str = Depends(security.get_current_user_id),
    db: Session = Depends(get_db),
):
    reconstruction = evidence_service.reconstruct_action_list(db, user_id)
    open_items = reconstruction.get("open_items", [])
    closed_items = reconstruction.get("closed_items", [])
    contextual_only = reconstruction.get("contextual_only", [])
    metadata_only = reconstruction.get("metadata_only", [])
    excluded_only = reconstruction.get("excluded_only", [])
    classified_units = reconstruction.get("classified_units", [])

    checks = []
    checks.append({
        "name": "primary_evidence_required_for_open_items",
        "passed": all(item.get("R", {}).get("primary", 0) > 0 for item in open_items),
        "details": "Every open action item must have at least one primary evidence unit.",
    })
    checks.append({
        "name": "contextual_only_does_not_create_open_task",
        "passed": all(item.get("status") == "contextual_only_not_action" for item in contextual_only),
        "details": "Browser/activity evidence alone must stay contextual and must not create obligations.",
    })
    checks.append({
        "name": "metadata_only_does_not_create_open_task",
        "passed": all(item.get("status") == "metadata_only_not_action" for item in metadata_only),
        "details": "Metadata-only evidence may route but must not describe an obligation from hidden content.",
    })
    checks.append({
        "name": "governance_excluded_does_not_create_open_task",
        "passed": all(item.get("status") == "governance_excluded" for item in excluded_only),
        "details": "Governance-excluded evidence must not support generation or action reconstruction.",
    })
    checks.append({
        "name": "contrastive_evidence_closes_items",
        "passed": all(item.get("R", {}).get("contrastive", 0) > 0 for item in closed_items),
        "details": "Closed/cancelled/completed evidence should be represented as contrastive support.",
    })
    checks.append({
        "name": "no_browser_history_primary_role",
        "passed": all(not (unit.get("source_type") == "BrowserHistory" and unit.get("role") == evidence_service.ACTION_ROLE_PRIMARY) for unit in classified_units),
        "details": "BrowserHistory units must not become primary obligation evidence.",
    })
    checks.append({
        "name": "all_open_items_have_evidence_ids",
        "passed": all(bool(item.get("E")) for item in open_items),
        "details": "Every reconstructed action must carry evidence ids E.",
    })

    passed = sum(1 for check in checks if check["passed"])
    return {
        "status": "success",
        "summary": {
            "passed": passed,
            "total": len(checks),
            "score": round(passed / max(1, len(checks)), 3),
            "open_items": len(open_items),
            "closed_items": len(closed_items),
            "contextual_only": len(contextual_only),
            "metadata_only": len(metadata_only),
            "excluded_only": len(excluded_only),
            "classified_units": len(classified_units),
        },
        "checks": checks,
        "reconstruction": reconstruction,
    }


@router.get("/implementation-status")
def implementation_status(user_id: str = Depends(security.get_current_user_id)):
    components = [
        {"component": "query_profiling", "status": "implemented", "notes": "lexical terms, semantic tags, entities, task intent, purpose, expected genres"},
        {"component": "nine_level_usable_relevance", "status": "implemented", "notes": ", ".join(relevance.RELEVANCE_LEVELS)},
        {"component": "source_roles", "status": "implemented", "notes": "primary/contextual/analogical/contrastive/aggregate-only/governance-excluded"},
        {"component": "governance_prefilter", "status": "implemented", "notes": "Full/Aggregate/Metadata/Deny policy decisions"},
        {"component": "aggregate_only_hardening", "status": "implemented", "notes": "raw content withheld, aggregate-safe facts allowed"},
        {"component": "metadata_only_mode", "status": "implemented", "notes": "metadata visible, content withheld"},
        {"component": "controlled_failure_schema", "status": "implemented", "notes": "status, reason, evidence_state, policy_state, safe_output, next_steps, trace"},
        {"component": "controlled_failure_matrix", "status": "implemented", "notes": "/api/citds/controlled-failure-matrix"},
        {"component": "evidence_checking", "status": "implemented", "notes": "role summary, primary/aggregate/contrastive warnings"},
        {"component": "action_list_reconstruction", "status": "implemented", "notes": "primary/contextual/contrastive evidence grouping plus controlled failure buckets"},
        {"component": "browser_history_contextual_rule", "status": "implemented", "notes": "BrowserHistory cannot create obligations alone"},
        {"component": "gmail_oauth_sync", "status": "implemented", "notes": "Gmail OAuth URL, callback token storage, readonly Gmail sync into EvidenceUnits"},
        {"component": "browser_history_import", "status": "implemented", "notes": "history-shaped import endpoint maps visits to contextual EvidenceUnit"},
        {"component": "classifier", "status": "implemented", "notes": "deterministic classifier with optional LLM refinement"},
        {"component": "audit_trace", "status": "implemented", "notes": "chat trace, coverage, evidence_check in audit logs"},
        {"component": "scenario_based_evaluation", "status": "implemented", "notes": "/api/citds/self-test and /api/citds/controlled-failure-matrix"},
    ]
    implemented = [c for c in components if c["status"] == "implemented"]
    return {
        "status": "success",
        "summary": {
            "implemented_components": len(implemented),
            "total_components": len(components),
            "coverage": round(len(implemented) / len(components), 3),
        },
        "components": components,
    }