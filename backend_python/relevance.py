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

RELEVANCE_LEVELS = [
    "lexical",
    "semantic",
    "ontological",
    "pragmatic",
    "genre",
    "perlocutionary",
    "temporal_status",
    "governance",
    "evidential",
]

_TASK_PATTERNS = {
    "current_action_list": [
        r"\b(todo|to-do|task|action item|action list|current action)\b",
        r"\b(teend[őo]|feladat|aktu[aá]lis teend[őo]|tennival[oó])\b",
    ],
    "deadline_or_status": [
        r"\b(deadline|due|status|open|closed|current|active)\b",
        r"\b(hat[aá]rid[őo]|st[aá]tusz|nyitott|lez[aá]rt|aktu[aá]lis)\b",
    ],
    "comparison": [
        r"\b(compare|difference|contrast|similar|analogy)\b",
        r"\b(összehasonlít|különbség|hasonló|analógia)\b",
    ],
    "governance_question": [
        r"\b(permission|access|owner|ownership|transfer|aggregate|governance|policy)\b",
        r"\b(jogosultság|tulajdonos|hozzáférés|átadás|aggregált|szabályzat)\b",
    ],
    "document_question_answering": [
        r"\b(what|why|how|when|where|who|mikor|mi[ée]rt|hogyan|hol|ki)\b",
    ],
}

_GENRE_PATTERNS = {
    "manual": [r"\bmanual\b", r"\binstruction\b", r"\bsetup\b", r"\bconfiguration\b"],
    "invoice": [r"\binvoice\b", r"\breceipt\b", r"\breimbursement\b", r"\bpayment\b"],
    "contract": [r"\bcontract\b", r"\bagreement\b", r"\bterms\b"],
    "warning": [r"\bwarning\b", r"\brisk\b", r"\bmust not\b", r"\bprohibited\b"],
    "email_or_message": [r"\bemail\b", r"\bmessage\b", r"\breply\b", r"\bunanswered\b", r"\bthread\b"],
    "calendar_or_schedule": [r"\bcalendar\b", r"\bschedule\b", r"\bdeadline\b", r"\bdue\b", r"\bby 20\d{2}\b"],
    "activity_trace": [r"\bbrowser\b", r"\bhistory\b", r"\bsearch\b", r"\bvisited\b", r"\bactivity trace\b"],
    "statistics": [r"\baverage\b", r"\bmedian\b", r"\bcount\b", r"\bstatistics\b", r"\bpilot\b"],
    "policy": [r"\bpolicy\b", r"\bgovernance\b", r"\bpermission\b", r"\bowner\b", r"\baggregate\b"],
}

_SPEECH_ACT_PATTERNS = {
    "request": [r"\bplease\b", r"\bcould you\b", r"\bcan you\b", r"\brequest\b", r"\basked\b"],
    "assignment": [r"\bassigned\b", r"\bmust\b", r"\brequired\b", r"\bobligation\b", r"\bshould\b"],
    "commitment": [r"\bi will\b", r"\baccepted\b", r"\bcommitted\b", r"\bparticipation\b"],
    "completion": [r"\bcompleted\b", r"\bclosed\b", r"\bdone\b", r"\banswered\b", r"\bsent\b"],
    "cancellation": [r"\bcancelled\b", r"\bcanceled\b", r"\bwithdrawn\b", r"\bnot needed\b"],
    "reminder": [r"\breminder\b", r"\bfollow up\b", r"\boverdue\b"],
}

_TEMPORAL_PATTERNS = {
    "explicit_deadline": [r"\bby\s+20\d{2}-\d{2}-\d{2}\b", r"\bdue\s+20\d{2}-\d{2}-\d{2}\b", r"\bdeadline\b"],
    "open": [r"\bopen\b", r"\bunanswered\b", r"\bpending\b", r"\bcurrent\b", r"\bactive\b"],
    "closed": [r"\bclosed\b", r"\bcompleted\b", r"\bdone\b", r"\bcancelled\b", r"\banswered\b"],
    "recent": [r"\brecent\b", r"\btoday\b", r"\byesterday\b", r"\bthis week\b"],
}

