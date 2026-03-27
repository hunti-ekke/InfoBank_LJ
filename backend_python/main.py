import os
import uuid
import time
import datetime
from typing import List
from fastapi import FastAPI, Depends, UploadFile, File, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session
from dotenv import load_dotenv
from pydantic import BaseModel
from sqlalchemy import func

# Auth
import jwt
from passlib.context import CryptContext

import fitz
import chromadb
from google import genai
from google.genai import types

import models
from database import engine, get_db

load_dotenv()

# GOOGLE AI 
client = genai.Client(api_key=os.getenv("GOOGLE_API_KEY"))
MODEL_NAME = "gemini-3.1-flash-lite-preview"
EMBEDDING_MODEL = "gemini-embedding-001"

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

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
SECRET_KEY = "info-bank-szuper-titkos-kulcs-2026"
ALGORITHM = "HS256"

# Token kinyeréséhez a fejlécből
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="api/login")

# --- SCHEMAS ---

class UserRegister(BaseModel):
    email: str
    username: str
    password: str

class UserLogin(BaseModel):
    email: str
    password: str

class ProfileUpdate(BaseModel):
    full_name: str | None = None
    email: str | None = None
    avatar_url: str | None = None

# --- UTILS ---

def chunk_text(text: str, chunk_size: int = 1000, overlap: int = 200) -> List[str]:
    chunks = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        chunks.append(text[start:end])
        start += (chunk_size - overlap)
    return chunks

def get_current_user_id(token: str = Depends(oauth2_scheme)):
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload.get("sub")
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Token invalid or expired.")

# --- AUTH ENDPOINTS ---

@app.post("/api/register")
def register_user(user_data: UserRegister, db: Session = Depends(get_db)):
    existing_user = db.query(models.User).filter(models.User.email == user_data.email).first()
    if existing_user:
        raise HTTPException(status_code=400, detail="Email already registered.")
    
    hashed_password = pwd_context.hash(user_data.password)
    new_user = models.User(
        id=str(uuid.uuid4()),
        email=user_data.email,
        username=user_data.username,
        password_hash=hashed_password
    )
    db.add(new_user)
    db.commit()
    
    return {"status": "success", "message": "Registration successful!"}

