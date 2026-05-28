"""Usable relevance helpers for the InfoBank RAG pipeline.

This module operationalizes the CITDS 2026 idea that a retrieved source is not
just "similar" to the query. It also has a governance decision and an evidential
role that must be exposed to the generator and the user interface.
"""

import re
from typing import Any, Dict, Iterable, List, Set


SOURCE_ROLE_PRIMARY = "primary"
SOURCE_ROLE_CONTEXTUAL = "contextual"
SOURCE_ROLE_ANALOGICAL = "analogical"
SOURCE_ROLE_CONTRASTIVE = "contrastive"
SOURCE_ROLE_AGGREGATE_ONLY = "aggregate-only"
SOURCE_ROLE_GOVERNANCE_EXCLUDED = "governance-excluded"

USE_FULL = "full"
USE_AGGREGATE = "aggregate"
USE_METADATA = "metadata"
USE_DENY = "deny"


_TASK_PATTERNS = {
    "current_action_list": [
        r"\b(todo|to-do|task|action item|action list|current action)\b",
        r"\b(teend[őo]|feladat|aktu[aá]lis teend[őo]|tennival[oó])\b",
    ],
    "deadline_or_status": [
        r"\b(deadline|due|status|open|closed|current|active)\b",
        r"\b(hat[aá]rid[őo]|st[aá]tusz|nyitott|lez[aá]rt|aktu[aá]lis)\b",
    ],
    "document_question_answering": [
        r"\b(what|why|how|when|where|who|mikor|mi[ée]rt|hogyan|hol|ki)\b",
    ],
}


def _normalize_word(value: str) -> str:
    return re.sub(r"[^\w\-áéíóöőúüűÁÉÍÓÖŐÚÜŰ]+", "", value).lower()


def extract_lexical_terms(question: str, max_terms: int = 12) -> List[str]:
    """Extract lightweight lexical signals for traceability.

    This is intentionally conservative. The LLM still performs semantic routing,
    but these terms make the retrieval profile inspectable and useful for audit.
    """

    stopwords = {
        "the", "and", "or", "to", "of", "in", "on", "for", "a", "an", "is",
        "are", "my", "me", "what", "which", "how", "mi", "milyen", "hogy",
        "hogyan", "az", "a", "egy", "van", "vagy", "és", "nekem", "kell",
    }
    terms: List[str] = []
    for raw in re.findall(r"[\w\-áéíóöőúüűÁÉÍÓÖŐÚÜŰ]+", question):
        term = _normalize_word(raw)
        if len(term) < 3 or term in stopwords:
            continue
        if term not in terms:
            terms.append(term)
        if len(terms) >= max_terms:
            break
    return terms


def infer_task_intent(question: str) -> str:
    q = question.lower()
    for intent, patterns in _TASK_PATTERNS.items():
        if any(re.search(pattern, q, flags=re.IGNORECASE) for pattern in patterns):
            return intent
    return "general_document_question"


def build_query_profile(question: str, selected_keywords: Iterable[str]) -> Dict[str, Any]:
    return {
        "lexical_terms": extract_lexical_terms(question),
        "semantic_tags": list(selected_keywords),
        "task_intent": infer_task_intent(question),
        "required_evidence_strength": "primary_or_aggregate_with_role_label",
    }


def build_governance_context(
    candidate_doc_ids: Iterable[str],
    direct_permission_doc_ids: Iterable[str],
    aggregate_doc_ids: Iterable[str],
) -> Dict[str, Any]:
    """Assign a use decision and source role to every candidate document."""

    candidate_set: Set[str] = set(candidate_doc_ids)
    direct_set: Set[str] = set(direct_permission_doc_ids)
    aggregate_set: Set[str] = set(aggregate_doc_ids)

    decisions: Dict[str, str] = {}
    roles: Dict[str, str] = {}

    for doc_id in candidate_set:
        if doc_id in direct_set:
            decisions[doc_id] = USE_FULL
            roles[doc_id] = SOURCE_ROLE_PRIMARY
        elif doc_id in aggregate_set:
            decisions[doc_id] = USE_AGGREGATE
            roles[doc_id] = SOURCE_ROLE_AGGREGATE_ONLY
        else:
            decisions[doc_id] = USE_DENY
            roles[doc_id] = SOURCE_ROLE_GOVERNANCE_EXCLUDED

    usable_doc_ids = [doc_id for doc_id, decision in decisions.items() if decision in {USE_FULL, USE_AGGREGATE}]
    denied_doc_ids = [doc_id for doc_id, decision in decisions.items() if decision == USE_DENY]

    return {
        "use_decisions": decisions,
        "source_roles": roles,
        "usable_doc_ids": usable_doc_ids,
        "denied_doc_ids": denied_doc_ids,
        "has_primary_evidence": any(roles.get(doc_id) == SOURCE_ROLE_PRIMARY for doc_id in usable_doc_ids),
        "has_aggregate_evidence": any(roles.get(doc_id) == SOURCE_ROLE_AGGREGATE_ONLY for doc_id in usable_doc_ids),
    }


def make_context_block(source_role: str, file_name: str, chunk_text: str) -> str:
    return (
        f"[Source role: {source_role}]\n"
        f"[Source name: {file_name}]\n"
        f"{chunk_text}"
    )


def public_source_text(source_role: str, chunk_text: str) -> str:
    """Return source text that may be shown in the UI.

    Aggregate-only sources may support generation, but the UI must not quote them
    as individual primary documents.
    """

    if source_role == SOURCE_ROLE_AGGREGATE_ONLY:
        return "[Aggregate-only source: individual content is hidden; used only as governed aggregate/contextual support.]"
    if source_role == SOURCE_ROLE_GOVERNANCE_EXCLUDED:
        return "[Governance-excluded source: content was not used.]"
    return chunk_text


def summarize_source_roles(sources: Iterable[Dict[str, Any]]) -> Dict[str, int]:
    summary: Dict[str, int] = {}
    for src in sources:
        role = src.get("role", "unknown")
        summary[role] = summary.get(role, 0) + 1
    return summary
