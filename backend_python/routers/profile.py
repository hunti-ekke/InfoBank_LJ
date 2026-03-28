from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
import models
import schemas
import security
from database import get_db

router = APIRouter(prefix="/api", tags=["Profile"])

@router.get("/profile/me")
def get_profile(user_id: str = Depends(security.get_current_user_id), db: Session = Depends(get_db)):
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

@router.put("/profile/update")
def update_profile(profile_data: schemas.ProfileUpdate, user_id: str = Depends(security.get_current_user_id), db: Session = Depends(get_db)):
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

@router.get("/users/search")
def search_users(q: str, db: Session = Depends(get_db)):
    if not q or len(q) < 2:
        return {"status": "success", "users": []}
    users = db.query(models.User).filter(models.User.username.ilike(f"%{q}%")).limit(5).all()
    return {"status": "success", "users": [{"username": u.username, "avatar_url": getattr(u, 'avatar_url', None)} for u in users]}