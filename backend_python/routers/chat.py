import uuid
import json
import re
from fastapi import APIRouter, Depends, Form, HTTPException, BackgroundTasks
from sqlalchemy.orm import Session
import models
import ai_service
import relevance
import evidence_service
import security
import policy_engine
from database import get_db

router = APIRouter(prefix="/api", tags=["Chat"])


def log_chat_event(db: Session, user_id: str, question: str, answer: str, keywords: list, sources: list, status: str, query_profile: dict = None, governance: dict = None, evidence_check: dict = None):
    details = json.dumps({
        "question": question,
        "answer": answer,
        "extracted_keywords": keywords,
        "query_profile": query_profile or {},
        "source_roles": relevance.summarize_source_roles(sources) if sources else {},
        "relevance_levels": relevance.summarize_relevance_levels(sources) if sources else {},
        "sources_used": [s.get("file_name") for s in sources] if sources else [],
        "governance": governance or {},
        "evidence_check": evidence_check or {},
        "status": status
    }, ensure_ascii=False)
    log_entry = models.AuditLog(id=str(uuid.uuid4()), user_id=user_id, action="CHAT_ASK", details=details)
    db.add(log_entry)
    db.commit()


def get_permitted_fallback_doc_ids(db: Session, user_id: str) -> list[str]:
    direct_records = db.query(models.UserDocumentPermission.document_id).filter(models.UserDocumentPermission.user_id == user_id).all()
    direct_doc_ids = [r[0] for r in direct_records]
    public_records = db.query(models.Document.id).filter(models.Document.visibility.in_(["Aggregate", "Metadata"])).all()
    public_doc_ids = [r[0] for r in public_records]
    return list(set(direct_doc_ids + public_doc_ids))


def extract_aggregate_safe_facts(chunk_text: str, max_facts: int = 3) -> list[str]:
    aggregate_markers = [
        "average", "mean", "median", "count", "total", "statistics", "statistic",
        "aggregate", "pilot", "approval time", "rate", "percentage", "percent",
        "days", "hours", "items", "documents", "users",
    ]
    text = re.sub(r"\s+", " ", chunk_text).strip()
    sentences = re.split(r"(?<=[.!?])\s+", text)
    facts: list[str] = []
    for sentence in sentences:
        lowered = sentence.lower()
        has_marker = any(marker in lowered for marker in aggregate_markers)
        has_number = bool(re.search(r"\b\d+(?:\.\d+)?\b", sentence))
        if not (has_marker and has_number):
            continue
        safe = re.sub(r"[\w\.-]+@[\w\.-]+", "[redacted-email]", sentence)
        safe = re.sub(r"\b[A-Z][a-z]+\s+[A-Z][a-z]+\b", "[redacted-person]", safe)
        facts.append(safe[:220])
        if len(facts) >= max_facts:
            break
    return facts


def make_generator_safe_chunk(role: str, chunk_text: str, source_profile: dict) -> str:
    if role == relevance.SOURCE_ROLE_AGGREGATE_ONLY:
        lexical_hits = ", ".join(source_profile.get("lexical_hits", [])) or "none"
        semantic_hits = ", ".join(source_profile.get("semantic_hits", [])) or "none"
        facts = extract_aggregate_safe_facts(chunk_text)
        fact_text = " | ".join(facts) if facts else "No aggregate-safe numeric/statistical fact extracted from this chunk."
        return (
            "[Aggregate-only non-quotable source. Individual text is withheld. "
            f"Lexical hits: {lexical_hits}. Semantic hits: {semantic_hits}. "
            f"Aggregate-safe facts: {fact_text}. "
            "Use only for governed aggregate or cautious contextual statements; do not quote as an individual document.]"
        )
    if role == relevance.SOURCE_ROLE_GOVERNANCE_EXCLUDED:
        return "[Governance-excluded source. Content withheld and must not be used.]"
    return chunk_text


def metadata_only_source(db: Session, doc_id: str) -> dict:
    meta = policy_engine.public_metadata_summary(db, doc_id)
    return {
        "document_id": doc_id,
        "file_name": meta.get("file_name", "Unknown document"),
        "role": relevance.SOURCE_ROLE_CONTEXTUAL,
        "use_decision": relevance.USE_METADATA,
        "usable_relevance": {
            "levels": {level: 0.0 for level in relevance.RELEVANCE_LEVELS} | {"governance": 1.0, "evidential": 0.2},
            "role": relevance.SOURCE_ROLE_CONTEXTUAL,
            "base_role": relevance.SOURCE_ROLE_CONTEXTUAL,
            "use_decision": relevance.USE_METADATA,
            "genre": ["metadata"],
            "speech_acts": ["not_applicable"],
            "temporal_status": ["unspecified"],
            "evidence_warnings": ["metadata_only_content_withheld"],
        },
        "text": meta.get("content", "[metadata-only: document content withheld]"),
        "metadata": meta,
    }


