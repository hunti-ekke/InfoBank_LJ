from fastapi import FastAPI, Depends
from sqlalchemy.orm import Session
import models
from database import engine, get_db

models.Base.metadata.create_all(bind=engine)

app = FastAPI(
    title="InfoBank API", 
    description="ICAI 2026 Proof of Concept",
    version="1.0.0"
)

@app.get("/")
def read_root():
    return {"message": "Az InfoBank Backend fut!", "status": "Aktív"}

@app.get("/api/test-db")
def test_db_connection(db: Session = Depends(get_db)):
    user_count = db.query(models.User).count()
    return {
        "status": "success",
        "message": "Adatbázis kapcsolat sikeres!", 
        "users_in_db": user_count
    }