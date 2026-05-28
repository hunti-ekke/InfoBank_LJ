import uuid
import json
from fastapi import APIRouter, Depends, Form, HTTPException, BackgroundTasks
from sqlalchemy.orm import Session
import models
import ai_service
import relevance
from database import get_db

router = APIRouter(prefix="/api", tags=["Chat"])


def log_chat_event(db: Session, user_id: str, question: str, answer: str, keywords: list, sources: list, status: str, query_profile: dict = None, governance: dict = None):
    details = json.dumps({
        "question": question,
        "answer": answer,
        "extracted_keywords": keywords,
        "query_profile": query_profile or {},
        "source_roles": relevance.summarize_source_roles(sources) if sources else {},
        "relevance_levels": relevance.summarize_relevance_levels(sources) if sources else {},
        "sources_used": [s["file_name"] for s in sources] if sources else [],
        "governance": governance or {},
        "status": status
    })
    
    log_entry = models.AuditLog(
        id=str(uuid.uuid4()),
        user_id=user_id,
        action="CHAT_ASK",
        details=details
    )
    db.add(log_entry)
    db.commit()


def get_permitted_fallback_doc_ids(db: Session, user_id: str) -> list[str]:
    """Return a conservative fallback corpus for lexical/keyword misses."""

    direct_records = db.query(models.UserDocumentPermission.document_id).filter(
        models.UserDocumentPermission.user_id == user_id
    ).all()
    direct_doc_ids = [r[0] for r in direct_records]

    aggregate_records = db.query(models.Document.id).filter(
        models.Document.visibility == "Aggregate"
    ).all()
    aggregate_doc_ids = [r[0] for r in aggregate_records]

    return list(set(direct_doc_ids + aggregate_doc_ids))


