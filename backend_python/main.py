import os
import uuid
import time
from typing import List
from fastapi import FastAPI, Depends, UploadFile, File, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
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

class UserRegister(BaseModel):
    email: str
    username: str
    password: str

class UserLogin(BaseModel):
    email: str
    password: str

def chunk_text(text: str, chunk_size: int = 1000, overlap: int = 200) -> List[str]:
    chunks = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        chunks.append(text[start:end])
        start += (chunk_size - overlap)
    return chunks

@app.post("/api/register")
def register_user(user_data: UserRegister, db: Session = Depends(get_db)):
    existing_user = db.query(models.User).filter(models.User.email == user_data.email).first()
    if existing_user:
        raise HTTPException(status_code=400, detail="Ez az email cím már regisztrálva van.")
    
    hashed_password = pwd_context.hash(user_data.password)
    new_user = models.User(
        id=str(uuid.uuid4()),
        email=user_data.email,
        username=user_data.username,
        password_hash=hashed_password
    )
    db.add(new_user)
    db.commit()
    
    return {"status": "success", "message": "Sikeres regisztráció!"}

@app.post("/api/login")
def login_user(user_data: UserLogin, db: Session = Depends(get_db)):
    user = db.query(models.User).filter(models.User.email == user_data.email).first()

    if not user or not getattr(user, 'password_hash', None) or not pwd_context.verify(user_data.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Hibás email vagy jelszó.")
    
    token_data = {"sub": user.id, "email": user.email}
    token = jwt.encode(token_data, SECRET_KEY, algorithm=ALGORITHM)
    
    return {
        "status": "success",
        "access_token": token,
        "user_id": user.id,
        "username": user.username
    }

# CORE endpoints

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

        prompt = "Provide exactly 3-5 English keywords for this document. Return ONLY the words, separated by commas."
        response = client.models.generate_content(
            model=MODEL_NAME,
            contents=[gemini_file, prompt]
        )
        keyword_list = [k.strip() for k in response.text.split(',')]

        doc_id = str(uuid.uuid4())
        db.add(models.Document(id=doc_id, file_path=gemini_file.name))
        db.add(models.UserDocumentPermission(
            id=str(uuid.uuid4()), user_id=user_id, document_id=doc_id, permission_type=permission_type
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
        kw_prompt = f"Extract the 3 most important English keywords from this question. Return ONLY the words, separated by commas. Question: {question}"
        kw_response = client.models.generate_content(model=MODEL_NAME, contents=kw_prompt)
        question_keywords = [k.strip() for k in kw_response.text.split(',')]
        
        matched_keywords = db.query(models.Keyword).filter(models.Keyword.word.in_(question_keywords)).all()
        if not matched_keywords:
            return {"status": "controlled_failure", "message": "Nincs a kérdéshez kapcsolódó dokumentum az InfoBankban."}

        kw_ids = [kw.id for kw in matched_keywords]
        matched_doc_records = db.query(models.DocumentKeyword.document_id).filter(
            models.DocumentKeyword.keyword_id.in_(kw_ids)
        ).distinct().all()
        candidate_doc_ids = [record[0] for record in matched_doc_records]

        if not candidate_doc_ids:
            return {"status": "controlled_failure", "message": "Nincs a kérdéshez kapcsolódó dokumentum az InfoBankban."}

        permissions = db.query(models.UserDocumentPermission).filter(
            models.UserDocumentPermission.user_id == user_id,
            models.UserDocumentPermission.document_id.in_(candidate_doc_ids)
        ).all()

        direct_access_docs = [p.document_id for p in permissions if p.permission_type in [models.PermissionType.Owner, models.PermissionType.Reader]]
        aggregate_docs = [p.document_id for p in permissions if p.permission_type == models.PermissionType.Aggregate]

        if not direct_access_docs and aggregate_docs:
            return {
                "status": "controlled_failure", 
                "message": "Governance szabályok miatt a lekérdezés megtagadva: a dokumentumokhoz csak aggregált (anonym) hozzáférésed van."
            }
        
        if not direct_access_docs:
            raise HTTPException(status_code=403, detail="Nincs jogosultságod a kérdéshez tartozó dokumentumok olvasásához.")

        emb_res = client.models.embed_content(model=EMBEDDING_MODEL, contents=question)
        question_vector = emb_res.embeddings[0].values

        where_clause = {"document_id": direct_access_docs[0]} if len(direct_access_docs) == 1 else {"document_id": {"$in": direct_access_docs}}
        results = collection.query(
            query_embeddings=[question_vector],
            n_results=4,
            where=where_clause
        )

        if not results['documents'] or not results['documents'][0]:
            return {"status": "success", "answer": "Erre a kérdésre nem található válasz a dokumentumokban."}

        context_text = "\n\n---\n\n".join(results['documents'][0])

        system_instruction = (
            "Te egy precíz adatelemző szakértő vagy. KIZÁRÓLAG a csatolt kontextus alapján válaszolj. "
            "Ha az információ hiányzik vagy nem egyértelmű, válaszold pontosan ezt: 'Erre a kérdésre nem található válasz a dokumentumban.' "
            "Tilos a hallucináció."
        )

        prompt = f"Kérdés: {question}\n\nKontextus a dokumentum(ok)ból:\n{context_text}"
        response = client.models.generate_content(
            model=MODEL_NAME,
            contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction=system_instruction,
                temperature=0.1
            )
        )

        return {
            "status": "success",
            "question": question,
            "extracted_keywords": question_keywords,
            "searched_documents_count": len(direct_access_docs),
            "answer": response.text
        }

    except Exception as e:
        db.rollback()
        print(f"HIBA AZ ASK VÉGPONTON: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/test-db")
def test_db_connection(db: Session = Depends(get_db)):
    return {"status": "success", "users_in_db": db.query(models.User).count()}


@app.get("/api/knowledge-map/{user_id}")
def get_knowledge_map(user_id: str, db: Session = Depends(get_db)):
    """Visszaadja a felhasználó kulcsszavait és azok előfordulási számát."""
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

@app.get("/api/documents/{user_id}")
def get_user_documents(user_id: str, db: Session = Depends(get_db)):
    """Visszaadja a felhasználó összes dokumentumát, a jogokat és a kulcsszavakat."""
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
            
            doc_list.append({
                "document_id": doc.id,
                "file_name": doc.file_path,
                "permission": p.permission_type,
                "keywords": keywords,
                "upload_date": doc.upload_date.strftime("%Y-%m-%d %H:%M") if doc.upload_date else "N/A"
            })
            
        return {"status": "success", "documents": doc_list}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))