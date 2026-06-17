"""
IdeaForge — App Runner
"""
import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

GENERATED_DIR = "generated_apps"

running_apps: dict = {}
app_logs: dict = {}

PORT_WAIT_TIMEOUT = 25
PORT_POLL_INTERVAL = 0.4


def check_port(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=1):
            return True
    except (OSError, ConnectionRefusedError):
        return False


def _wait_for_port(host: str, port: int, timeout: float = PORT_WAIT_TIMEOUT) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if check_port(host, port):
            return True
        time.sleep(PORT_POLL_INTERVAL)
    return False


def _load_ports(app_path: Path) -> dict:
    ports_file = app_path / "ports.json"
    if ports_file.exists():
        try:
            data = json.loads(ports_file.read_text(encoding="utf-8"))
            return {"backend": int(data.get("backend", 8100)), "app_type": data.get("app_type", "Custom CRUD App")}
        except Exception:
            pass
    return {"backend": 8100, "app_type": "Custom CRUD App"}


def _stream_output(proc: subprocess.Popen, key: str, label: str):
    def _reader(stream):
        if stream is None:
            return
        try:
            for raw in iter(stream.readline, b""):
                line = raw.decode("utf-8", errors="replace").rstrip()
                if line:
                    _append_log(key, f"[{label}] {line}")
        except Exception:
            pass
    threading.Thread(target=_reader, args=(proc.stdout,), daemon=True).start()
    threading.Thread(target=_reader, args=(proc.stderr,), daemon=True).start()


def _append_log(key: str, message: str):
    app_logs.setdefault(key, []).append(message)


def _install_deps(backend_path: Path, key: str) -> bool:
    req_file = backend_path / "requirements.txt"
    if not req_file.exists():
        _append_log(key, "WARNING: requirements.txt not found")
        return True
    _append_log(key, "Installing dependencies…")
    try:
        proc = subprocess.Popen(
            [sys.executable, "-m", "pip", "install", "-r", "requirements.txt", "--quiet", "--disable-pip-version-check"],
            cwd=str(backend_path),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        for raw in iter(proc.stdout.readline, b""):
            line = raw.decode("utf-8", errors="replace").rstrip()
            if line:
                _append_log(key, f"[pip] {line}")
        proc.wait(timeout=120)
        if proc.returncode != 0:
            _append_log(key, f"ERROR: pip install failed (exit {proc.returncode})")
            return False
        _append_log(key, "Dependencies OK ✓")
        return True
    except subprocess.TimeoutExpired:
        proc.kill()
        _append_log(key, "ERROR: pip install timed out")
        return False
    except Exception as exc:
        _append_log(key, f"ERROR: pip install — {exc}")
        return False


def get_app_info(app_path_str: str) -> dict:
    app_path = Path(app_path_str)
    if not app_path.exists():
        return {}
    ports = _load_ports(app_path)
    name = app_path.name
    try:
        ts = app_path.stat().st_mtime
        created_at = datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")
    except Exception:
        created_at = "Unknown"
    status = get_app_status(name)
    entry = running_apps.get(name, {})
    return {
        "name": name,
        "display": name.replace("_", " ").title(),
        "app_type": ports.get("app_type", "Custom CRUD App"),
        "port": ports["backend"],
        "created_at": created_at,
        "running": status.get("running", False),
        "url": f"http://127.0.0.1:{entry.get('_backend_port', ports['backend'])}" if status.get("running") else None,
        "run_count": 0,
    }


def run_app(app_name: str, app_path_override: str | None = None) -> dict:
    app_path = Path(app_path_override) if app_path_override else Path(GENERATED_DIR) / app_name

    if not app_path.exists():
        return {"status": "error", "message": f"App folder not found: {app_path}"}

    key = app_name
    if key in running_apps:
        return {"status": "already_running", "message": "App is already running",
                "url": f"http://127.0.0.1:{running_apps[key]['_backend_port']}"}

    backend_path = app_path / "backend"
    if not backend_path.exists():
        return {"status": "error", "message": "backend/ missing — regenerate the app."}

    app_logs[key] = []
    ports = _load_ports(app_path)
    backend_port = ports["backend"]
    _append_log(key, f"Using port {backend_port}")

    if not _install_deps(backend_path, key):
        return {"status": "error", "message": "Dependency installation failed. Check logs."}

    if check_port("127.0.0.1", backend_port):
        _append_log(key, f"WARNING: Port {backend_port} already in use.")

    _append_log(key, f"Starting uvicorn on port {backend_port}…")
    try:
        proc = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "main:app", "--port", str(backend_port), "--host", "127.0.0.1"],
            cwd=str(backend_path),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except FileNotFoundError as exc:
        return {"status": "error", "message": f"uvicorn not found: {exc}"}

    _stream_output(proc, key, "backend")

    time.sleep(1.5)
    if proc.poll() is not None:
        try:
            _, err = proc.communicate(timeout=3)
            err_text = err.decode("utf-8", errors="replace").strip()
        except Exception:
            err_text = "(stderr unavailable)"
        _append_log(key, f"ERROR: Backend exited immediately.\n{err_text}")
        return {"status": "error", "message": f"Backend crashed on startup. stderr: {err_text[:400]}"}

    _append_log(key, f"Waiting for port {backend_port}…")
    if not _wait_for_port("127.0.0.1", backend_port):
        proc.terminate()
        return {"status": "error", "message": f"Backend did not open port {backend_port} in time."}

    _append_log(key, f"Backend up at http://127.0.0.1:{backend_port} ✓")
    running_apps[key] = {"backend": proc, "_backend_port": backend_port, "docker_id": None, "app_path": str(app_path)}

    try:
        import webbrowser
        time.sleep(0.3)
        webbrowser.open(f"http://127.0.0.1:{backend_port}")
    except Exception:
        pass

    return {"status": "running", "message": f"App running at http://127.0.0.1:{backend_port}",
            "url": f"http://127.0.0.1:{backend_port}", "port": backend_port}


