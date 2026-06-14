# Controlled Failure Evaluation Matrix

This document summarizes the prototype-level evaluation cases used to answer the reviewer request for more concrete empirical validation. The endpoint `/api/citds/controlled-failure-matrix` exposes the same checks over the current user's imported EvidenceUnits.

| ID | Scenario | Expected behavior | Implementation check |
|---|---|---|---|
| CF1 | Primary evidence supports open action items | Open action items require permitted primary evidence. | Open items are accepted only when primary evidence is present. |
| CF2 | Browser/search-history is contextual-only | Activity traces may provide context but cannot create obligations alone. | BrowserHistory must never become primary obligation evidence. |
| CF3 | Metadata-only content is withheld | Metadata can route or identify a source, but cannot support content claims. | Metadata-only evidence is classified as non-action/content-withheld. |
| CF4 | Governance-excluded evidence is not used | Excluded evidence must not support answer generation. | Governance-excluded items are reported separately and not used as proof. |
| CF5 | Contrastive evidence closes candidates | Completion, cancellation, or later contradiction closes or weakens a candidate. | Closed items require contrastive evidence in the role summary. |
| CF6 | Unsupported action avoidance | The assistant must not infer tasks from weak or contextual evidence. | No BrowserHistory evidence is promoted to primary evidence. |

## Controlled failure response shape

```json
{
  "status": "controlled_failure",
  "reason": "contextual_only_evidence",
  "message": "Contextual activity evidence is not sufficient to create an action item without primary obligation evidence.",
  "evidence_state": {},
  "policy_state": {},
  "safe_output": "Related activity may be shown as context, but no new task is inferred.",
  "next_steps": [],
  "trace": {}
}
```

## Paper wording

The current prototype implements controlled failure for governed RAG cases: missing primary evidence, metadata-only access, governance-excluded evidence, contextual-only browser/search evidence, contrastive closure, and unsupported action-item avoidance. It does not claim to implement a general LLM safety framework, moderation layer, prompt-injection shield, or human escalation workflow.
