import os
from fastapi import FastAPI, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import List, Optional
from database import engine, get_db, Base
from models import Study_planner

Base.metadata.create_all(bind=engine)

app = FastAPI(title="Study_Planner API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

FRONTEND_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "frontend")
)
app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")


@app.get("/", response_class=FileResponse, include_in_schema=False)
def serve_index():
    return FileResponse(os.path.join(FRONTEND_DIR, "index.html"))


# ── Pydantic schemas ──────────────────────────────────────────────────────────

class ItemCreate(BaseModel):
    subject: str
    topic: Optional[str] = ""
    duration_minutes: Optional[int] = 0
    study_date: Optional[str] = ""
    completed: Optional[bool] = False


class ItemUpdate(BaseModel):
    subject: Optional[str] = None
    topic: Optional[str] = None
    duration_minutes: Optional[int] = None
    study_date: Optional[str] = None
    completed: Optional[bool] = None


class ItemOut(BaseModel):
    id: int
    subject: str
    topic: str
    duration_minutes: int
    study_date: str
    completed: bool

    class Config:
        from_attributes = True


# ── Health ────────────────────────────────────────────────────────────────────

@app.get("/health", tags=["health"])
def health():
    return {"status": "ok", "app": "Study_Planner"}


# ── CRUD ──────────────────────────────────────────────────────────────────────

@app.post("/items", response_model=ItemOut, tags=["items"])
def create_item(item: ItemCreate, db: Session = Depends(get_db)):
    obj = Study_planner(
        subject=item.subject,
        topic=item.topic or "",
        duration_minutes=item.duration_minutes or 0,
        study_date=item.study_date or "",
        completed=item.completed or False,
    )
    db.add(obj)
    db.commit()
    db.refresh(obj)
    return obj


@app.get("/items", response_model=List[ItemOut], tags=["items"])
def read_items(skip: int = 0, limit: int = 100, db: Session = Depends(get_db)):
    return db.query(Study_planner).offset(skip).limit(limit).all()


@app.get("/items/{item_id}", response_model=ItemOut, tags=["items"])
def read_item(item_id: int, db: Session = Depends(get_db)):
    obj = db.query(Study_planner).filter(Study_planner.id == item_id).first()
    if not obj:
        raise HTTPException(status_code=404, detail="Item not found")
    return obj


@app.put("/items/{item_id}", response_model=ItemOut, tags=["items"])
def update_item(item_id: int, item: ItemUpdate, db: Session = Depends(get_db)):
    obj = db.query(Study_planner).filter(Study_planner.id == item_id).first()
    if not obj:
        raise HTTPException(status_code=404, detail="Item not found")
    if item.subject is not None:
        obj.subject = item.subject
    if item.topic is not None:
        obj.topic = item.topic
    if item.duration_minutes is not None:
        obj.duration_minutes = item.duration_minutes
    if item.study_date is not None:
        obj.study_date = item.study_date
    if item.completed is not None:
        obj.completed = item.completed
    db.commit()
    db.refresh(obj)
    return obj


@app.delete("/items/{item_id}", tags=["items"])
def delete_item(item_id: int, db: Session = Depends(get_db)):
    obj = db.query(Study_planner).filter(Study_planner.id == item_id).first()
    if not obj:
        raise HTTPException(status_code=404, detail="Item not found")
    db.delete(obj)
    db.commit()
    return {"detail": "Deleted"}