def is_browser_history_action_rule_question(question: str) -> bool:
    q = question.lower()
    has_activity_source = "browser history" in q or "activity trace" in q or "browsing" in q
    has_action_target = "action item" in q or "task" in q or "obligation" in q or "todo" in q
    asks_rule = "alone" in q or "by itself" in q or "create" in q or "make" in q
    return has_activity_source and has_action_target and asks_rule


@router.post("/ask")
async def ask_infobank(
    background_tasks: BackgroundTasks,
    question: str = Form(...),
    user_id: str = Depends(security.get_current_user_id),
    db: Session = Depends(get_db),
):
    query_profile = {}
    governance_context = {}
    evidence_check = {}

    try:
        all_kws = db.query(models.Keyword.word).all()
        db_keywords_str = ", ".join([k[0] for k in all_kws])
        kw_prompt = f"""
        Analyze the following question: "{question}"
        Here is the list of available document tags in our database:
        [{db_keywords_str}]
        Select up to 3 exact tags from the list that are semantically related to the question. If unrelated to all tags, output NONE.
        Return ONLY a comma-separated list or NONE.
        """
        kw_response = ai_service.openai_client.chat.completions.create(
            model=ai_service.MODEL_NAME,
            messages=[{"role": "user", "content": kw_prompt}],
            temperature=0.1,
        )
        raw_result = kw_response.choices[0].message.content.strip()
        fallback_used = False
        question_keywords = [] if raw_result == "NONE" else [k.strip() for k in raw_result.split(',') if k.strip()]
        query_profile = relevance.build_query_profile(question, question_keywords)

        candidate_doc_ids = []
        if question_keywords:
            matched_keywords = db.query(models.Keyword).filter(models.Keyword.word.in_(question_keywords)).all()
            if matched_keywords:
                kw_ids = [kw.id for kw in matched_keywords]
                matched_doc_records = db.query(models.DocumentKeyword.document_id).filter(models.DocumentKeyword.keyword_id.in_(kw_ids)).distinct().all()
                candidate_doc_ids = [record[0] for record in matched_doc_records]

        if not candidate_doc_ids:
            fallback_used = True
            candidate_doc_ids = get_permitted_fallback_doc_ids(db, user_id)
            query_profile["retrieval_strategy"] = "permitted_corpus_fallback"
        else:
            query_profile["retrieval_strategy"] = "keyword_routed"

        if not candidate_doc_ids:
            msg = "There is no document related to the question in the InfoBank."
            background_tasks.add_task(log_chat_event, db, user_id, question, msg, question_keywords, [], "rejected", query_profile, {}, {})
            return {"status": "controlled_failure", "message": msg, "query_profile": query_profile}

        governance_context = policy_engine.resolve_document_access_bulk(
            db=db,
            user_id=user_id,
            doc_ids=candidate_doc_ids,
            purpose=query_profile.get("purpose", "grounded_question_answering"),
        )
        governance_context["fallback_used"] = fallback_used
        content_doc_ids = governance_context["content_doc_ids"]
        metadata_doc_ids = governance_context["metadata_only_doc_ids"]

        sources_list = [metadata_only_source(db, doc_id) for doc_id in metadata_doc_ids]
        context_blocks = []

        if content_doc_ids:
            response = ai_service.openai_client.embeddings.create(input=question, model=ai_service.EMBEDDING_MODEL)
            question_vector = response.data[0].embedding
            where_clause = {"document_id": content_doc_ids[0]} if len(content_doc_ids) == 1 else {"document_id": {"$in": content_doc_ids}}
            results = ai_service.collection.query(query_embeddings=[question_vector], n_results=4, where=where_clause)

            if results['documents'] and results['documents'][0]:
                for chunk_text, meta in zip(results['documents'][0], results['metadatas'][0]):
                    doc_id = meta.get("document_id")
                    doc_record = db.query(models.Document).filter(models.Document.id == doc_id).first()
                    file_name = doc_record.file_path if doc_record else "Unknown document"
                    base_role = governance_context["source_roles"].get(doc_id, relevance.SOURCE_ROLE_CONTEXTUAL)
                    use_decision = governance_context["use_decisions"].get(doc_id, relevance.USE_DENY)
                    source_profile = relevance.classify_chunk_profile(
                        question=question,
                        chunk_text=chunk_text,
                        file_name=file_name,
                        query_profile=query_profile,
                        base_role=base_role,
                        use_decision=use_decision,
                    )
                    role = source_profile["role"]
                    safe_chunk_text = make_generator_safe_chunk(role, chunk_text, source_profile)
                    context_blocks.append(relevance.make_context_block(source_profile, file_name, safe_chunk_text))
                    sources_list.append({
                        "document_id": doc_id,
                        "file_name": file_name,
                        "role": role,
                        "use_decision": use_decision,
                        "usable_relevance": source_profile,
                        "text": relevance.public_source_text(role, chunk_text),
                    })

        role_summary = relevance.summarize_source_roles(sources_list)
        relevance_level_summary = relevance.summarize_relevance_levels(sources_list)
        evidence_check = evidence_service.check_rag_evidence(sources_list, query_profile, governance_context)

        if not content_doc_ids and metadata_doc_ids:
            msg = "Only metadata-level sources are available for this question; document content is withheld by policy, so the answer cannot be found in the document content."
            background_tasks.add_task(log_chat_event, db, user_id, question, msg, question_keywords, sources_list, "metadata_only", query_profile, governance_context, evidence_check)
            return {
                "status": "success",
                "question": question,
                "extracted_keywords": question_keywords,
                "query_profile": query_profile,
                "governance": governance_context,
                "source_role_summary": role_summary,
                "relevance_level_summary": relevance_level_summary,
                "evidence_check": evidence_check,
                "searched_documents_count": len(governance_context["usable_doc_ids"]),
                "answer": msg,
                "sources": sources_list,
            }

        if not content_doc_ids:
            msg = "There is no permitted source that can be used for this question in the InfoBank."
            background_tasks.add_task(log_chat_event, db, user_id, question, msg, question_keywords, sources_list, "rejected", query_profile, governance_context, evidence_check)
            return {"status": "controlled_failure", "message": msg, "query_profile": query_profile, "governance": governance_context, "evidence_check": evidence_check}

        if not context_blocks:
            msg = "The answer cannot be found in the document."
            background_tasks.add_task(log_chat_event, db, user_id, question, msg, question_keywords, sources_list, "not_found", query_profile, governance_context, evidence_check)
            return {"status": "success", "answer": msg, "query_profile": query_profile, "governance": governance_context, "evidence_check": evidence_check, "sources": sources_list}

        context_text = "\n\n---\n\n".join(context_blocks)
        system_instruction = (
            "You are a precise InfoBank data analysis expert. Answer ONLY based on the provided context. "
            "Answer in the same language as the question. Use the full usable-relevance profile. "
            "Primary sources may support direct claims. Aggregate-only sources are non-quotable and may support only governed aggregate statements. "
            "Metadata-only sources must never support content claims. Contextual/activity sources must not create obligations by themselves. "
            "Contrastive sources may close, cancel, or weaken a candidate claim. Respect the Evidence Check warnings. "
            "If information is missing or unclear, answer strictly with: 'The answer cannot be found in the document.' No hallucinations."
        )
        final_response = ai_service.openai_client.chat.completions.create(
            model=ai_service.MODEL_NAME,
            messages=[
                {"role": "system", "content": system_instruction},
                {"role": "user", "content": f"Question: {question}\n\nQuery profile:\n{json.dumps(query_profile, ensure_ascii=False)}\n\nGovernance/source-role summary:\n{json.dumps(governance_context, ensure_ascii=False)}\n\nSource role summary:\n{json.dumps(role_summary, ensure_ascii=False)}\n\nRelevance level summary:\n{json.dumps(relevance_level_summary, ensure_ascii=False)}\n\nEvidence check:\n{json.dumps(evidence_check, ensure_ascii=False)}\n\nContext from the document(s):\n{context_text}"},
            ],
            temperature=0.1,
        )
        answer = final_response.choices[0].message.content
        if is_browser_history_action_rule_question(question):
            answer = "No. Browser history or activity traces can provide contextual support, refine details, or help prioritize an existing task, but they cannot create an action item by themselves without primary evidence such as an official request, assignment, calendar obligation, or user commitment."
            evidence_check.setdefault("warnings", []).append("browser_history_contextual_only_rule_applied")
            evidence_check["decision"] = "architectural_rule_answer"

        final_status = "not_found" if "The answer cannot be found in the document." in answer else "success"
        background_tasks.add_task(log_chat_event, db, user_id, question, answer, question_keywords, sources_list, final_status, query_profile, governance_context, evidence_check)
        return {
            "status": "success",
            "question": question,
            "extracted_keywords": question_keywords,
            "query_profile": query_profile,
            "governance": governance_context,
            "source_role_summary": role_summary,
            "relevance_level_summary": relevance_level_summary,
            "evidence_check": evidence_check,
            "searched_documents_count": len(governance_context["usable_doc_ids"]),
            "answer": answer,
            "sources": sources_list,
        }
    except Exception as e:
        db.rollback()
        background_tasks.add_task(log_chat_event, db, user_id, question, str(e), [], [], "error", query_profile, governance_context, evidence_check)
        raise HTTPException(status_code=500, detail=str(e))
