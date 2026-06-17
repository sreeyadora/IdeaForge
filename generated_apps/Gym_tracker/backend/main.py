import os
from fastapi import FastAPI, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import List, Optional
from database import engine, get_db, Base
from models import Gym_tracker

Base.metadata.create_all(bind=engine)

app = FastAPI(title="gym_tracker API")

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
    exercise: str
    sets: Optional[int] = 0
    reps: Optional[int] = 0
    weight: Optional[float] = 0.0
    workout_date: Optional[str] = ""


class ItemUpdate(BaseModel):
    exercise: Optional[str] = None
    sets: Optional[int] = None
    reps: Optional[int] = None
    weight: Optional[float] = None
    workout_date: Optional[str] = None


class ItemOut(BaseModel):
    id: int
    exercise: str
    sets: int
    reps: int
    weight: float
    workout_date: str

    class Config:
        from_attributes = True


# ── Health ────────────────────────────────────────────────────────────────────

@app.get("/health", tags=["health"])
def health():
    return {"status": "ok", "app": "gym_tracker"}


# ── CRUD ──────────────────────────────────────────────────────────────────────

@app.post("/items", response_model=ItemOut, tags=["items"])
def create_item(item: ItemCreate, db: Session = Depends(get_db)):
    obj = Gym_tracker(
        exercise=item.exercise,
        sets=item.sets or 0,
        reps=item.reps or 0,
        weight=item.weight or 0.0,
        workout_date=item.workout_date or "",
    )
    db.add(obj)
    db.commit()
    db.refresh(obj)
    return obj


@app.get("/items", response_model=List[ItemOut], tags=["items"])
def read_items(skip: int = 0, limit: int = 100, db: Session = Depends(get_db)):
    return db.query(Gym_tracker).offset(skip).limit(limit).all()


@app.get("/items/{item_id}", response_model=ItemOut, tags=["items"])
def read_item(item_id: int, db: Session = Depends(get_db)):
    obj = db.query(Gym_tracker).filter(Gym_tracker.id == item_id).first()
    if not obj:
        raise HTTPException(status_code=404, detail="Item not found")
    return obj


@app.put("/items/{item_id}", response_model=ItemOut, tags=["items"])
def update_item(item_id: int, item: ItemUpdate, db: Session = Depends(get_db)):
    obj = db.query(Gym_tracker).filter(Gym_tracker.id == item_id).first()
    if not obj:
        raise HTTPException(status_code=404, detail="Item not found")
    if item.exercise is not None:
        obj.exercise = item.exercise
    if item.sets is not None:
        obj.sets = item.sets
    if item.reps is not None:
        obj.reps = item.reps
    if item.weight is not None:
        obj.weight = item.weight
    if item.workout_date is not None:
        obj.workout_date = item.workout_date
    db.commit()
    db.refresh(obj)
    return obj


@app.delete("/items/{item_id}", tags=["items"])
def delete_item(item_id: int, db: Session = Depends(get_db)):
    obj = db.query(Gym_tracker).filter(Gym_tracker.id == item_id).first()
    if not obj:
        raise HTTPException(status_code=404, detail="Item not found")
    db.delete(obj)
    db.commit()
    return {"detail": "Deleted"}
