from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

import controlled_failure as cf
import evidence_service
import security
from database import get_db

router = APIRouter(prefix="/api/evidence", tags=["Action List"])


def _build_failures(result):
    counts = result.get("counts", {})
    evidence_state = {
        "counts": counts,
        "open_items": counts.get("open", 0),
        "closed_items": counts.get("closed", 0),
        "contextual_only": counts.get("contextual_only", 0),
        "metadata_only": counts.get("metadata_only", 0),
        "governance_excluded": counts.get("excluded_only", 0),
        "evidence_units": counts.get("evidence_units", 0),
    }
    policy_state = result.get("policy", {})
    failures = []
    if evidence_state["evidence_units"] == 0:
        failures.append(cf.build_controlled_failure(cf.CF_NO_RELEVANT_SOURCE, "No evidence units are available for action-list reconstruction.", evidence_state=evidence_state, policy_state=policy_state, safe_output="No action list can be reconstructed until evidence is imported.", trace={"pipeline": "action_list_reconstruction"}))
    if result.get("contextual_only"):
        failures.append(cf.build_controlled_failure(cf.CF_CONTEXTUAL_ONLY, "Contextual evidence cannot create an obligation without primary evidence.", evidence_state=evidence_state, policy_state=policy_state, safe_output="Related activity is kept as context; no task is inferred.", trace={"rule": "contextual_only_not_action"}))
    if result.get("metadata_only"):
        failures.append(cf.build_controlled_failure(cf.CF_METADATA_ONLY, "Metadata-only evidence cannot support action content.", evidence_state=evidence_state, policy_state=policy_state, safe_output="Only metadata-level information may be exposed.", trace={"rule": "metadata_only_not_action"}))
    if result.get("excluded_only"):
        failures.append(cf.build_controlled_failure(cf.CF_GOVERNANCE_DENIED, "Governance-excluded evidence cannot be used.", evidence_state=evidence_state, policy_state=policy_state, safe_output="No task is inferred from excluded evidence.", trace={"rule": "governance_excluded"}))
    if result.get("closed_items") and not result.get("open_items"):
        failures.append(cf.build_controlled_failure(cf.CF_CONTRASTIVE, "Candidate action evidence is closed or cancelled by contrastive evidence.", evidence_state=evidence_state, policy_state=policy_state, safe_output="No open item is returned because the candidate appears closed.", trace={"rule": "contrastive_closure"}))
    if not result.get("open_items") and not failures and evidence_state["evidence_units"] > 0:
        failures.append(cf.build_controlled_failure(cf.CF_NO_PRIMARY_EVIDENCE, "No open action item is supported by primary evidence.", evidence_state=evidence_state, policy_state=policy_state, safe_output="I found no open action items supported by primary evidence.", trace={"rule": "no_primary_open_item"}))
    return failures


@router.get("/action-list")
def action_list(user_id: str = Depends(security.get_current_user_id), db: Session = Depends(get_db)):
    try:
        result = evidence_service.reconstruct_action_list(db, user_id)
        failures = _build_failures(result)
        result["controlled_failures"] = failures
        result["controlled_failure"] = failures[0] if failures else None
        result["status"] = "success" if result.get("open_items") else "controlled_failure"
        return result
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))