def stop_app(app_name: str) -> dict:
    entry = running_apps.pop(app_name, None)
    if not entry:
        return {"status": "not_running", "message": "App is not running"}
    proc = entry.get("backend")
    if proc is not None:
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        except Exception:
            pass
    return {"status": "stopped", "message": f"{app_name} stopped"}


def delete_app(app_name: str, app_path_override: str | None = None) -> dict:
    if app_name in running_apps:
        stop_app(app_name)
    app_path = Path(app_path_override) if app_path_override else Path(GENERATED_DIR) / app_name
    if not app_path.exists():
        return {"status": "error", "message": f"App not found: {app_path}"}
    try:
        shutil.rmtree(app_path)
        app_logs.pop(app_name, None)
        return {"status": "deleted", "message": f"{app_name} deleted"}
    except Exception as exc:
        return {"status": "error", "message": f"Could not delete: {exc}"}


def rename_app(app_name: str, new_name: str, app_path_override: str | None = None) -> dict:
    from app_generator import sanitize
    safe_new = sanitize(new_name)
    if not safe_new:
        return {"status": "error", "message": "Invalid new name"}
    if app_name in running_apps:
        stop_app(app_name)
    src = Path(app_path_override) if app_path_override else Path(GENERATED_DIR) / app_name
    dst = src.parent / safe_new
    if not src.exists():
        return {"status": "error", "message": f"App not found: {src}"}
    if dst.exists():
        return {"status": "error", "message": f"Name '{safe_new}' already exists"}
    try:
        src.rename(dst)
        app_logs.pop(app_name, None)
        return {"status": "renamed", "message": f"Renamed to {safe_new}", "new_name": safe_new, "new_path": str(dst)}
    except Exception as exc:
        return {"status": "error", "message": str(exc)}


def get_app_status(app_name: str) -> dict:
    if app_name not in running_apps:
        return {"running": False, "backend": False, "frontend": False}
    entry = running_apps[app_name]
    backend_port = entry.get("_backend_port", 8100)
    proc = entry.get("backend")
    process_alive = proc is not None and proc.poll() is None
    port_open = check_port("127.0.0.1", backend_port)
    alive = process_alive and port_open
    if not alive:
        running_apps.pop(app_name, None)
    return {"running": alive, "backend": alive, "frontend": alive}


def get_app_logs(app_name: str) -> list:
    return app_logs.get(app_name, [])