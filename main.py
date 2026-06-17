"""
IdeaForge — Platform Server
"""
import asyncio
import hashlib
import json
import os
from datetime import datetime
from pathlib import Path

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from sqlalchemy.orm import Session
from starlette.middleware.sessions import SessionMiddleware

from platform_db import AppRecord, User, get_platform_db, get_platform_db_sync
from app_generator import GENERATED_DIR, detect_app_type, generate_app, normalize_prompt, sanitize
from app_runner import delete_app, get_app_info, get_app_logs, get_app_status, rename_app, run_app, stop_app

# ── Admin credentials (hardcoded) ─────────────────────────────────────────────
ADMIN_EMAIL = "bedekarmadhura19@gmail.com"
ADMIN_PASSWORD = "Madhura@123"

app = FastAPI(title="IdeaForge", version="4.0.0")

# NOTE: Starlette applies middlewares in REVERSE order of add_middleware() calls.
# The LAST one added becomes the OUTERMOST wrapper (first to see the request).
# SessionMiddleware MUST be outermost so the session cookie is read before any
# route logic runs — therefore it must be added LAST.

# Add CORS first (innermost — runs after session is already available)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:8000", "http://localhost:8000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Add Session last (outermost — first to process every request)
app.add_middleware(
    SessionMiddleware,
    secret_key=os.environ.get("IDEAFORGE_SECRET", "ideaforge-secret-change-in-prod-32ch!"),
    session_cookie="ideaforge_session",   
    max_age=86400,
    https_only=False,
    same_site="lax",
)

_static_dir = Path("static")
if _static_dir.exists():
    app.mount("/static", StaticFiles(directory="static"), name="static")

Path("templates").mkdir(exist_ok=True)
templates = Jinja2Templates(directory="templates")
Path(GENERATED_DIR).mkdir(exist_ok=True)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _hash_pw(pw: str) -> str:
    return hashlib.sha256(pw.encode()).hexdigest()


def _current_session(request: Request) -> dict:
    try:
        uid = request.session.get("user_id")
        uname = request.session.get("username")
        if uid and uname:
            return {
                "user_id": int(uid),
                "username": uname,
                "is_admin": bool(request.session.get("is_admin", False)),
            }
    except Exception:
        pass
    return {}


def _require_auth(request: Request) -> dict:
    sess = _current_session(request)
    if not sess:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return sess


def _require_admin(request: Request) -> dict:
    sess = _require_auth(request)
    if not sess.get("is_admin"):
        raise HTTPException(status_code=403, detail="Admin access required")
    return sess


def get_current_user(request: Request, db: Session):
    """
    Return the User ORM object for the logged-in user, or None if not logged in.
    Use this anywhere you need the full User row (e.g. to check is_admin or
    read user-level fields directly).  For simple auth checks use _require_auth().
    """
    user_id = request.session.get("user_id")
    if not user_id:
        return None
    return db.query(User).filter(User.id == int(user_id)).first()


def _list_apps_for_user(user_id: int, db: Session) -> list:
    records = (
        db.query(AppRecord)
        .filter(AppRecord.user_id == user_id)
        .order_by(AppRecord.created_at.desc())
        .all()
    )
    result = []
    for rec in records:
        info = get_app_info(rec.path) if rec.path else {}
        result.append({
            "name": rec.name,
            "display": rec.name.replace("_", " ").title(),
            "app_type": rec.app_type or "Custom CRUD App",
            "port": rec.port or info.get("port", "—"),
            "run_count": rec.run_count or 0,
            "created_at": rec.created_at.strftime("%Y-%m-%d %H:%M") if rec.created_at else "—",
            "running": info.get("running", False),
            "url": info.get("url"),
        })
    return result


def _get_app_record(app_name: str, user_id: int, db: Session) -> AppRecord:
    # Filter by BOTH name AND user_id so name collisions across users never
    # return the wrong record (which would cause a 403 for the legitimate owner).
    record = (
        db.query(AppRecord)
        .filter(AppRecord.name == app_name, AppRecord.user_id == user_id)
        .first()
    )
    if not record:
        raise HTTPException(status_code=404, detail="App not found")
    return record


