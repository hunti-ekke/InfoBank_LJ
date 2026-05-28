import uuid
import re
import fitz
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from sqlalchemy.orm import Session
import models
import ai_service
import security
from database import get_db

router = APIRouter(prefix="/api", tags=["Documents"])


def require_owner(db: Session, doc_id: str, user_id: str) -> models.UserDocumentPermission:
    perm_record = db.query(models.UserDocumentPermission).filter(
        models.UserDocumentPermission.document_id == doc_id,
        models.UserDocumentPermission.user_id == user_id,
    ).first()
    if not perm_record or perm_record.permission_type != models.PermissionType.Owner:
        raise HTTPException(status_code=403, detail="Only the Owner can perform this operation.")
    return perm_record


def visibility_from_permission(permission_type: models.PermissionType | str) -> str:
    value = permission_type.value if hasattr(permission_type, "value") else str(permission_type)
    if value == "Aggregate":
        return "Aggregate"
    if value == "Metadata":
        return "Metadata"
    return "Private"


def display_permission(doc: models.Document, permission: models.UserDocumentPermission) -> str:
    if doc.visibility in {"Aggregate", "Metadata"}:
        return doc.visibility
    return permission.permission_type.value if hasattr(permission.permission_type, "value") else str(permission.permission_type)


def deterministic_keywords_from_text(text: str) -> list[str]:
    """Add stable routing keywords that LLM keyword extraction may miss.

    The chat router uses document keywords as its first routing signal. Some
    specific factual questions such as project codename can fail if the LLM
    extractor chooses only broad topical words. These deterministic additions
    keep uploaded owner documents discoverable without weakening governance.
    """

    lowered = text.lower()
    rules = {
        "project": [r"\bproject\b"],
        "codename": [r"\bcodename\b", r"\bcode\s*name\b"],
        "action": [r"\baction\b", r"\baction item\b"],
        "deadline": [r"\bdeadline\b", r"\bdue\b", r"\bby\s+20\d{2}-\d{2}-\d{2}\b"],
        "approval": [r"\bapproval\b"],
        "average": [r"\baverage\b", r"\bmean\b"],
        "metadata": [r"\bmetadata\b"],
        "aggregate": [r"\baggregate\b"],
        "browser": [r"\bbrowser\b"],
        "history": [r"\bhistory\b"],
        "ownership": [r"\bownership\b", r"\bowner\b"],
        "document": [r"\bdocument\b"],
    }
    found: list[str] = []
    for keyword, patterns in rules.items():
        if any(re.search(pattern, lowered) for pattern in patterns):
            found.append(keyword)
    return found


