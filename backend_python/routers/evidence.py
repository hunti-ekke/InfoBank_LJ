from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

import evidence_service
import models
import security
from database import get_db

router = APIRouter(prefix="/api/evidence", tags=["Evidence"])


class EvidenceUnitIn(BaseModel):
    source_type: str = "Other"
    title: str
    content: str
    source_timestamp: str | None = None
    thread_id: str | None = None
    relation_key: str | None = None
    metadata: Dict[str, Any] | None = None


class EvidenceImportRequest(BaseModel):
    units: List[EvidenceUnitIn]


@router.post("/import")
def import_evidence_units(
    payload: EvidenceImportRequest,
    user_id: str = Depends(security.get_current_user_id),
    db: Session = Depends(get_db),
):
    try:
        created = evidence_service.import_evidence_units(
            db,
            user_id,
            [unit.model_dump() for unit in payload.units],
        )
        return {
            "status": "success",
            "imported": len(created),
            "ids": [unit.id for unit in created],
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/units")
def list_evidence_units(
    user_id: str = Depends(security.get_current_user_id),
    db: Session = Depends(get_db),
):
    units = db.query(models.EvidenceUnit).filter(models.EvidenceUnit.user_id == user_id).order_by(models.EvidenceUnit.created_at.desc()).all()
    return {
        "status": "success",
        "units": [
            {
                "id": unit.id,
                "source_type": unit.source_type.value if hasattr(unit.source_type, "value") else str(unit.source_type),
                "title": unit.title,
                "content": unit.content,
                "source_timestamp": unit.source_timestamp.isoformat() if unit.source_timestamp else None,
                "thread_id": unit.thread_id,
                "relation_key": unit.relation_key,
                "metadata_json": unit.metadata_json,
            }
            for unit in units
        ],
    }


@router.get("/action-list")
def reconstruct_current_action_list(
    user_id: str = Depends(security.get_current_user_id),
    db: Session = Depends(get_db),
):
    try:
        return evidence_service.reconstruct_action_list(db, user_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/demo-action-list")
def seed_demo_action_list(
    user_id: str = Depends(security.get_current_user_id),
    db: Session = Depends(get_db),
):
    """Seed the exact illustrative scenario from the CITDS draft as evidence units."""

    demo_units = [
        {
            "source_type": "Email",
            "title": "Official PhD review request - Anna Toth",
            "content": "Official request. Action: review the PhD dissertation draft for candidate Anna Toth. Deadline: 2026-06-03.",
            "source_timestamp": "2026-05-20T09:00:00",
            "thread_id": "phd-anna-toth",
            "relation_key": "phd-review-anna-toth",
        },
        {
            "source_type": "BrowserHistory",
            "title": "Visited doctoral review template page",
            "content": "Browser history: search for PhD review template and doctoral rules. This is contextual support only.",
            "source_timestamp": "2026-05-21T10:00:00",
            "relation_key": "phd-review-anna-toth",
        },
        {
            "source_type": "Calendar",
            "title": "Data Science exam announcement obligation",
            "content": "Course obligation. Action: announce the Data Science exam dates. Deadline: 2026-06-05.",
            "source_timestamp": "2026-05-22T12:00:00",
            "relation_key": "data-science-exam-announcement",
        },
        {
            "source_type": "Email",
            "title": "Kosice research trip invitation accepted",
            "content": "Invitation accepted for a three-day research trip. Action: book a hotel in Kosice for the three-day research trip. Deadline: 2026-06-10.",
            "source_timestamp": "2026-05-23T14:00:00",
            "relation_key": "kosice-trip",
        },
        {
            "source_type": "BrowserHistory",
            "title": "Kosice hotel and transport searches",
            "content": "Browser history: searched hotel in Kosice, map, transport, program pages. Contextual support, cannot create obligation alone.",
            "source_timestamp": "2026-05-24T16:00:00",
            "relation_key": "kosice-trip",
        },
        {
            "source_type": "Email",
            "title": "Student emails about database systems course",
            "content": "Five unanswered student emails about the database systems course. Action: answer five unanswered student emails about the database systems course.",
            "source_timestamp": "2026-05-25T08:30:00",
            "thread_id": "db-course-students",
            "relation_key": "database-systems-student-emails",
        },
        {
            "source_type": "Email",
            "title": "Previous trip documentation task completed",
            "content": "The previous-trip documentation task was completed and closed. This contrastive evidence closes the earlier reporting obligation.",
            "source_timestamp": "2026-05-18T11:00:00",
            "relation_key": "previous-trip-documentation",
        },
        {
            "source_type": "BrowserHistory",
            "title": "Previous trip invoice form page",
            "content": "Browser history: visited reimbursement form and invoice page for previous trip. Contextual only.",
            "source_timestamp": "2026-05-17T11:00:00",
            "relation_key": "previous-trip-documentation",
        },
    ]
    try:
        created = evidence_service.import_evidence_units(db, user_id, demo_units)
        return {"status": "success", "imported": len(created), "ids": [u.id for u in created]}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
