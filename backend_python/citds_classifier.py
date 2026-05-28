"""CITDS classifier service.

The PDF requires more than embedding similarity: source genre, speech act,
temporal status, role, and confidence must be explicit and auditable. This file
provides a deterministic classifier with an optional LLM refinement contract.
The deterministic path is always available, so tests do not depend on an LLM.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List

import ai_service
import relevance


CLASSIFIER_VERSION = "citds-rule-llm-hybrid-v1"


def _hits(text: str, pattern_map: Dict[str, List[str]]) -> List[str]:
    out: List[str] = []
    for label, patterns in pattern_map.items():
        if any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in patterns):
            out.append(label)
    return out


GENRE_PATTERNS = {
    "email_or_message": [r"\bemail\b", r"\bmessage\b", r"\bthread\b", r"\bfrom:\b", r"\bto:\b", r"\binbox\b"],
    "calendar_or_schedule": [r"\bcalendar\b", r"\bschedule\b", r"\bdeadline\b", r"\bdue\b", r"\bmeeting\b", r"\bappointment\b"],
    "activity_trace": [r"\bbrowser\b", r"\bhistory\b", r"\bvisited\b", r"\bsearch\b", r"\burl\b", r"\bactivity trace\b"],
    "statistics": [r"\baverage\b", r"\bmean\b", r"\bmedian\b", r"\bcount\b", r"\bpercentage\b", r"\bstatistics\b", r"\baggregate\b"],
    "policy": [r"\bpolicy\b", r"\bpermission\b", r"\bgovernance\b", r"\bowner\b", r"\bmetadata\b", r"\baggregate\b"],
    "contract_or_terms": [r"\bcontract\b", r"\bagreement\b", r"\bterms\b", r"\bclause\b"],
    "manual_or_instruction": [r"\bmanual\b", r"\binstruction\b", r"\bsetup\b", r"\bconfiguration\b"],
}

SPEECH_ACT_PATTERNS = {
    "request": [r"\bplease\b", r"\bcould you\b", r"\bcan you\b", r"\brequest\b", r"\basked\b"],
    "assignment": [r"\bassigned\b", r"\bmust\b", r"\brequired\b", r"\bobligation\b", r"\bshould\b"],
    "commitment": [r"\bi will\b", r"\baccepted\b", r"\bcommitted\b", r"\bparticipation\b"],
    "completion": [r"\bcompleted\b", r"\bclosed\b", r"\bdone\b", r"\banswered\b", r"\bsent reply\b"],
    "cancellation": [r"\bcancelled\b", r"\bcanceled\b", r"\bwithdrawn\b", r"\bnot needed\b"],
    "reminder": [r"\breminder\b", r"\bfollow up\b", r"\boverdue\b"],
    "informative": [r"\binformation\b", r"\bnote\b", r"\bsummary\b"],
}

TEMPORAL_PATTERNS = {
    "explicit_deadline": [r"\bby\s+20\d{2}-\d{2}-\d{2}\b", r"\bdue\s+20\d{2}-\d{2}-\d{2}\b", r"\bdeadline\b"],
    "open": [r"\bopen\b", r"\bunanswered\b", r"\bpending\b", r"\bcurrent\b", r"\bactive\b"],
    "closed": [r"\bclosed\b", r"\bcompleted\b", r"\bdone\b", r"\bcancelled\b", r"\bcanceled\b", r"\banswered\b"],
    "recent": [r"\brecent\b", r"\btoday\b", r"\byesterday\b", r"\bthis week\b"],
}


def deterministic_classify(text: str, task_intent: str = "general_document_question", use_decision: str = relevance.USE_FULL) -> Dict[str, Any]:
    genre = _hits(text, GENRE_PATTERNS) or ["generic_document"]
    speech_acts = _hits(text, SPEECH_ACT_PATTERNS) or ["informative"]
    temporal_status = _hits(text, TEMPORAL_PATTERNS) or ["unspecified"]

    role = relevance.SOURCE_ROLE_PRIMARY
    warnings: List[str] = []
    if use_decision == relevance.USE_DENY:
        role = relevance.SOURCE_ROLE_GOVERNANCE_EXCLUDED
        warnings.append("governance_denied_do_not_use")
    elif use_decision == relevance.USE_METADATA:
        role = relevance.SOURCE_ROLE_CONTEXTUAL
        warnings.append("metadata_only_content_withheld")
    elif use_decision == relevance.USE_AGGREGATE:
        role = relevance.SOURCE_ROLE_AGGREGATE_ONLY
        warnings.append("aggregate_only_do_not_quote_individual_content")
    elif "closed" in temporal_status or any(a in speech_acts for a in ["completion", "cancellation"]):
        role = relevance.SOURCE_ROLE_CONTRASTIVE
        warnings.append("may_close_cancel_or_contradict_an_item")
    elif "activity_trace" in genre:
        role = relevance.SOURCE_ROLE_CONTEXTUAL
        warnings.append("contextual_support_not_direct_proof")
    elif task_intent == "current_action_list" and not any(a in speech_acts for a in ["request", "assignment", "commitment", "reminder"]):
        role = relevance.SOURCE_ROLE_CONTEXTUAL
        warnings.append("no_obligation_speech_act_detected")

    confidence = 0.45
    confidence += 0.15 if genre != ["generic_document"] else 0
    confidence += 0.15 if speech_acts != ["informative"] else 0
    confidence += 0.10 if temporal_status != ["unspecified"] else 0
    confidence += 0.10 if role in {relevance.SOURCE_ROLE_PRIMARY, relevance.SOURCE_ROLE_CONTRASTIVE, relevance.SOURCE_ROLE_AGGREGATE_ONLY} else 0
    confidence = round(min(0.95, confidence), 3)

    return {
        "classifier_version": CLASSIFIER_VERSION,
        "mode": "deterministic",
        "genre": genre,
        "speech_acts": speech_acts,
        "temporal_status": temporal_status,
        "source_role": role,
        "confidence": confidence,
        "warnings": warnings,
    }


def llm_refine(text: str, deterministic_result: Dict[str, Any], task_intent: str) -> Dict[str, Any]:
    """Optional LLM refinement with strict JSON fallback.

    The deterministic classifier is authoritative if the LLM fails, returns
    malformed JSON, or omits required fields.
    """

    prompt = f"""