@app.post("/api/login")
def login_user(user_data: UserLogin, db: Session = Depends(get_db)):
    user = db.query(models.User).filter(models.User.email == user_data.email).first()

    if not user or not getattr(user, 'password_hash', None) or not pwd_context.verify(user_data.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Incorrect email or password.")
    
    token_data = {"sub": user.id, "email": user.email}
    token = jwt.encode(token_data, SECRET_KEY, algorithm=ALGORITHM)
    
    return {
        "status": "success",
        "access_token": token,
        "user_id": user.id,
        "username": user.username
    }

# --- PROFILE ENDPOINTS ---

@app.get("/api/profile/me")
def get_profile(user_id: str = Depends(get_current_user_id), db: Session = Depends(get_db)):
    user = db.query(models.User).filter(models.User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found.")
    return {
        "username": user.username,
        "email": user.email,
        "full_name": getattr(user, 'full_name', None),
        "avatar_url": getattr(user, 'avatar_url', None),
        "users_in_db": db.query(models.User).count()
    }

@app.put("/api/profile/update")
def update_profile(
    profile_data: ProfileUpdate, 
    user_id: str = Depends(get_current_user_id), 
    db: Session = Depends(get_db)
):
    user = db.query(models.User).filter(models.User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found.")

    if profile_data.full_name is not None:
        user.full_name = profile_data.full_name
    if profile_data.email is not None:
        user.email = profile_data.email
    if profile_data.avatar_url is not None:
        user.avatar_url = profile_data.avatar_url

    db.commit()
    return {"status": "success", "message": "Profile updated."}

# --- CORE ENDPOINTS ---

@app.post("/api/upload")
async def upload_document(
    file: UploadFile = File(...),
    user_id: str = Form(...),
    permission_type: models.PermissionType = Form(...),
    db: Session = Depends(get_db)
):
    temp_path = f"temp_{uuid.uuid4()}.pdf"
    try:
        content = await file.read()
        with open(temp_path, "wb") as f:
            f.write(content)

        # Gemini API uploading
        gemini_file = client.files.upload(
            file=temp_path,
            config={'display_name': file.filename}
        )

        while True:
            file_info = client.files.get(name=gemini_file.name)
            if file_info.state.name == 'ACTIVE':
                break
            elif file_info.state.name == 'FAILED':
                raise HTTPException(status_code=500, detail="Gemini file processing failed.")
            time.sleep(2)

        prompt = """
        Analyze the following document text.
        Extract the 3 to 5 most important core concepts in English.
        
        CRITICAL RULE: You must output ONLY single words (unigrams)! DO NOT output multi-word phrases (e.g., output "health", not "health benefits").
        Return ONLY a single, comma-separated list of these words in lowercase. No extra text.
        """
        response = client.models.generate_content(
            model=MODEL_NAME,
            contents=[gemini_file, prompt]
        )
        
        raw_keywords = [k.strip().lower() for k in response.text.split(',') if k.strip()]
        keyword_list = list(set(raw_keywords))

        doc_id = str(uuid.uuid4())
        
        # LÁTHATÓSÁG ÉS JOGOSULTSÁG SZÉTVÁLASZTÁSA
        doc_visibility = "Aggregate" if permission_type.value == "Aggregate" else "Private"
        db.add(models.Document(id=doc_id, file_path=file.filename, visibility=doc_visibility))
        
        # A feltöltő MINDIG Owner marad a jogosultsági táblában!
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

        doc_pdf = fitz.open(stream=content, filetype="pdf")
        full_text = "".join(page.get_text() for page in doc_pdf)
        chunks = chunk_text(full_text)

        for i, chunk in enumerate(chunks):
            emb_res = client.models.embed_content(
                model=EMBEDDING_MODEL,
                contents=chunk
            )
            
            chunk_id = str(uuid.uuid4())
            collection.add(
                ids=[chunk_id],
                embeddings=[emb_res.embeddings[0].values],
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
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)

@app.post("/api/ask")
async def ask_infobank(
    question: str = Form(...),
    user_id: str = Form(...),
    db: Session = Depends(get_db)
):
    try:
        kw_prompt = f"""
        Analyze the following question. 
        Use the recent conversation history to understand the context if the question relies on previous concepts.
        
        Current Question: {question}
        
        1. Identify the 3 most important core concepts in English for the current question.
        2. For each concept, generate 2 common English synonyms.
        3. CRITICAL RULE: You must output ONLY single words (unigrams)! DO NOT output multi-word phrases (e.g., output "health", not "health of residents").
        4. Return ONLY a single, comma-separated list of these ~9 single words. No extra text, no markdown.
        5. Answer in the same language as the question was given in.
        """
        kw_response = client.models.generate_content(model=MODEL_NAME, contents=kw_prompt)
        question_keywords = [k.strip().lower() for k in kw_response.text.split(',') if k.strip()]
        
        matched_keywords = db.query(models.Keyword).filter(models.Keyword.word.in_(question_keywords)).all()
        print(f"--- DEBUG: Gemini searched for these keywords: {question_keywords} ---")

        if not matched_keywords:
            return {"status": "controlled_failure", "message": "There is no document related to the question in the InfoBank."}

        kw_ids = [kw.id for kw in matched_keywords]
        matched_doc_records = db.query(models.DocumentKeyword.document_id).filter(
            models.DocumentKeyword.keyword_id.in_(kw_ids)
        ).distinct().all()
        candidate_doc_ids = [record[0] for record in matched_doc_records]

        if not candidate_doc_ids:
            return {"status": "controlled_failure", "message": "There is no document related to the question in the InfoBank."}

        # 1. Keresd meg a felhasználó SAJÁT (Owner, Reader) dokumentumait
        user_permissions = db.query(models.UserDocumentPermission).filter(
            models.UserDocumentPermission.user_id == user_id,
            models.UserDocumentPermission.document_id.in_(candidate_doc_ids)
        ).all()
        direct_access_docs = [p.document_id for p in user_permissions]

        # 2. Keresd meg az ÖSSZES "Aggregate" láthatóságú dokumentumot a jelöltek közül
        aggregate_docs_records = db.query(models.Document.id).filter(
            models.Document.id.in_(candidate_doc_ids),
            models.Document.visibility == "Aggregate"
        ).all()
        aggregate_docs = [r[0] for r in aggregate_docs_records]

        # 3. Egyesítsd a saját és a közös dokumentumokat (duplikációk kiszűrésével)
        all_allowed_docs = list(set(direct_access_docs + aggregate_docs))

        if not all_allowed_docs:
            return {"status": "controlled_failure", "message": "There is no document related to the question in the InfoBank."}

        emb_res = client.models.embed_content(model=EMBEDDING_MODEL, contents=question)
        question_vector = emb_res.embeddings[0].values

        # Keresés a ChromaDB-ben csak az engedélyezett doksikon
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

        prompt = f"Question: {question}\n\nContext from the document(s):\n{context_text}"
        response = client.models.generate_content(
            model=MODEL_NAME,
            contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction=system_instruction,
                temperature=0.1
            )
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
            "answer": response.text,
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
                "is_owner": p.permission_type.value == "Owner", # <--- EZT A SORT ADD HOZZÁ!
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

    # Ne a user "Owner" jogát írjuk felül, hanem a dokumentum láthatóságát!
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

@app.get("/api/users/search")
def search_users(q: str, db: Session = Depends(get_db)):
    if not q or len(q) < 2:
        return {"status": "success", "users": []}
    
    users = db.query(models.User).filter(
        models.User.username.ilike(f"%{q}%")
    ).limit(5).all()
    
    return {
        "status": "success", 
        "users": [{"username": u.username, "avatar_url": getattr(u, 'avatar_url', None)} for u in users]
    }

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