@router.post("/upload")
async def upload_document(
    file: UploadFile = File(...),
    permission_type: models.PermissionType = Form(...),
    user_id: str = Depends(security.get_current_user_id),
    db: Session = Depends(get_db),
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
        CRITICAL RULE: You must output ONLY single words (unigrams)! DO NOT output multi-word phrases.
        Return ONLY a single, comma-separated list of these words in lowercase. No extra text.
        """
        
        text_for_analysis = full_text[:10000]
        completion = ai_service.openai_client.chat.completions.create(
            model=ai_service.MODEL_NAME,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": text_for_analysis},
            ],
            temperature=0.3,
        )
        raw_keywords = [k.strip().lower() for k in completion.choices[0].message.content.split(',') if k.strip()]
        keyword_list = list(dict.fromkeys(raw_keywords + deterministic_keywords_from_text(full_text)))

        doc_id = str(uuid.uuid4())
        doc_visibility = visibility_from_permission(permission_type)
        db.add(models.Document(id=doc_id, file_path=file.filename, visibility=doc_visibility))
        db.add(models.UserDocumentPermission(id=str(uuid.uuid4()), user_id=user_id, document_id=doc_id, permission_type=models.PermissionType.Owner))

        for word in keyword_list:
            db_kw = db.query(models.Keyword).filter(models.Keyword.word == word).first()
            if not db_kw:
                db_kw = models.Keyword(word=word)
                db.add(db_kw)
                db.flush()
            db.add(models.DocumentKeyword(document_id=doc_id, keyword_id=db_kw.id))

        chunks = ai_service.chunk_text(full_text)
        for i, chunk in enumerate(chunks):
            response = ai_service.openai_client.embeddings.create(input=chunk, model=ai_service.EMBEDDING_MODEL)
            embedding_vector = response.data[0].embedding
            chunk_id = str(uuid.uuid4())
            ai_service.collection.add(ids=[chunk_id], embeddings=[embedding_vector], metadatas=[{"document_id": doc_id}], documents=[chunk])
            db.add(models.DocumentChunk(id=chunk_id, document_id=doc_id, chunk_index=i, text_content=chunk, vector_id=chunk_id))

        db.commit()
        return {"status": "success", "keywords": keyword_list, "document_id": doc_id, "visibility": doc_visibility}
    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/documents/me")
def get_my_documents(user_id: str = Depends(security.get_current_user_id), db: Session = Depends(get_db)):
    return build_document_list(user_id, db)


@router.get("/documents/{requested_user_id}")
def get_user_documents(requested_user_id: str, user_id: str = Depends(security.get_current_user_id), db: Session = Depends(get_db)):
    if requested_user_id != user_id:
        raise HTTPException(status_code=403, detail="You can only access your own document list.")
    return build_document_list(user_id, db)


def build_document_list(user_id: str, db: Session):
    try:
        permissions = db.query(models.UserDocumentPermission).filter(models.UserDocumentPermission.user_id == user_id).all()
        doc_list = []
        for p in permissions:
            doc = db.query(models.Document).filter(models.Document.id == p.document_id).first()
            if not doc:
                continue
            kw_records = db.query(models.Keyword.word).join(models.DocumentKeyword, models.Keyword.id == models.DocumentKeyword.keyword_id).filter(models.DocumentKeyword.document_id == doc.id).all()
            keywords = [k[0] for k in kw_records]
            doc_list.append({
                "document_id": doc.id,
                "file_name": doc.file_path,
                "permission": display_permission(doc, p),
                "visibility": doc.visibility,
                "is_owner": p.permission_type.value == "Owner",
                "keywords": keywords,
                "upload_date": doc.upload_date.strftime("%Y-%m-%d %H:%M") if doc.upload_date else "N/A",
            })
        return {"status": "success", "documents": doc_list}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/documents/update-keywords")
def update_keywords(doc_id: str = Form(...), keywords: str = Form(...), user_id: str = Depends(security.get_current_user_id), db: Session = Depends(get_db)):
    require_owner(db, doc_id, user_id)
    db.query(models.DocumentKeyword).filter(models.DocumentKeyword.document_id == doc_id).delete()
    new_kw_list = [k.strip().lower() for k in keywords.split(',') if k.strip()]
    for word in new_kw_list:
        db_kw = db.query(models.Keyword).filter(models.Keyword.word == word).first()
        if not db_kw:
            db_kw = models.Keyword(word=word)
            db.add(db_kw)
            db.flush()
        db.add(models.DocumentKeyword(document_id=doc_id, keyword_id=db_kw.id))
    db.commit()
    return {"status": "success", "message": "Keywords updated"}


@router.post("/documents/update-permission")
def update_permission(doc_id: str = Form(...), new_perm: str = Form(...), user_id: str = Depends(security.get_current_user_id), db: Session = Depends(get_db)):
    require_owner(db, doc_id, user_id)
    if new_perm not in {"Owner", "Reader", "Aggregate", "Metadata"}:
        raise HTTPException(status_code=400, detail="Invalid permission/visibility mode.")
    doc = db.query(models.Document).filter(models.Document.id == doc_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found.")
    doc.visibility = visibility_from_permission(new_perm)
    db.commit()
    return {"status": "success", "message": "Permission updated", "visibility": doc.visibility}


@router.delete("/documents/delete")
def delete_document(doc_id: str = Form(...), user_id: str = Depends(security.get_current_user_id), db: Session = Depends(get_db)):
    perm_record = db.query(models.UserDocumentPermission).filter(
        models.UserDocumentPermission.document_id == doc_id,
        models.UserDocumentPermission.user_id == user_id,
    ).first()
    if not perm_record:
        raise HTTPException(status_code=404, detail="The requested document or permission could not be found.")
    if perm_record.permission_type != models.PermissionType.Owner:
        db.delete(perm_record)
        db.commit()
        return {"status": "success", "message": "Successfully unsubscribed from the document."}
    try:
        try:
            ai_service.collection.delete(where={"document_id": doc_id})
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


@router.post("/documents/transfer")
def transfer_document_ownership(doc_id: str = Form(...), new_username: str = Form(...), user_id: str = Depends(security.get_current_user_id), db: Session = Depends(get_db)):
    current_perm = require_owner(db, doc_id, user_id)
    target_user = db.query(models.User).filter(models.User.username == new_username).first()
    if not target_user:
        raise HTTPException(status_code=404, detail="The provided user was not found in the system.")
    if target_user.id == user_id:
        raise HTTPException(status_code=400, detail="You are not permitted to transfer this document to yourself.")
    existing_target_perm = db.query(models.UserDocumentPermission).filter(models.UserDocumentPermission.document_id == doc_id, models.UserDocumentPermission.user_id == target_user.id).first()
    if existing_target_perm:
        db.delete(existing_target_perm)
    current_perm.user_id = target_user.id
    db.commit()
    return {"status": "success", "message": f"Ownership successfully transferred to the user: {target_user.username}"}