# ── Pydantic models ───────────────────────────────────────────────────────────

class GenerateRequest(BaseModel):
    app_name: str
    app_idea: str


class RenameRequest(BaseModel):
    new_name: str


# ── Auth routes ───────────────────────────────────────────────────────────────

@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    if _current_session(request):
        sess = _current_session(request)
        return RedirectResponse("/admin" if sess.get("is_admin") else "/dashboard", status_code=303)
    return templates.TemplateResponse(
    request=request,
    name="login.html"
)

@app.post("/auth/login")
async def do_login(
    request: Request,
    login_type: str = Form(...),
    username: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_platform_db),
):
    if login_type == "admin":
        if username.strip() == ADMIN_EMAIL and password == ADMIN_PASSWORD:
            # Find or create admin user in DB
            admin_user = db.query(User).filter(User.username == ADMIN_EMAIL).first()
            if not admin_user:
                admin_user = User(
                    username=ADMIN_EMAIL,
                    password=_hash_pw(ADMIN_PASSWORD),
                    is_admin=True,
                    created_at=datetime.utcnow(),
                )
                db.add(admin_user)
                db.commit()
                db.refresh(admin_user)
            admin_user.is_admin = True
            admin_user.last_login = datetime.utcnow()
            db.commit()
            request.session["user_id"] = admin_user.id
            request.session["username"] = ADMIN_EMAIL
            request.session["is_admin"] = True
            return JSONResponse({"status": "ok", "redirect": "/admin"})
        else:
            raise HTTPException(status_code=401, detail="Invalid admin credentials")
    else:
        user = db.query(User).filter(User.username == username.strip()).first()
        if not user or user.password != _hash_pw(password):
            raise HTTPException(status_code=401, detail="Invalid username or password")
        user.last_login = datetime.utcnow()
        db.commit()
        request.session["user_id"] = user.id
        request.session["username"] = user.username
        request.session["is_admin"] = bool(user.is_admin)
        return JSONResponse({"status": "ok", "redirect": "/dashboard"})


@app.post("/auth/register")
async def do_register(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_platform_db),
):
    username = username.strip()
    if username == ADMIN_EMAIL:
        raise HTTPException(status_code=403, detail="Cannot register with this username")
    if len(username) < 2:
        raise HTTPException(status_code=422, detail="Username must be at least 2 characters")
    if len(password) < 4:
        raise HTTPException(status_code=422, detail="Password must be at least 4 characters")
    if db.query(User).filter(User.username == username).first():
        raise HTTPException(status_code=409, detail="Username already taken")
    new_user = User(username=username, password=_hash_pw(password), is_admin=False, created_at=datetime.utcnow())
    db.add(new_user)
    db.commit()
    db.refresh(new_user)
    request.session["user_id"] = new_user.id
    request.session["username"] = new_user.username
    request.session["is_admin"] = False
    return JSONResponse({"status": "ok", "redirect": "/dashboard"})


@app.post("/auth/logout")
async def do_logout(request: Request):
    request.session.clear()
    return JSONResponse({"status": "ok"})


# ── Page routes ───────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    sess = _current_session(request)
    if not sess:
        return RedirectResponse("/login", status_code=303)
    return RedirectResponse("/admin" if sess.get("is_admin") else "/dashboard", status_code=303)


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request):
    sess = _current_session(request)
    if not sess:
        return RedirectResponse("/login", status_code=303)
    if sess.get("is_admin"):
        return RedirectResponse("/admin", status_code=303)
    return templates.TemplateResponse(
    request=request,
    name="dashboard.html",
    context={"username": sess["username"]}
)


@app.get("/generate", response_class=HTMLResponse)
async def generate_page(request: Request):
    return await dashboard(request)


@app.get("/apps", response_class=HTMLResponse)
async def apps_page(request: Request):
    return await dashboard(request)


# ── Admin routes ──────────────────────────────────────────────────────────────

