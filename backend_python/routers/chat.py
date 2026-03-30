import uuid
import json
from fastapi import APIRouter, Depends, Form, HTTPException, BackgroundTasks
from sqlalchemy.orm import Session
import models
import ai_service
from database import get_db

router = APIRouter(prefix="/api", tags=["Chat"])

def log_chat_event(db: Session, user_id: str, question: str, answer: str, keywords: list, sources: list, status: str):
    details = json.dumps({
        "question": question,
        "answer": answer,
        "extracted_keywords": keywords,
        "sources_used": [s["file_name"] for s in sources] if sources else [],
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

@router.post("/ask")
async def ask_infobank(
    background_tasks: BackgroundTasks,
    question: str = Form(...), 
    user_id: str = Form(...), 
    db: Session = Depends(get_db)
):
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
        
        if raw_result == "NONE":
            msg = "There is no document related to the question in the InfoBank."
            background_tasks.add_task(log_chat_event, db, user_id, question, msg, [], [], "controlled_failure")
            return {"status": "controlled_failure", "message": msg}

        question_keywords = [k.strip() for k in raw_result.split(',') if k.strip()]
        matched_keywords = db.query(models.Keyword).filter(models.Keyword.word.in_(question_keywords)).all()

        if not matched_keywords:
            msg = "There is no document related to the question in the InfoBank."
            background_tasks.add_task(log_chat_event, db, user_id, question, msg, question_keywords, [], "controlled_failure")
            return {"status": "controlled_failure", "message": msg}

        kw_ids = [kw.id for kw in matched_keywords]
        matched_doc_records = db.query(models.DocumentKeyword.document_id).filter(models.DocumentKeyword.keyword_id.in_(kw_ids)).distinct().all()
        candidate_doc_ids = [record[0] for record in matched_doc_records]

        if not candidate_doc_ids:
            msg = "There is no document related to the question in the InfoBank."
            background_tasks.add_task(log_chat_event, db, user_id, question, msg, question_keywords, [], "controlled_failure")
            return {"status": "controlled_failure", "message": msg}

        user_permissions = db.query(models.UserDocumentPermission).filter(models.UserDocumentPermission.user_id == user_id, models.UserDocumentPermission.document_id.in_(candidate_doc_ids)).all()
        direct_access_docs = [p.document_id for p in user_permissions]

        aggregate_docs_records = db.query(models.Document.id).filter(models.Document.id.in_(candidate_doc_ids), models.Document.visibility == "Aggregate").all()
        aggregate_docs = [r[0] for r in aggregate_docs_records]

        all_allowed_docs = list(set(direct_access_docs + aggregate_docs))

        if not all_allowed_docs:
            msg = "There is no document related to the question in the InfoBank."
            background_tasks.add_task(log_chat_event, db, user_id, question, msg, question_keywords, [], "controlled_failure")
            return {"status": "controlled_failure", "message": msg}

        response = ai_service.openai_client.embeddings.create(input=question, model=ai_service.EMBEDDING_MODEL)
        question_vector = response.data[0].embedding

        where_clause = {"document_id": all_allowed_docs[0]} if len(all_allowed_docs) == 1 else {"document_id": {"$in": all_allowed_docs}}
        results = ai_service.collection.query(query_embeddings=[question_vector], n_results=4, where=where_clause)

        if not results['documents'] or not results['documents'][0]:
            msg = "The answer cannot be found in the document."
            background_tasks.add_task(log_chat_event, db, user_id, question, msg, question_keywords, [], "success")
            return {"status": "success", "answer": msg}

        context_text = "\n\n---\n\n".join(results['documents'][0])

        system_instruction = (
            "You are a precise data analysis expert. Answer ONLY based on the provided context. "
            "CRITICAL RULE: Answer in the exact same language as the question was given in. "
            "Do not translate the answer to another language unless explicitly requested. "
            "If the information is missing or unclear, answer strictly with: 'The answer cannot be found in the document.' "
            "No hallucinations are allowed."
        )

        final_response = ai_service.openai_client.chat.completions.create(
            model=ai_service.MODEL_NAME,
            messages=[
                {"role": "system", "content": system_instruction},
                {"role": "user", "content": f"Question: {question}\n\nContext from the document(s):\n{context_text}"}
            ],
            temperature=0.1
        )

        sources_list = []
        if results['documents'] and results['documents'][0]:
            for chunk_text, meta in zip(results['documents'][0], results['metadatas'][0]):
                doc_id = meta.get("document_id")
                doc_record = db.query(models.Document).filter(models.Document.id == doc_id).first()
                file_name = doc_record.file_path if doc_record else "Unknown document"
                sources_list.append({"file_name": file_name, "text": chunk_text})

        answer = final_response.choices[0].message.content

        background_tasks.add_task(log_chat_event, db, user_id, question, answer, question_keywords, sources_list, "success")

        return {
            "status": "success",
            "question": question,
            "extracted_keywords": question_keywords,
            "searched_documents_count": len(all_allowed_docs),
            "answer": answer,
            "sources": sources_list
        }
    except Exception as e:
        db.rollback()
        background_tasks.add_task(log_chat_event, db, user_id, question, str(e), [], [], "error")
        raise HTTPException(status_code=500, detail=str(e))