_OBJECT_PATTERNS = {
    "person": r"\b[A-Z][a-z]+\s+[A-Z][a-z]+\b",
    "date": r"\b20\d{2}-\d{2}-\d{2}\b",
    "identifier": r"\b[A-Z]{2,}[A-Z0-9\-]{2,}\b",
    "course_or_topic": r"\b(Data Science|database systems|PhD|Ko[sš]ice|InfoBank|RAG)\b",
}


def _normalize_word(value: str) -> str:
    return re.sub(r"[^\w\-áéíóöőúüűÁÉÍÓÖŐÚÜŰ]+", "", value).lower()


def _pattern_hits(text: str, pattern_map: Dict[str, List[str]]) -> List[str]:
    hits: List[str] = []
    for label, patterns in pattern_map.items():
        if any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in patterns):
            hits.append(label)
    return hits


def extract_lexical_terms(question: str, max_terms: int = 12) -> List[str]:
    """Extract lightweight lexical signals for traceability."""

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


def extract_entities(text: str) -> Dict[str, List[str]]:
    entities: Dict[str, List[str]] = {}
    for label, pattern in _OBJECT_PATTERNS.items():
        found = []
        for match in re.findall(pattern, text):
            value = match if isinstance(match, str) else " ".join(match)
            if value and value not in found:
                found.append(value)
        if found:
            entities[label] = found[:8]
    return entities


def infer_task_intent(question: str) -> str:
    q = question.lower()
    for intent, patterns in _TASK_PATTERNS.items():
        if any(re.search(pattern, q, flags=re.IGNORECASE) for pattern in patterns):
            return intent
    return "general_document_question"


def infer_expected_genres(question: str, task_intent: str) -> List[str]:
    text_genres = _pattern_hits(question, _GENRE_PATTERNS)
    task_genre_defaults = {
        "current_action_list": ["email_or_message", "calendar_or_schedule", "activity_trace"],
        "deadline_or_status": ["calendar_or_schedule", "email_or_message"],
        "governance_question": ["policy"],
    }
    merged = text_genres + task_genre_defaults.get(task_intent, [])
    return list(dict.fromkeys(merged)) or ["any"]


def build_query_profile(question: str, selected_keywords: Iterable[str]) -> Dict[str, Any]:
    task_intent = infer_task_intent(question)
    return {
        "lexical_terms": extract_lexical_terms(question),
        "semantic_tags": list(selected_keywords),
        "entities": extract_entities(question),
        "task_intent": task_intent,
        "purpose": infer_purpose(task_intent),
        "expected_genres": infer_expected_genres(question, task_intent),
        "required_evidence_strength": infer_required_evidence_strength(task_intent),
        "levels": RELEVANCE_LEVELS,
    }


def infer_purpose(task_intent: str) -> str:
    if task_intent == "current_action_list":
        return "action_reconstruction"
    if task_intent == "comparison":
        return "comparison_or_analogy"
    if task_intent == "governance_question":
        return "permission_and_policy_reasoning"
    if task_intent == "deadline_or_status":
        return "status_checking"
    return "grounded_question_answering"


def infer_required_evidence_strength(task_intent: str) -> str:
    if task_intent in {"current_action_list", "deadline_or_status", "governance_question"}:
        return "primary_required_for_direct_claim"
    if task_intent == "comparison":
        return "primary_or_analogical_with_label"
    return "primary_or_aggregate_with_role_label"


def build_governance_context(
    candidate_doc_ids: Iterable[str],
    direct_permission_doc_ids: Iterable[str],
    aggregate_doc_ids: Iterable[str],
) -> Dict[str, Any]:
    """Assign a use decision and base source role to every candidate document."""

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