You are a CITDS source classifier. Return ONLY JSON.
Classify the source for task_intent={task_intent}.
Allowed source_role values: primary, contextual, analogical, contrastive, aggregate-only, governance-excluded.
Allowed top-level fields: genre, speech_acts, temporal_status, source_role, confidence, warnings.
Keep confidence between 0 and 1.

Deterministic baseline:
{json.dumps(deterministic_result, ensure_ascii=False)}

Source text:
{text[:4000]}
"""
    try:
        response = ai_service.openai_client.chat.completions.create(
            model=ai_service.MODEL_NAME,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
        )
        raw = response.choices[0].message.content.strip()
        parsed = json.loads(raw)
        required = {"genre", "speech_acts", "temporal_status", "source_role", "confidence", "warnings"}
        if not required.issubset(parsed.keys()):
            raise ValueError("missing classifier fields")
        if parsed["source_role"] not in {
            relevance.SOURCE_ROLE_PRIMARY,
            relevance.SOURCE_ROLE_CONTEXTUAL,
            relevance.SOURCE_ROLE_ANALOGICAL,
            relevance.SOURCE_ROLE_CONTRASTIVE,
            relevance.SOURCE_ROLE_AGGREGATE_ONLY,
            relevance.SOURCE_ROLE_GOVERNANCE_EXCLUDED,
        }:
            raise ValueError("invalid source role")
        parsed["classifier_version"] = CLASSIFIER_VERSION
        parsed["mode"] = "llm_refined"
        return parsed
    except Exception as e:
        fallback = dict(deterministic_result)
        fallback["mode"] = "deterministic_fallback"
        fallback.setdefault("warnings", []).append(f"llm_classifier_fallback:{str(e)[:120]}")
        return fallback


def classify_source(text: str, task_intent: str = "general_document_question", use_decision: str = relevance.USE_FULL, use_llm: bool = False) -> Dict[str, Any]:
    base = deterministic_classify(text, task_intent=task_intent, use_decision=use_decision)
    if use_llm:
        return llm_refine(text, base, task_intent)
    return base