@app.get("/admin", response_class=HTMLResponse)
async def admin_page(request: Request):
    sess = _current_session(request)
    if not sess:
        return RedirectResponse("/login", status_code=303)
    if not sess.get("is_admin"):
        return HTMLResponse("<h2 style='color:#f87171;padding:2rem;font-family:sans-serif'>403 — Admin only</h2>", status_code=403)
    return templates.TemplateResponse(
    request=request,
    name="admin.html",
    context={"username": sess["username"]}
)


@app.get("/admin/stats")
async def admin_stats(request: Request, db: Session = Depends(get_platform_db)):
    _require_admin(request)
    users = db.query(User).filter(User.username != ADMIN_EMAIL).all()
    apps = db.query(AppRecord).all()
    top_users = sorted(
        [{"id": u.id, "username": u.username, "apps": len(u.apps), "is_admin": u.is_admin,
          "last_login": u.last_login.strftime("%Y-%m-%d %H:%M") if u.last_login else None,
          "created_at": u.created_at.strftime("%Y-%m-%d %H:%M") if u.created_at else None}
         for u in users],
        key=lambda x: x["apps"], reverse=True,
    )
    top_apps = sorted(
        [{"id": a.id, "name": a.name, "owner": a.owner.username if a.owner else "—",
          "app_type": a.app_type, "run_count": a.run_count,
          "created_at": a.created_at.strftime("%Y-%m-%d %H:%M") if a.created_at else None}
         for a in apps],
        key=lambda x: x["run_count"], reverse=True,
    )
    return {
        "total_users": len(users),
        "total_apps": len(apps),
        "total_runs": sum(a.run_count for a in apps),
        "top_users": top_users,
        "all_users": top_users,
        "top_apps": top_apps,
    }


@app.delete("/admin/app/{app_id}")
async def admin_delete_app(app_id: int, request: Request, db: Session = Depends(get_platform_db)):
    _require_admin(request)
    record = db.query(AppRecord).filter(AppRecord.id == app_id).first()
    if not record:
        raise HTTPException(status_code=404, detail="App not found")
    delete_app(record.name, app_path_override=record.path)
    db.delete(record)
    db.commit()
    return {"status": "deleted"}


# ── User stats ────────────────────────────────────────────────────────────────

@app.get("/api/user/stats")
async def user_stats(request: Request, db: Session = Depends(get_platform_db)):
    sess = _require_auth(request)
    user = db.query(User).filter(User.id == sess["user_id"]).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return {
        "total_apps": len(user.apps),
        "total_runs": sum(a.run_count for a in user.apps),
        "last_login": user.last_login.strftime("%Y-%m-%d %H:%M") if user.last_login else None,
    }


# ── App listing ───────────────────────────────────────────────────────────────

@app.get("/api/apps")
async def get_apps(request: Request, db: Session = Depends(get_platform_db)):
    sess = _require_auth(request)
    return _list_apps_for_user(sess["user_id"], db)


@app.get("/api/apps/{app_name}")
async def get_one_app(app_name: str, request: Request, db: Session = Depends(get_platform_db)):
    sess = _require_auth(request)
    record = _get_app_record(app_name, sess["user_id"], db)
    return get_app_info(record.path)


# ── Run / Stop / Delete / Rename ──────────────────────────────────────────────

@app.post("/api/run/{app_name}")
async def run(app_name: str, request: Request, db: Session = Depends(get_platform_db)):
    sess = _require_auth(request)
    record = _get_app_record(app_name, sess["user_id"], db)
    result = run_app(app_name, app_path_override=record.path)
    if result.get("status") not in ("running", "already_running"):
        raise HTTPException(status_code=500, detail=result.get("message", "Failed to start"))
    record.run_count = (record.run_count or 0) + 1
    record.last_run = datetime.utcnow()
    if result.get("port"):
        record.port = result["port"]
    db.commit()
    return result


@app.post("/api/stop/{app_name}")
async def stop(app_name: str, request: Request, db: Session = Depends(get_platform_db)):
    sess = _require_auth(request)
    _get_app_record(app_name, sess["user_id"], db)
    return stop_app(app_name)


