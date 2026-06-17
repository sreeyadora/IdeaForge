import os
from fastapi import FastAPI, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import List, Optional
from database import engine, get_db, Base
from models import Invoice_manager

Base.metadata.create_all(bind=engine)

app = FastAPI(title="Invoice_Manager API")

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
    title: str
    amount: Optional[float] = 0.0
    category: Optional[str] = ""
    date: Optional[str] = ""


class ItemUpdate(BaseModel):
    title: Optional[str] = None
    amount: Optional[float] = None
    category: Optional[str] = None
    date: Optional[str] = None


class ItemOut(BaseModel):
    id: int
    title: str
    amount: float
    category: str
    date: str

    class Config:
        from_attributes = True


# ── Health ────────────────────────────────────────────────────────────────────

@app.get("/health", tags=["health"])
def health():
    return {"status": "ok", "app": "Invoice_Manager"}


# ── CRUD ──────────────────────────────────────────────────────────────────────

@app.post("/items", response_model=ItemOut, tags=["items"])
def create_item(item: ItemCreate, db: Session = Depends(get_db)):
    obj = Invoice_manager(
        title=item.title,
        amount=item.amount or 0.0,
        category=item.category or "",
        date=item.date or "",
    )
    db.add(obj)
    db.commit()
    db.refresh(obj)
    return obj


@app.get("/items", response_model=List[ItemOut], tags=["items"])
def read_items(skip: int = 0, limit: int = 100, db: Session = Depends(get_db)):
    return db.query(Invoice_manager).offset(skip).limit(limit).all()


@app.get("/items/{item_id}", response_model=ItemOut, tags=["items"])
def read_item(item_id: int, db: Session = Depends(get_db)):
    obj = db.query(Invoice_manager).filter(Invoice_manager.id == item_id).first()
    if not obj:
        raise HTTPException(status_code=404, detail="Item not found")
    return obj


@app.put("/items/{item_id}", response_model=ItemOut, tags=["items"])
def update_item(item_id: int, item: ItemUpdate, db: Session = Depends(get_db)):
    obj = db.query(Invoice_manager).filter(Invoice_manager.id == item_id).first()
    if not obj:
        raise HTTPException(status_code=404, detail="Item not found")
    if item.title is not None:
        obj.title = item.title
    if item.amount is not None:
        obj.amount = item.amount
    if item.category is not None:
        obj.category = item.category
    if item.date is not None:
        obj.date = item.date
    db.commit()
    db.refresh(obj)
    return obj


@app.delete("/items/{item_id}", tags=["items"])
def delete_item(item_id: int, db: Session = Depends(get_db)):
    obj = db.query(Invoice_manager).filter(Invoice_manager.id == item_id).first()
    if not obj:
        raise HTTPException(status_code=404, detail="Item not found")
    db.delete(obj)
    db.commit()
    return {"detail": "Deleted"}
