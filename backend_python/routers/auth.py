import uuid
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
import models
import schemas
import security
from database import get_db

router = APIRouter(prefix="/api", tags=["Auth"])

@router.post("/register")
def register_user(user_data: schemas.UserRegister, db: Session = Depends(get_db)):
    existing_user = db.query(models.User).filter(models.User.email == user_data.email).first()
    if existing_user:
        raise HTTPException(status_code=400, detail="Email already registered.")
    
    hashed_password = security.hash_password(user_data.password)
    new_user = models.User(
        id=str(uuid.uuid4()),
        email=user_data.email,
        username=user_data.username,
        password_hash=hashed_password
    )
    db.add(new_user)
    db.commit()
    return {"status": "success", "message": "Registration successful!"}

@router.post("/login")
def login_user(user_data: schemas.UserLogin, db: Session = Depends(get_db)):
    user = db.query(models.User).filter(models.User.email == user_data.email).first()
    if not user or not getattr(user, 'password_hash', None) or not security.verify_password(user_data.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Incorrect email or password.")
    
    token_data = {"sub": user.id, "email": user.email}
    token = security.create_access_token(token_data)
    return {"status": "success", "access_token": token, "user_id": user.id, "username": user.username}