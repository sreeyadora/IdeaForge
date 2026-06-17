import os
import sys
import csv
import json
import hashlib
import io
import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("ideaforge")

from fastapi import FastAPI, Depends, HTTPException, Request, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, StreamingResponse
from starlette.middleware.sessions import SessionMiddleware
from sqlalchemy import Column, Integer, String
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import List, Optional
from database import engine, get_db, Base
from models import Study_planner


# ── User model ────────────────────────────────────────────────────────────────

class User(Base):
    __tablename__ = "users"
    id       = Column(Integer, primary_key=True, index=True)
    username = Column(String(80), unique=True, nullable=False)
    password = Column(String(128), nullable=False)


# ── Safe DB initialisation ────────────────────────────────────────────────────

try:
    Base.metadata.create_all(bind=engine)
    logger.info("Database tables created / verified OK")
except Exception as _db_exc:
    logger.error("DB init failed: %s", _db_exc)
    raise SystemExit(f"Cannot initialise database: {_db_exc}") from _db_exc


# ── App + middleware ──────────────────────────────────────────────────────────

app = FastAPI(title="Study_Planner API")

# SessionMiddleware MUST be added before CORSMiddleware
app.add_middleware(SessionMiddleware, secret_key="ideaforge-secret-change-in-prod-32chars!")
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


# ── Startup / shutdown events ─────────────────────────────────────────────────

@app.on_event("startup")
async def on_startup():
    logger.info("Study_Planner starting up — frontend: %s", FRONTEND_DIR)


@app.on_event("shutdown")
async def on_shutdown():
    logger.info("Study_Planner shutting down")


# ── Global exception handler ──────────────────────────────────────────────────

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error("Unhandled exception on %s: %s", request.url.path, exc)
    return JSONResponse(
        status_code=500,
        content={"error": str(exc), "path": str(request.url.path)},
    )


# ── Safe input helpers ────────────────────────────────────────────────────────

def _safe_str(v, max_len: int = 500) -> str:
    if v is None:
        return ""
    return str(v).strip()[:max_len]


def _safe_int(v, default: int = 0) -> int:
    try:
        return int(v) if v not in (None, "", "null") else default
    except (ValueError, TypeError):
        return default


def _safe_float(v, default: float = 0.0) -> float:
    try:
        return float(v) if v not in (None, "", "null") else default
    except (ValueError, TypeError):
        return default


def _safe_bool(v) -> bool:
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        return v.lower() in ("true", "1", "yes", "on")
    return bool(v)


def _safe_date(v) -> str:
    # Accept yyyy-mm-dd or dd-mm-yyyy; always store as yyyy-mm-dd.
    if not v:
        return ""
    s = str(v).strip()
    # dd-mm-yyyy → yyyy-mm-dd
    import re as _re
    m = _re.match(r"^(\d{1,2})[/\\\-\.](\d{1,2})[/\\\-\.](\d{4})$", s)
    if m:
        d, mo, y = m.group(1), m.group(2), m.group(3)
        return f"{y}-{mo.zfill(2)}-{d.zfill(2)}"
    return s


# ── Auth helpers ──────────────────────────────────────────────────────────────

def _hash_pw(pw: str) -> str:
    return hashlib.sha256(pw.encode()).hexdigest()


def _current_user(request: Request):
    try:
        return request.session.get("user")
    except Exception:
        return None


def _require_user(request: Request):
    user = _current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user


# ── Auth routes ───────────────────────────────────────────────────────────────

@app.get("/login", response_class=FileResponse, include_in_schema=False)
def login_page():
    return FileResponse(os.path.join(FRONTEND_DIR, "login.html"))


@app.post("/login", include_in_schema=False)
def do_login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    try:
        user = db.query(User).filter(User.username == username).first()
        if not user or user.password != _hash_pw(password):
            return JSONResponse(status_code=401, content={"error": "Invalid credentials"})
        request.session["user"] = username
        return RedirectResponse("/", status_code=303)
    except Exception as exc:
        logger.error("Login error: %s", exc)
        return JSONResponse(status_code=500, content={"error": str(exc)})


