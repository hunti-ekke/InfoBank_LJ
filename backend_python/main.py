import os
import uuid
import time
import datetime
import schemas
import security

from typing import List
from fastapi import FastAPI, Depends, UploadFile, File, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from dotenv import load_dotenv
from sqlalchemy import func

import fitz
import chromadb

from openai import OpenAI

import models
from database import engine, get_db

from routers import auth, profile

load_dotenv()

openai_client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
MODEL_NAME = "gpt-4o-mini"
EMBEDDING_MODEL = "text-embedding-3-small"

chroma_client = chromadb.PersistentClient(path="./chroma_data")
collection = chroma_client.get_or_create_collection(name="infobank_vectors")
models.Base.metadata.create_all(bind=engine)

app = FastAPI(title="InfoBank API", version="3.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(profile.router)


def chunk_text(text: str, chunk_size: int = 1000, overlap: int = 200) -> List[str]:
    chunks = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        chunks.append(text[start:end])
        start += (chunk_size - overlap)
    return chunks

@app.post("/api/upload")
async def upload_document(
    file: UploadFile = File(...),
    user_id: str = Form(...),
    permission_type: models.PermissionType = Form(...),
    db: Session = Depends(get_db)
):
    try:
        content = await file.read()
        
        doc_pdf = fitz.open(stream=content, filetype="pdf")
        full_text = "".join(page.get_text() for page in doc_pdf)
        
        if not full_text.strip():
             raise HTTPException(status_code=400, detail="The PDF is empty or text could not be extracted.")

        system_prompt = """
        Analyze the following document text.
        Extract the 3 to 5 most important core concepts in English.
        
        CRITICAL RULE: You must output ONLY single words (unigrams)! DO NOT output multi-word phrases (e.g., output "health", not "health benefits").
        Return ONLY a single, comma-separated list of these words in lowercase. No extra text.
        """
        
        text_for_analysis = full_text[:10000]
        
        completion = openai_client.chat.completions.create(
            model=MODEL_NAME,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": text_for_analysis}
            ],
            temperature=0.3
        )
        
        raw_keywords = [k.strip().lower() for k in completion.choices[0].message.content.split(',') if k.strip()]
        keyword_list = list(set(raw_keywords))

        doc_id = str(uuid.uuid4())
        doc_visibility = "Aggregate" if permission_type.value == "Aggregate" else "Private"
        db.add(models.Document(id=doc_id, file_path=file.filename, visibility=doc_visibility))
        db.add(models.UserDocumentPermission(
            id=str(uuid.uuid4()), user_id=user_id, document_id=doc_id, permission_type=models.PermissionType.Owner
        ))

        for word in keyword_list:
            db_kw = db.query(models.Keyword).filter(models.Keyword.word == word).first()
            if not db_kw:
                db_kw = models.Keyword(word=word)
                db.add(db_kw)
                db.flush()
            db.add(models.DocumentKeyword(document_id=doc_id, keyword_id=db_kw.id))

        chunks = chunk_text(full_text)

        for i, chunk in enumerate(chunks):
            response = openai_client.embeddings.create(
                input=chunk,
                model=EMBEDDING_MODEL
            )
            embedding_vector = response.data[0].embedding
            
            chunk_id = str(uuid.uuid4())
            collection.add(
                ids=[chunk_id],
                embeddings=[embedding_vector],
                metadatas=[{"document_id": doc_id}],
                documents=[chunk]
            )
            
            db.add(models.DocumentChunk(
                id=chunk_id, document_id=doc_id, chunk_index=i, text_content=chunk, vector_id=chunk_id
            ))

        db.commit()
        return {"status": "success", "keywords": keyword_list, "document_id": doc_id}

    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/ask")