def classify_chunk_profile(question: str, chunk_text: str, file_name: str, query_profile: Dict[str, Any], base_role: str, use_decision: str) -> Dict[str, Any]:
    """Compute the full usable-relevance profile for a retrieved chunk.

    The scores are deliberately simple and inspectable. They are not a benchmark;
    they make every semiotic layer from the paper visible and usable by the
    generator.
    """

    text = f"{file_name}\n{chunk_text}"
    lexical_terms = query_profile.get("lexical_terms", [])
    lexical_hits = [term for term in lexical_terms if term.lower() in text.lower()]
    semantic_tags = query_profile.get("semantic_tags", [])
    semantic_hits = [tag for tag in semantic_tags if tag.lower() in text.lower()]
    genres = _pattern_hits(text, _GENRE_PATTERNS) or ["generic_document"]
    speech_acts = _pattern_hits(text, _SPEECH_ACT_PATTERNS) or ["informative"]
    temporal_signals = _pattern_hits(text, _TEMPORAL_PATTERNS) or ["unspecified"]
    chunk_entities = extract_entities(text)
    query_entities = query_profile.get("entities", {})

    ontological_links = infer_ontological_links(query_entities, chunk_entities, text)
    pragmatic_match = infer_pragmatic_match(query_profile.get("task_intent"), genres, speech_acts, temporal_signals)
    refined_role = refine_source_role(base_role, use_decision, genres, speech_acts, temporal_signals, pragmatic_match)

    scores = {
        "lexical": min(1.0, len(lexical_hits) / max(1, len(lexical_terms))),
        "semantic": 1.0 if semantic_hits else (0.35 if semantic_tags else 0.0),
        "ontological": 1.0 if ontological_links else 0.0,
        "pragmatic": 1.0 if pragmatic_match else 0.0,
        "genre": 1.0 if set(genres).intersection(set(query_profile.get("expected_genres", []))) else 0.4,
        "perlocutionary": 1.0 if any(act in speech_acts for act in ["request", "assignment", "commitment", "reminder"]) else 0.25,
        "temporal_status": 1.0 if any(sig in temporal_signals for sig in ["explicit_deadline", "open", "closed", "recent"]) else 0.2,
        "governance": 1.0 if use_decision in {USE_FULL, USE_AGGREGATE} else 0.0,
        "evidential": evidential_score(refined_role),
    }

    return {
        "levels": scores,
        "role": refined_role,
        "base_role": base_role,
        "use_decision": use_decision,
        "lexical_hits": lexical_hits,
        "semantic_hits": semantic_hits,
        "entities": chunk_entities,
        "ontological_links": ontological_links,
        "genre": genres,
        "speech_acts": speech_acts,
        "temporal_status": temporal_signals,
        "pragmatic_match": pragmatic_match,
        "evidence_warnings": evidence_warnings(refined_role, use_decision, temporal_signals, speech_acts),
    }


def infer_ontological_links(query_entities: Dict[str, List[str]], chunk_entities: Dict[str, List[str]], text: str) -> List[str]:
    links: List[str] = []
    for label, q_values in query_entities.items():
        c_values = chunk_entities.get(label, [])
        overlap = set(q_values).intersection(c_values)
        if overlap:
            links.append(f"same_{label}:" + ",".join(sorted(overlap)))
    relation_keywords = ["manual", "invoice", "warranty", "document", "course", "candidate", "trip", "email", "owner"]
    for keyword in relation_keywords:
        if keyword in text.lower() and keyword not in links:
            links.append(keyword)
    return links[:8]