@router.post("/ask")
async def ask_infobank(
    background_tasks: BackgroundTasks,
    question: str = Form(...), 
    user_id: str = Form(...), 
    db: Session = Depends(get_db)
):
    query_profile = {}
    governance_context = {}

    try:
        all_kws = db.query(models.Keyword.word).all()
        db_keywords_str = ", ".join([k[0] for k in all_kws])

        kw_prompt = f"""
        Analyze the following question: "{question}"
        Here is the list of available document tags in our database:
        [{db_keywords_str}]
        Your task:
        1. Select up to 3 tags from the list above that are semantically related to the question's core concepts.
        2. Handle synonyms and grammar.
        3. CRITICAL RULE: You MUST strictly copy the exact words from the list. Do not invent new words.
        4. If the question is completely unrelated to ALL of these tags, output the exact word: NONE.
        5. Return ONLY a comma-separated list of the selected tags (or NONE). No extra text.
        """
        
        kw_response = ai_service.openai_client.chat.completions.create(
            model=ai_service.MODEL_NAME,
            messages=[{"role": "user", "content": kw_prompt}],
            temperature=0.1
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
                matched_doc_records = db.query(models.DocumentKeyword.document_id).filter(
                    models.DocumentKeyword.keyword_id.in_(kw_ids)
                ).distinct().all()
                candidate_doc_ids = [record[0] for record in matched_doc_records]

        if not candidate_doc_ids:
            fallback_used = True
            candidate_doc_ids = get_permitted_fallback_doc_ids(db, user_id)
            query_profile["retrieval_strategy"] = "permitted_corpus_fallback"
        else:
            query_profile["retrieval_strategy"] = "keyword_routed"

        if not candidate_doc_ids:
            msg = "There is no document related to the question in the InfoBank."
            background_tasks.add_task(log_chat_event, db, user_id, question, msg, question_keywords, [], "rejected", query_profile, {})
            return {"status": "controlled_failure", "message": msg, "query_profile": query_profile}

        user_permissions = db.query(models.UserDocumentPermission).filter(
            models.UserDocumentPermission.user_id == user_id,
            models.UserDocumentPermission.document_id.in_(candidate_doc_ids)
        ).all()
        direct_access_docs = [p.document_id for p in user_permissions]

        aggregate_docs_records = db.query(models.Document.id).filter(
            models.Document.id.in_(candidate_doc_ids),
            models.Document.visibility == "Aggregate"
        ).all()
        aggregate_docs = [r[0] for r in aggregate_docs_records]

        governance_context = relevance.build_governance_context(candidate_doc_ids, direct_access_docs, aggregate_docs)
        governance_context["fallback_used"] = fallback_used
        all_allowed_docs = governance_context["usable_doc_ids"]

        if not all_allowed_docs:
            msg = "There is no permitted source that can be used for this question in the InfoBank."
            background_tasks.add_task(log_chat_event, db, user_id, question, msg, question_keywords, [], "rejected", query_profile, governance_context)
            return {
                "status": "controlled_failure",
                "message": msg,
                "query_profile": query_profile,
                "governance": governance_context,
            }

        response = ai_service.openai_client.embeddings.create(input=question, model=ai_service.EMBEDDING_MODEL)
        question_vector = response.data[0].embedding

        where_clause = {"document_id": all_allowed_docs[0]} if len(all_allowed_docs) == 1 else {"document_id": {"$in": all_allowed_docs}}
        results = ai_service.collection.query(query_embeddings=[question_vector], n_results=4, where=where_clause)

        if not results['documents'] or not results['documents'][0]:
            msg = "The answer cannot be found in the document."
            background_tasks.add_task(log_chat_event, db, user_id, question, msg, question_keywords, [], "not_found", query_profile, governance_context)
            return {
                "status": "success",
                "answer": msg,
                "query_profile": query_profile,
                "governance": governance_context,
            }

        sources_list = []
        context_blocks = []
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

                context_blocks.append(relevance.make_context_block(source_profile, file_name, chunk_text))
                sources_list.append({
                    "document_id": doc_id,
                    "file_name": file_name,
                    "role": role,
                    "use_decision": use_decision,
                    "usable_relevance": source_profile,
                    "text": relevance.public_source_text(role, chunk_text)
                })

        context_text = "\n\n---\n\n".join(context_blocks)
        role_summary = relevance.summarize_source_roles(sources_list)
        relevance_level_summary = relevance.summarize_relevance_levels(sources_list)

        system_instruction = (
            "You are a precise InfoBank data analysis expert. Answer ONLY based on the provided context. "
            "CRITICAL RULE: Answer in the exact same language as the question was given in. "
            "Do not translate the answer to another language unless explicitly requested. "
            "Use the full usable-relevance profile: lexical, semantic, ontological, pragmatic, genre, perlocutionary, temporal/status, governance, and evidential. "
            "Primary sources may support direct claims. Aggregate-only sources may support only governed aggregate or cautious contextual statements. "
            "Contextual, analogical, and activity-trace-like sources must not create obligations by themselves. "
            "Contrastive sources may close, cancel, or weaken a candidate claim. "
            "If there is no primary evidence for a precise claim, explicitly say that the answer is based on limited or aggregate/contextual evidence. "
            "If the information is missing or unclear, answer strictly with: 'The answer cannot be found in the document.' "
            "No hallucinations are allowed."
        )

        final_response = ai_service.openai_client.chat.completions.create(
            model=ai_service.MODEL_NAME,
            messages=[
                {"role": "system", "content": system_instruction},
                {"role": "user", "content": f"Question: {question}\n\nQuery profile:\n{json.dumps(query_profile, ensure_ascii=False)}\n\nGovernance/source-role summary:\n{json.dumps(governance_context, ensure_ascii=False)}\n\nSource role summary:\n{json.dumps(role_summary, ensure_ascii=False)}\n\nRelevance level summary:\n{json.dumps(relevance_level_summary, ensure_ascii=False)}\n\nContext from the document(s):\n{context_text}"}
            ],
            temperature=0.1
        )

        answer = final_response.choices[0].message.content
        
        if "The answer cannot be found in the document." in answer:
            final_status = "not_found"
        else:
            final_status = "success"

        background_tasks.add_task(
            log_chat_event,
            db,
            user_id,
            question,
            answer,
            question_keywords,
            sources_list,
            final_status,
            query_profile,
            governance_context,
        )

        return {
            "status": "success",
            "question": question,
            "extracted_keywords": question_keywords,
            "query_profile": query_profile,
            "governance": governance_context,
            "source_role_summary": role_summary,
            "relevance_level_summary": relevance_level_summary,
            "searched_documents_count": len(all_allowed_docs),
            "answer": answer,
            "sources": sources_list
        }
    except Exception as e:
        db.rollback()
        background_tasks.add_task(log_chat_event, db, user_id, question, str(e), [], [], "error", query_profile, governance_context)
        raise HTTPException(status_code=500, detail=str(e))
