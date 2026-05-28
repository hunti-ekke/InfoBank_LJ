from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import func
import models
import security
from database import get_db

router = APIRouter(prefix="/api", tags=["Analytics"])


@router.get("/knowledge-map/me")
def get_my_knowledge_map(user_id: str = Depends(security.get_current_user_id), db: Session = Depends(get_db)):
    return build_knowledge_map(user_id, db)


@router.get("/knowledge-map/{requested_user_id}")
def get_knowledge_map(
    requested_user_id: str,
    user_id: str = Depends(security.get_current_user_id),
    db: Session = Depends(get_db),
):
    if requested_user_id != user_id:
        raise HTTPException(status_code=403, detail="You can only access your own knowledge map.")
    return build_knowledge_map(user_id, db)


def build_knowledge_map(user_id: str, db: Session):
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

        return {"status": "success", "map": [{"keyword": r[0], "count": r[1]} for r in results]}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/ontology/me")
def get_my_ontology(user_id: str = Depends(security.get_current_user_id), db: Session = Depends(get_db)):
    return build_ontology(user_id, db)


@router.get("/ontology/{requested_user_id}")
def get_ontology(
    requested_user_id: str,
    user_id: str = Depends(security.get_current_user_id),
    db: Session = Depends(get_db),
):
    if requested_user_id != user_id:
        raise HTTPException(status_code=403, detail="You can only access your own ontology.")
    return build_ontology(user_id, db)


def build_ontology(user_id: str, db: Session):
    try:
        permissions = db.query(models.UserDocumentPermission).filter(models.UserDocumentPermission.user_id == user_id).all()
        doc_ids = [p.document_id for p in permissions]

        if not doc_ids:
            return {"nodes": [], "links": []}

        doc_kws = db.query(models.DocumentKeyword).filter(models.DocumentKeyword.document_id.in_(doc_ids)).all()
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