@app.post("/register", include_in_schema=False)
def do_register(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    try:
        if not username or len(username) < 2:
            return JSONResponse(status_code=422, content={"error": "Username too short"})
        if not password or len(password) < 4:
            return JSONResponse(status_code=422, content={"error": "Password must be at least 4 characters"})
        if db.query(User).filter(User.username == username).first():
            return JSONResponse(status_code=409, content={"error": "Username already taken"})
        db.add(User(username=username, password=_hash_pw(password)))
        db.commit()
        request.session["user"] = username
        return RedirectResponse("/", status_code=303)
    except Exception as exc:
        db.rollback()
        logger.error("Register error: %s", exc)
        return JSONResponse(status_code=500, content={"error": str(exc)})


@app.post("/logout", include_in_schema=False)
def do_logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


@app.get("/api/me", tags=["auth"])
def get_me(request: Request):
    user = _current_user(request)
    return {"user": user, "authenticated": bool(user)}


# ── Pages ─────────────────────────────────────────────────────────────────────

@app.get("/", response_class=FileResponse, include_in_schema=False)
def serve_index(request: Request):
    if not _current_user(request):
        return RedirectResponse("/login", status_code=303)
    return FileResponse(os.path.join(FRONTEND_DIR, "index.html"))


# ── Pydantic schemas ──────────────────────────────────────────────────────────

class ItemCreate(BaseModel):
    service_name: str
    cost: Optional[float] = 0.0
    billing_cycle: Optional[str] = ""
    next_payment_date: Optional[str] = ""
    active: Optional[bool] = False


class ItemUpdate(BaseModel):
    service_name: Optional[str] = None
    cost: Optional[float] = None
    billing_cycle: Optional[str] = None
    next_payment_date: Optional[str] = None
    active: Optional[bool] = None


class ItemOut(BaseModel):
    id: int
    service_name: str = ""
    cost: float = 0.0
    billing_cycle: Optional[str] = ""
    next_payment_date: Optional[str] = ""
    active: bool = False

    class Config:
        from_attributes = True


# ── Health ────────────────────────────────────────────────────────────────────

@app.get("/health", tags=["health"])
def health():
    return {"status": "ok", "app": "Study_Planner"}


# ── CRUD ──────────────────────────────────────────────────────────────────────

@app.post("/items", response_model=ItemOut, tags=["items"])
def create_item(item: ItemCreate, request: Request, db: Session = Depends(get_db)):
    _require_user(request)
    try:
        obj = Study_planner(
        service_name=_safe_str(item.service_name),
        cost=_safe_float(item.cost),
        billing_cycle=_safe_str(item.billing_cycle),
        next_payment_date=_safe_date(item.next_payment_date),
        active=_safe_bool(item.active),
        )
        db.add(obj)
        db.commit()
        db.refresh(obj)
        return obj
    except Exception as exc:
        db.rollback()
        logger.error("create_item error: %s", exc)
        raise HTTPException(status_code=422, detail=str(exc))


@app.get("/items", response_model=List[ItemOut], tags=["items"])
def read_items(request: Request, skip: int = 0, limit: int = 500, db: Session = Depends(get_db)):
    _require_user(request)
    try:
        return (
            db.query(Study_planner)
            .order_by(Study_planner.id.desc())
            .offset(skip).limit(limit).all()
        )
    except Exception as exc:
        logger.error("read_items error: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/items/{item_id}", response_model=ItemOut, tags=["items"])
def read_item(item_id: int, request: Request, db: Session = Depends(get_db)):
    _require_user(request)
    obj = db.query(Study_planner).filter(Study_planner.id == item_id).first()
    if not obj:
        raise HTTPException(status_code=404, detail="Item not found")
    return obj


@app.put("/items/{item_id}", response_model=ItemOut, tags=["items"])
def update_item(item_id: int, item: ItemUpdate, request: Request, db: Session = Depends(get_db)):
    _require_user(request)
    obj = db.query(Study_planner).filter(Study_planner.id == item_id).first()
    if not obj:
        raise HTTPException(status_code=404, detail="Item not found")
    try:
        if item.service_name is not None:
            obj.service_name = _safe_str(item.service_name)
        if item.cost is not None:
            obj.cost = _safe_float(item.cost)
        if item.billing_cycle is not None:
            obj.billing_cycle = _safe_str(item.billing_cycle)
        if item.next_payment_date is not None:
            obj.next_payment_date = _safe_date(item.next_payment_date)
        if item.active is not None:
            obj.active = _safe_bool(item.active)
        db.commit()
        db.refresh(obj)
        return obj
    except Exception as exc:
        db.rollback()
        logger.error("update_item error: %s", exc)
        raise HTTPException(status_code=422, detail=str(exc))


@app.delete("/items/{item_id}", tags=["items"])
def delete_item(item_id: int, request: Request, db: Session = Depends(get_db)):
    _require_user(request)
    obj = db.query(Study_planner).filter(Study_planner.id == item_id).first()
    if not obj:
        raise HTTPException(status_code=404, detail="Item not found")
    db.delete(obj)
    db.commit()
    return {"detail": "Deleted"}


# ── Export ────────────────────────────────────────────────────────────────────

@app.get("/export/json", tags=["export"])
def export_json(request: Request, db: Session = Depends(get_db)):
    _require_user(request)
    try:
        items = db.query(Study_planner).order_by(Study_planner.id.desc()).all()
        data = [{c.name: getattr(obj, c.name) for c in obj.__table__.columns} for obj in items]
        content = json.dumps(data, indent=2, default=str)
        return StreamingResponse(
            io.BytesIO(content.encode()),
            media_type="application/json",
            headers={"Content-Disposition": "attachment; filename=export.json"},
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/export/csv", tags=["export"])
def export_csv(request: Request, db: Session = Depends(get_db)):
    _require_user(request)
    try:
        items = db.query(Study_planner).order_by(Study_planner.id.desc()).all()
        if not items:
            return StreamingResponse(
                io.BytesIO(b""),
                media_type="text/csv",
                headers={"Content-Disposition": "attachment; filename=export.csv"},
            )
        cols = [c.name for c in items[0].__table__.columns]
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=cols)
        writer.writeheader()
        for obj in items:
            writer.writerow({c: getattr(obj, c) for c in cols})
        return StreamingResponse(
            io.BytesIO(buf.getvalue().encode()),
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=export.csv"},
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