async def ask_infobank(
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
        2. Handle synonyms and grammar (e.g., if the question asks about "fish" or "creatures", map it to the tag "ecosystem" if available).
        3. CRITICAL RULE: You MUST strictly copy the exact words from the list. Do not invent new words.
        4. If the question is completely unrelated to ALL of these tags, output the exact word: NONE.
        5. Return ONLY a comma-separated list of the selected tags (or NONE). No extra text.
        """
        
        kw_response = openai_client.chat.completions.create(
            model=MODEL_NAME,
            messages=[{"role": "user", "content": kw_prompt}],
            temperature=0.1
        )
        
        raw_result = kw_response.choices[0].message.content.strip()
        print(f"--- DEBUG: LLM Smart Routing Result: {raw_result} ---")
        
        if raw_result == "NONE":
            return {"status": "controlled_failure", "message": "There is no document related to the question in the InfoBank."}

        question_keywords = [k.strip() for k in raw_result.split(',') if k.strip()]
        
        matched_keywords = db.query(models.Keyword).filter(models.Keyword.word.in_(question_keywords)).all()

        if not matched_keywords:
            return {"status": "controlled_failure", "message": "There is no document related to the question in the InfoBank."}

        kw_ids = [kw.id for kw in matched_keywords]
        matched_doc_records = db.query(models.DocumentKeyword.document_id).filter(
            models.DocumentKeyword.keyword_id.in_(kw_ids)
        ).distinct().all()
        candidate_doc_ids = [record[0] for record in matched_doc_records]

        if not candidate_doc_ids:
            return {"status": "controlled_failure", "message": "There is no document related to the question in the InfoBank."}

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

        all_allowed_docs = list(set(direct_access_docs + aggregate_docs))

        if not all_allowed_docs:
            return {"status": "controlled_failure", "message": "There is no document related to the question in the InfoBank."}

        response = openai_client.embeddings.create(
            input=question,
            model=EMBEDDING_MODEL
        )
        question_vector = response.data[0].embedding

        where_clause = {"document_id": all_allowed_docs[0]} if len(all_allowed_docs) == 1 else {"document_id": {"$in": all_allowed_docs}}
        results = collection.query(
            query_embeddings=[question_vector],
            n_results=4,
            where=where_clause
        )

        if not results['documents'] or not results['documents'][0]:
            return {"status": "success", "answer": "The answer cannot be found in the document."}

        context_text = "\n\n---\n\n".join(results['documents'][0])

        system_instruction = (
            "You are a precise data analysis expert. Answer ONLY based on the provided context. "
            "CRITICAL RULE: Answer in the exact same language as the question was given in. "
            "Do not translate the answer to another language unless explicitly requested. "
            "If the information is missing or unclear, answer strictly with: 'The answer cannot be found in the document.' (translated to the language of the question). "
            "No hallucinations are allowed."
        )

        final_response = openai_client.chat.completions.create(
            model=MODEL_NAME,
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
                
                sources_list.append({
                    "file_name": file_name,
                    "text": chunk_text
                })

        return {
            "status": "success",
            "question": question,
            "extracted_keywords": question_keywords,
            "searched_documents_count": len(all_allowed_docs),
            "answer": final_response.choices[0].message.content,
            "sources": sources_list
        }

    except Exception as e:
        db.rollback()
        print(f"ERROR AT THE ASK ENDPOINT: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/test-db")
def test_db_connection(db: Session = Depends(get_db)):
    return {"status": "success", "users_in_db": db.query(models.User).count()}

@app.get("/api/knowledge-map/{user_id}")
def get_knowledge_map(user_id: str, db: Session = Depends(get_db)):
    try:
        results = db.query(
            models.Keyword.word,
            func.count(models.DocumentKeyword.document_id).label('doc_count')
        ).join(
            models.DocumentKeyword, models.Keyword.id == models.DocumentKeyword.keyword_id
        ).join(
            models.UserDocumentPermission, models.DocumentKeyword.document_id == models.UserDocumentPermission.document_id
        ).filter(
            models.UserDocumentPermission.user_id == user_id
        ).group_by(
            models.Keyword.word
        ).order_by(
            func.count(models.DocumentKeyword.document_id).desc()
        ).all()

        return {
            "status": "success", 
            "map": [{"keyword": r[0], "count": r[1]} for r in results]
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/ontology/{user_id}")
def get_ontology(user_id: str, db: Session = Depends(get_db)):
    try:
        permissions = db.query(models.UserDocumentPermission).filter(
            models.UserDocumentPermission.user_id == user_id
        ).all()
        doc_ids = [p.document_id for p in permissions]

        if not doc_ids:
            return {"nodes": [], "links": []}

        doc_kws = db.query(models.DocumentKeyword).filter(
            models.DocumentKeyword.document_id.in_(doc_ids)
        ).all()

        doc_to_kw = {}
        kw_counts = {}
        all_kw_ids = set()

        for dk in doc_kws:
            if dk.document_id not in doc_to_kw:
                doc_to_kw[dk.document_id] = []
            doc_to_kw[dk.document_id].append(dk.keyword_id)
            kw_counts[dk.keyword_id] = kw_counts.get(dk.keyword_id, 0) + 1
            all_kw_ids.add(dk.keyword_id)

        kws = db.query(models.Keyword).filter(models.Keyword.id.in_(list(all_kw_ids))).all()
        kw_id_to_word = {kw.id: kw.word for kw in kws}

        nodes = [{"id": kw_id_to_word[k_id], "val": count} for k_id, count in kw_counts.items()]

        links_dict = {}
        for doc_id, k_ids in doc_to_kw.items():
            for i in range(len(k_ids)):
                for j in range(i + 1, len(k_ids)):
                    kw1 = kw_id_to_word[k_ids[i]]
                    kw2 = kw_id_to_word[k_ids[j]]
                    pair = tuple(sorted([kw1, kw2]))
                    links_dict[pair] = links_dict.get(pair, 0) + 1

        links = [{"source": pair[0], "target": pair[1], "value": weight} for pair, weight in links_dict.items()]

        return {"status": "success", "nodes": nodes, "links": links}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/documents/{user_id}")
def get_user_documents(user_id: str, db: Session = Depends(get_db)):
    try:
        permissions = db.query(models.UserDocumentPermission).filter(
            models.UserDocumentPermission.user_id == user_id
        ).all()
        
        doc_list = []
        for p in permissions:
            doc = db.query(models.Document).filter(models.Document.id == p.document_id).first()
            if not doc:
                continue
                
            kw_records = db.query(models.Keyword.word).join(
                models.DocumentKeyword, models.Keyword.id == models.DocumentKeyword.keyword_id
            ).filter(
                models.DocumentKeyword.document_id == doc.id
            ).all()
            
            keywords = [k[0] for k in kw_records]

            display_perm = "Aggregate" if doc.visibility == "Aggregate" else p.permission_type.value
            
            doc_list.append({
                "document_id": doc.id,
                "file_name": doc.file_path,
                "permission": display_perm,
                "is_owner": p.permission_type.value == "Owner",
                "keywords": keywords,
                "upload_date": doc.upload_date.strftime("%Y-%m-%d %H:%M") if doc.upload_date else "N/A"
            })
            
        return {"status": "success", "documents": doc_list}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/documents/update-keywords")
def update_keywords(doc_id: str = Form(...), keywords: str = Form(...), db: Session = Depends(get_db)):
    db.query(models.DocumentKeyword).filter(models.DocumentKeyword.document_id == doc_id).delete()
    
    new_kw_list = [k.strip() for k in keywords.split(',') if k.strip()]
    for word in new_kw_list:
        db_kw = db.query(models.Keyword).filter(models.Keyword.word == word).first()
        if not db_kw:
            db_kw = models.Keyword(word=word)
            db.add(db_kw)
            db.flush()
        db.add(models.DocumentKeyword(document_id=doc_id, keyword_id=db_kw.id))
    
    db.commit()
    return {"status": "success", "message": "Keywords updated"}

@app.post("/api/documents/update-permission")
def update_permission(
    doc_id: str = Form(...), 
    user_id: str = Form(...), 
    new_perm: str = Form(...), 
    db: Session = Depends(get_db)
):
    perm_record = db.query(models.UserDocumentPermission).filter(
        models.UserDocumentPermission.document_id == doc_id,
        models.UserDocumentPermission.user_id == user_id
    ).first()

    if not perm_record or perm_record.permission_type != models.PermissionType.Owner:
        raise HTTPException(status_code=403, detail="Only the Owner can change permissions.")

    doc = db.query(models.Document).filter(models.Document.id == doc_id).first()
    if doc:
        doc.visibility = "Aggregate" if new_perm == "Aggregate" else "Private"

    db.commit()
    return {"status": "success", "message": "Permission updated"}

@app.delete("/api/documents/delete")
def delete_document(
    doc_id: str = Form(...), 
    user_id: str = Form(...), 
    db: Session = Depends(get_db)
):
    perm_record = db.query(models.UserDocumentPermission).filter(
        models.UserDocumentPermission.document_id == doc_id,
        models.UserDocumentPermission.user_id == user_id
    ).first()

    if not perm_record:
        raise HTTPException(status_code=404, detail="The requested document or permission could not be found.")
    
    if perm_record.permission_type != models.PermissionType.Owner:
        db.delete(perm_record)
        db.commit()
        return {"status": "success", "message": "Successfully unsubscribed from the document."}

    try:
        try:
            collection.delete(where={"document_id": doc_id})
        except Exception:
            pass
        db.query(models.DocumentChunk).filter(models.DocumentChunk.document_id == doc_id).delete()
        db.query(models.DocumentKeyword).filter(models.DocumentKeyword.document_id == doc_id).delete()
        db.query(models.UserDocumentPermission).filter(models.UserDocumentPermission.document_id == doc_id).delete()

        doc = db.query(models.Document).filter(models.Document.id == doc_id).first()
        if doc:
            db.delete(doc)
        
        db.commit()
        return {"status": "success", "message": "The document and its vectors have been permanently deleted."}
    
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"An error occurred while deleting: {str(e)}")

@app.post("/api/documents/transfer")
def transfer_document_ownership(
    doc_id: str = Form(...),
    user_id: str = Form(...),
    new_username: str = Form(...),
    db: Session = Depends(get_db)
):

    current_perm = db.query(models.UserDocumentPermission).filter(
        models.UserDocumentPermission.document_id == doc_id,
        models.UserDocumentPermission.user_id == user_id
    ).first()

    if not current_perm or current_perm.permission_type != models.PermissionType.Owner:
        raise HTTPException(status_code=403, detail="You are not authorized to transfer this document (only the Owner can do so).")

    target_user = db.query(models.User).filter(models.User.username == new_username).first()
    if not target_user:
        raise HTTPException(status_code=404, detail="The provided user was not found in the system.")

    if target_user.id == user_id:
        raise HTTPException(status_code=400, detail="You are not permitted to transfer this document to yourself.")

    existing_target_perm = db.query(models.UserDocumentPermission).filter(
        models.UserDocumentPermission.document_id == doc_id,
        models.UserDocumentPermission.user_id == target_user.id
    ).first()

    if existing_target_perm:
        db.delete(existing_target_perm)

    current_perm.user_id = target_user.id
    
    db.commit()
    
    return {"status": "success", "message": f"Ownership successfully transferred to the user: {target_user.username}"}