@app.delete("/api/delete/{app_name}")
async def delete(app_name: str, request: Request, db: Session = Depends(get_platform_db)):
    sess = _require_auth(request)
    record = _get_app_record(app_name, sess["user_id"], db)
    result = delete_app(app_name, app_path_override=record.path)
    if result.get("status") == "deleted":
        db.delete(record)
        db.commit()
    return result


@app.post("/api/rename/{app_name}")
async def rename(app_name: str, body: RenameRequest, request: Request, db: Session = Depends(get_platform_db)):
    sess = _require_auth(request)
    record = _get_app_record(app_name, sess["user_id"], db)
    result = rename_app(app_name, body.new_name, app_path_override=record.path)
    if result.get("status") == "renamed":
        record.name = result["new_name"]
        record.path = result["new_path"]
        db.commit()
    return result


@app.get("/api/status/{app_name}")
async def status(app_name: str, request: Request):
    _require_auth(request)
    return get_app_status(app_name)


@app.get("/api/logs/{app_name}")
async def logs(app_name: str, request: Request):
    _require_auth(request)
    return get_app_logs(app_name)


# ── Generate (form POST) ──────────────────────────────────────────────────────

@app.post("/generate")
async def generate_form(request: Request, db: Session = Depends(get_platform_db)):
    sess = _require_auth(request)
    user_id = sess["user_id"]
    form = await request.form()
    app_name = (form.get("app_name") or "").strip()
    app_idea = (form.get("app_idea") or "").strip()
    if not app_name:
        raise HTTPException(status_code=422, detail="app_name is required")
    if not app_idea:
        raise HTTPException(status_code=422, detail="app_idea is required")
    safe_name = sanitize(app_name)
    if not safe_name:
        raise HTTPException(status_code=422, detail="Invalid app name")

    async def log_fn(message: str, level: str = "info"):
        print(f"[{level.upper()}] {message}")

    try:
        app_path = await generate_app(safe_name, app_idea, log_fn, user_id=user_id)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Generation failed: {exc}")

    app_type = detect_app_type(normalize_prompt(app_idea))
    record = db.query(AppRecord).filter(AppRecord.name == safe_name, AppRecord.user_id == user_id).first()
    if not record:
        record = AppRecord(name=safe_name, path=str(app_path), user_id=user_id, app_type=app_type, created_at=datetime.utcnow())
        db.add(record)
    else:
        record.path = str(app_path)
        record.app_type = app_type
    db.commit()
    return RedirectResponse("/dashboard", status_code=303)


# ── Generate (streaming SSE) ──────────────────────────────────────────────────

@app.post("/api/generate/stream")
async def generate_stream(req: GenerateRequest, request: Request, db: Session = Depends(get_platform_db)):
    sess = _require_auth(request)
    user_id = sess["user_id"]
    safe_name = sanitize(req.app_name)
    if not safe_name:
        raise HTTPException(status_code=422, detail="Invalid app name")

    queue: asyncio.Queue = asyncio.Queue()

    async def log_fn(message: str, level: str = "info"):
        await queue.put({"message": message, "level": level})

    async def run_and_close():
        fresh_db = get_platform_db_sync()
        try:
            app_path = await generate_app(req.app_name, req.app_idea, log_fn, user_id=user_id)
            app_type = detect_app_type(normalize_prompt(req.app_idea))
            record = fresh_db.query(AppRecord).filter(AppRecord.name == safe_name, AppRecord.user_id == user_id).first()
            if not record:
                record = AppRecord(name=safe_name, path=str(app_path), user_id=user_id, app_type=app_type, created_at=datetime.utcnow())
                fresh_db.add(record)
            else:
                record.path = str(app_path)
                record.app_type = app_type
            fresh_db.commit()
            await queue.put({"message": f"✅ Registered '{safe_name}' in database", "level": "success"})
        except Exception as exc:
            await queue.put({"message": f"ERROR: {exc}", "level": "error"})
        finally:
            fresh_db.close()
            await queue.put(None)

    asyncio.create_task(run_and_close())

    async def event_stream():
        while True:
            msg = await queue.get()
            if msg is None:
                yield "data: [DONE]\n\n"
                break
            yield f"data: {json.dumps(msg)}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)