def infer_pragmatic_match(task_intent: str, genres: List[str], speech_acts: List[str], temporal_signals: List[str]) -> bool:
    if task_intent == "current_action_list":
        return bool(set(speech_acts).intersection({"request", "assignment", "commitment", "reminder"}) or set(temporal_signals).intersection({"explicit_deadline", "open"}))
    if task_intent == "deadline_or_status":
        return bool(set(temporal_signals).intersection({"explicit_deadline", "open", "closed", "recent"}))
    if task_intent == "governance_question":
        return "policy" in genres or any(act in speech_acts for act in ["assignment", "request"])
    if task_intent == "comparison":
        return True
    return True


def refine_source_role(base_role: str, use_decision: str, genres: List[str], speech_acts: List[str], temporal_signals: List[str], pragmatic_match: bool) -> str:
    if use_decision == USE_DENY:
        return SOURCE_ROLE_GOVERNANCE_EXCLUDED
    if use_decision == USE_AGGREGATE:
        return SOURCE_ROLE_AGGREGATE_ONLY
    if any(sig in temporal_signals for sig in ["closed"]) or any(act in speech_acts for act in ["completion", "cancellation"]):
        return SOURCE_ROLE_CONTRASTIVE
    if "activity_trace" in genres or not pragmatic_match:
        return SOURCE_ROLE_CONTEXTUAL
    if "manual" in genres and base_role != SOURCE_ROLE_PRIMARY:
        return SOURCE_ROLE_ANALOGICAL
    return base_role


def evidential_score(role: str) -> float:
    return {
        SOURCE_ROLE_PRIMARY: 1.0,
        SOURCE_ROLE_CONTRASTIVE: 0.85,
        SOURCE_ROLE_AGGREGATE_ONLY: 0.65,
        SOURCE_ROLE_CONTEXTUAL: 0.45,
        SOURCE_ROLE_ANALOGICAL: 0.35,
        SOURCE_ROLE_GOVERNANCE_EXCLUDED: 0.0,
    }.get(role, 0.25)


def evidence_warnings(role: str, use_decision: str, temporal_signals: List[str], speech_acts: List[str]) -> List[str]:
    warnings: List[str] = []
    if role == SOURCE_ROLE_AGGREGATE_ONLY:
        warnings.append("aggregate_only_do_not_quote_individual_content")
    if role == SOURCE_ROLE_CONTEXTUAL:
        warnings.append("contextual_support_not_direct_proof")
    if role == SOURCE_ROLE_ANALOGICAL:
        warnings.append("analogical_support_not_direct_proof")
    if role == SOURCE_ROLE_CONTRASTIVE:
        warnings.append("may_close_cancel_or_contradict_an_item")
    if use_decision == USE_DENY:
        warnings.append("governance_denied_do_not_use")
    if "closed" in temporal_signals or "completion" in speech_acts or "cancellation" in speech_acts:
        warnings.append("closed_or_cancelled_status_detected")
    return warnings


def make_context_block(source_profile: Dict[str, Any], file_name: str, chunk_text: str) -> str:
    return (
        f"[Source role: {source_profile.get('role')} ]\n"
        f"[Use decision: {source_profile.get('use_decision')}]\n"
        f"[Relevance levels: {source_profile.get('levels')}]\n"
        f"[Genre: {source_profile.get('genre')}]\n"
        f"[Speech acts: {source_profile.get('speech_acts')}]\n"
        f"[Temporal/status: {source_profile.get('temporal_status')}]\n"
        f"[Evidence warnings: {source_profile.get('evidence_warnings')}]\n"
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


def summarize_relevance_levels(sources: Iterable[Dict[str, Any]]) -> Dict[str, float]:
    totals: Dict[str, float] = {level: 0.0 for level in RELEVANCE_LEVELS}
    count = 0
    for src in sources:
        profile = src.get("usable_relevance", {})
        levels = profile.get("levels", {})
        if not levels:
            continue
        count += 1
        for level in RELEVANCE_LEVELS:
            totals[level] += float(levels.get(level, 0.0))
    if count == 0:
        return totals
    return {level: round(value / count, 3) for level, value in totals.items()}
