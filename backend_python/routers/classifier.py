from fastapi import APIRouter, Depends
from pydantic import BaseModel

import citds_classifier
import relevance
import security

router = APIRouter(prefix="/api/citds", tags=["CITDS Classifier"])


class ClassifierRequest(BaseModel):
    text: str
    task_intent: str = "general_document_question"
    use_decision: str = relevance.USE_FULL
    use_llm: bool = False


@router.post("/classify")
def classify_source(
    payload: ClassifierRequest,
    user_id: str = Depends(security.get_current_user_id),
):
    # user_id dependency intentionally scopes this endpoint to authenticated users;
    # the classifier itself does not need the user id.
    return {
        "status": "success",
        "classification": citds_classifier.classify_source(
            text=payload.text,
            task_intent=payload.task_intent,
            use_decision=payload.use_decision,
            use_llm=payload.use_llm,
        ),
    }
