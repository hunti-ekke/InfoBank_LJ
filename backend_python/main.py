from fastapi import FastAPI, Depends
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from dotenv import load_dotenv
import models
from database import engine, get_db

# IMPORTÁLÁS A ROUTERS MAPPÁBÓL:
from routers import auth, profile, analytics, documents, chat, admin

load_dotenv()

models.Base.metadata.create_all(bind=engine)

app = FastAPI(title="InfoBank API", version="3.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ROUTEREK BEKÖTÉSE
app.include_router(auth.router)
app.include_router(profile.router)
app.include_router(analytics.router)
app.include_router(documents.router)
app.include_router(chat.router)
app.include_router(admin.router)

@app.get("/api/test-db")
def test_db_connection(db: Session = Depends(get_db)):
    return {"status": "success", "users_in_db": db.query(models.User).count()}