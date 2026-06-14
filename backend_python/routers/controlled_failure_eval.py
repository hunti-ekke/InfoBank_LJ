from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

import controlled_failure
import evidence_service
import security
from database import get_db

router = APIRouter(prefix="/api/citds", tags=["Controlled Failure Evaluation"])


@router.get("/controlled-failure-matrix")
def controlled_failure_matrix(
    user_id: str = Depends(security.get_current_user_id),
    db: Session = Depends(get_db),
):
    reconstruction = evidence_service.reconstruct_action_list(db, user_id)
    open_items = reconstruction.get("open_items", [])
    closed_items = reconstruction.get("closed_items", [])
    contextual_only = reconstruction.get("contextual_only", [])
    metadata_only = reconstruction.get("metadata_only", [])
    excluded_only = reconstruction.get("excluded_only", [])
    controlled_failures = reconstruction.get("controlled_failures", [])
    classified_units = reconstruction.get("classified_units", [])

    role_counts = {
        "primary": sum(1 for unit in classified_units if unit.get("role") == evidence_service.ACTION_ROLE_PRIMARY),
        "contextual": sum(1 for unit in classified_units if unit.get("role") == evidence_service.ACTION_ROLE_CONTEXTUAL),
        "contrastive": sum(1 for unit in classified_units if unit.get("role") == evidence_service.ACTION_ROLE_CONTRASTIVE),
        "governance-excluded": sum(1 for unit in classified_units if unit.get("role") == evidence_service.ACTION_ROLE_EXCLUDED),
    }
    evidence_state = {
        "open_items": len(open_items),
        "closed_items": len(closed_items),
        "contextual_only": len(contextual_only),
        "metadata_only": len(metadata_only),
        "governance_excluded": len(excluded_only),
        "classified_units": len(classified_units),
        "source_roles": controlled_failure.summarize_source_roles(role_counts),
    }

    contextual_failure = controlled_failure.build_controlled_failure(
        controlled_failure.CF_CONTEXTUAL_ONLY,
        "Contextual activity evidence is not sufficient to create an action item without primary obligation evidence.",
        evidence_state=evidence_state,
        safe_output="Related activity may be shown as context, but no new task is inferred.",
        next_steps=["Import primary evidence such as an e-mail request or accepted assignment."],
        trace={"pipeline": "action_list_reconstruction", "rule": "browser_history_contextual_only"},
    )
    metadata_failure = controlled_failure.build_controlled_failure(
        controlled_failure.CF_METADATA_ONLY,
        "Metadata-only evidence can route or identify a source, but its content cannot support factual claims.",
        evidence_state=evidence_state,
        safe_output="Only metadata-level information may be exposed.",
        next_steps=["Request full access or use another primary source."],
        trace={"pipeline": "governed_rag", "rule": "metadata_content_withheld"},
    )
    excluded_failure = controlled_failure.build_controlled_failure(
        controlled_failure.CF_GOVERNANCE_DENIED,
        "Governance-excluded evidence cannot be used for answer generation under the current policy.",
        evidence_state=evidence_state,
        safe_output="No answer evidence is taken from excluded sources.",
        next_steps=["Change policy only if the user has the right to do so."],
        trace={"pipeline": "governed_rag", "rule": "excluded_source_not_used"},
    )

    browser_primary_count = sum(
        1 for unit in classified_units
        if unit.get("source_type") == "BrowserHistory" and unit.get("role") == evidence_service.ACTION_ROLE_PRIMARY
    )
    scenarios = [
        controlled_failure.scenario_result(
            "CF1",
            "Primary evidence supports open action items",
            "Open action items require primary evidence.",
            f"open_items={len(open_items)}, primary_units={role_counts['primary']}",
            len(open_items) == 0 or role_counts["primary"] >= 1,
        ),
        controlled_failure.scenario_result(
            "CF2",
            "Browser/search-history is contextual-only",
            "Contextual activity must not create a task alone.",
            f"contextual_only={len(contextual_only)}",
            all(item.get("status") == "contextual_only_not_action" for item in contextual_only),
            contextual_failure if contextual_only else None,
        ),
        controlled_failure.scenario_result(
            "CF3",
            "Metadata-only content is withheld",
            "Metadata can route but cannot support content claims.",
            f"metadata_only={len(metadata_only)}",
            all(item.get("status") == "metadata_only_not_action" for item in metadata_only),
            metadata_failure if metadata_only else None,
        ),
        controlled_failure.scenario_result(
            "CF4",
            "Governance-excluded evidence is not used",
            "Excluded sources must not support generation.",
            f"governance_excluded={len(excluded_only)}",
            all(item.get("status") == "governance_excluded" for item in excluded_only),
            excluded_failure if excluded_only else None,
        ),
        controlled_failure.scenario_result(
            "CF5",
            "Contrastive evidence closes candidates",
            "Closed items should include contrastive evidence.",
            f"closed_items={len(closed_items)}",
            all(item.get("R", {}).get("contrastive", 0) > 0 for item in closed_items),
        ),
        controlled_failure.scenario_result(
            "CF6",
            "Unsupported action avoidance",
            "No BrowserHistory unit is promoted to primary obligation evidence.",
            f"browser_primary={browser_primary_count}",
            browser_primary_count == 0,
            contextual_failure if contextual_only else None,
        ),
    ]
    passed = sum(1 for scenario in scenarios if scenario["pass"])
    return {
        "status": "success",
        "summary": {
            "passed": passed,
            "total": len(scenarios),
            "score": round(passed / max(1, len(scenarios)), 3),
            "controlled_failures": len(controlled_failures),
            **evidence_state,
        },
        "controlled_failure_schema": {
            "status": "controlled_failure",
            "fields": ["reason", "message", "evidence_state", "policy_state", "safe_output", "next_steps", "trace"],
        },
        "scenarios": scenarios,
        "action_list_controlled_failures": controlled_failures,
    }