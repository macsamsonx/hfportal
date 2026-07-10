import os
import uuid
import random
import asyncio
import json
import csv
import io
import secrets
import urllib.request
import urllib.parse
from collections import defaultdict
from time import time as _time
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import Optional

# ── reCAPTCHA v3 ─────────────────────────────────────────────────────────────
RECAPTCHA_SITE_KEY   = os.environ.get("RECAPTCHA_SITE_KEY", "")
RECAPTCHA_SECRET_KEY = os.environ.get("RECAPTCHA_SECRET_KEY", "")
RECAPTCHA_THRESHOLD  = 0.5   # scores below this are treated as bots

def _verify_recaptcha(token: str, action: str = "") -> bool:
    if not RECAPTCHA_SECRET_KEY or not token:
        return True   # skip verification when keys are not configured
    try:
        data = urllib.parse.urlencode({
            "secret":   RECAPTCHA_SECRET_KEY,
            "response": token,
        }).encode()
        req  = urllib.request.Request("https://www.google.com/recaptcha/api/siteverify", data=data)
        resp = json.loads(urllib.request.urlopen(req, timeout=5).read())
        if not resp.get("success"):
            return False
        if action and resp.get("action") != action:
            return False
        return resp.get("score", 0) >= RECAPTCHA_THRESHOLD
    except Exception:
        return True   # fail open on network errors — don't lock out real users

# ── Login rate limiter (in-memory, per IP) ────────────────────────────────────
_login_fail_times: dict[str, list[float]] = defaultdict(list)
_RATE_LIMIT_MAX    = 5    # max failures
_RATE_LIMIT_WINDOW = 300  # seconds (5 minutes)

def _check_login_rate(ip: str) -> bool:
    now = _time()
    attempts = [t for t in _login_fail_times[ip] if now - t < _RATE_LIMIT_WINDOW]
    _login_fail_times[ip] = attempts
    return len(attempts) < _RATE_LIMIT_MAX

def _record_login_fail(ip: str) -> None:
    _login_fail_times[ip].append(_time())

from fastapi import FastAPI, Request, Form, UploadFile, File, Depends
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from database import (
    get_db, init_db, hash_password, verify_password,
    get_today_date, get_week_range, calculate_hours, get_pht_now,
    compute_payroll_for_employee, get_compliance_status, get_break_minutes,
    get_clients, get_unread_count, push_notification, audit, log_card_activity,
    dm_room, get_chat_unread_count,
    KANBAN_STATUSES, BIBLE_VERSES,
    UPLOAD_DIR,
)

# ── CSRF helpers ──────────────────────────────────────────────────────────────
def _get_csrf_token(request: Request) -> str:
    if "csrf_token" not in request.session:
        request.session["csrf_token"] = secrets.token_hex(32)
    return request.session["csrf_token"]

# ── App bootstrap ──────────────────────────────────────────────────────────────
app = FastAPI(title="Employee Portal")
_SESSION_SECRET = os.environ.get("SESSION_SECRET", "dev-only-insecure-secret")
# ── CSRF middleware ────────────────────────────────────────────────────────────
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response as _StarletteResponse

class _CSRFMiddleware(BaseHTTPMiddleware):
    _SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
    _EXEMPT_PREFIXES = ("/static", "/uploads", "/tv", "/login", "/api/register")

    async def dispatch(self, request: Request, call_next):
        if request.method in self._SAFE_METHODS:
            return await call_next(request)
        path = request.url.path
        if any(path.startswith(p) for p in self._EXEMPT_PREFIXES):
            return await call_next(request)
        # JS fetch calls always send this header — same-origin and already protected
        if request.headers.get("X-Requested-With") == "fetch":
            return await call_next(request)
        content_type = request.headers.get("content-type", "")
        if "form" not in content_type:
            return await call_next(request)

        # Read body once; it is cached in request._body for downstream handlers
        body = await request.body()
        session_token = request.session.get("csrf_token", "")
        submitted_token = ""

        if "multipart" in content_type:
            # Robust multipart scan: find ALL occurrences of name="_csrf" and extract value
            raw = body.decode("latin-1")
            search = 'name="_csrf"'
            pos = 0
            while pos < len(raw):
                idx = raw.find(search, pos)
                if idx == -1:
                    break
                chunk = raw[idx:]
                # Skip past the Content-Disposition header to the blank line
                val_start = chunk.find("\r\n\r\n")
                if val_start != -1:
                    value_region = chunk[val_start + 4:]
                    val_end = value_region.find("\r\n")
                    candidate = (value_region[:val_end] if val_end != -1 else value_region[:68]).strip()
                    # A valid CSRF token is 64 hex chars
                    if len(candidate) == 64 and all(c in "0123456789abcdef" for c in candidate):
                        submitted_token = candidate
                        break
                pos = idx + len(search)
        else:
            from urllib.parse import parse_qs
            parsed = parse_qs(body.decode("utf-8", errors="replace"), keep_blank_values=True)
            submitted_token = parsed.get("_csrf", [""])[0]

        if not session_token or not submitted_token or not secrets.compare_digest(session_token, submitted_token):
            return _StarletteResponse(
                status_code=303,
                headers={"location": "/login?error=session_expired"},
            )
        return await call_next(request)

class _SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"]  = "nosniff"
        response.headers["X-Frame-Options"]          = "DENY"
        response.headers["X-XSS-Protection"]         = "1; mode=block"
        response.headers["Referrer-Policy"]          = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"]       = "geolocation=(), microphone=(), camera=()"
        response.headers["Content-Security-Policy"]  = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline' https://www.google.com https://www.gstatic.com; "
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
            "font-src 'self' https://fonts.gstatic.com; "
            "img-src 'self' data:; "
            "frame-src https://www.google.com; "
            "connect-src 'self' https://www.google.com;"
        )
        return response

app.add_middleware(_SecurityHeadersMiddleware)
app.add_middleware(_CSRFMiddleware)
# SessionMiddleware must be added AFTER _CSRFMiddleware so it runs first (Starlette LIFO order)
app.add_middleware(SessionMiddleware, secret_key=_SESSION_SECRET, https_only=False, same_site="lax")
app.mount("/static", StaticFiles(directory="static"), name="static")
# Avatars and TV posters are non-sensitive — stay public for TV display and sidebar
app.mount("/uploads/avatars", StaticFiles(directory=os.path.join("secure_vault", "avatars")), name="avatars")
app.mount("/uploads/posters", StaticFiles(directory=os.path.join("secure_vault", "posters")), name="posters")
templates = Jinja2Templates(directory="templates")
init_db()

# ── Sensitive document serving (auth-gated) ────────────────────────────────────
from fastapi.responses import FileResponse as _FileResponse

@app.get("/uploads/docs/{path:path}")
async def serve_doc(request: Request, path: str):
    uid = request.session.get("user_id")
    if not uid:
        return RedirectResponse("/login", status_code=302)
    safe_path = os.path.normpath(os.path.join(UPLOAD_DIR, "docs", path))
    docs_root  = os.path.normpath(os.path.join(UPLOAD_DIR, "docs"))
    if not safe_path.startswith(docs_root):          # path-traversal guard
        return JSONResponse({"error": "Forbidden"}, status_code=403)
    if not os.path.isfile(safe_path):
        return JSONResponse({"error": "Not found"}, status_code=404)
    return _FileResponse(safe_path)


# ── Auth helpers ───────────────────────────────────────────────────────────────
def current_user(request: Request) -> Optional[dict]:
    uid = request.session.get("user_id")
    if not uid:
        return None
    with get_db() as conn:
        row = conn.execute("SELECT * FROM employees WHERE id=? AND is_active=1", (uid,)).fetchone()
    return dict(row) if row else None


def require_user(request: Request) -> dict:
    user = current_user(request)
    if not user:
        raise _Redirect("/login")
    return user


def require_role(request: Request, *roles: str) -> dict:
    user = require_user(request)
    if user["role"] not in roles:
        raise _Redirect("/dashboard")
    return user


class _Redirect(Exception):
    def __init__(self, url: str):
        self.url = url


@app.exception_handler(_Redirect)
async def redirect_handler(request: Request, exc: _Redirect):
    return RedirectResponse(exc.url, status_code=302)


def flash(request: Request, msg: str, kind: str = "success"):
    request.session["flash"] = {"msg": msg, "kind": kind}


def get_flash(request: Request) -> Optional[dict]:
    return request.session.pop("flash", None)


def shared_ctx(user: dict, request: Request = None) -> dict:
    """Sidebar badge counts + CSRF token injected into every template response."""
    uid = user["id"]
    role = user["role"]
    ctx: dict = {"notif_count": get_unread_count(uid), "chat_unread_count": get_chat_unread_count(uid)}
    if request is not None:
        ctx["csrf_token"] = _get_csrf_token(request)
    with get_db() as conn:
        if role in ("HR Manager", "Admin"):
            ctx["pending_ot_count"] = conn.execute(
                "SELECT COUNT(*) FROM overtime_requests WHERE status='Pending'"
            ).fetchone()[0]
            ctx["pending_leave_count"] = conn.execute(
                "SELECT COUNT(*) FROM leave_requests WHERE status='Pending'"
            ).fetchone()[0]
            ctx["for_review_count"] = conn.execute(
                "SELECT COUNT(*) FROM work_logs WHERE status='For Review' AND COALESCE(is_archived,0)=0"
            ).fetchone()[0]
            ctx["pending_reg_count"] = conn.execute(
                "SELECT COUNT(*) FROM registration_requests WHERE status='Pending'"
            ).fetchone()[0]
        elif role == "Employee":
            ctx["pending_ot_count"] = conn.execute(
                "SELECT COUNT(*) FROM overtime_requests WHERE emp_id=? AND status='Pending'",
                (uid,)
            ).fetchone()[0]
            ctx["for_review_count"] = conn.execute(
                "SELECT COUNT(*) FROM work_logs WHERE emp_id=? AND status='For Review' AND COALESCE(is_archived,0)=0",
                (uid,)
            ).fetchone()[0]
            # Attendance pill data
            today = get_pht_now().strftime("%Y-%m-%d")
            att = conn.execute(
                "SELECT clock_in, clock_out, is_on_break FROM attendance WHERE emp_id=? AND date_logged=?",
                (uid, today)
            ).fetchone()
            if att and att["clock_in"] and not att["clock_out"]:
                ctx["att_status"]   = "on_break" if att["is_on_break"] else "clocked_in"
                ctx["att_clock_in"] = att["clock_in"][:5]
            elif att and att["clock_in"] and att["clock_out"]:
                ctx["att_status"]    = "clocked_out"
                ctx["att_clock_in"]  = att["clock_in"][:5]
                ctx["att_clock_out"] = att["clock_out"][:5]
            else:
                ctx["att_status"] = "not_clocked_in"
    ctx["vl_enabled"] = bool(user.get("vl_enabled", 1))
    ctx["sl_enabled"] = bool(user.get("sl_enabled", 1))
    ctx["now"] = get_pht_now().isoformat()
    return ctx


# ── Login / Logout ─────────────────────────────────────────────────────────────
@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    if current_user(request):
        return RedirectResponse("/dashboard", status_code=302)
    error = request.query_params.get("error")
    if error == "session_expired" and "flash" not in request.session:
        flash(request, "Your session expired. Please log in again.", "warning")
    return templates.TemplateResponse(request, "login.html", {
        "flash": get_flash(request),
        "csrf_token": _get_csrf_token(request),
        "recaptcha_site_key": RECAPTCHA_SITE_KEY,
    })


@app.post("/login")
async def login_post(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    g_recaptcha_response: str = Form(""),
):
    u = username.strip()
    ip = request.client.host if request.client else "unknown"
    ua = request.headers.get("user-agent", "")

    if not _verify_recaptcha(g_recaptcha_response, action="login"):
        flash(request, "reCAPTCHA verification failed. Please try again.", "error")
        return RedirectResponse("/login", status_code=302)

    if not _check_login_rate(ip):
        flash(request, "Too many failed attempts. Please wait 5 minutes.", "error")
        return RedirectResponse("/login", status_code=302)

    with get_db() as conn:
        emp = conn.execute(
            "SELECT * FROM employees WHERE (LOWER(username)=LOWER(?) OR email=?) AND is_active=1",
            (u, u),
        ).fetchone()
        if emp and verify_password(password, emp["password"]):
            conn.execute(
                "INSERT INTO login_log (emp_id, ip_address, user_agent, success) VALUES (?,?,?,1)",
                (emp["id"], ip, ua[:200]),
            )
            audit(conn, emp["id"], emp["name"], "login", ip=ip)
            request.session["user_id"] = emp["id"]
            dest = "/dashboard" if emp["role"] == "Employee" else "/kanban"
            return RedirectResponse(dest, status_code=302)
        _record_login_fail(ip)
        if emp:
            conn.execute(
                "INSERT INTO login_log (emp_id, ip_address, user_agent, success) VALUES (?,?,?,0)",
                (emp["id"], ip, ua[:200]),
            )
    flash(request, "Invalid username or password.", "error")
    return RedirectResponse("/login", status_code=302)


@app.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=302)


# ── Self-Registration (public) ────────────────────────────────────────────────
@app.get("/register", response_class=HTMLResponse)
async def register_page(request: Request):
    u = current_user(request)
    if u and u.get("role") not in ("HR Manager", "Admin"):
        return RedirectResponse("/dashboard", status_code=302)
    with get_db() as conn:
        skills = conn.execute("SELECT name FROM skills ORDER BY name").fetchall()
    skills_list = [s["name"] for s in skills]
    return templates.TemplateResponse(request, "register.html", {
        "flash": get_flash(request),
        "csrf_token": _get_csrf_token(request),
        "skills_list": skills_list,
        "now": get_pht_now().isoformat(),
        "recaptcha_site_key": RECAPTCHA_SITE_KEY,
    })


@app.post("/api/register")
async def register_submit(
    request: Request,
    first_name: str = Form(...),
    last_name: str = Form(...),
    middle_name: str = Form(""),
    no_middle_name: str = Form(""),
    prefix: str = Form(""),
    nickname: str = Form(""),
    email: str = Form(...),
    phone: str = Form(""),
    gender: str = Form(""),
    birthday: str = Form(""),
    address: str = Form(""),
    skills_json: str = Form("[]"),
    privacy_agreed: str = Form(""),
    terms_agreed: str = Form(""),
    g_recaptcha_response: str = Form(""),
):
    if not _verify_recaptcha(g_recaptcha_response, action="register"):
        flash(request, "reCAPTCHA verification failed. Please try again.", "error")
        return RedirectResponse("/register", status_code=302)

    if not privacy_agreed or not terms_agreed:
        flash(request, "You must agree to all required consents to proceed.", "error")
        return RedirectResponse("/register", status_code=302)

    first_name = first_name.strip()
    last_name = last_name.strip()
    middle_name = middle_name.strip()
    has_no_middle = bool(no_middle_name)

    # Build display name: Prefix First [Middle] Last (Nickname)
    parts = []
    if prefix.strip():
        parts.append(prefix.strip())
    parts.append(first_name)
    if not has_no_middle and middle_name:
        parts.append(middle_name)
    parts.append(last_name)
    full_name = " ".join(parts)
    if nickname.strip():
        full_name += f" ({nickname.strip()})"

    # Parse skills list
    try:
        skills_list = json.loads(skills_json) if skills_json else []
        if not isinstance(skills_list, list):
            skills_list = []
        position_applied = ", ".join(str(s) for s in skills_list)
    except Exception:
        position_applied = ""

    with get_db() as conn:
        dup_pending = conn.execute(
            "SELECT id FROM registration_requests WHERE LOWER(email)=LOWER(?) AND status='Pending'",
            (email.strip(),),
        ).fetchone()
        if dup_pending:
            flash(request, "A registration with this email is already pending review.", "error")
            return RedirectResponse("/register", status_code=302)
        emp_exists = conn.execute(
            "SELECT id FROM employees WHERE LOWER(email)=LOWER(?)", (email.strip(),)
        ).fetchone()
        if emp_exists:
            flash(request, "An account with this email already exists. Please contact HR.", "error")
            return RedirectResponse("/register", status_code=302)
        conn.execute(
            """INSERT INTO registration_requests
               (name, first_name, last_name, middle_name, no_middle_name, prefix, nickname,
                email, phone, gender, birthday, address, position_applied, privacy_agreed, terms_agreed)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 1)""",
            (full_name, first_name, last_name, middle_name,
             1 if has_no_middle else 0, prefix.strip(), nickname.strip(),
             email.strip(), phone.strip(), gender, birthday, address.strip(),
             position_applied),
        )
        hr_ids = conn.execute(
            "SELECT id FROM employees WHERE role IN ('HR Manager', 'Admin') AND is_active=1"
        ).fetchall()
        for hr in hr_ids:
            push_notification(conn, hr["id"], "New Registration",
                              f"{full_name} applied for access", "/hr-registrations")
    return RedirectResponse("/register/success", status_code=302)


@app.get("/register/success", response_class=HTMLResponse)
async def register_success(request: Request):
    return templates.TemplateResponse(request, "register_success.html", {})


# ── Static policy pages (public) ─────────────────────────────────────────────
@app.get("/privacy", response_class=HTMLResponse)
async def privacy_page(request: Request):
    user = current_user(request)
    ctx = {"request": request, "now": get_pht_now().isoformat(), "user": user}
    if user:
        ctx.update(shared_ctx(user))
    tmpl = "public/privacy_auth.html" if user else "public/privacy.html"
    return templates.TemplateResponse(request, tmpl, ctx)


@app.get("/terms", response_class=HTMLResponse)
async def terms_page(request: Request):
    user = current_user(request)
    ctx = {"request": request, "now": get_pht_now().isoformat(), "user": user}
    if user:
        ctx.update(shared_ctx(user))
    tmpl = "public/terms_auth.html" if user else "public/terms.html"
    return templates.TemplateResponse(request, tmpl, ctx)


# ── Admin: Skills management ──────────────────────────────────────────────────
@app.get("/api/skills")
async def list_skills(request: Request):
    require_user(request)
    with get_db() as conn:
        skills = conn.execute("SELECT id, name FROM skills ORDER BY name").fetchall()
    return [{"id": s["id"], "name": s["name"]} for s in skills]


@app.post("/api/skills")
async def add_skill(request: Request, name: str = Form(...)):
    require_role(request, "HR Manager", "Admin")
    name = name.strip()
    if not name:
        from fastapi import HTTPException
        raise HTTPException(400, "Skill name required")
    with get_db() as conn:
        try:
            conn.execute("INSERT INTO skills (name) VALUES (?)", (name,))
            skill_id = conn.execute("SELECT id FROM skills WHERE name=?", (name,)).fetchone()["id"]
        except Exception:
            existing = conn.execute("SELECT id, name FROM skills WHERE LOWER(name)=LOWER(?)", (name,)).fetchone()
            return {"id": existing["id"], "name": existing["name"], "existed": True}
    return {"id": skill_id, "name": name, "existed": False}


@app.post("/api/employees/{emp_id}/skills")
async def set_employee_skills(request: Request, emp_id: int):
    require_role(request, "HR Manager", "Admin")
    data = await request.json()
    skill_names = data.get("skills", [])
    with get_db() as conn:
        conn.execute("DELETE FROM employee_skills WHERE emp_id=?", (emp_id,))
        for name in skill_names:
            name = name.strip()
            if not name:
                continue
            row = conn.execute("SELECT id FROM skills WHERE LOWER(name)=LOWER(?)", (name,)).fetchone()
            if not row:
                conn.execute("INSERT INTO skills (name) VALUES (?)", (name,))
                row = conn.execute("SELECT id FROM skills WHERE LOWER(name)=LOWER(?)", (name,)).fetchone()
            conn.execute("INSERT OR IGNORE INTO employee_skills (emp_id, skill_id) VALUES (?,?)",
                         (emp_id, row["id"]))
    return {"ok": True}


# ── Admin: Clear database ─────────────────────────────────────────────────────
@app.get("/admin/clear-database", response_class=HTMLResponse)
async def clear_db_page(request: Request):
    user = require_role(request, "Admin")
    ctx = {"request": request}
    ctx.update(shared_ctx(user))
    return templates.TemplateResponse(request, "admin/clear_database.html", ctx)


@app.post("/api/admin/clear-database")
async def clear_database(request: Request, confirm_code: str = Form(...)):
    user = require_role(request, "Admin")
    CLEAR_CODE = "142857"
    if confirm_code.strip() != CLEAR_CODE:
        flash(request, "Incorrect confirmation code. Database was NOT cleared.", "error")
        return RedirectResponse("/admin/clear-database", status_code=302)
    admin_id = user["id"]
    with get_db() as conn:
        conn.execute("DELETE FROM employees WHERE id != ?", (admin_id,))
        for tbl in ["work_logs", "attendance", "break_logs", "leave_requests",
                    "overtime_requests", "registration_requests", "payroll_runs",
                    "chat_messages", "notifications", "tasks", "task_comments",
                    "employee_skills"]:
            try:
                conn.execute(f"DELETE FROM {tbl}")
            except Exception:
                pass
    flash(request, "Database cleared. All employee records have been removed.", "success")
    return RedirectResponse("/admin/clear-database", status_code=302)


# ── HR: Registration Queue ────────────────────────────────────────────────────
@app.get("/hr-registrations", response_class=HTMLResponse)
async def hr_registrations(request: Request, status_filter: str = "Pending"):
    user = require_role(request, "HR Manager", "Admin")
    with get_db() as conn:
        if status_filter == "All":
            regs = conn.execute(
                "SELECT * FROM registration_requests ORDER BY created_at DESC"
            ).fetchall()
        else:
            regs = conn.execute(
                "SELECT * FROM registration_requests WHERE status=? ORDER BY created_at DESC",
                (status_filter,),
            ).fetchall()
        counts = {
            "Pending":  conn.execute("SELECT COUNT(*) FROM registration_requests WHERE status='Pending'").fetchone()[0],
            "Approved": conn.execute("SELECT COUNT(*) FROM registration_requests WHERE status='Approved'").fetchone()[0],
            "Rejected": conn.execute("SELECT COUNT(*) FROM registration_requests WHERE status='Rejected'").fetchone()[0],
        }
    return templates.TemplateResponse(request, "shared/hr_registrations.html", {
        "user": user,
        "regs": [dict(r) for r in regs],
        "status_filter": status_filter,
        "counts": counts,
        **shared_ctx(user, request),
    })


@app.post("/api/registrations/{reg_id}/approve")
async def approve_registration(
    request: Request,
    reg_id: int,
    username: str = Form(...),
    password: str = Form(...),
    role: str = Form("Employee"),
    shift_type: str = Form("Morning"),
    hourly_rate: float = Form(0.0),
    employment_type: str = Form("Full-time"),
    department: str = Form(""),
    capabilities: str = Form(""),
):
    user = require_role(request, "HR Manager", "Admin")
    with get_db() as conn:
        reg = conn.execute(
            "SELECT * FROM registration_requests WHERE id=?", (reg_id,)
        ).fetchone()
        if not reg or reg["status"] != "Pending":
            flash(request, "Registration not found or already processed.", "error")
            return RedirectResponse("/hr-registrations", status_code=302)
        dup = conn.execute(
            "SELECT id FROM employees WHERE LOWER(username)=LOWER(?)", (username.strip(),)
        ).fetchone()
        if dup:
            flash(request, f"Username '{username}' is already taken. Choose another.", "error")
            return RedirectResponse("/hr-registrations", status_code=302)
        hashed = hash_password(password)
        cursor = conn.execute(
            """INSERT INTO employees
               (name, email, phone, gender, birthday, address, username, password, role,
                shift_type, hourly_rate, employment_type, department, capabilities, is_active)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)""",
            (reg["name"], reg["email"], reg["phone"] or "", reg["gender"] or "",
             reg["birthday"] or "", reg["address"] or "",
             username.strip(), hashed, role,
             shift_type, hourly_rate, employment_type, department.strip(), capabilities.strip()),
        )
        emp_id = cursor.lastrowid
        conn.execute(
            """UPDATE registration_requests
               SET status='Approved', reviewed_by=?, reviewed_at=datetime('now','+8 hours'), employee_id=?
               WHERE id=?""",
            (user["name"], emp_id, reg_id),
        )
        push_notification(conn, emp_id, "Account Approved",
                          "Your registration has been approved. You can now log in.", "/dashboard")
        audit(conn, user["id"], user["name"], "approve_registration",
              target_table="registration_requests", target_id=reg_id, new_value=reg["email"])
    flash(request, f"Account created for {reg['name']}. They can now log in as '{username}'.", "success")
    return RedirectResponse("/hr-registrations", status_code=302)


@app.post("/api/registrations/{reg_id}/reject")
async def reject_registration(
    request: Request,
    reg_id: int,
    rejection_reason: str = Form(""),
):
    user = require_role(request, "HR Manager", "Admin")
    with get_db() as conn:
        reg = conn.execute(
            "SELECT * FROM registration_requests WHERE id=?", (reg_id,)
        ).fetchone()
        if not reg or reg["status"] != "Pending":
            flash(request, "Registration not found or already processed.", "error")
            return RedirectResponse("/hr-registrations", status_code=302)
        conn.execute(
            """UPDATE registration_requests
               SET status='Rejected', reviewed_by=?, reviewed_at=datetime('now','+8 hours'), rejection_reason=?
               WHERE id=?""",
            (user["name"], rejection_reason.strip(), reg_id),
        )
        audit(conn, user["id"], user["name"], "reject_registration",
              target_table="registration_requests", target_id=reg_id, new_value=reg["email"])
    flash(request, f"Registration from {reg['name']} has been rejected.", "success")
    return RedirectResponse("/hr-registrations", status_code=302)


# ── Root redirect ──────────────────────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    user = current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=302)
    return RedirectResponse("/dashboard", status_code=302)


# ── Employee Dashboard ─────────────────────────────────────────────────────────
@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request):
    user = require_user(request)
    today = get_today_date(user["shift_type"])
    now_pht = get_pht_now()
    week_start, week_end = get_week_range()

    # Month range for monthly summary
    month_start = now_pht.replace(day=1).strftime("%Y-%m-%d")
    month_end = now_pht.strftime("%Y-%m-%d")

    with get_db() as conn:
        attendance = conn.execute(
            "SELECT * FROM attendance WHERE emp_id=? AND date_logged=?",
            (user["id"], today),
        ).fetchone()
        attendance = dict(attendance) if attendance else None

        # Active break for this attendance
        active_break = None
        if attendance:
            active_break = conn.execute(
                "SELECT * FROM attendance_breaks WHERE att_id=? AND break_end IS NULL",
                (attendance["id"],),
            ).fetchone()
            active_break = dict(active_break) if active_break else None

        tasks_month = conn.execute(
            "SELECT * FROM work_logs WHERE emp_id=? AND date_logged BETWEEN ? AND ? ORDER BY is_running DESC, date_logged DESC, timestamp DESC",
            (user["id"], month_start, month_end),
        ).fetchall()
        tasks_month = [dict(t) for t in tasks_month]

        week_records = conn.execute(
            "SELECT * FROM attendance WHERE emp_id=? AND date_logged BETWEEN ? AND ?",
            (user["id"], week_start, week_end),
        ).fetchall()

    # Break minutes for today
    break_minutes = 0
    if attendance:
        break_minutes = get_break_minutes(attendance["id"])

    # Calculate hours in active shift (excluding breaks)
    active_hours = None
    if attendance and attendance["clock_in"] and not attendance["clock_out"]:
        raw_h = calculate_hours(attendance["clock_in"], now_pht.strftime("%H:%M:%S"))
        active_hours = max(0.0, round(raw_h - break_minutes / 60, 2))

    # Weekly hours
    week_hours = sum(
        calculate_hours(r["clock_in"], r["clock_out"] or now_pht.strftime("%H:%M:%S"))
        for r in week_records
    )

    year_start = now_pht.replace(month=1, day=1).strftime("%Y-%m-%d")
    with get_db() as conn:
        announcements = conn.execute(
            "SELECT * FROM announcements WHERE audience IN ('All','Employee') ORDER BY is_pinned DESC, created_at DESC LIMIT 5",
        ).fetchall()
        vl_used = conn.execute(
            "SELECT COALESCE(SUM(days_count),0) FROM leave_requests WHERE emp_id=? AND leave_type='Vacation Leave' AND status='Approved' AND start_date>=?",
            (user["id"], year_start)
        ).fetchone()[0]
        sl_used = conn.execute(
            "SELECT COALESCE(SUM(days_count),0) FROM leave_requests WHERE emp_id=? AND leave_type='Sick Leave' AND status='Approved' AND start_date>=?",
            (user["id"], year_start)
        ).fetchone()[0]
        # Pending OT/leave for widgets
        pending_ot_mine = conn.execute(
            "SELECT COUNT(*) FROM overtime_requests WHERE emp_id=? AND status='Pending'",
            (user["id"],)
        ).fetchone()[0]
        pending_leave_mine = conn.execute(
            "SELECT COUNT(*) FROM leave_requests WHERE emp_id=? AND status='Pending'",
            (user["id"],)
        ).fetchone()[0]
        # Current week timesheet status
        ts_status = conn.execute(
            "SELECT status FROM timesheet_submissions WHERE emp_id=? AND week_start=?",
            (user["id"], week_start)
        ).fetchone()
        timesheet_status = ts_status["status"] if ts_status else None
        # Tasks for review (submitted by this employee)
        for_review_mine = conn.execute(
            "SELECT COUNT(*) FROM work_logs WHERE emp_id=? AND status='For Review' AND COALESCE(is_archived,0)=0",
            (user["id"],)
        ).fetchone()[0]
        # Today's birthdays (MM-DD match)
        today_md = now_pht.strftime("%m-%d")
        birthday_people = conn.execute(
            """SELECT name, profile_pic_path FROM employees
               WHERE is_active=1 AND birthday IS NOT NULL
                 AND SUBSTR(birthday, 6, 5) = ?
               ORDER BY name""",
            (today_md,)
        ).fetchall()
        birthday_people = [dict(b) for b in birthday_people]

    vl_total = int(user.get("vl_days_per_year") or 15)
    sl_total = int(user.get("sl_days_per_year") or 15)
    verse = random.choice(BIBLE_VERSES)
    return templates.TemplateResponse(request, "employee/dashboard.html", {
        "user": user,
        "today": today,
        "attendance": attendance,
        "active_break": active_break,
        "break_minutes": break_minutes,
        "active_hours": active_hours,
        "tasks_month": tasks_month,
        "week_hours": round(week_hours, 2),
        "clients": get_clients(),
        "flash": get_flash(request),
        "now": now_pht,
        "week_start": week_start,
        "week_end": week_end,
        "month_start": month_start,
        "announcements": [dict(a) for a in announcements],
        "vl_enabled": bool(user.get("vl_enabled", 1)),
        "sl_enabled": bool(user.get("sl_enabled", 1)),
        "vl_balance": max(0, vl_total - int(vl_used or 0)),
        "sl_balance": max(0, sl_total - int(sl_used or 0)),
        "vl_total": vl_total,
        "sl_total": sl_total,
        "pending_ot_mine": pending_ot_mine,
        "pending_leave_mine": pending_leave_mine,
        "timesheet_status": timesheet_status,
        "for_review_mine": for_review_mine,
        "birthday_people": birthday_people,
        "verse": verse,
        **shared_ctx(user, request),
    })


@app.post("/api/clock-in")
async def clock_in(request: Request, clock_time: str = Form("")):
    user = require_user(request)
    today = get_today_date(user["shift_type"])
    raw = clock_time.strip()
    now = (raw + ":00")[:8] if raw else get_pht_now().strftime("%H:%M:%S")
    ip = request.client.host if request.client else "unknown"
    with get_db() as conn:
        existing = conn.execute(
            "SELECT id FROM attendance WHERE emp_id=? AND date_logged=?", (user["id"], today)
        ).fetchone()
        if not existing:
            conn.execute(
                "INSERT INTO attendance (emp_id, clock_in, date_logged, ip_address) VALUES (?, ?, ?, ?)",
                (user["id"], now, today, ip),
            )
            audit(conn, user["id"], user["name"], "clock_in", "attendance", ip=ip)
    return RedirectResponse("/dashboard", status_code=302)


@app.post("/api/clock-out")
async def clock_out(request: Request, clock_time: str = Form("")):
    user = require_user(request)
    today = get_today_date(user["shift_type"])
    raw = clock_time.strip()
    now = (raw + ":00")[:8] if raw else get_pht_now().strftime("%H:%M:%S")
    with get_db() as conn:
        # End any open break first
        att = conn.execute(
            "SELECT id FROM attendance WHERE emp_id=? AND date_logged=?", (user["id"], today)
        ).fetchone()
        if att:
            conn.execute(
                "UPDATE attendance_breaks SET break_end=? WHERE att_id=? AND break_end IS NULL",
                (now, att["id"]),
            )
        conn.execute(
            "UPDATE attendance SET clock_out=?, is_on_break=0 WHERE emp_id=? AND date_logged=? AND clock_out IS NULL",
            (now, user["id"], today),
        )
    return RedirectResponse("/dashboard", status_code=302)


@app.post("/api/attendance/edit")
async def edit_attendance(
    request: Request,
    clock_in_time: str = Form(""),
    clock_out_time: str = Form(""),
):
    user = require_user(request)
    today = get_today_date(user["shift_type"])
    with get_db() as conn:
        att = conn.execute(
            "SELECT id FROM attendance WHERE emp_id=? AND date_logged=?", (user["id"], today)
        ).fetchone()
        if att:
            if clock_in_time.strip():
                ci = (clock_in_time.strip() + ":00")[:8]
                conn.execute("UPDATE attendance SET clock_in=? WHERE id=?", (ci, att["id"]))
            if clock_out_time.strip():
                co = (clock_out_time.strip() + ":00")[:8]
                conn.execute("UPDATE attendance SET clock_out=? WHERE id=?", (co, att["id"]))
    flash(request, "Attendance times updated.")
    return RedirectResponse("/dashboard", status_code=302)


# ── Break (Pause) ──────────────────────────────────────────────────────────────
@app.post("/api/break/start")
async def break_start(request: Request):
    user = require_user(request)
    today = get_today_date(user["shift_type"])
    now_str = get_pht_now().strftime("%H:%M:%S")
    with get_db() as conn:
        att = conn.execute(
            "SELECT id, clock_out FROM attendance WHERE emp_id=? AND date_logged=?",
            (user["id"], today),
        ).fetchone()
        if not att or att["clock_out"]:
            flash(request, "You must be clocked in to start a break.", "error")
            return RedirectResponse("/dashboard", status_code=302)
        # Don't allow double break
        open_break = conn.execute(
            "SELECT id FROM attendance_breaks WHERE att_id=? AND break_end IS NULL", (att["id"],)
        ).fetchone()
        if open_break:
            flash(request, "You are already on break.", "warning")
            return RedirectResponse("/dashboard", status_code=302)
        conn.execute(
            "INSERT INTO attendance_breaks (att_id, break_start) VALUES (?, ?)",
            (att["id"], now_str),
        )
        conn.execute("UPDATE attendance SET is_on_break=1 WHERE id=?", (att["id"],))
    flash(request, "Break started — click Resume when you're back.")
    return RedirectResponse("/dashboard", status_code=302)


@app.post("/api/break/end")
async def break_end(request: Request):
    user = require_user(request)
    today = get_today_date(user["shift_type"])
    now_str = get_pht_now().strftime("%H:%M:%S")
    with get_db() as conn:
        att = conn.execute(
            "SELECT id FROM attendance WHERE emp_id=? AND date_logged=?",
            (user["id"], today),
        ).fetchone()
        if not att:
            flash(request, "No attendance record found.", "error")
            return RedirectResponse("/dashboard", status_code=302)
        result = conn.execute(
            "UPDATE attendance_breaks SET break_end=? WHERE att_id=? AND break_end IS NULL",
            (now_str, att["id"]),
        )
        conn.execute("UPDATE attendance SET is_on_break=0 WHERE id=?", (att["id"],))
    flash(request, "Break ended — back to work!")
    return RedirectResponse("/dashboard", status_code=302)


# ── Tasks ──────────────────────────────────────────────────────────────────────
@app.post("/api/tasks")
async def start_task(
    request: Request,
    client: str = Form(...),
    task_title: str = Form(...),
    output_files: str = Form(""),
    notes: str = Form(""),
):
    user = require_user(request)
    today = get_today_date(user["shift_type"])
    with get_db() as conn:
        conn.execute(
            """INSERT INTO work_logs
               (emp_id, client, task_title, hours_worked, notes, output_files,
                status, date_logged, started_at, is_running)
               VALUES (?, ?, ?, 0, ?, ?, 'Todo', ?, NULL, 0)""",
            (user["id"], client, task_title, notes, output_files or None, today),
        )
        task_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        log_card_activity(conn, task_id, user["name"], "created",
                          client if client else None)
    flash(request, "Task logged. Move it to 'In Progress' on the board when you start.")
    return RedirectResponse("/dashboard", status_code=302)


@app.post("/api/tasks/{task_id}/stop")
async def stop_task(request: Request, task_id: int):
    user = require_user(request)
    now = get_pht_now()
    with get_db() as conn:
        task = conn.execute(
            "SELECT * FROM work_logs WHERE id=? AND emp_id=?", (task_id, user["id"])
        ).fetchone()
        if task and task["is_running"] and task["started_at"]:
            started = datetime.strptime(task["started_at"], "%Y-%m-%d %H:%M:%S")
            elapsed_hours = (now - started).total_seconds() / 3600
            rounded = max(0.25, round(elapsed_hours / 0.25) * 0.25)
            elapsed_mins = int((now - started).total_seconds() / 60)
            conn.execute(
                "UPDATE work_logs SET is_running=0, hours_worked=? WHERE id=?",
                (rounded, task_id),
            )
            flash(request, f"Task stopped — {rounded}h logged ({elapsed_mins} min actual).")
        else:
            flash(request, "Task stopped.", "warning")
    return RedirectResponse("/dashboard", status_code=302)


@app.post("/api/tasks/{task_id}/edit")
async def edit_task(
    request: Request,
    task_id: int,
    client: str = Form(...),
    task_title: str = Form(...),
    hours_worked: float = Form(...),
    notes: str = Form(""),
    output_files: str = Form(""),
):
    user = require_user(request)
    with get_db() as conn:
        task = conn.execute("SELECT emp_id, hours_worked FROM work_logs WHERE id=?", (task_id,)).fetchone()
        if not task or task["emp_id"] != user["id"]:
            flash(request, "Not authorized to edit this task.", "error")
            return RedirectResponse("/dashboard", status_code=302)
        old_hours = task["hours_worked"] or 0
        conn.execute(
            "UPDATE work_logs SET client=?, task_title=?, hours_worked=?, notes=?, output_files=? WHERE id=?",
            (client, task_title, hours_worked, notes, output_files or None, task_id),
        )
        if round(old_hours, 2) != round(hours_worked, 2):
            log_card_activity(conn, task_id, user["name"], "hours_updated",
                              f"{old_hours}h → {hours_worked}h")
    flash(request, "Task updated.")
    return RedirectResponse("/dashboard", status_code=302)


@app.post("/api/tasks/{task_id}/delete")
async def delete_task(request: Request, task_id: int):
    user = require_user(request)
    with get_db() as conn:
        task = conn.execute("SELECT emp_id, is_running FROM work_logs WHERE id=?", (task_id,)).fetchone()
        if not task:
            flash(request, "Task not found.", "error")
            return RedirectResponse("/dashboard", status_code=302)
        if user["role"] == "Employee":
            if task["emp_id"] != user["id"]:
                flash(request, "Not authorized.", "error")
                return RedirectResponse("/dashboard", status_code=302)
            if task["is_running"]:
                flash(request, "Stop the task before deleting.", "error")
                return RedirectResponse("/dashboard", status_code=302)
        conn.execute("DELETE FROM work_logs WHERE id=?", (task_id,))
    flash(request, "Task deleted.")
    referer = request.headers.get("referer", "/dashboard")
    return RedirectResponse(referer, status_code=302)


@app.post("/api/tasks/{task_id}/assign-reviewer")
async def assign_reviewer(request: Request, task_id: int, reviewer_name: str = Form(...)):
    user = require_user(request)
    with get_db() as conn:
        task = conn.execute("SELECT emp_id FROM work_logs WHERE id=?", (task_id,)).fetchone()
        if not task:
            return JSONResponse({"error": "Not found"}, status_code=404)
        # Any logged-in user can change reviewer
        reviewer = reviewer_name.strip() or None
        conn.execute("UPDATE work_logs SET reviewer_name=? WHERE id=?", (reviewer, task_id))
        detail = f"Assigned: {reviewer}" if reviewer else "Reviewer cleared"
        log_card_activity(conn, task_id, user["name"], "reviewer_set", detail)
    return JSONResponse({"ok": True})


# ── Profile ────────────────────────────────────────────────────────────────────
@app.get("/profile", response_class=HTMLResponse)
async def profile_page(request: Request):
    user = require_user(request)
    with get_db() as conn:
        emp = conn.execute("SELECT * FROM employees WHERE id=?", (user["id"],)).fetchone()
    emp_dict = dict(emp)
    emp_dict.pop("hourly_rate", None)  # employees should not see their own rate
    status, doc_count = get_compliance_status(emp_dict)
    return templates.TemplateResponse(request, "employee/profile.html", {
        "user": user,
        "emp": emp_dict,
        "compliance_status": status,
        "doc_count": doc_count,
        "now": get_pht_now(),
        "flash": get_flash(request),
        **shared_ctx(user, request),
    })


@app.post("/profile/update")
async def profile_update(
    request: Request,
    phone: str = Form(""),
    address: str = Form(""),
    capabilities: str = Form(""),
    birthday: str = Form(""),
    marital_status: str = Form(""),
    gender: str = Form(""),
    emergency_contact: str = Form(""),
    emergency_phone: str = Form(""),
):
    user = require_user(request)
    with get_db() as conn:
        conn.execute(
            """UPDATE employees SET phone=?, address=?, capabilities=?,
               birthday=?, marital_status=?, gender=?,
               emergency_contact=?, emergency_phone=? WHERE id=?""",
            (phone, address, capabilities,
             birthday or None, marital_status or None, gender or None,
             emergency_contact or None, emergency_phone or None,
             user["id"]),
        )
    flash(request, "Profile updated.")
    return RedirectResponse("/profile", status_code=302)


_MAX_AVATAR_BYTES = 5 * 1024 * 1024   # 5 MB
_MAX_DOC_BYTES    = 10 * 1024 * 1024  # 10 MB
_MAX_POSTER_BYTES = 20 * 1024 * 1024  # 20 MB

@app.post("/profile/avatar")
async def profile_avatar(request: Request, avatar: UploadFile = File(...)):
    user = require_user(request)
    ext = Path(avatar.filename).suffix.lower()
    if ext not in {".jpg", ".jpeg", ".png", ".gif", ".webp"}:
        flash(request, "Invalid file type. Use JPG, PNG, or GIF.", "error")
        return RedirectResponse("/profile", status_code=302)
    content = await avatar.read()
    if len(content) > _MAX_AVATAR_BYTES:
        flash(request, "Image too large. Maximum is 5 MB.", "error")
        return RedirectResponse("/profile", status_code=302)
    filename = f"AVATAR_{user['id']}_{uuid.uuid4().hex[:8]}{ext}"
    dest = os.path.join(UPLOAD_DIR, "avatars", filename)
    with open(dest, "wb") as f:
        f.write(content)
    with get_db() as conn:
        conn.execute("UPDATE employees SET profile_pic_path=? WHERE id=?",
                     (f"avatars/{filename}", user["id"]))
    flash(request, "Profile picture updated.")
    return RedirectResponse("/profile", status_code=302)


@app.post("/profile/documents")
async def profile_documents(
    request: Request,
    doc_resume: UploadFile = File(None),
    doc_nbi: UploadFile = File(None),
    doc_sss: UploadFile = File(None),
    doc_tin: UploadFile = File(None),
    doc_philhealth: UploadFile = File(None),
    doc_pagibig: UploadFile = File(None),
    doc_nbi_expiry: str = Form(""),
    doc_sss_expiry: str = Form(""),
    doc_tin_expiry: str = Form(""),
    doc_philhealth_expiry: str = Form(""),
    doc_pagibig_expiry: str = Form(""),
):
    user = require_user(request)
    doc_fields = {
        "doc_resume": doc_resume, "doc_nbi": doc_nbi, "doc_sss": doc_sss,
        "doc_tin": doc_tin, "doc_philhealth": doc_philhealth, "doc_pagibig": doc_pagibig,
    }
    updates = {}
    for field, upload in doc_fields.items():
        if upload and upload.filename:
            ext = Path(upload.filename).suffix.lower()
            if ext != ".pdf":
                continue
            content = await upload.read()
            if len(content) > _MAX_DOC_BYTES:
                flash(request, f"{field}: file too large. Maximum is 10 MB.", "error")
                return RedirectResponse("/profile", status_code=302)
            fname = f"{field.upper()}_{user['id']}_{uuid.uuid4().hex[:8]}.pdf"
            dest = os.path.join(UPLOAD_DIR, "docs", fname)
            with open(dest, "wb") as f:
                f.write(content)
            updates[field] = f"docs/{fname}"

    # Expiry dates (save even without new file upload)
    for expiry_field, val in [
        ("doc_nbi_expiry", doc_nbi_expiry),
        ("doc_sss_expiry", doc_sss_expiry),
        ("doc_tin_expiry", doc_tin_expiry),
        ("doc_philhealth_expiry", doc_philhealth_expiry),
        ("doc_pagibig_expiry", doc_pagibig_expiry),
    ]:
        if val and val.strip():
            updates[expiry_field] = val.strip()

    if updates:
        set_clause = ", ".join(f"{k}=?" for k in updates)
        with get_db() as conn:
            conn.execute(
                f"UPDATE employees SET {set_clause} WHERE id=?",
                (*updates.values(), user["id"]),
            )
    flash(request, "Documents updated successfully.")
    return RedirectResponse("/profile", status_code=302)


# ── Kanban Board ───────────────────────────────────────────────────────────────
@app.get("/kanban", response_class=HTMLResponse)
async def kanban_page(request: Request):
    user = require_user(request)
    with get_db() as conn:
        cards = conn.execute(
            """SELECT w.*, e.name as emp_name, e.profile_pic_path
               FROM work_logs w JOIN employees e ON w.emp_id=e.id
               WHERE COALESCE(w.is_archived,0)=0
               ORDER BY w.timestamp DESC"""
        ).fetchall()
        all_employees = conn.execute(
            "SELECT id, name FROM employees WHERE is_active=1 ORDER BY name"
        ).fetchall()
        archived_count = conn.execute(
            "SELECT COUNT(*) FROM work_logs WHERE COALESCE(is_archived,0)=1"
        ).fetchone()[0]
    columns = {s: [] for s in KANBAN_STATUSES}
    for c in cards:
        d = dict(c)
        if d["status"] in columns:
            columns[d["status"]].append(d)
    return templates.TemplateResponse(request, "shared/kanban.html", {
        "user": user,
        "columns": columns,
        "statuses": KANBAN_STATUSES,
        "clients": get_clients(),
        "all_employees": [dict(e) for e in all_employees],
        "archived_count": archived_count,
        "flash": get_flash(request),
        **shared_ctx(user, request),
    })


@app.post("/api/tasks/{task_id}/move")
async def move_task(request: Request, task_id: int):
    user = require_user(request)
    data = await request.json()
    new_status = data.get("status")
    all_statuses = KANBAN_STATUSES + ["Archived"]
    if new_status not in all_statuses:
        return JSONResponse({"error": "Invalid status"}, status_code=400)
    with get_db() as conn:
        task = conn.execute("SELECT status, emp_id FROM work_logs WHERE id=?", (task_id,)).fetchone()
        if not task:
            return JSONResponse({"error": "Not found"}, status_code=404)
        old_status = task["status"]
        if user["role"] in ("HR Manager", "Admin"):
            conn.execute("UPDATE work_logs SET status=? WHERE id=?", (new_status, task_id))
        else:
            conn.execute(
                "UPDATE work_logs SET status=? WHERE id=? AND emp_id=?",
                (new_status, task_id, user["id"]),
            )
        if old_status != new_status:
            log_card_activity(conn, task_id, user["name"], "status_changed",
                              f"{old_status} → {new_status}")
    return JSONResponse({"ok": True, "actual_status": new_status})


@app.post("/api/tasks/{task_id}/review")
async def review_task(request: Request, task_id: int):
    user = require_user(request)
    with get_db() as conn:
        if user["role"] in ("HR Manager", "Admin"):
            conn.execute(
                "UPDATE work_logs SET status='Done', hr_reviewed_by=? WHERE id=? AND status='For Review'",
                (user["name"], task_id),
            )
        else:
            conn.execute(
                "UPDATE work_logs SET status='Done', hr_reviewed_by=? WHERE id=? AND status='For Review' AND emp_id != ?",
                (user["name"], task_id, user["id"]),
            )
        log_card_activity(conn, task_id, user["name"], "reviewed", "Marked Done via review")
    flash(request, "Task reviewed and marked Done.")
    return RedirectResponse("/kanban", status_code=302)


@app.post("/api/tasks/{task_id}/return")
async def return_task(request: Request, task_id: int, return_note: str = Form("")):
    user = require_user(request)
    with get_db() as conn:
        task = conn.execute("SELECT * FROM work_logs WHERE id=?", (task_id,)).fetchone()
        if not task:
            return JSONResponse({"error": "Not found"}, status_code=404)
        new_revision = (task["revision_count"] or 0) + 1
        conn.execute(
            """UPDATE work_logs SET status='In Progress', revision_count=?, hr_reviewed_by=NULL
               WHERE id=?""",
            (new_revision, task_id),
        )
        note = return_note.strip() or "Returned for revision"
        log_card_activity(conn, task_id, user["name"], "returned",
                          f"Revision #{new_revision}: {note}")
        push_notification(conn, task["emp_id"], "Task Returned for Revision",
                          f"'{task['task_title']}' needs revision. Revision #{new_revision}: {note}",
                          "/dashboard")
    flash(request, f"Task returned for revision (revision #{new_revision}).")
    return RedirectResponse("/kanban", status_code=302)


@app.post("/api/tasks/{task_id}/approve")
async def approve_task(request: Request, task_id: int):
    user = require_role(request, "Admin")
    with get_db() as conn:
        conn.execute(
            "UPDATE work_logs SET admin_approved_by=? WHERE id=?",
            (user["name"], task_id),
        )
        log_card_activity(conn, task_id, user["name"], "approved", "Admin approved")
    flash(request, "Task approved.")
    return RedirectResponse("/kanban", status_code=302)


@app.post("/api/tasks/{task_id}/comment")
async def add_comment(request: Request, task_id: int, comment_text: str = Form(...)):
    user = require_user(request)
    now_str = get_pht_now().strftime("%Y-%m-%d %H:%M:%S")
    with get_db() as conn:
        conn.execute(
            "INSERT INTO card_comments (card_id, author_name, comment_text, timestamp) VALUES (?, ?, ?, ?)",
            (task_id, user["name"], comment_text, now_str),
        )
    return JSONResponse({"ok": True, "comment": {
        "author_name": user["name"],
        "comment_text": comment_text,
        "timestamp": now_str,
    }})


@app.get("/api/tasks/{task_id}/detail")
async def task_detail(request: Request, task_id: int):
    user = require_user(request)
    with get_db() as conn:
        card = conn.execute(
            "SELECT w.*, e.name as emp_name FROM work_logs w JOIN employees e ON w.emp_id=e.id WHERE w.id=?",
            (task_id,),
        ).fetchone()
        comments = conn.execute(
            "SELECT * FROM card_comments WHERE card_id=? ORDER BY timestamp ASC",
            (task_id,),
        ).fetchall()
        activities = conn.execute(
            "SELECT * FROM card_activities WHERE card_id=? ORDER BY created_at ASC",
            (task_id,),
        ).fetchall()
        all_employees = conn.execute(
            "SELECT id, name FROM employees WHERE is_active=1 AND role='Employee' ORDER BY name"
        ).fetchall()
    if not card:
        return JSONResponse({"error": "Not found"}, status_code=404)
    return JSONResponse({
        "card": dict(card),
        "comments": [dict(c) for c in comments],
        "activities": [dict(a) for a in activities],
        "statuses": KANBAN_STATUSES,
        "user_role": user["role"],
        "user_name": user["name"],
        "user_id": user["id"],
        "all_employees": [dict(e) for e in all_employees],
    })


@app.post("/api/tasks/{task_id}/assign")
async def assign_task(request: Request, task_id: int):
    user = require_user(request)
    with get_db() as conn:
        conn.execute(
            "UPDATE work_logs SET emp_id=?, status='In Progress' WHERE id=? AND (emp_id IS NULL OR emp_id=?)",
            (user["id"], task_id, user["id"]),
        )
    return JSONResponse({"ok": True})


@app.post("/api/tasks/{task_id}/archive")
async def archive_task(request: Request, task_id: int):
    user = require_user(request)
    with get_db() as conn:
        task = conn.execute(
            "SELECT emp_id, is_archived FROM work_logs WHERE id=?", (task_id,)
        ).fetchone()
        if not task:
            return JSONResponse({"error": "Not found"}, status_code=404)
        if user["role"] not in ("HR Manager", "Admin") and task["emp_id"] != user["id"]:
            return JSONResponse({"error": "Not authorized"}, status_code=403)
        new_archived = 0 if task["is_archived"] else 1
        conn.execute("UPDATE work_logs SET is_archived=? WHERE id=?", (new_archived, task_id))
        action = "archived" if new_archived else "unarchived"
        log_card_activity(conn, task_id, user["name"], action)
    return JSONResponse({"ok": True, "archived": bool(new_archived)})


# ══════════════════════════════════════════════════════════════════════════════
# ── HR KANBAN (private HR/Admin board) ────────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/hr-kanban", response_class=HTMLResponse)
async def hr_kanban_page(request: Request):
    user = require_role(request, "HR Manager", "Admin")
    with get_db() as conn:
        tasks = conn.execute(
            """SELECT t.*, e.name as assignee_name
               FROM hr_tasks t
               LEFT JOIN employees e ON t.assigned_to=e.id
               WHERE COALESCE(t.is_archived,0)=0
               ORDER BY t.created_at DESC"""
        ).fetchall()
        staff = conn.execute(
            "SELECT id, name FROM employees WHERE role IN ('HR Manager','Admin') AND is_active=1 ORDER BY name"
        ).fetchall()
    return templates.TemplateResponse(request, "shared/hr_kanban.html", {
        "user": user,
        "tasks": [dict(t) for t in tasks],
        "staff": [dict(s) for s in staff],
        "statuses": ["Todo", "In Progress", "Review", "Done"],
        "flash": get_flash(request),
        **shared_ctx(user, request),
    })


@app.post("/api/hr-tasks")
async def create_hr_task(
    request: Request,
    title: str = Form(...),
    description: str = Form(""),
    assigned_to: str = Form(""),
    priority: str = Form("Normal"),
    due_date: str = Form(""),
):
    user = require_role(request, "HR Manager", "Admin")
    with get_db() as conn:
        assigned_id = int(assigned_to) if assigned_to.strip().isdigit() else None
        assigned_name = None
        if assigned_id:
            row = conn.execute("SELECT name FROM employees WHERE id=?", (assigned_id,)).fetchone()
            assigned_name = row["name"] if row else None
        conn.execute(
            """INSERT INTO hr_tasks (title, description, assigned_to, assigned_name, priority, due_date, status, created_by, created_by_name)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (title.strip(), description.strip(), assigned_id, assigned_name,
             priority, due_date or None, "Todo", user["id"], user["name"])
        )
    flash(request, "HR task created.")
    return RedirectResponse("/hr-kanban", status_code=302)


@app.post("/api/hr-tasks/{task_id}/move")
async def move_hr_task(request: Request, task_id: int):
    user = require_role(request, "HR Manager", "Admin")
    data = await request.json()
    new_status = data.get("status")
    if new_status not in ("Todo", "In Progress", "Review", "Done"):
        return JSONResponse({"error": "Invalid status"}, status_code=400)
    with get_db() as conn:
        conn.execute("UPDATE hr_tasks SET status=? WHERE id=?", (new_status, task_id))
    return JSONResponse({"ok": True})


@app.post("/api/hr-tasks/{task_id}/delete")
async def delete_hr_task(request: Request, task_id: int):
    user = require_role(request, "HR Manager", "Admin")
    with get_db() as conn:
        conn.execute("DELETE FROM hr_tasks WHERE id=?", (task_id,))
    flash(request, "Task deleted.")
    return RedirectResponse("/hr-kanban", status_code=302)


@app.get("/kanban/archive", response_class=HTMLResponse)
async def kanban_archive(request: Request):
    user = require_role(request, "HR Manager", "Admin")
    with get_db() as conn:
        cards = conn.execute(
            """SELECT w.*, e.name as emp_name, e.profile_pic_path
               FROM work_logs w JOIN employees e ON w.emp_id=e.id
               WHERE COALESCE(w.is_archived,0)=1
               ORDER BY w.timestamp DESC"""
        ).fetchall()
    return templates.TemplateResponse(request, "shared/kanban_archive.html", {
        "user": user,
        "cards": [dict(c) for c in cards],
        "clients": get_clients(),
        "flash": get_flash(request),
        **shared_ctx(user, request),
    })


# ── Client Management ──────────────────────────────────────────────────────────
@app.get("/clients", response_class=HTMLResponse)
async def clients_page(request: Request):
    user = require_role(request, "HR Manager", "Admin")
    with get_db() as conn:
        clients = [dict(r) for r in conn.execute(
            "SELECT * FROM clients ORDER BY sort_order, name"
        ).fetchall()]
    return templates.TemplateResponse(request, "admin/clients.html",
        {"user": user, "clients": clients, "flash": get_flash(request)})


@app.post("/api/clients")
async def add_client(request: Request, name: str = Form(...), hex_color: str = Form("#3b82f6")):
    require_role(request, "HR Manager", "Admin")
    name = name.strip()
    if not name:
        return RedirectResponse("/clients", status_code=302)
    with get_db() as conn:
        max_order = conn.execute("SELECT COALESCE(MAX(sort_order),0) FROM clients").fetchone()[0]
        conn.execute("INSERT OR IGNORE INTO clients (name, hex_color, sort_order) VALUES (?, ?, ?)",
                     (name, hex_color, max_order + 1))
    flash(request, f'Client "{name}" added.')
    return RedirectResponse("/clients", status_code=302)


@app.post("/api/clients/{client_id}/update")
async def update_client(request: Request, client_id: int,
                        name: str = Form(...), hex_color: str = Form("#3b82f6")):
    require_role(request, "HR Manager", "Admin")
    with get_db() as conn:
        conn.execute("UPDATE clients SET name=?, hex_color=? WHERE id=?",
                     (name.strip(), hex_color, client_id))
    flash(request, "Client updated.")
    return RedirectResponse("/clients", status_code=302)


@app.post("/api/clients/{client_id}/delete")
async def delete_client(request: Request, client_id: int):
    require_role(request, "Admin")
    with get_db() as conn:
        conn.execute("DELETE FROM clients WHERE id=?", (client_id,))
    flash(request, "Client removed.")
    return RedirectResponse("/clients", status_code=302)


# ── Client Dashboard ────────────────────────────────────────────────────────────
@app.get("/clients/{client_id}/dashboard", response_class=HTMLResponse)
async def client_dashboard(request: Request, client_id: int):
    user = require_role(request, "HR Manager", "Admin")
    with get_db() as conn:
        client = conn.execute("SELECT * FROM clients WHERE id=?", (client_id,)).fetchone()
        if not client:
            return RedirectResponse("/clients", status_code=302)
        client = dict(client)

        tasks = conn.execute(
            """SELECT w.*, e.name as emp_name FROM work_logs w
               JOIN employees e ON w.emp_id=e.id
               WHERE w.client=? ORDER BY w.date_logged DESC, w.timestamp DESC""",
            (client["name"],),
        ).fetchall()

        notes = conn.execute(
            "SELECT * FROM client_notes WHERE client_id=? ORDER BY created_at DESC",
            (client_id,),
        ).fetchall()

    tasks = [dict(t) for t in tasks]
    today = date.today().strftime("%Y-%m-%d")
    week_start, week_end = get_week_range()
    month_start = date.today().replace(day=1).strftime("%Y-%m-%d")

    total_hours = sum(t["hours_worked"] or 0 for t in tasks)
    week_hours  = sum(t["hours_worked"] or 0 for t in tasks if week_start <= (t["date_logged"] or "") <= week_end)
    month_hours = sum(t["hours_worked"] or 0 for t in tasks if (t["date_logged"] or "") >= month_start)
    today_hours = sum(t["hours_worked"] or 0 for t in tasks if t["date_logged"] == today)

    from collections import defaultdict
    day_map = defaultdict(float)
    for t in tasks:
        if t["date_logged"]:
            day_map[t["date_logged"]] += t["hours_worked"] or 0
    hours_by_day = sorted(day_map.items(), reverse=True)[:30]

    return templates.TemplateResponse(request, "admin/client_dashboard.html", {
        "user": user, "client": client, "tasks": tasks, "notes": [dict(n) for n in notes],
        "total_hours": round(total_hours, 2),
        "week_hours":  round(week_hours, 2),
        "month_hours": round(month_hours, 2),
        "today_hours": round(today_hours, 2),
        "hours_by_day": hours_by_day,
        "flash": get_flash(request),
    })


@app.post("/api/clients/{client_id}/notes")
async def add_client_note(
    request: Request, client_id: int,
    title: str = Form(...), content: str = Form(...),
):
    user = require_role(request, "HR Manager", "Admin")
    with get_db() as conn:
        conn.execute(
            "INSERT INTO client_notes (client_id, title, content, author_name) VALUES (?, ?, ?, ?)",
            (client_id, title.strip(), content.strip(), user["name"]),
        )
    flash(request, "Note added.")
    return RedirectResponse(f"/clients/{client_id}/dashboard", status_code=302)


@app.post("/api/clients/{client_id}/notes/{note_id}/delete")
async def delete_client_note(request: Request, client_id: int, note_id: int):
    require_role(request, "HR Manager", "Admin")
    with get_db() as conn:
        conn.execute("DELETE FROM client_notes WHERE id=? AND client_id=?", (note_id, client_id))
    flash(request, "Note deleted.")
    return RedirectResponse(f"/clients/{client_id}/dashboard", status_code=302)


# ── Roster ─────────────────────────────────────────────────────────────────────
@app.get("/roster", response_class=HTMLResponse)
async def roster_page(request: Request):
    user = require_role(request, "HR Manager", "Admin")
    week_start, week_end = get_week_range()

    with get_db() as conn:
        employees = conn.execute(
            "SELECT * FROM employees WHERE role='Employee' ORDER BY name"
        ).fetchall()

    roster = []
    for emp in employees:
        e = dict(emp)
        emp_today = get_today_date(emp["shift_type"])
        today_att = None
        with get_db() as conn:
            today_att = conn.execute(
                "SELECT * FROM attendance WHERE emp_id=? AND date_logged=?",
                (emp["id"], emp_today),
            ).fetchone()
            week_atts = conn.execute(
                "SELECT * FROM attendance WHERE emp_id=? AND date_logged BETWEEN ? AND ?",
                (emp["id"], week_start, week_end),
            ).fetchall()
        e["today_attendance"] = dict(today_att) if today_att else None
        week_hours = sum(calculate_hours(r["clock_in"], r["clock_out"] or "") for r in week_atts)
        e["week_hours"] = round(week_hours, 2)
        status, doc_count = get_compliance_status(e)
        e["compliance"] = status
        e["doc_count"] = doc_count
        roster.append(e)

    # ── Composition stats ────────────────────────────────────────────────────
    from collections import Counter
    active_roster = [e for e in roster if e.get("is_active", 1)]
    total = len(active_roster)

    gender_counts = Counter(e.get("gender") or "Unspecified" for e in active_roster)
    employment_counts = Counter(e.get("employment_type") or "Full-time" for e in active_roster)
    shift_counts = Counter(e.get("shift_type") or "Morning" for e in active_roster)

    skill_counter: Counter = Counter()
    for e in active_roster:
        caps = e.get("capabilities") or ""
        for sk in caps.split(","):
            sk = sk.strip()
            if sk:
                skill_counter[sk] += 1
    top_skills = skill_counter.most_common(8)

    comp_stats = {
        "gender": dict(gender_counts),
        "employment": dict(employment_counts),
        "shift": dict(shift_counts),
    }

    return templates.TemplateResponse(request, "shared/roster.html", {
        "user": user, "roster": roster,
        "total": total,
        "comp_stats": comp_stats,
        "top_skills": top_skills,
        "flash": get_flash(request), **shared_ctx(user, request),
    })


# ── Employee Directory ─────────────────────────────────────────────────────────
@app.get("/directory", response_class=HTMLResponse)
async def directory_page(request: Request):
    user = require_role(request, "HR Manager", "Admin")
    with get_db() as conn:
        people = conn.execute(
            """SELECT id, name, role, shift_type, capabilities, profile_pic_path,
                      phone, email, birthday, is_active
               FROM employees WHERE is_active=1 ORDER BY role, name"""
        ).fetchall()
    return templates.TemplateResponse(request, "shared/directory.html", {
        "user": user,
        "people": [dict(p) for p in people],
        "flash": get_flash(request),
        **shared_ctx(user, request),
    })


# ── Payroll ────────────────────────────────────────────────────────────────────
@app.get("/payroll", response_class=HTMLResponse)
async def payroll_page(request: Request, week: str = None, start: str = None, end: str = None):
    user = require_role(request, "HR Manager", "Admin")

    # Support custom date range OR standard week
    if start and end:
        try:
            datetime.strptime(start, "%Y-%m-%d")
            datetime.strptime(end, "%Y-%m-%d")
            week_start, week_end = start, end
            is_custom = True
        except ValueError:
            week_start, week_end = get_week_range()
            is_custom = False
    elif week:
        try:
            ref = datetime.strptime(week, "%Y-%m-%d").date()
        except ValueError:
            ref = date.today()
        week_start, week_end = get_week_range(ref)
        is_custom = False
    else:
        week_start, week_end = get_week_range()
        is_custom = False

    with get_db() as conn:
        employees = conn.execute(
            "SELECT * FROM employees WHERE role='Employee' AND is_active=1 ORDER BY name"
        ).fetchall()
        existing_run = conn.execute(
            "SELECT * FROM payroll_runs WHERE week_start=? LIMIT 1", (week_start,)
        ).fetchone()

        if existing_run:
            # Load from saved snapshot — protects historical data from rate changes
            saved_rows = conn.execute(
                """SELECT pr.*, e.shift_type FROM payroll_runs pr
                   JOIN employees e ON pr.emp_id = e.id
                   WHERE pr.week_start=? ORDER BY pr.emp_name""",
                (week_start,)
            ).fetchall()
            payroll_data = []
            for r in saved_rows:
                d = dict(r)
                d["run_id"] = d["id"]
                payroll_data.append(d)
        else:
            # Preview: calculate from attendance
            payroll_data = [
                compute_payroll_for_employee(emp["id"], week_start, week_end)
                for emp in employees
            ]
            payroll_data = [p for p in payroll_data if p]

    total_gross = round(sum(p.get("total_pay", p.get("gross_pay", 0)) for p in payroll_data), 2)
    total_net   = round(sum(p.get("net_pay", p.get("total_pay", 0)) for p in payroll_data), 2)

    # Build week options for last 12 weeks
    week_options = []
    for i in range(12):
        d = date.today() - timedelta(weeks=i)
        ws, we = get_week_range(d)
        week_options.append({"value": ws, "label": f"{ws} – {we}"})

    return templates.TemplateResponse(request, "shared/payroll.html", {
        "user": user,
        "week_start": week_start, "week_end": week_end,
        "payroll_data": payroll_data,
        "total_gross": total_gross,
        "total_net": total_net,
        "existing_run": dict(existing_run) if existing_run else None,
        "week_options": week_options,
        "selected_week": week_start,
        "is_custom": is_custom,
        "custom_start": start or "", "custom_end": end or "",
        "flash": get_flash(request),
        **shared_ctx(user, request),
    })


@app.post("/api/payroll/generate")
async def generate_payroll(request: Request, week_start: str = Form(...), week_end: str = Form("")):
    user = require_role(request, "Admin", "HR Manager")
    if not week_end:
        week_end = (datetime.strptime(week_start, "%Y-%m-%d").date() + timedelta(days=5)).strftime("%Y-%m-%d")
    with get_db() as conn:
        employees = conn.execute(
            "SELECT * FROM employees WHERE role='Employee' AND is_active=1"
        ).fetchall()
        conn.execute("DELETE FROM payroll_runs WHERE week_start=?", (week_start,))
        conn.execute("DELETE FROM payslip_logs WHERE week_start=?", (week_start,))
        for emp in employees:
            p = compute_payroll_for_employee(emp["id"], week_start, week_end)
            if p:
                cur = conn.execute(
                    """INSERT INTO payroll_runs
                       (week_start, week_end, emp_id, emp_name, regular_hours, overtime_hours,
                        hourly_rate, regular_pay, overtime_pay, total_pay,
                        gross_pay, sss_deduction, philhealth_deduction, pagibig_deduction,
                        tax_deduction, total_deductions, net_pay)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (week_start, week_end, p["emp_id"], p["emp_name"],
                     p["regular_hours"], p["overtime_hours"], p["hourly_rate"],
                     p["regular_pay"], p["overtime_pay"], p["total_pay"],
                     p["gross_pay"], p["sss_deduction"], p["philhealth_deduction"],
                     p["pagibig_deduction"], p["tax_deduction"], p["total_deductions"],
                     p["net_pay"]),
                )
                conn.execute(
                    """INSERT INTO payslip_logs
                       (emp_id, payroll_run_id, week_start, week_end,
                        gross_pay, total_deductions, net_pay, generated_by)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (p["emp_id"], cur.lastrowid, week_start, week_end,
                     p["gross_pay"], p["total_deductions"], p["net_pay"], user["name"]),
                )
    flash(request, f"Payroll generated for {week_start} – {week_end}.")
    return RedirectResponse(f"/payroll?start={week_start}&end={week_end}", status_code=302)


@app.post("/api/payroll/{run_id}/approve")
async def approve_payroll(request: Request, run_id: int):
    user = require_role(request, "Admin")
    with get_db() as conn:
        run = conn.execute("SELECT week_start FROM payroll_runs WHERE id=?", (run_id,)).fetchone()
        if run:
            week_start = run["week_start"]
            conn.execute(
                "UPDATE payroll_runs SET status='Approved', approved_by=? WHERE week_start=?",
                (user["name"], week_start),
            )
    flash(request, "Payroll approved.")
    return RedirectResponse(f"/payroll?week={week_start}", status_code=302)


@app.post("/api/payroll/{run_id}/hr-approve")
async def hr_approve_payroll(request: Request, run_id: int):
    user = require_role(request, "HR Manager", "Admin")
    now_str = get_pht_now().strftime("%Y-%m-%d %H:%M:%S")
    with get_db() as conn:
        run = conn.execute("SELECT week_start FROM payroll_runs WHERE id=?", (run_id,)).fetchone()
        if run:
            week_start = run["week_start"]
            conn.execute(
                "UPDATE payroll_runs SET hr_approved_by=?, hr_approved_at=? WHERE week_start=?",
                (user["name"], now_str, week_start),
            )
    flash(request, "Payroll marked as HR reviewed.")
    return RedirectResponse(f"/payroll?week={week_start}", status_code=302)


@app.post("/api/payroll/{run_id}/edit-hours")
async def edit_payroll_hours(
    request: Request,
    run_id: int,
    regular_hours: float = Form(...),
    overtime_hours: float = Form(...),
):
    user = require_role(request, "HR Manager", "Admin")
    with get_db() as conn:
        run = conn.execute("SELECT * FROM payroll_runs WHERE id=?", (run_id,)).fetchone()
        if run:
            rate = run["hourly_rate"]
            reg_pay = round(regular_hours * rate, 2)
            ot_pay = round(overtime_hours * rate, 2)
            conn.execute(
                """UPDATE payroll_runs SET regular_hours=?, overtime_hours=?,
                   regular_pay=?, overtime_pay=?, total_pay=? WHERE id=?""",
                (regular_hours, overtime_hours, reg_pay, ot_pay, round(reg_pay + ot_pay, 2), run_id),
            )
            week_start = run["week_start"]
    flash(request, "Hours updated.")
    return RedirectResponse(f"/payroll?week={week_start}", status_code=302)


@app.post("/api/payroll/{run_id}/edit-rate")
async def edit_payroll_rate(
    request: Request,
    run_id: int,
    hourly_rate: float = Form(...),
):
    """HR can update the rate for a specific payroll record and recalculate."""
    user = require_role(request, "HR Manager", "Admin")
    with get_db() as conn:
        run = conn.execute("SELECT * FROM payroll_runs WHERE id=?", (run_id,)).fetchone()
        if run:
            reg_pay = round(run["regular_hours"] * hourly_rate, 2)
            ot_pay = round(run["overtime_hours"] * hourly_rate, 2)
            conn.execute(
                """UPDATE payroll_runs SET hourly_rate=?, regular_pay=?, overtime_pay=?, total_pay=? WHERE id=?""",
                (hourly_rate, reg_pay, ot_pay, round(reg_pay + ot_pay, 2), run_id),
            )
            week_start = run["week_start"]
    flash(request, "Rate updated for this payroll run.")
    return RedirectResponse(f"/payroll?week={week_start}", status_code=302)


@app.post("/api/attendance/{att_id}/edit")
async def edit_attendance_record(
    request: Request,
    att_id: int,
    clock_in: str = Form(""),
    clock_out: str = Form(""),
):
    user = require_role(request, "HR Manager", "Admin")
    with get_db() as conn:
        if clock_in.strip():
            conn.execute("UPDATE attendance SET clock_in=? WHERE id=?",
                         ((clock_in.strip() + ":00")[:8], att_id))
        if clock_out.strip():
            conn.execute("UPDATE attendance SET clock_out=? WHERE id=?",
                         ((clock_out.strip() + ":00")[:8], att_id))
    flash(request, "Attendance updated.")
    referer = request.headers.get("referer", "/attendance")
    return RedirectResponse(referer, status_code=302)


# ── Employee Management (Admin + HR for rate) ──────────────────────────────────
@app.get("/employees", response_class=HTMLResponse)
async def employees_page(request: Request, status_tab: str = "Active"):
    user = require_role(request, "Admin", "HR Manager")
    with get_db() as conn:
        if status_tab in ("Terminated", "Resigned"):
            employees = conn.execute(
                "SELECT * FROM employees WHERE COALESCE(emp_status,?) = ? ORDER BY name",
                (status_tab, status_tab)
            ).fetchall()
        elif status_tab == "Inactive":
            employees = conn.execute(
                "SELECT * FROM employees WHERE COALESCE(emp_status,'Active') = 'Inactive' ORDER BY name"
            ).fetchall()
        else:
            employees = conn.execute(
                "SELECT * FROM employees WHERE COALESCE(emp_status,'Active') = 'Active' ORDER BY role, name"
            ).fetchall()
        counts = {s: conn.execute(
            "SELECT COUNT(*) FROM employees WHERE COALESCE(emp_status,'Active')=?", (s,)
        ).fetchone()[0] for s in ("Active", "Inactive", "Terminated", "Resigned")}
    return templates.TemplateResponse(request, "admin/employees.html", {
        "user": user,
        "employees": [dict(e) for e in employees],
        "status_tab": status_tab,
        "counts": counts,
        "flash": get_flash(request),
        **shared_ctx(user, request),
    })


@app.get("/employees/{emp_id}/profile", response_class=HTMLResponse)
async def employee_profile_page(request: Request, emp_id: int):
    user = require_role(request, "Admin", "HR Manager")
    with get_db() as conn:
        emp = conn.execute("SELECT * FROM employees WHERE id=?", (emp_id,)).fetchone()
        if not emp:
            flash(request, "Employee not found.", "error")
            return RedirectResponse("/employees", status_code=302)
        attendance = conn.execute(
            "SELECT * FROM attendance WHERE emp_id=? ORDER BY date_logged DESC LIMIT 14",
            (emp_id,),
        ).fetchall()
        tasks = conn.execute(
            "SELECT * FROM work_logs WHERE emp_id=? ORDER BY timestamp DESC LIMIT 20",
            (emp_id,),
        ).fetchall()
        payslips = conn.execute(
            "SELECT * FROM payslip_logs WHERE emp_id=? ORDER BY generated_at DESC LIMIT 30",
            (emp_id,),
        ).fetchall()
    status, doc_count = get_compliance_status(dict(emp))
    today_str = get_pht_now().strftime("%Y-%m-%d")
    return templates.TemplateResponse(request, "admin/employee_profile.html", {
        "user": user,
        "emp": dict(emp),
        "attendance": [dict(a) for a in attendance],
        "tasks": [dict(t) for t in tasks],
        "payslips": [dict(p) for p in payslips],
        "compliance_status": status,
        "doc_count": doc_count,
        "today_str": today_str,
        "flash": get_flash(request),
        **shared_ctx(user, request),
    })


@app.post("/api/employees/{emp_id}/update-rate")
async def update_employee_rate(
    request: Request,
    emp_id: int,
    hourly_rate: float = Form(...),
):
    user = require_role(request, "HR Manager", "Admin")
    with get_db() as conn:
        conn.execute("UPDATE employees SET hourly_rate=? WHERE id=?", (hourly_rate, emp_id))
    flash(request, f"Hourly rate updated to ₱{hourly_rate:.2f}/hr.")
    referer = request.headers.get("referer", "/employees")
    return RedirectResponse(referer, status_code=302)


@app.post("/api/employees/{emp_id}/hr-update")
async def hr_update_employee(
    request: Request,
    emp_id: int,
    name: str = Form(...),
    shift_type: str = Form("Morning"),
    hourly_rate: float = Form(0.0),
    capabilities: str = Form(""),
):
    """HR Manager can edit employee name, shift, rate, capabilities — not role."""
    user = require_role(request, "HR Manager", "Admin")
    with get_db() as conn:
        emp = conn.execute("SELECT role FROM employees WHERE id=?", (emp_id,)).fetchone()
        if not emp:
            flash(request, "Employee not found.", "error")
            return RedirectResponse("/employees", status_code=302)
        if user["role"] == "HR Manager" and emp["role"] != "Employee":
            flash(request, "HR can only edit Employee accounts.", "error")
            return RedirectResponse("/employees", status_code=302)
        conn.execute(
            "UPDATE employees SET name=?, shift_type=?, hourly_rate=?, capabilities=? WHERE id=?",
            (name.strip(), shift_type, hourly_rate, capabilities, emp_id),
        )
    flash(request, f"Employee '{name}' updated.")
    referer = request.headers.get("referer", "/employees")
    return RedirectResponse(referer, status_code=302)


@app.post("/api/employees/{emp_id}/hr-notes")
async def update_hr_notes(
    request: Request,
    emp_id: int,
    hr_feedback: str = Form(""),
    admin_notes: str = Form(""),
):
    user = require_role(request, "Admin", "HR Manager")
    with get_db() as conn:
        if user["role"] == "Admin":
            conn.execute(
                "UPDATE employees SET hr_feedback=?, admin_notes=? WHERE id=?",
                (hr_feedback or None, admin_notes or None, emp_id),
            )
        else:
            conn.execute(
                "UPDATE employees SET hr_feedback=? WHERE id=?",
                (hr_feedback or None, emp_id),
            )
    flash(request, "Notes saved.")
    return RedirectResponse(f"/employees/{emp_id}/profile", status_code=302)


@app.post("/api/employees")
async def create_employee(
    request: Request,
    name: str = Form(...),
    username: str = Form(...),
    email: str = Form(""),
    password: str = Form(...),
    role: str = Form("Employee"),
    hourly_rate: float = Form(0.0),
    shift_type: str = Form("Morning"),
    capabilities: str = Form(""),
    employment_type: str = Form("Full-time"),
    department: str = Form(""),
):
    require_role(request, "Admin")
    with get_db() as conn:
        try:
            conn.execute(
                """INSERT INTO employees
                   (name, username, email, password, role, hourly_rate, shift_type, capabilities, employment_type, department)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (name, username.strip().lower(), email or None, hash_password(password),
                 role, hourly_rate, shift_type, capabilities,
                 employment_type, department or None),
            )
        except Exception:
            flash(request, "Username already exists.", "error")
            return RedirectResponse("/employees", status_code=302)
    flash(request, f"Employee '{name}' created.")
    return RedirectResponse("/employees", status_code=302)


@app.post("/api/employees/{emp_id}/update")
async def update_employee(
    request: Request,
    emp_id: int,
    name: str = Form(...),
    username: str = Form(...),
    email: str = Form(""),
    role: str = Form(...),
    hourly_rate: float = Form(0.0),
    shift_type: str = Form("Morning"),
    capabilities: str = Form(""),
    employment_type: str = Form("Full-time"),
    department: str = Form(""),
    new_password: str = Form(""),
):
    require_role(request, "Admin")
    with get_db() as conn:
        if new_password:
            conn.execute(
                """UPDATE employees SET name=?, username=?, email=?, role=?, hourly_rate=?,
                   shift_type=?, capabilities=?, employment_type=?, department=?, password=?
                   WHERE id=?""",
                (name, username.strip().lower(), email or None, role, hourly_rate,
                 shift_type, capabilities, employment_type, department or None,
                 hash_password(new_password), emp_id),
            )
        else:
            conn.execute(
                """UPDATE employees SET name=?, username=?, email=?, role=?, hourly_rate=?,
                   shift_type=?, capabilities=?, employment_type=?, department=?
                   WHERE id=?""",
                (name, username.strip().lower(), email or None, role, hourly_rate,
                 shift_type, capabilities, employment_type, department or None, emp_id),
            )
    flash(request, "Employee updated.")
    return RedirectResponse("/employees", status_code=302)


@app.post("/api/employees/{emp_id}/toggle")
async def toggle_employee(request: Request, emp_id: int):
    require_role(request, "Admin")
    with get_db() as conn:
        emp = conn.execute("SELECT is_active FROM employees WHERE id=?", (emp_id,)).fetchone()
        if emp:
            conn.execute("UPDATE employees SET is_active=? WHERE id=?", (0 if emp["is_active"] else 1, emp_id))
    flash(request, "Employee status updated.")
    return RedirectResponse("/employees", status_code=302)


@app.post("/api/employees/{emp_id}/set-status")
async def set_employee_status(
    request: Request,
    emp_id: int,
    emp_status: str = Form(...),
    status_note: str = Form(""),
):
    user = require_role(request, "HR Manager", "Admin")
    valid_statuses = ("Active", "Inactive", "Terminated", "Resigned")
    if emp_status not in valid_statuses:
        flash(request, "Invalid status.", "error")
        return RedirectResponse("/employees", status_code=302)
    is_active = 1 if emp_status == "Active" else 0
    with get_db() as conn:
        conn.execute(
            "UPDATE employees SET emp_status=?, is_active=?, status_note=? WHERE id=?",
            (emp_status, is_active, status_note.strip(), emp_id),
        )
        audit(conn, user["id"], user["name"], "set_emp_status", "employees", emp_id,
              f"Status → {emp_status}")
    flash(request, f"Employee status changed to {emp_status}.")
    return RedirectResponse(f"/employees/{emp_id}/profile", status_code=302)


@app.post("/api/profile/bank")
async def update_bank_info(
    request: Request,
    bank_name: str = Form(""),
    bank_account: str = Form(""),
    bank_account_name: str = Form(""),
):
    user = require_user(request)
    with get_db() as conn:
        conn.execute(
            "UPDATE employees SET bank_name=?, bank_account=?, bank_account_name=? WHERE id=?",
            (bank_name.strip(), bank_account.strip(), bank_account_name.strip(), user["id"]),
        )
    flash(request, "Bank details updated.")
    return RedirectResponse("/profile", status_code=302)


@app.post("/api/profile/bank-qr")
async def upload_bank_qr(request: Request, qr_file: UploadFile = File(None)):
    user = require_user(request)
    if not qr_file or not qr_file.filename:
        flash(request, "No file selected.", "error")
        return RedirectResponse("/profile", status_code=302)
    ext = qr_file.filename.rsplit(".", 1)[-1].lower()
    if ext not in ("png", "jpg", "jpeg", "gif", "webp"):
        flash(request, "Only image files allowed.", "error")
        return RedirectResponse("/profile", status_code=302)
    upload_dir = Path("static/uploads/bank_qr")
    upload_dir.mkdir(parents=True, exist_ok=True)
    fname = f"bank_qr_{user['id']}.{ext}"
    dest = upload_dir / fname
    content = await qr_file.read()
    dest.write_bytes(content)
    with get_db() as conn:
        conn.execute("UPDATE employees SET bank_qr_path=? WHERE id=?",
                     (f"/static/uploads/bank_qr/{fname}", user["id"]))
    flash(request, "QR code uploaded.")
    return RedirectResponse("/profile", status_code=302)


# ── TV Dashboard ───────────────────────────────────────────────────────────────
@app.get("/tv", response_class=HTMLResponse)
async def tv_dashboard(request: Request):
    return templates.TemplateResponse(request, "tv.html")


@app.get("/api/tv/data")
async def tv_data(request: Request):
    with get_db() as conn:
        employees = conn.execute(
            "SELECT id, name, shift_type, profile_pic_path FROM employees WHERE role='Employee' AND is_active=1 ORDER BY name"
        ).fetchall()
        cards = conn.execute(
            """SELECT w.*, e.name as emp_name, e.profile_pic_path as emp_pic
               FROM work_logs w JOIN employees e ON w.emp_id=e.id
               WHERE COALESCE(w.is_archived,0)=0
               ORDER BY w.timestamp DESC"""
        ).fetchall()

    attendance_status = []
    for emp in employees:
        emp_today = get_today_date(emp["shift_type"])
        with get_db() as conn:
            att = conn.execute(
                "SELECT * FROM attendance WHERE emp_id=? AND date_logged=?",
                (emp["id"], emp_today),
            ).fetchone()
        clocked_in = bool(att and att["clock_in"] and not att["clock_out"])
        clocked_out = bool(att and att["clock_out"])
        attendance_status.append({
            "id": emp["id"],
            "name": emp["name"],
            "shift_type": emp["shift_type"],
            "emp_pic": emp["profile_pic_path"],
            "clocked_in": clocked_in,
            "clocked_out": clocked_out,
            "clock_in_time": att["clock_in"] if att else None,
            "clock_out_time": att["clock_out"] if att else None,
        })

    columns = {s: [] for s in KANBAN_STATUSES}
    for c in cards:
        d = dict(c)
        if d["status"] in columns:
            columns[d["status"]].append(d)

    verse = random.choice(BIBLE_VERSES)

    return JSONResponse({
        "attendance": attendance_status,
        "columns": columns,
        "verse": verse,
        "timestamp": get_pht_now().isoformat(),
    })


# ── Attendance report (admin) ──────────────────────────────────────────────────
@app.get("/attendance", response_class=HTMLResponse)
async def attendance_report(request: Request, start: str = None, end: str = None):
    user = require_role(request, "HR Manager", "Admin")
    week_start, week_end = get_week_range()
    start = start or week_start
    end = end or week_end

    with get_db() as conn:
        records = conn.execute(
            """SELECT a.*, e.name, e.shift_type
               FROM attendance a
               JOIN employees e ON a.emp_id=e.id
               WHERE a.date_logged BETWEEN ? AND ?
               ORDER BY a.date_logged DESC, e.name""",
            (start, end),
        ).fetchall()

    return templates.TemplateResponse(request, "shared/attendance.html", {
        "user": user,
        "records": [dict(r) for r in records],
        "start": start, "end": end,
        "flash": get_flash(request),
        **shared_ctx(user, request),
    })


# ══════════════════════════════════════════════════════════════════════════════
# ── OVERTIME SYSTEM ───────────────────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/my-ot", response_class=HTMLResponse)
async def my_ot_page(request: Request):
    user = require_role(request, "Employee", "HR Manager", "Admin")
    with get_db() as conn:
        requests_list = conn.execute(
            "SELECT * FROM overtime_requests WHERE emp_id=? ORDER BY filed_at DESC",
            (user["id"],)
        ).fetchall()
    return templates.TemplateResponse(request, "employee/my_ot.html", {
        "user": user,
        "ot_requests": [dict(r) for r in requests_list],
        "flash": get_flash(request),
        **shared_ctx(user, request),
    })


@app.post("/api/ot/file")
async def file_ot(
    request: Request,
    ot_date: str = Form(...),
    ot_start: str = Form(...),
    ot_end: str = Form(...),
    ot_type: str = Form("Regular"),
    reason: str = Form(""),
):
    user = require_user(request)
    # compute hours
    try:
        from datetime import datetime as dt
        s = dt.strptime(ot_start, "%H:%M")
        e = dt.strptime(ot_end, "%H:%M")
        if e <= s:
            e = e.replace(day=e.day + 1)  # past midnight
        hrs = round((e - s).total_seconds() / 3600, 2)
    except Exception:
        hrs = 0.0

    with get_db() as conn:
        conn.execute(
            """INSERT INTO overtime_requests (emp_id, ot_date, ot_start, ot_end, ot_type, reason, hours_computed)
               VALUES (?,?,?,?,?,?,?)""",
            (user["id"], ot_date, ot_start, ot_end, ot_type, reason.strip(), hrs),
        )
        # notify HR Managers
        hr_list = conn.execute(
            "SELECT id FROM employees WHERE role IN ('HR Manager','Admin') AND is_active=1"
        ).fetchall()
        for hr in hr_list:
            push_notification(conn, hr["id"],
                f"OT Request from {user['name']}",
                f"{ot_date} · {ot_start}–{ot_end} ({hrs}h) · {ot_type}",
                "/hr-ot")
    flash(request, f"OT filed for {ot_date} ({hrs}h). Awaiting HR approval.")
    return RedirectResponse("/my-ot", status_code=302)


@app.get("/hr-ot", response_class=HTMLResponse)
async def hr_ot_page(request: Request):
    user = require_role(request, "HR Manager", "Admin")
    with get_db() as conn:
        pending = conn.execute(
            """SELECT o.*, e.name as emp_name FROM overtime_requests o
               JOIN employees e ON o.emp_id=e.id
               WHERE o.status='Pending' ORDER BY o.filed_at DESC"""
        ).fetchall()
        history = conn.execute(
            """SELECT o.*, e.name as emp_name FROM overtime_requests o
               JOIN employees e ON o.emp_id=e.id
               WHERE o.status != 'Pending' ORDER BY o.filed_at DESC LIMIT 50"""
        ).fetchall()
    return templates.TemplateResponse(request, "shared/hr_ot.html", {
        "user": user,
        "pending": [dict(r) for r in pending],
        "history": [dict(r) for r in history],
        "flash": get_flash(request),
        **shared_ctx(user, request),
    })


@app.post("/api/ot/{ot_id}/approve")
async def approve_ot(request: Request, ot_id: int):
    user = require_role(request, "HR Manager", "Admin")
    now = get_pht_now().strftime("%Y-%m-%d %H:%M:%S")
    with get_db() as conn:
        ot = conn.execute("SELECT * FROM overtime_requests WHERE id=?", (ot_id,)).fetchone()
        if not ot:
            flash(request, "OT request not found.", "error")
            return RedirectResponse("/hr-ot", status_code=302)
        conn.execute(
            "UPDATE overtime_requests SET status='Approved', approved_by=?, approved_at=? WHERE id=?",
            (user["name"], now, ot_id),
        )
        push_notification(conn, ot["emp_id"],
            "OT Approved",
            f"Your OT on {ot['ot_date']} ({ot['hours_computed']}h) has been approved.",
            "/my-ot")
        audit(conn, user["id"], user["name"], "approve_ot", "overtime_requests", ot_id)
    flash(request, "OT approved.")
    return RedirectResponse("/hr-ot", status_code=302)


@app.post("/api/ot/{ot_id}/deny")
async def deny_ot(request: Request, ot_id: int, denied_reason: str = Form("")):
    user = require_role(request, "HR Manager", "Admin")
    now = get_pht_now().strftime("%Y-%m-%d %H:%M:%S")
    with get_db() as conn:
        ot = conn.execute("SELECT * FROM overtime_requests WHERE id=?", (ot_id,)).fetchone()
        if not ot:
            flash(request, "OT request not found.", "error")
            return RedirectResponse("/hr-ot", status_code=302)
        conn.execute(
            "UPDATE overtime_requests SET status='Denied', approved_by=?, approved_at=?, denied_reason=? WHERE id=?",
            (user["name"], now, denied_reason.strip(), ot_id),
        )
        push_notification(conn, ot["emp_id"],
            "OT Request Denied",
            f"Your OT on {ot['ot_date']} was denied. {denied_reason}",
            "/my-ot")
    flash(request, "OT denied.")
    return RedirectResponse("/hr-ot", status_code=302)


@app.post("/api/ot/{ot_id}/change-status")
async def change_ot_status(
    request: Request,
    ot_id: int,
    new_status: str = Form(...),
    denied_reason: str = Form(""),
):
    user = require_role(request, "HR Manager", "Admin")
    if new_status not in ("Pending", "Approved", "Denied"):
        flash(request, "Invalid status.", "error")
        return RedirectResponse("/hr-ot", status_code=302)
    now = get_pht_now().strftime("%Y-%m-%d %H:%M:%S")
    with get_db() as conn:
        ot = conn.execute("SELECT * FROM overtime_requests WHERE id=?", (ot_id,)).fetchone()
        if not ot:
            flash(request, "OT request not found.", "error")
            return RedirectResponse("/hr-ot", status_code=302)
        conn.execute(
            """UPDATE overtime_requests SET status=?, approved_by=?, approved_at=?, denied_reason=?
               WHERE id=?""",
            (new_status, user["name"], now, denied_reason.strip() if new_status == "Denied" else "", ot_id),
        )
        notif_title = f"OT Request {new_status}"
        notif_body = f"Your OT on {ot['ot_date']} status changed to {new_status} by {user['name']}."
        if new_status == "Denied" and denied_reason:
            notif_body += f" Reason: {denied_reason}"
        push_notification(conn, ot["emp_id"], notif_title, notif_body, "/my-ot")
        audit(conn, user["id"], user["name"], "change_ot_status", "overtime_requests", ot_id)
    flash(request, f"OT request status changed to {new_status}.")
    return RedirectResponse("/hr-ot", status_code=302)


# ══════════════════════════════════════════════════════════════════════════════
# ── LEAVE MANAGEMENT ──────────────────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════

LEAVE_TYPES = ["Vacation Leave", "Sick Leave", "Emergency Leave", "Unpaid Leave"]

@app.get("/my-leave", response_class=HTMLResponse)
async def my_leave_page(request: Request):
    user = require_user(request)
    with get_db() as conn:
        my_requests = conn.execute(
            "SELECT * FROM leave_requests WHERE emp_id=? ORDER BY filed_at DESC",
            (user["id"],)
        ).fetchall()
        # leave balance: approved days per type this year
        year_start = get_pht_now().strftime("%Y-01-01")
        balances = conn.execute(
            """SELECT leave_type, SUM(days_count) as used
               FROM leave_requests WHERE emp_id=? AND status='Approved' AND start_date >= ?
               GROUP BY leave_type""",
            (user["id"], year_start)
        ).fetchall()
    used_map = {r["leave_type"]: r["used"] for r in balances}
    available_types = [lt for lt in LEAVE_TYPES if not (
        (lt == "Vacation Leave" and not user.get("vl_enabled", 1)) or
        (lt == "Sick Leave"     and not user.get("sl_enabled", 1))
    )]
    return templates.TemplateResponse(request, "employee/my_leave.html", {
        "user": user,
        "leave_requests": [dict(r) for r in my_requests],
        "leave_types": available_types,
        "used_vl": used_map.get("Vacation Leave", 0),
        "used_sl": used_map.get("Sick Leave", 0),
        "flash": get_flash(request),
        **shared_ctx(user, request),
    })


@app.post("/api/leave/file")
async def file_leave(
    request: Request,
    leave_type: str = Form(...),
    start_date: str = Form(...),
    end_date: str = Form(...),
    reason: str = Form(""),
):
    user = require_user(request)
    if leave_type == "Vacation Leave" and not user.get("vl_enabled", 1):
        flash(request, "Vacation Leave is not enabled for your account.", "error")
        return RedirectResponse("/my-leave", status_code=302)
    if leave_type == "Sick Leave" and not user.get("sl_enabled", 1):
        flash(request, "Sick Leave is not enabled for your account.", "error")
        return RedirectResponse("/my-leave", status_code=302)
    try:
        from datetime import date as dt_date
        s = dt_date.fromisoformat(start_date)
        e = dt_date.fromisoformat(end_date)
        days = max(1.0, float((e - s).days + 1))
    except Exception:
        days = 1.0
    with get_db() as conn:
        conn.execute(
            """INSERT INTO leave_requests (emp_id, leave_type, start_date, end_date, days_count, reason)
               VALUES (?,?,?,?,?,?)""",
            (user["id"], leave_type, start_date, end_date, days, reason.strip()),
        )
        hr_list = conn.execute(
            "SELECT id FROM employees WHERE role IN ('HR Manager','Admin') AND is_active=1"
        ).fetchall()
        for hr in hr_list:
            push_notification(conn, hr["id"],
                f"Leave Request from {user['name']}",
                f"{leave_type} · {start_date} to {end_date} ({days}d)",
                "/hr-leave")
    flash(request, f"Leave request filed for {start_date}–{end_date}.")
    return RedirectResponse("/my-leave", status_code=302)


@app.get("/hr-leave", response_class=HTMLResponse)
async def hr_leave_page(request: Request):
    user = require_role(request, "HR Manager", "Admin")
    with get_db() as conn:
        pending = conn.execute(
            """SELECT l.*, e.name as emp_name FROM leave_requests l
               JOIN employees e ON l.emp_id=e.id
               WHERE l.status='Pending' ORDER BY l.filed_at DESC"""
        ).fetchall()
        history = conn.execute(
            """SELECT l.*, e.name as emp_name FROM leave_requests l
               JOIN employees e ON l.emp_id=e.id
               WHERE l.status != 'Pending' ORDER BY l.filed_at DESC LIMIT 60"""
        ).fetchall()
    return templates.TemplateResponse(request, "shared/hr_leave.html", {
        "user": user,
        "pending": [dict(r) for r in pending],
        "history": [dict(r) for r in history],
        "flash": get_flash(request),
        **shared_ctx(user, request),
    })


@app.post("/api/leave/{leave_id}/approve")
async def approve_leave(request: Request, leave_id: int):
    user = require_role(request, "HR Manager", "Admin")
    now = get_pht_now().strftime("%Y-%m-%d %H:%M:%S")
    with get_db() as conn:
        lr = conn.execute("SELECT * FROM leave_requests WHERE id=?", (leave_id,)).fetchone()
        if lr:
            conn.execute(
                "UPDATE leave_requests SET status='Approved', approved_by=?, approved_at=? WHERE id=?",
                (user["name"], now, leave_id),
            )
            push_notification(conn, lr["emp_id"],
                "Leave Approved",
                f"Your {lr['leave_type']} ({lr['start_date']} – {lr['end_date']}) has been approved.",
                "/my-leave")
            audit(conn, user["id"], user["name"], "approve_leave", "leave_requests", leave_id)
    flash(request, "Leave approved.")
    return RedirectResponse("/hr-leave", status_code=302)


@app.post("/api/leave/{leave_id}/deny")
async def deny_leave(request: Request, leave_id: int, denied_reason: str = Form("")):
    user = require_role(request, "HR Manager", "Admin")
    now = get_pht_now().strftime("%Y-%m-%d %H:%M:%S")
    with get_db() as conn:
        lr = conn.execute("SELECT * FROM leave_requests WHERE id=?", (leave_id,)).fetchone()
        if lr:
            conn.execute(
                "UPDATE leave_requests SET status='Denied', approved_by=?, approved_at=?, denied_reason=? WHERE id=?",
                (user["name"], now, denied_reason.strip(), leave_id),
            )
            push_notification(conn, lr["emp_id"],
                "Leave Request Denied",
                f"Your {lr['leave_type']} request was denied. {denied_reason}",
                "/my-leave")
    flash(request, "Leave denied.")
    return RedirectResponse("/hr-leave", status_code=302)


@app.post("/api/leave/{leave_id}/change-status")
async def change_leave_status(
    request: Request,
    leave_id: int,
    new_status: str = Form(...),
    denied_reason: str = Form(""),
):
    user = require_role(request, "HR Manager", "Admin")
    if new_status not in ("Pending", "Approved", "Denied"):
        flash(request, "Invalid status.", "error")
        return RedirectResponse("/hr-leave", status_code=302)
    now = get_pht_now().strftime("%Y-%m-%d %H:%M:%S")
    with get_db() as conn:
        lr = conn.execute("SELECT * FROM leave_requests WHERE id=?", (leave_id,)).fetchone()
        if not lr:
            flash(request, "Leave request not found.", "error")
            return RedirectResponse("/hr-leave", status_code=302)
        conn.execute(
            """UPDATE leave_requests SET status=?, approved_by=?, approved_at=?, denied_reason=?
               WHERE id=?""",
            (new_status, user["name"], now, denied_reason.strip() if new_status == "Denied" else "", leave_id),
        )
        notif_title = f"Leave Request {new_status}"
        notif_body = f"Your {lr['leave_type']} ({lr['start_date']}–{lr['end_date']}) status changed to {new_status}."
        if new_status == "Denied" and denied_reason:
            notif_body += f" Reason: {denied_reason}"
        push_notification(conn, lr["emp_id"], notif_title, notif_body, "/my-leave")
        audit(conn, user["id"], user["name"], "change_leave_status", "leave_requests", leave_id)
    flash(request, f"Leave request status changed to {new_status}.")
    return RedirectResponse("/hr-leave", status_code=302)


# ══════════════════════════════════════════════════════════════════════════════
# ── ANNOUNCEMENTS ─────────────────────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/announcements", response_class=HTMLResponse)
async def announcements_page(request: Request):
    user = require_user(request)
    # Build audience filter based on role
    if user["role"] == "Employee":
        aud_filter = "audience IN ('All', 'Employee')"
    elif user["role"] == "HR Manager":
        aud_filter = "audience IN ('All', 'Employee', 'HR')"
    else:  # Admin sees everything
        aud_filter = "1=1"
    with get_db() as conn:
        items = conn.execute(
            f"SELECT * FROM announcements WHERE {aud_filter} ORDER BY is_pinned DESC, created_at DESC"
        ).fetchall()
    return templates.TemplateResponse(request, "shared/announcements.html", {
        "user": user,
        "announcements": [dict(a) for a in items],
        "flash": get_flash(request),
        **shared_ctx(user, request),
    })


@app.post("/api/announcements")
async def post_announcement(
    request: Request,
    title: str = Form(...),
    body: str = Form(...),
    is_pinned: str = Form("0"),
    audience: str = Form("All"),
):
    user = require_role(request, "HR Manager", "Admin")
    pinned = 1 if is_pinned == "1" else 0
    valid_audiences = {"All", "Employee", "HR", "Admin"}
    if audience not in valid_audiences:
        audience = "All"
    with get_db() as conn:
        conn.execute(
            "INSERT INTO announcements (posted_by, posted_by_name, title, body, is_pinned, audience) VALUES (?,?,?,?,?,?)",
            (user["id"], user["name"], title.strip(), body.strip(), pinned, audience),
        )
        # Notify only the relevant audience
        if audience == "All":
            role_filter = "role IN ('Employee','HR Manager','Admin')"
        elif audience == "Employee":
            role_filter = "role='Employee'"
        elif audience == "HR":
            role_filter = "role IN ('HR Manager','Admin')"
        else:  # Admin
            role_filter = "role='Admin'"
        targets = conn.execute(
            f"SELECT id FROM employees WHERE is_active=1 AND {role_filter} AND id!=?",
            (user["id"],)
        ).fetchall()
        for e in targets:
            push_notification(conn, e["id"], f"📢 {title}", body[:100], "/announcements")
    flash(request, "Announcement posted.")
    return RedirectResponse("/announcements", status_code=302)


@app.post("/api/announcements/{ann_id}/delete")
async def delete_announcement(request: Request, ann_id: int):
    user = require_role(request, "HR Manager", "Admin")
    with get_db() as conn:
        conn.execute("DELETE FROM announcements WHERE id=?", (ann_id,))
    flash(request, "Announcement deleted.")
    return RedirectResponse("/announcements", status_code=302)


# ══════════════════════════════════════════════════════════════════════════════
# ── NOTIFICATIONS API ─────────────────────────────────────────════════════════
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/api/notifications")
async def get_notifications(request: Request):
    user = require_user(request)
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM notifications WHERE user_id=? ORDER BY created_at DESC LIMIT 20",
            (user["id"],)
        ).fetchall()
    return JSONResponse({"notifications": [dict(r) for r in rows]})


@app.get("/notifications/mark-read")
async def mark_notifications_read(request: Request):
    user = require_user(request)
    with get_db() as conn:
        conn.execute("UPDATE notifications SET is_read=1 WHERE user_id=?", (user["id"],))
    return RedirectResponse(request.headers.get("referer", "/dashboard"), status_code=302)


# ══════════════════════════════════════════════════════════════════════════════
# ── CHAT (group channel + direct messages) ───────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/chat", response_class=HTMLResponse)
async def chat_page(request: Request, room: str = "group"):
    user = require_user(request)

    with get_db() as conn:
        contacts = conn.execute(
            "SELECT id, name, role, profile_pic_path FROM employees WHERE id!=? AND is_active=1 ORDER BY name",
            (user["id"],)
        ).fetchall()
        contacts = [dict(c) for c in contacts]

        # Validate room access: 'group' or a DM room this user belongs to
        if room != "group":
            if not room.startswith("dm_") or str(user["id"]) not in room.split("_")[1:3]:
                room = "group"

        messages = conn.execute(
            "SELECT * FROM chat_messages WHERE room=? ORDER BY id ASC LIMIT 200",
            (room,)
        ).fetchall()
        messages = [dict(m) for m in messages]

        conn.execute(
            """INSERT INTO chat_reads (user_id, room, last_read_msg_id) VALUES (?, ?, ?)
               ON CONFLICT(user_id, room) DO UPDATE SET last_read_msg_id=excluded.last_read_msg_id""",
            (user["id"], room, messages[-1]["id"] if messages else 0)
        )

        # Per-contact unread + last message preview for the sidebar
        for c in contacts:
            r = dm_room(user["id"], c["id"])
            last_read = conn.execute(
                "SELECT last_read_msg_id FROM chat_reads WHERE user_id=? AND room=?",
                (user["id"], r)
            ).fetchone()
            last_read_id = last_read["last_read_msg_id"] if last_read else 0
            c["unread"] = conn.execute(
                "SELECT COUNT(*) FROM chat_messages WHERE room=? AND id>? AND sender_id!=?",
                (r, last_read_id, user["id"])
            ).fetchone()[0]
            c["room"] = r

    other_user = None
    if room.startswith("dm_"):
        other_id = [int(p) for p in room.split("_")[1:3] if int(p) != user["id"]][0]
        other_user = next((c for c in contacts if c["id"] == other_id), None)

    return templates.TemplateResponse(request, "shared/chat.html", {
        "user": user,
        "room": room,
        "messages": messages,
        "contacts": contacts,
        "other_user": other_user,
        "flash": get_flash(request),
        **shared_ctx(user, request),
    })


@app.post("/api/chat/send")
async def send_chat_message(request: Request, room: str = Form(...), body: str = Form(...)):
    user = require_user(request)
    body = body.strip()
    if not body:
        return JSONResponse({"ok": False, "error": "Empty message"}, status_code=400)
    if room != "group" and (not room.startswith("dm_") or str(user["id"]) not in room.split("_")[1:3]):
        return JSONResponse({"ok": False, "error": "Invalid room"}, status_code=403)

    with get_db() as conn:
        cur = conn.execute(
            "INSERT INTO chat_messages (room, sender_id, sender_name, body) VALUES (?, ?, ?, ?)",
            (room, user["id"], user["name"], body[:2000])
        )
        msg_id = cur.lastrowid
        conn.execute(
            """INSERT INTO chat_reads (user_id, room, last_read_msg_id) VALUES (?, ?, ?)
               ON CONFLICT(user_id, room) DO UPDATE SET last_read_msg_id=excluded.last_read_msg_id""",
            (user["id"], room, msg_id)
        )
        if room.startswith("dm_"):
            other_id = [int(p) for p in room.split("_")[1:3] if int(p) != user["id"]][0]
            push_notification(conn, other_id, f"💬 {user['name']}", body[:100], f"/chat?room={room}")

    return JSONResponse({"ok": True})


@app.get("/api/chat/poll")
async def poll_chat_messages(request: Request, room: str = "group", after_id: int = 0):
    user = require_user(request)
    if room != "group" and (not room.startswith("dm_") or str(user["id"]) not in room.split("_")[1:3]):
        return JSONResponse({"messages": []})

    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM chat_messages WHERE room=? AND id>? ORDER BY id ASC LIMIT 100",
            (room, after_id)
        ).fetchall()
        rows = [dict(r) for r in rows]
        if rows:
            conn.execute(
                """INSERT INTO chat_reads (user_id, room, last_read_msg_id) VALUES (?, ?, ?)
                   ON CONFLICT(user_id, room) DO UPDATE SET last_read_msg_id=excluded.last_read_msg_id""",
                (user["id"], room, rows[-1]["id"])
            )
    return JSONResponse({"messages": rows})


@app.get("/api/chat/contacts")
async def chat_contacts_api(request: Request):
    user = require_user(request)
    with get_db() as conn:
        contacts = conn.execute(
            "SELECT id, name, role FROM employees WHERE id!=? AND is_active=1 ORDER BY name",
            (user["id"],)
        ).fetchall()
        result = []
        for c in contacts:
            r = dm_room(user["id"], c["id"])
            last_read = conn.execute(
                "SELECT last_read_msg_id FROM chat_reads WHERE user_id=? AND room=?",
                (user["id"], r)
            ).fetchone()
            last_read_id = last_read["last_read_msg_id"] if last_read else 0
            unread = conn.execute(
                "SELECT COUNT(*) FROM chat_messages WHERE room=? AND id>? AND sender_id!=?",
                (r, last_read_id, user["id"])
            ).fetchone()[0]
            last_msg = conn.execute(
                "SELECT body, sent_at FROM chat_messages WHERE room=? ORDER BY id DESC LIMIT 1",
                (r,)
            ).fetchone()
            result.append({
                "id": c["id"],
                "name": c["name"],
                "initial": c["name"][0].upper() if c["name"] else "?",
                "room": r,
                "unread": unread,
                "last_msg": last_msg["body"][:60] if last_msg else "",
                "last_msg_at": last_msg["sent_at"] if last_msg else "",
            })
    # Sort: unread first, then most-recently-messaged, then A-Z for no-message contacts
    with_msgs    = sorted([c for c in result if c["last_msg_at"]],
                          key=lambda x: (-x["unread"], x["last_msg_at"]), reverse=False)
    with_msgs    = (
        sorted([c for c in with_msgs if c["unread"]],     key=lambda x: -x["unread"]) +
        sorted([c for c in with_msgs if not c["unread"]], key=lambda x: x["last_msg_at"], reverse=True)
    )
    without_msgs = sorted([c for c in result if not c["last_msg_at"]], key=lambda x: x["name"].lower())
    return JSONResponse({"contacts": with_msgs + without_msgs})


@app.get("/api/chat/stream")
async def stream_chat_messages(request: Request, room: str = "group", after_id: int = 0):
    user = require_user(request)
    if room != "group" and (not room.startswith("dm_") or str(user["id"]) not in room.split("_")[1:3]):
        return JSONResponse({"error": "forbidden"}, status_code=403)

    uid = user["id"]

    async def event_generator():
        last = after_id
        while True:
            if await request.is_disconnected():
                break
            with get_db() as conn:
                rows = conn.execute(
                    "SELECT * FROM chat_messages WHERE room=? AND id>? ORDER BY id ASC LIMIT 50",
                    (room, last)
                ).fetchall()
                rows = [dict(r) for r in rows]
                if rows:
                    conn.execute(
                        """INSERT INTO chat_reads (user_id, room, last_read_msg_id) VALUES (?, ?, ?)
                           ON CONFLICT(user_id, room) DO UPDATE SET last_read_msg_id=excluded.last_read_msg_id""",
                        (uid, room, rows[-1]["id"])
                    )
                    for m in rows:
                        last = m["id"]
                        yield f"id: {m['id']}\ndata: {json.dumps(m)}\n\n"
            await asyncio.sleep(1)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


# ══════════════════════════════════════════════════════════════════════════════
# ── MY TIMESHEET ──────────────────────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/my-timesheet", response_class=HTMLResponse)
async def my_timesheet(request: Request, week: str = None):
    user = require_user(request)
    from datetime import date as date_type
    try:
        ref = date_type.fromisoformat(week) if week else date_type.today()
    except ValueError:
        ref = date_type.today()

    week_start, week_end = get_week_range(ref)
    ws = date_type.fromisoformat(week_start)
    prev_week = (ws - timedelta(days=7)).strftime("%Y-%m-%d")
    next_week = (ws + timedelta(days=7)).strftime("%Y-%m-%d")
    days = [(ws + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(6)]

    with get_db() as conn:
        submission = conn.execute(
            "SELECT * FROM timesheet_submissions WHERE emp_id=? AND week_start=?",
            (user["id"], week_start)
        ).fetchone()
        submission = dict(submission) if submission else None

        att_records = conn.execute(
            "SELECT * FROM attendance WHERE emp_id=? AND date_logged BETWEEN ? AND ?",
            (user["id"], week_start, week_end)
        ).fetchall()
        att_by_date = {r["date_logged"]: dict(r) for r in att_records}

        leaves = conn.execute(
            """SELECT * FROM leave_requests
               WHERE emp_id=? AND status='Approved'
               AND start_date <= ? AND end_date >= ?""",
            (user["id"], week_end, week_start)
        ).fetchall()

        ot_records = conn.execute(
            """SELECT * FROM overtime_requests
               WHERE emp_id=? AND status='Approved'
               AND ot_date BETWEEN ? AND ?""",
            (user["id"], week_start, week_end)
        ).fetchall()
        ot_by_date = {r["ot_date"]: dict(r) for r in ot_records}

        entries_by_date = {}
        if submission:
            entries = conn.execute(
                "SELECT * FROM timesheet_entries WHERE submission_id=? ORDER BY date",
                (submission["id"],)
            ).fetchall()
            entries_by_date = {e["date"]: dict(e) for e in entries}

    # Expand leave ranges to individual dates
    leave_by_date = {}
    for lv in leaves:
        lv = dict(lv)
        start = date_type.fromisoformat(lv["start_date"])
        end = date_type.fromisoformat(lv["end_date"])
        cur = start
        while cur <= end:
            ds = cur.strftime("%Y-%m-%d")
            if ds in days:
                leave_by_date[ds] = lv
            cur += timedelta(days=1)

    rows = []
    for d in days:
        att = att_by_date.get(d)
        entry = entries_by_date.get(d)
        lv = leave_by_date.get(d)
        ot = ot_by_date.get(d)

        computed = 0.0
        if att and att["clock_in"] and att["clock_out"]:
            raw = calculate_hours(att["clock_in"], att["clock_out"])
            break_mins = get_break_minutes(att["id"])
            computed = max(0.0, round(raw - break_mins / 60, 2))

        leave_hours = 8.0 if lv else 0.0
        ot_hours_approved = round(ot["hours_computed"], 2) if ot else 0.0

        if entry:
            manual_hours = entry["manual_hours"]
            ot_hours = entry["ot_hours"]
        else:
            manual_hours = 0.0 if lv else min(computed, 8.0)
            ot_hours = ot_hours_approved

        rows.append({
            "date": d,
            "day_name": date_type.fromisoformat(d).strftime("%A"),
            "att": att,
            "computed": computed,
            "manual_hours": round(manual_hours, 2),
            "ot_hours": round(ot_hours, 2),
            "ot_hours_approved": ot_hours_approved,
            "ot_approved": ot is not None,
            "leave": lv,
            "leave_hours": leave_hours,
            "total": round(manual_hours + ot_hours + leave_hours, 2),
            "is_weekend": date_type.fromisoformat(d).weekday() == 5,
        })

    total_computed = round(sum(r["computed"] for r in rows), 2)
    total_manual  = round(sum(r["manual_hours"] for r in rows), 2)
    total_ot      = round(sum(r["ot_hours"] for r in rows), 2)
    total_leave   = round(sum(r["leave_hours"] for r in rows), 2)

    return templates.TemplateResponse(request, "employee/my_timesheet.html", {
        "user": user,
        "week_start": week_start,
        "week_end": week_end,
        "prev_week": prev_week,
        "next_week": next_week,
        "rows": rows,
        "submission": submission,
        "total_computed": total_computed,
        "total_manual": total_manual,
        "total_ot": total_ot,
        "total_leave": total_leave,
        "flash": get_flash(request),
        **shared_ctx(user, request),
    })


@app.post("/my-timesheet/save")
async def timesheet_save(request: Request):
    user = require_user(request)
    form = await request.form()
    week_start = form.get("week_start", "")
    week_end   = form.get("week_end", "")
    action     = form.get("action", "save")
    from datetime import date as date_type

    try:
        ws = date_type.fromisoformat(week_start)
    except ValueError:
        flash(request, "Invalid week.", "error")
        return RedirectResponse("/my-timesheet", status_code=302)

    with get_db() as conn:
        existing = conn.execute(
            "SELECT id, status FROM timesheet_submissions WHERE emp_id=? AND week_start=?",
            (user["id"], week_start)
        ).fetchone()

        if existing and existing["status"] == "Approved":
            flash(request, "This timesheet has already been approved and cannot be changed.", "error")
            return RedirectResponse(f"/my-timesheet?week={week_start}", status_code=302)

        new_status = "Submitted" if action == "submit" else "Draft"
        now_str = get_pht_now().strftime("%Y-%m-%d %H:%M:%S")

        if existing:
            sub_id = existing["id"]
            conn.execute(
                "UPDATE timesheet_submissions SET status=?, submitted_at=? WHERE id=?",
                (new_status, now_str if action == "submit" else None, sub_id)
            )
        else:
            conn.execute(
                "INSERT INTO timesheet_submissions (emp_id, week_start, week_end, status, submitted_at) VALUES (?,?,?,?,?)",
                (user["id"], week_start, week_end, new_status,
                 now_str if action == "submit" else None)
            )
            sub_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

        conn.execute("DELETE FROM timesheet_entries WHERE submission_id=?", (sub_id,))

        for i in range(6):
            d = (ws + timedelta(days=i)).strftime("%Y-%m-%d")
            try:
                manual_h = max(0.0, float(form.get(f"manual_{d}") or 0))
                ot_h     = max(0.0, float(form.get(f"ot_{d}") or 0))
            except ValueError:
                manual_h = 0.0
                ot_h = 0.0

            att = conn.execute(
                "SELECT * FROM attendance WHERE emp_id=? AND date_logged=?",
                (user["id"], d)
            ).fetchone()
            time_in  = att["clock_in"][:5]  if att and att["clock_in"]  else None
            time_out = att["clock_out"][:5] if att and att["clock_out"] else None
            computed = 0.0
            if att and att["clock_in"] and att["clock_out"]:
                raw = calculate_hours(att["clock_in"], att["clock_out"])
                break_mins = get_break_minutes(att["id"])
                computed = max(0.0, round(raw - break_mins / 60, 2))

            lv = conn.execute(
                """SELECT leave_type FROM leave_requests
                   WHERE emp_id=? AND status='Approved' AND start_date<=? AND end_date>=?""",
                (user["id"], d, d)
            ).fetchone()
            leave_hours = 8.0 if lv else 0.0
            leave_type  = lv["leave_type"] if lv else None

            ot_ok = conn.execute(
                "SELECT id FROM overtime_requests WHERE emp_id=? AND ot_date=? AND status='Approved'",
                (user["id"], d)
            ).fetchone()

            conn.execute(
                """INSERT INTO timesheet_entries
                   (submission_id, date, time_in, time_out, computed_hours,
                    manual_hours, ot_hours, ot_approved, leave_hours, leave_type, total_hours)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (sub_id, d, time_in, time_out, computed,
                 manual_h, ot_h, 1 if ot_ok else 0,
                 leave_hours, leave_type, round(manual_h + ot_h + leave_hours, 2))
            )

        if action == "submit":
            hr_staff = conn.execute(
                "SELECT id FROM employees WHERE role IN ('HR Manager','Admin') AND is_active=1"
            ).fetchall()
            from datetime import datetime as dt_cls
            ws_fmt = dt_cls.strptime(week_start, "%Y-%m-%d").strftime("%b %d")
            we_fmt = dt_cls.strptime(week_end,   "%Y-%m-%d").strftime("%b %d, %Y")
            for hr in hr_staff:
                push_notification(conn, hr["id"],
                    "Timesheet Submitted",
                    f"{user['name']} submitted their timesheet for {ws_fmt}–{we_fmt}",
                    "/hr-timesheets")
            audit(conn, user["id"], user["name"], "timesheet_submit",
                  "timesheet_submissions", sub_id)
            flash(request, "Timesheet submitted for HR approval.")
        else:
            flash(request, "Timesheet draft saved.")

    return RedirectResponse(f"/my-timesheet?week={week_start}", status_code=302)


# ══════════════════════════════════════════════════════════════════════════════
# ── HR TIMESHEETS ─────────────────────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/hr-timesheets", response_class=HTMLResponse)
async def hr_timesheets(request: Request, status: str = "Submitted", week: str = ""):
    user = require_role(request, "HR Manager", "Admin")
    valid_statuses = ("Submitted", "Approved", "Rejected", "Draft", "Not Submitted")
    if status not in valid_statuses:
        status = "Submitted"

    # Resolve week for "Not Submitted" tab
    current_week_start, _ = get_week_range()
    if not week:
        week = current_week_start
    try:
        _ref = datetime.strptime(week, "%Y-%m-%d").date()
    except ValueError:
        week = current_week_start
        _ref = datetime.strptime(week, "%Y-%m-%d").date()
    prev_week = (_ref - timedelta(days=7)).isoformat()
    next_week = (_ref + timedelta(days=7)).isoformat()

    subs = []
    missing_employees = []

    with get_db() as conn:
        if status == "Not Submitted":
            # Employees who have no submission for the selected week
            all_emps = conn.execute(
                "SELECT id, name, profile_pic_path, shift_type FROM employees WHERE role='Employee' AND is_active=1 ORDER BY name"
            ).fetchall()
            submitted_ids = {
                r[0] for r in conn.execute(
                    "SELECT emp_id FROM timesheet_submissions WHERE week_start=?", (week,)
                ).fetchall()
            }
            missing_employees = [dict(e) for e in all_emps if e["id"] not in submitted_ids]
        else:
            subs = conn.execute(
                """SELECT ts.*, e.name as emp_name, e.profile_pic_path
                   FROM timesheet_submissions ts
                   JOIN employees e ON ts.emp_id = e.id
                   WHERE ts.status = ?
                   ORDER BY ts.submitted_at DESC, ts.created_at DESC""",
                (status,)
            ).fetchall()
            subs = [dict(s) for s in subs]

            for sub in subs:
                entries = conn.execute(
                    "SELECT * FROM timesheet_entries WHERE submission_id=? ORDER BY date",
                    (sub["id"],)
                ).fetchall()
                sub["entries"] = [dict(e) for e in entries]
                sub["total_manual"] = round(sum(e["manual_hours"] for e in sub["entries"]), 2)
                sub["total_ot"]     = round(sum(e["ot_hours"]     for e in sub["entries"]), 2)
                sub["total_leave"]  = round(sum(e["leave_hours"]  for e in sub["entries"]), 2)
                sub["has_unapproved_ot"] = any(
                    e["ot_hours"] > 0 and not e["ot_approved"] for e in sub["entries"]
                )

        counts = {}
        for s in ("Submitted", "Approved", "Rejected", "Draft"):
            counts[s] = conn.execute(
                "SELECT COUNT(*) FROM timesheet_submissions WHERE status=?", (s,)
            ).fetchone()[0]

        total_employees = conn.execute(
            "SELECT COUNT(*) FROM employees WHERE role='Employee' AND is_active=1"
        ).fetchone()[0]
        submitted_this_week = conn.execute(
            "SELECT COUNT(*) FROM timesheet_submissions WHERE week_start=?", (week,)
        ).fetchone()[0]
        counts["Not Submitted"] = max(0, total_employees - submitted_this_week)

    return templates.TemplateResponse(request, "shared/hr_timesheets.html", {
        "user": user,
        "subs": subs,
        "missing_employees": missing_employees,
        "filter_status": status,
        "counts": counts,
        "week": week,
        "prev_week": prev_week,
        "next_week": next_week,
        "total_employees": total_employees,
        "submitted_this_week": submitted_this_week,
        "flash": get_flash(request),
        **shared_ctx(user, request),
    })


@app.post("/hr-timesheets/{sub_id}/approve")
async def hr_approve_timesheet(request: Request, sub_id: int):
    user = require_role(request, "HR Manager", "Admin")
    with get_db() as conn:
        sub = conn.execute(
            "SELECT * FROM timesheet_submissions WHERE id=?", (sub_id,)
        ).fetchone()
        if not sub:
            flash(request, "Timesheet not found.", "error")
            return RedirectResponse("/hr-timesheets", status_code=302)
        conn.execute(
            """UPDATE timesheet_submissions
               SET status='Approved', reviewed_by=?, reviewed_at=? WHERE id=?""",
            (user["name"], get_pht_now().strftime("%Y-%m-%d %H:%M:%S"), sub_id)
        )
        push_notification(conn, sub["emp_id"],
            "Timesheet Approved",
            f"Your timesheet for the week of {sub['week_start']} was approved by {user['name']}.",
            f"/my-timesheet?week={sub['week_start']}")
        audit(conn, user["id"], user["name"], "timesheet_approve",
              "timesheet_submissions", sub_id)
    flash(request, "Timesheet approved.")
    return RedirectResponse("/hr-timesheets", status_code=302)


@app.post("/hr-timesheets/{sub_id}/reject")
async def hr_reject_timesheet(request: Request, sub_id: int, hr_notes: str = Form("")):
    user = require_role(request, "HR Manager", "Admin")
    with get_db() as conn:
        sub = conn.execute(
            "SELECT * FROM timesheet_submissions WHERE id=?", (sub_id,)
        ).fetchone()
        if not sub:
            flash(request, "Timesheet not found.", "error")
            return RedirectResponse("/hr-timesheets", status_code=302)
        conn.execute(
            """UPDATE timesheet_submissions
               SET status='Rejected', reviewed_by=?, reviewed_at=?, hr_notes=? WHERE id=?""",
            (user["name"], get_pht_now().strftime("%Y-%m-%d %H:%M:%S"),
             hr_notes.strip() or None, sub_id)
        )
        push_notification(conn, sub["emp_id"],
            "Timesheet Returned",
            f"Your timesheet for {sub['week_start']} was returned: {hr_notes.strip() or 'Please review and resubmit.'}",
            f"/my-timesheet?week={sub['week_start']}")
        audit(conn, user["id"], user["name"], "timesheet_reject",
              "timesheet_submissions", sub_id)
    flash(request, "Timesheet returned to employee.")
    return RedirectResponse("/hr-timesheets", status_code=302)


# ══════════════════════════════════════════════════════════════════════════════
# ── MY PAYSLIP (Employee self-service) ────────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/my-payslip", response_class=HTMLResponse)
async def my_payslip(request: Request):
    user = require_role(request, "HR Manager", "Admin")
    with get_db() as conn:
        runs = conn.execute(
            "SELECT * FROM payroll_runs WHERE emp_id=? ORDER BY week_start DESC LIMIT 24",
            (user["id"],)
        ).fetchall()
    return templates.TemplateResponse(request, "employee/my_payslip.html", {
        "user": user,
        "runs": [dict(r) for r in runs],
        "flash": get_flash(request),
        **shared_ctx(user, request),
    })


# ══════════════════════════════════════════════════════════════════════════════
# ── PASSWORD CHANGE ───────────────────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════

@app.post("/api/profile/change-password")
async def change_password(
    request: Request,
    current_password: str = Form(...),
    new_password: str = Form(...),
    confirm_password: str = Form(...),
):
    user = require_user(request)
    if not verify_password(current_password, user["password"]):
        flash(request, "Current password is incorrect.", "error")
        return RedirectResponse("/profile", status_code=302)
    if new_password != confirm_password:
        flash(request, "New passwords do not match.", "error")
        return RedirectResponse("/profile", status_code=302)
    if len(new_password) < 6:
        flash(request, "Password must be at least 6 characters.", "error")
        return RedirectResponse("/profile", status_code=302)
    with get_db() as conn:
        conn.execute("UPDATE employees SET password=? WHERE id=?",
                     (hash_password(new_password), user["id"]))
        audit(conn, user["id"], user["name"], "change_password", "employees", user["id"])
    flash(request, "Password updated successfully.")
    return RedirectResponse("/profile", status_code=302)


@app.post("/api/profile/gov-ids")
async def update_gov_ids(
    request: Request,
    sss_no: str = Form(""),
    philhealth_no: str = Form(""),
    tin_no: str = Form(""),
    pagibig_no: str = Form(""),
    bank_name: str = Form(""),
    bank_account: str = Form(""),
):
    user = require_user(request)
    with get_db() as conn:
        conn.execute(
            """UPDATE employees SET
               sss_no=?, philhealth_no=?, tin_no=?, pagibig_no=?,
               bank_name=?, bank_account=? WHERE id=?""",
            (sss_no.strip(), philhealth_no.strip(), tin_no.strip(), pagibig_no.strip(),
             bank_name.strip(), bank_account.strip(), user["id"]),
        )
    flash(request, "Government IDs updated.")
    return RedirectResponse("/profile", status_code=302)


# ══════════════════════════════════════════════════════════════════════════════
# ── ADMIN: RESET EMPLOYEE PASSWORD ───────────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════

@app.post("/api/employees/{emp_id}/reset-password")
async def reset_employee_password(
    request: Request,
    emp_id: int,
    new_password: str = Form(...),
    confirm_password: str = Form(...),
):
    user = require_role(request, "Admin")
    if new_password != confirm_password:
        flash(request, "Passwords do not match.", "error")
        return RedirectResponse(f"/employees/{emp_id}/profile", status_code=302)
    if len(new_password) < 6:
        flash(request, "Password must be at least 6 characters.", "error")
        return RedirectResponse(f"/employees/{emp_id}/profile", status_code=302)
    with get_db() as conn:
        conn.execute("UPDATE employees SET password=? WHERE id=?",
                     (hash_password(new_password), emp_id))
        audit(conn, user["id"], user["name"], "reset_password", "employees", emp_id)
    flash(request, "Password reset successfully.")
    return RedirectResponse(f"/employees/{emp_id}/profile", status_code=302)


# ══════════════════════════════════════════════════════════════════════════════
# ── GOVERNMENT DEDUCTIONS ENROLLMENT ─────────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════

@app.post("/api/employees/{emp_id}/deductions")
async def update_deductions(
    request: Request,
    emp_id: int,
    sss_enrolled: str = Form("0"),
    philhealth_enrolled: str = Form("0"),
    pagibig_enrolled: str = Form("0"),
    tax_enrolled: str = Form("0"),
    sss_no: str = Form(""),
    philhealth_no: str = Form(""),
    tin_no: str = Form(""),
    pagibig_no: str = Form(""),
    bank_name: str = Form(""),
    bank_account: str = Form(""),
):
    user = require_role(request, "HR Manager", "Admin")
    with get_db() as conn:
        conn.execute(
            """UPDATE employees SET
               sss_enrolled=?, philhealth_enrolled=?, pagibig_enrolled=?, tax_enrolled=?,
               sss_no=?, philhealth_no=?, tin_no=?, pagibig_no=?,
               bank_name=?, bank_account=?
               WHERE id=?""",
            (
                1 if sss_enrolled == "1" else 0,
                1 if philhealth_enrolled == "1" else 0,
                1 if pagibig_enrolled == "1" else 0,
                1 if tax_enrolled == "1" else 0,
                sss_no.strip(), philhealth_no.strip(), tin_no.strip(), pagibig_no.strip(),
                bank_name.strip(), bank_account.strip(),
                emp_id,
            )
        )
        audit(conn, user["id"], user["name"], "update_deductions", "employees", emp_id)
    flash(request, "Deduction settings updated.")
    return RedirectResponse(f"/employees/{emp_id}/profile", status_code=302)


@app.post("/api/employees/{emp_id}/leave-allowance")
async def update_leave_allowance(
    request: Request,
    emp_id: int,
    vl_enabled: str = Form("0"),
    sl_enabled: str = Form("0"),
    vl_days_per_year: int = Form(15),
    sl_days_per_year: int = Form(15),
):
    user = require_role(request, "HR Manager", "Admin")
    vl_days_per_year = max(0, min(365, vl_days_per_year))
    sl_days_per_year = max(0, min(365, sl_days_per_year))
    with get_db() as conn:
        conn.execute(
            """UPDATE employees SET
               vl_enabled=?, sl_enabled=?,
               vl_days_per_year=?, sl_days_per_year=?
               WHERE id=?""",
            (
                1 if vl_enabled == "1" else 0,
                1 if sl_enabled == "1" else 0,
                vl_days_per_year,
                sl_days_per_year,
                emp_id,
            )
        )
        audit(conn, user["id"], user["name"], "update_leave_allowance", "employees", emp_id)
    flash(request, "Leave allowance updated.")
    return RedirectResponse(f"/employees/{emp_id}/profile", status_code=302)


# ══════════════════════════════════════════════════════════════════════════════
# ── PRINT / REPORT VIEWS ──────────────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/print/employee/{emp_id}", response_class=HTMLResponse)
async def print_employee_profile(request: Request, emp_id: int):
    user = require_role(request, "HR Manager", "Admin")
    now_pht = get_pht_now()
    year_start = now_pht.strftime("%Y-01-01")
    with get_db() as conn:
        emp = conn.execute("SELECT * FROM employees WHERE id=?", (emp_id,)).fetchone()
        if not emp:
            return HTMLResponse("Employee not found", status_code=404)
        emp = dict(emp)
        vl_used = conn.execute(
            "SELECT COALESCE(SUM(days_count),0) FROM leave_requests WHERE emp_id=? AND leave_type='Vacation Leave' AND status='Approved' AND start_date>=?",
            (emp_id, year_start)
        ).fetchone()[0]
        sl_used = conn.execute(
            "SELECT COALESCE(SUM(days_count),0) FROM leave_requests WHERE emp_id=? AND leave_type='Sick Leave' AND status='Approved' AND start_date>=?",
            (emp_id, year_start)
        ).fetchone()[0]
    vl_total = int(emp.get("vl_days_per_year") or 15)
    sl_total = int(emp.get("sl_days_per_year") or 15)
    return templates.TemplateResponse(request, "print/employee_profile.html", {
        "emp": emp,
        "vl_used": int(vl_used or 0), "sl_used": int(sl_used or 0),
        "vl_balance": max(0, vl_total - int(vl_used or 0)),
        "sl_balance": max(0, sl_total - int(sl_used or 0)),
        "now": now_pht.strftime("%B %d, %Y %I:%M %p"),
        "user": user,
    })


@app.get("/print/payslip/{payslip_id}", response_class=HTMLResponse)
async def print_payslip(request: Request, payslip_id: int):
    user = require_role(request, "HR Manager", "Admin")
    with get_db() as conn:
        ps = conn.execute(
            """SELECT pl.*, e.name AS emp_name, e.position, e.bank_name, e.bank_account,
                      e.bank_account_name, e.bank_qr_path, e.hourly_rate
               FROM payslip_logs pl
               JOIN employees e ON e.id = pl.emp_id
               WHERE pl.id=?""",
            (payslip_id,)
        ).fetchone()
        if not ps:
            return HTMLResponse("Payslip not found", status_code=404)
        pr = conn.execute(
            "SELECT * FROM payroll_runs WHERE id=?",
            (ps["payroll_run_id"],)
        ).fetchone() if ps["payroll_run_id"] else None
        conn.execute(
            "UPDATE payslip_logs SET printed_by=?, printed_at=datetime('now','+8 hours') WHERE id=?",
            (user["name"], payslip_id)
        )
    return templates.TemplateResponse(request, "print/payslip.html", {
        "ps": dict(ps),
        "pr": dict(pr) if pr else {},
        "now": get_pht_now().strftime("%B %d, %Y %I:%M %p"),
        "user": user,
    })


@app.get("/print/payment-qr", response_class=HTMLResponse)
async def print_payment_qr(request: Request, week_start: str = "", week_end: str = ""):
    user = require_role(request, "HR Manager", "Admin")
    with get_db() as conn:
        if week_start and week_end:
            runs = conn.execute(
                """SELECT pr.*, e.bank_name, e.bank_account, e.bank_account_name, e.bank_qr_path
                   FROM payroll_runs pr
                   JOIN employees e ON e.id = pr.emp_id
                   WHERE pr.week_start=? AND pr.week_end=? AND pr.status='Approved'
                   ORDER BY pr.emp_name ASC""",
                (week_start, week_end)
            ).fetchall()
        else:
            latest = conn.execute(
                "SELECT week_start, week_end FROM payroll_runs WHERE status='Approved' ORDER BY week_start DESC LIMIT 1"
            ).fetchone()
            if latest:
                week_start, week_end = latest["week_start"], latest["week_end"]
                runs = conn.execute(
                    """SELECT pr.*, e.bank_name, e.bank_account, e.bank_account_name, e.bank_qr_path
                       FROM payroll_runs pr
                       JOIN employees e ON e.id = pr.emp_id
                       WHERE pr.week_start=? AND pr.week_end=? AND pr.status='Approved'
                       ORDER BY pr.emp_name ASC""",
                    (week_start, week_end)
                ).fetchall()
            else:
                runs = []
    return templates.TemplateResponse(request, "print/payment_qr.html", {
        "runs": [dict(r) for r in runs],
        "week_start": week_start,
        "week_end": week_end,
        "now": get_pht_now().strftime("%B %d, %Y %I:%M %p"),
        "user": user,
    })


@app.get("/print/roster", response_class=HTMLResponse)
async def print_roster(request: Request):
    user = require_role(request, "HR Manager", "Admin")
    with get_db() as conn:
        employees = conn.execute(
            "SELECT * FROM employees ORDER BY is_active DESC, name ASC"
        ).fetchall()
    return templates.TemplateResponse(request, "print/roster.html", {
        "employees": [dict(e) for e in employees],
        "now": get_pht_now().strftime("%B %d, %Y %I:%M %p"),
        "user": user,
    })


@app.get("/print/payroll", response_class=HTMLResponse)
async def print_payroll(request: Request, week_start: str = "", week_end: str = ""):
    user = require_role(request, "HR Manager", "Admin")
    with get_db() as conn:
        if week_start and week_end:
            rows = conn.execute(
                "SELECT * FROM payroll_runs WHERE week_start=? AND week_end=? ORDER BY emp_name",
                (week_start, week_end)
            ).fetchall()
        else:
            # Most recent pay period
            latest = conn.execute(
                "SELECT week_start, week_end FROM payroll_runs ORDER BY week_start DESC LIMIT 1"
            ).fetchone()
            if latest:
                week_start, week_end = latest["week_start"], latest["week_end"]
                rows = conn.execute(
                    "SELECT * FROM payroll_runs WHERE week_start=? AND week_end=? ORDER BY emp_name",
                    (week_start, week_end)
                ).fetchall()
            else:
                rows = []
    payroll = [dict(r) for r in rows]
    total_gross = sum(p.get("gross_pay") or p.get("total_pay", 0) for p in payroll)
    total_sss   = sum(p.get("sss_deduction", 0) for p in payroll)
    total_phic  = sum(p.get("philhealth_deduction", 0) for p in payroll)
    total_hdmf  = sum(p.get("pagibig_deduction", 0) for p in payroll)
    total_tax   = sum(p.get("tax_deduction", 0) for p in payroll)
    total_ded   = sum(p.get("total_deductions", 0) for p in payroll)
    total_net   = sum(p.get("net_pay") or p.get("gross_pay") or p.get("total_pay", 0) for p in payroll)
    return templates.TemplateResponse(request, "print/payroll.html", {
        "payroll": payroll,
        "week_start": week_start, "week_end": week_end,
        "total_gross": round(total_gross, 2), "total_sss": round(total_sss, 2),
        "total_phic": round(total_phic, 2), "total_hdmf": round(total_hdmf, 2),
        "total_tax": round(total_tax, 2), "total_ded": round(total_ded, 2),
        "total_net": round(total_net, 2),
        "now": get_pht_now().strftime("%B %d, %Y %I:%M %p"),
        "user": user,
    })


@app.get("/print/leave", response_class=HTMLResponse)
async def print_leave_report(request: Request, year: str = ""):
    user = require_role(request, "HR Manager", "Admin")
    now_pht = get_pht_now()
    year = year or now_pht.strftime("%Y")
    year_start = f"{year}-01-01"
    year_end   = f"{year}-12-31"
    with get_db() as conn:
        requests = conn.execute(
            """SELECT lr.*, e.name as emp_name FROM leave_requests lr
               JOIN employees e ON e.id = lr.emp_id
               WHERE lr.start_date >= ? AND lr.start_date <= ?
               ORDER BY e.name, lr.start_date""",
            (year_start, year_end)
        ).fetchall()
        employees = conn.execute(
            "SELECT id, name, vl_days_per_year, sl_days_per_year FROM employees WHERE is_active=1 ORDER BY name"
        ).fetchall()
        # Build balance map
        balances = []
        for e in employees:
            vl_used = conn.execute(
                "SELECT COALESCE(SUM(days_count),0) FROM leave_requests WHERE emp_id=? AND leave_type='Vacation Leave' AND status='Approved' AND start_date>=?",
                (e["id"], year_start)
            ).fetchone()[0]
            sl_used = conn.execute(
                "SELECT COALESCE(SUM(days_count),0) FROM leave_requests WHERE emp_id=? AND leave_type='Sick Leave' AND status='Approved' AND start_date>=?",
                (e["id"], year_start)
            ).fetchone()[0]
            vl_total = int(e["vl_days_per_year"] or 15)
            sl_total = int(e["sl_days_per_year"] or 15)
            balances.append({
                "name": e["name"],
                "vl_total": vl_total, "vl_used": int(vl_used or 0), "vl_balance": max(0, vl_total - int(vl_used or 0)),
                "sl_total": sl_total, "sl_used": int(sl_used or 0), "sl_balance": max(0, sl_total - int(sl_used or 0)),
            })
    reqs = [dict(r) for r in requests]
    approved = [r for r in reqs if r["status"] == "Approved"]
    return templates.TemplateResponse(request, "print/leave_report.html", {
        "requests": reqs,
        "balances": balances,
        "year": year,
        "total_approved_days": sum(r["days_count"] for r in approved),
        "now": now_pht.strftime("%B %d, %Y %I:%M %p"),
        "user": user,
    })


@app.get("/print/attendance", response_class=HTMLResponse)
async def print_attendance_report(request: Request, date_from: str = "", date_to: str = ""):
    user = require_role(request, "HR Manager", "Admin")
    now_pht = get_pht_now()
    if not date_from:
        date_from = now_pht.replace(day=1).strftime("%Y-%m-%d")
    if not date_to:
        date_to = now_pht.strftime("%Y-%m-%d")
    with get_db() as conn:
        rows = conn.execute(
            """SELECT a.*, e.name as emp_name, e.shift_type,
                      ROUND(
                        (JULIANDAY(COALESCE(a.clock_out, datetime('now','+8 hours'))) - JULIANDAY(a.date || ' ' || a.clock_in)) * 24
                        - COALESCE((SELECT SUM(ROUND((JULIANDAY(clock_out) - JULIANDAY(clock_in))*24*60)/60.0) FROM break_logs bl WHERE bl.att_id=a.id AND bl.clock_out IS NOT NULL), 0)
                      , 2) as hours
               FROM attendance a JOIN employees e ON e.id=a.emp_id
               WHERE a.date >= ? AND a.date <= ? AND a.clock_in IS NOT NULL
               ORDER BY e.name, a.date""",
            (date_from, date_to)
        ).fetchall()
    records = [dict(r) for r in rows]
    total_hours = sum(r.get("hours") or 0 for r in records)
    avg_hours = total_hours / len(records) if records else 0
    return templates.TemplateResponse(request, "print/attendance_report.html", {
        "records": records,
        "date_from": date_from, "date_to": date_to,
        "total_hours": round(total_hours, 1),
        "avg_hours": round(avg_hours, 1),
        "now": now_pht.strftime("%B %d, %Y %I:%M %p"),
        "user": user,
    })


# ══════════════════════════════════════════════════════════════════════════════
# ── TV POSTER MANAGEMENT ──────────────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/tv-manage", response_class=HTMLResponse)
async def tv_manage_page(request: Request):
    user = require_role(request, "HR Manager", "Admin")
    with get_db() as conn:
        posters = conn.execute(
            "SELECT * FROM tv_posters ORDER BY display_order, id"
        ).fetchall()
    return templates.TemplateResponse(request, "shared/tv_manage.html", {
        "user": user,
        "posters": [dict(p) for p in posters],
        "flash": get_flash(request),
        **shared_ctx(user, request),
    })


@app.post("/api/tv/posters/upload")
async def upload_poster(
    request: Request,
    caption: str = Form(""),
    duration_secs: int = Form(8),
    file: UploadFile = File(...),
):
    user = require_role(request, "HR Manager", "Admin")
    allowed = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
    ext = Path(file.filename).suffix.lower()
    if ext not in allowed:
        flash(request, "Only PNG, JPG, GIF, WEBP files allowed.", "error")
        return RedirectResponse("/tv-manage", status_code=302)
    content = await file.read()
    if len(content) > _MAX_POSTER_BYTES:
        flash(request, "Poster too large. Maximum is 20 MB.", "error")
        return RedirectResponse("/tv-manage", status_code=302)
    fname = f"{uuid.uuid4().hex}{ext}"
    dest = Path(UPLOAD_DIR) / "posters" / fname
    with open(dest, "wb") as f:
        f.write(content)
    with get_db() as conn:
        max_order = conn.execute("SELECT MAX(display_order) FROM tv_posters").fetchone()[0] or 0
        conn.execute(
            "INSERT INTO tv_posters (filename, caption, display_order, duration_secs, uploaded_by) VALUES (?,?,?,?,?)",
            (fname, caption.strip(), max_order + 1, max(3, min(60, duration_secs)), user["id"]),
        )
    flash(request, "Poster uploaded.")
    return RedirectResponse("/tv-manage", status_code=302)


@app.post("/api/tv/posters/{poster_id}/toggle")
async def toggle_poster(request: Request, poster_id: int):
    require_role(request, "HR Manager", "Admin")
    with get_db() as conn:
        p = conn.execute("SELECT is_active FROM tv_posters WHERE id=?", (poster_id,)).fetchone()
        if p:
            conn.execute("UPDATE tv_posters SET is_active=? WHERE id=?",
                         (0 if p["is_active"] else 1, poster_id))
    return RedirectResponse("/tv-manage", status_code=302)


@app.post("/api/tv/posters/{poster_id}/delete")
async def delete_poster(request: Request, poster_id: int):
    require_role(request, "HR Manager", "Admin")
    with get_db() as conn:
        p = conn.execute("SELECT filename FROM tv_posters WHERE id=?", (poster_id,)).fetchone()
        if p:
            try:
                (Path(UPLOAD_DIR) / "posters" / p["filename"]).unlink(missing_ok=True)
            except Exception:
                pass
            conn.execute("DELETE FROM tv_posters WHERE id=?", (poster_id,))
    flash(request, "Poster deleted.")
    return RedirectResponse("/tv-manage", status_code=302)


@app.get("/api/tv/posters")
async def get_tv_posters():
    with get_db() as conn:
        posters = conn.execute(
            "SELECT * FROM tv_posters WHERE is_active=1 ORDER BY display_order, id"
        ).fetchall()
    return JSONResponse({"posters": [dict(p) for p in posters]})


# ══════════════════════════════════════════════════════════════════════════════
# ── REPORTS ───────────────────────────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/reports", response_class=HTMLResponse)
async def reports_landing(request: Request):
    user = require_role(request, "HR Manager", "Admin")
    return templates.TemplateResponse(request, "shared/reports.html", {
        "user": user,
        **shared_ctx(user, request),
    })


@app.get("/reports/employees", response_class=HTMLResponse)
async def report_employees(request: Request):
    user = require_role(request, "HR Manager", "Admin")
    with get_db() as conn:
        employees = conn.execute(
            """SELECT * FROM employees WHERE is_active=1 AND role='Employee' ORDER BY name"""
        ).fetchall()
    return templates.TemplateResponse(request, "shared/report_employees.html", {
        "user": user,
        "employees": [dict(e) for e in employees],
        "now_str": get_pht_now().strftime("%B %d, %Y %I:%M %p"),
        **shared_ctx(user, request),
    })


@app.get("/reports/attendance", response_class=HTMLResponse)
async def report_attendance(request: Request, date_from: str = "", date_to: str = ""):
    user = require_role(request, "HR Manager", "Admin")
    today = get_pht_now().strftime("%Y-%m-%d")
    d_from = date_from or (get_pht_now().replace(day=1).strftime("%Y-%m-%d"))
    d_to   = date_to or today
    with get_db() as conn:
        records = conn.execute(
            """SELECT a.*, e.name as emp_name
               FROM attendance a JOIN employees e ON a.emp_id=e.id
               WHERE e.role='Employee' AND a.date_logged BETWEEN ? AND ?
               ORDER BY a.date_logged DESC, e.name""",
            (d_from, d_to)
        ).fetchall()
    return templates.TemplateResponse(request, "shared/report_attendance.html", {
        "user": user,
        "records": [dict(r) for r in records],
        "date_from": d_from, "date_to": d_to,
        "now_str": get_pht_now().strftime("%B %d, %Y %I:%M %p"),
        **shared_ctx(user, request),
    })


@app.get("/reports/ot", response_class=HTMLResponse)
async def report_ot(request: Request, date_from: str = "", date_to: str = ""):
    user = require_role(request, "HR Manager", "Admin")
    today = get_pht_now().strftime("%Y-%m-%d")
    d_from = date_from or (get_pht_now().replace(day=1).strftime("%Y-%m-%d"))
    d_to   = date_to or today
    with get_db() as conn:
        records = conn.execute(
            """SELECT o.*, e.name as emp_name FROM overtime_requests o
               JOIN employees e ON o.emp_id=e.id
               WHERE o.ot_date BETWEEN ? AND ?
               ORDER BY o.ot_date DESC""",
            (d_from, d_to)
        ).fetchall()
    return templates.TemplateResponse(request, "shared/report_ot.html", {
        "user": user,
        "records": [dict(r) for r in records],
        "date_from": d_from, "date_to": d_to,
        "now_str": get_pht_now().strftime("%B %d, %Y %I:%M %p"),
        **shared_ctx(user, request),
    })


@app.get("/reports/leave", response_class=HTMLResponse)
async def report_leave(request: Request, date_from: str = "", date_to: str = ""):
    user = require_role(request, "HR Manager", "Admin")
    today = get_pht_now().strftime("%Y-%m-%d")
    d_from = date_from or (get_pht_now().replace(day=1).strftime("%Y-%m-%d"))
    d_to   = date_to or today
    with get_db() as conn:
        records = conn.execute(
            """SELECT l.*, e.name as emp_name FROM leave_requests l
               JOIN employees e ON l.emp_id=e.id
               WHERE l.start_date BETWEEN ? AND ?
               ORDER BY l.start_date DESC""",
            (d_from, d_to)
        ).fetchall()
    return templates.TemplateResponse(request, "shared/report_leave.html", {
        "user": user,
        "records": [dict(r) for r in records],
        "date_from": d_from, "date_to": d_to,
        "now_str": get_pht_now().strftime("%B %d, %Y %I:%M %p"),
        **shared_ctx(user, request),
    })


# ══════════════════════════════════════════════════════════════════════════════
# ── ADMIN / OWNER EXECUTIVE DASHBOARD ────────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/admin-dashboard", response_class=HTMLResponse)
async def admin_dashboard(request: Request):
    user = require_role(request, "Admin")
    now = get_pht_now()
    week_start, week_end = get_week_range()
    month_start = now.replace(day=1).strftime("%Y-%m-%d")
    today_str = now.strftime("%Y-%m-%d")

    with get_db() as conn:
        # Headcount clocked in right now
        active_employees = conn.execute(
            """SELECT COUNT(DISTINCT a.emp_id) FROM attendance a
               JOIN employees e ON a.emp_id=e.id
               WHERE e.role='Employee' AND a.date_logged=? AND a.clock_in IS NOT NULL AND a.clock_out IS NULL""",
            (today_str,)
        ).fetchone()[0]

        total_employees = conn.execute(
            "SELECT COUNT(*) FROM employees WHERE role='Employee' AND is_active=1"
        ).fetchone()[0]

        # Pending approvals
        pending_ot    = conn.execute("SELECT COUNT(*) FROM overtime_requests WHERE status='Pending'").fetchone()[0]
        pending_leave = conn.execute("SELECT COUNT(*) FROM leave_requests WHERE status='Pending'").fetchone()[0]
        for_review    = conn.execute("SELECT COUNT(*) FROM work_logs WHERE status='For Review'").fetchone()[0]

        # This week payroll preview
        emp_list = conn.execute(
            "SELECT id, name, hourly_rate FROM employees WHERE role='Employee' AND is_active=1"
        ).fetchall()

        # Hours per client this month
        client_hours = conn.execute(
            """SELECT client, SUM(hours_worked) as total_hours
               FROM work_logs WHERE date_logged BETWEEN ? AND ? AND client IS NOT NULL
               GROUP BY client ORDER BY total_hours DESC""",
            (month_start, today_str)
        ).fetchall()

        # Tasks completed today
        tasks_today = conn.execute(
            "SELECT COUNT(*) FROM work_logs WHERE date_logged=? AND status='Done'",
            (today_str,)
        ).fetchone()[0]

        # Recent audit log
        recent_audit = conn.execute(
            "SELECT * FROM audit_log ORDER BY created_at DESC LIMIT 10"
        ).fetchall()

        # Login activity flags (multiple IPs same user today)
        login_flags = conn.execute(
            """SELECT l.emp_id, e.name, COUNT(DISTINCT l.ip_address) as ip_count
               FROM login_log l JOIN employees e ON l.emp_id=e.id
               WHERE l.created_at >= ? AND l.success=1
               GROUP BY l.emp_id HAVING ip_count > 1""",
            (today_str,)
        ).fetchall()

        # Document expiry alerts (within 30 days or already expired)
        expiry_threshold = (now + timedelta(days=30)).strftime("%Y-%m-%d")
        doc_expiry_alerts = conn.execute(
            """SELECT name,
                doc_nbi_expiry, doc_sss_expiry, doc_tin_expiry,
                doc_philhealth_expiry, doc_pagibig_expiry
               FROM employees WHERE is_active=1 AND role='Employee'
               AND (
                 (doc_nbi_expiry IS NOT NULL AND doc_nbi_expiry <= ?)
                 OR (doc_sss_expiry IS NOT NULL AND doc_sss_expiry <= ?)
                 OR (doc_tin_expiry IS NOT NULL AND doc_tin_expiry <= ?)
                 OR (doc_philhealth_expiry IS NOT NULL AND doc_philhealth_expiry <= ?)
                 OR (doc_pagibig_expiry IS NOT NULL AND doc_pagibig_expiry <= ?)
               ) ORDER BY name""",
            (expiry_threshold,) * 5
        ).fetchall()

        # Top performers this month (most hours logged)
        top_performers = conn.execute(
            """SELECT e.name, SUM(w.hours_worked) as total_hours, COUNT(*) as task_count
               FROM work_logs w JOIN employees e ON w.emp_id=e.id
               WHERE w.date_logged BETWEEN ? AND ? AND w.status='Done'
               GROUP BY w.emp_id ORDER BY total_hours DESC LIMIT 5""",
            (month_start, today_str)
        ).fetchall()

        # Pending timesheets count
        pending_timesheets = conn.execute(
            "SELECT COUNT(*) FROM timesheet_submissions WHERE status='Submitted'"
        ).fetchone()[0]

        # Active clients this month (distinct clients with logged tasks)
        active_clients = conn.execute(
            "SELECT COUNT(DISTINCT client) FROM work_logs WHERE date_logged BETWEEN ? AND ? AND client IS NOT NULL AND client != ''",
            (month_start, today_str)
        ).fetchone()[0]

        # Task status breakdown (all active, non-archived)
        task_status_counts = {
            row["status"]: row["cnt"]
            for row in conn.execute(
                "SELECT status, COUNT(*) as cnt FROM work_logs WHERE COALESCE(is_archived,0)=0 GROUP BY status"
            ).fetchall()
        }

        # Employees on approved leave today
        on_leave_today = conn.execute(
            """SELECT e.name FROM leave_requests l JOIN employees e ON l.emp_id=e.id
               WHERE l.status='Approved' AND l.start_date<=? AND l.end_date>=? AND e.is_active=1""",
            (today_str, today_str)
        ).fetchall()

        # Who is clocked in right now
        clocked_in_now = conn.execute(
            """SELECT e.name, a.clock_in, e.profile_pic_path
               FROM attendance a JOIN employees e ON a.emp_id=e.id
               WHERE e.role='Employee' AND a.date_logged=? AND a.clock_in IS NOT NULL AND a.clock_out IS NULL
               ORDER BY a.clock_in""",
            (today_str,)
        ).fetchall()

    # Estimate this week's labor cost
    week_cost = 0.0
    for emp in emp_list:
        data = compute_payroll_for_employee(emp["id"], week_start, week_end)
        week_cost += data.get("gross_pay", 0)

    return templates.TemplateResponse(request, "admin/admin_dashboard.html", {
        "user": user,
        "active_employees": active_employees,
        "total_employees": total_employees,
        "pending_ot": pending_ot,
        "pending_leave": pending_leave,
        "for_review": for_review,
        "week_cost": round(week_cost, 2),
        "client_hours": [dict(r) for r in client_hours],
        "tasks_today": tasks_today,
        "recent_audit": [dict(r) for r in recent_audit],
        "login_flags": [dict(r) for r in login_flags],
        "doc_expiry_alerts": [dict(r) for r in doc_expiry_alerts],
        "top_performers": [dict(r) for r in top_performers],
        "pending_timesheets": pending_timesheets,
        "active_clients": active_clients,
        "task_status_counts": task_status_counts,
        "on_leave_today": [dict(r) for r in on_leave_today],
        "clocked_in_now": [dict(r) for r in clocked_in_now],
        "today_str": today_str,
        "week_start": week_start,
        "week_end": week_end,
        "flash": get_flash(request),
        **shared_ctx(user, request),
    })


# ══════════════════════════════════════════════════════════════════════════════
# ── AUDIT LOG VIEWER ──────────────────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/admin/audit-log", response_class=HTMLResponse)
async def audit_log_page(
    request: Request,
    page: int = 1,
    user_name: str = "",
    action: str = "",
    date_from: str = "",
    date_to: str = "",
):
    user = require_role(request, "Admin")
    per_page = 50
    offset = (page - 1) * per_page

    filters = ["1=1"]
    params: list = []
    if user_name:
        filters.append("user_name LIKE ?")
        params.append(f"%{user_name}%")
    if action:
        filters.append("action LIKE ?")
        params.append(f"%{action}%")
    if date_from:
        filters.append("DATE(created_at) >= ?")
        params.append(date_from)
    if date_to:
        filters.append("DATE(created_at) <= ?")
        params.append(date_to)

    where = " AND ".join(filters)

    with get_db() as conn:
        total = conn.execute(
            f"SELECT COUNT(*) FROM audit_log WHERE {where}", params
        ).fetchone()[0]
        rows = conn.execute(
            f"SELECT * FROM audit_log WHERE {where} ORDER BY id DESC LIMIT ? OFFSET ?",
            params + [per_page, offset]
        ).fetchall()
        distinct_actions = conn.execute(
            "SELECT DISTINCT action FROM audit_log ORDER BY action"
        ).fetchall()

    total_pages = max(1, (total + per_page - 1) // per_page)
    return templates.TemplateResponse(request, "admin/audit_log.html", {
        "user": user,
        "rows": [dict(r) for r in rows],
        "total": total,
        "page": page,
        "total_pages": total_pages,
        "per_page": per_page,
        "user_name": user_name,
        "action": action,
        "date_from": date_from,
        "date_to": date_to,
        "distinct_actions": [r["action"] for r in distinct_actions],
        "flash": get_flash(request),
        **shared_ctx(user, request),
    })


# ══════════════════════════════════════════════════════════════════════════════
# ── CSV EXPORTS ───────────────────────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════

def _csv_response(filename: str, headers: list[str], rows: list[dict]) -> StreamingResponse:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=headers, extrasaction="ignore")
    w.writeheader()
    w.writerows(rows)
    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/export/attendance.csv")
async def export_attendance(
    request: Request,
    date_from: str = "",
    date_to: str = "",
    emp_id: int = 0,
):
    require_role(request, "HR Manager", "Admin")
    now = get_pht_now()
    df = date_from or now.replace(day=1).strftime("%Y-%m-%d")
    dt = date_to   or now.strftime("%Y-%m-%d")

    filters = ["a.date_logged BETWEEN ? AND ?"]
    params: list = [df, dt]
    if emp_id:
        filters.append("a.emp_id=?")
        params.append(emp_id)

    with get_db() as conn:
        rows = conn.execute(
            f"""SELECT e.name AS employee, a.date_logged AS date,
                       a.clock_in, a.clock_out,
                       ROUND(
                         (JULIANDAY(a.clock_out) - JULIANDAY(a.clock_in)) * 24, 2
                       ) AS hours_worked,
                       a.late_flag AS late
               FROM attendance a
               JOIN employees e ON a.emp_id = e.id
               WHERE {" AND ".join(filters)}
               ORDER BY a.date_logged DESC, e.name""",
            params,
        ).fetchall()

    headers = ["employee", "date", "clock_in", "clock_out", "hours_worked", "late"]
    filename = f"attendance_{df}_to_{dt}.csv"
    return _csv_response(filename, headers, [dict(r) for r in rows])


@app.get("/export/payroll.csv")
async def export_payroll(
    request: Request,
    week_start: str = "",
    week_end: str = "",
):
    require_role(request, "HR Manager", "Admin")
    ws, we = get_week_range()
    ws = week_start or ws
    we = week_end   or we

    with get_db() as conn:
        rows = conn.execute(
            """SELECT e.name AS employee, e.role,
                      pr.week_start, pr.week_end,
                      pr.regular_hours, pr.overtime_hours,
                      pr.gross_pay, pr.sss_ee, pr.philhealth_ee,
                      pr.pagibig_ee, pr.tax, pr.net_pay, pr.status
               FROM payroll_runs pr
               JOIN employees e ON pr.emp_id = e.id
               WHERE pr.week_start = ?
               ORDER BY e.name""",
            (ws,),
        ).fetchall()

    headers = [
        "employee", "role", "week_start", "week_end",
        "regular_hours", "overtime_hours", "gross_pay",
        "sss_ee", "philhealth_ee", "pagibig_ee", "tax", "net_pay", "status",
    ]
    return _csv_response(f"payroll_{ws}.csv", headers, [dict(r) for r in rows])


@app.get("/export/timesheets.csv")
async def export_timesheets(
    request: Request,
    date_from: str = "",
    date_to: str = "",
    status: str = "",
):
    require_role(request, "HR Manager", "Admin")
    now = get_pht_now()
    df = date_from or now.replace(day=1).strftime("%Y-%m-%d")
    dt = date_to   or now.strftime("%Y-%m-%d")

    filters = ["ts.week_start BETWEEN ? AND ?"]
    params: list = [df, dt]
    if status:
        filters.append("ts.status=?")
        params.append(status)

    with get_db() as conn:
        rows = conn.execute(
            f"""SELECT e.name AS employee, ts.week_start, ts.week_end,
                       ts.total_hours, ts.total_ot_hours, ts.status,
                       ts.submitted_at, ts.reviewed_at, ts.reviewer_notes
               FROM timesheet_submissions ts
               JOIN employees e ON ts.emp_id = e.id
               WHERE {" AND ".join(filters)}
               ORDER BY ts.week_start DESC, e.name""",
            params,
        ).fetchall()

    headers = [
        "employee", "week_start", "week_end",
        "total_hours", "total_ot_hours", "status",
        "submitted_at", "reviewed_at", "reviewer_notes",
    ]
    return _csv_response(f"timesheets_{df}_to_{dt}.csv", headers, [dict(r) for r in rows])


# ══════════════════════════════════════════════════════════════════════════════
# ── PERFORMANCE REVIEWS ───────────────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/my-review", response_class=HTMLResponse)
async def my_review(request: Request):
    user = require_role(request, "Employee")
    with get_db() as conn:
        reviews = conn.execute(
            "SELECT * FROM performance_reviews WHERE emp_id=? ORDER BY created_at DESC",
            (user["id"],)
        ).fetchall()
    return templates.TemplateResponse(request, "employee/my_review.html", {
        "user": user, "reviews": [dict(r) for r in reviews],
        "flash": get_flash(request), **shared_ctx(user, request),
    })


@app.post("/api/reviews/{review_id}/self-rate")
async def self_rate_review(
    request: Request,
    review_id: int,
    self_rating: int = Form(...),
    self_comments: str = Form(""),
):
    user = require_role(request, "Employee")
    now_str = get_pht_now().strftime("%Y-%m-%d %H:%M:%S")
    with get_db() as conn:
        rev = conn.execute(
            "SELECT * FROM performance_reviews WHERE id=? AND emp_id=?",
            (review_id, user["id"])
        ).fetchone()
        if not rev or rev["status"] not in ("Pending Self-Review",):
            flash(request, "Review not found or already submitted.", "error")
            return RedirectResponse("/my-review", status_code=302)
        conn.execute(
            """UPDATE performance_reviews
               SET self_rating=?, self_comments=?, self_submitted_at=?, status='Pending HR Review'
               WHERE id=?""",
            (max(1, min(5, self_rating)), self_comments.strip(), now_str, review_id)
        )
        push_notification(conn, rev["created_by"] or user["id"],
                          f"⭐ {user['name']} submitted self-review",
                          f"Period: {rev['period']}", "/hr-reviews")
    flash(request, "Self-review submitted.")
    return RedirectResponse("/my-review", status_code=302)


@app.get("/hr-reviews", response_class=HTMLResponse)
async def hr_reviews(request: Request, status: str = ""):
    user = require_role(request, "HR Manager", "Admin")
    with get_db() as conn:
        q = "SELECT pr.*, e.name as emp_name FROM performance_reviews pr JOIN employees e ON pr.emp_id=e.id"
        params: list = []
        if status:
            q += " WHERE pr.status=?"
            params.append(status)
        q += " ORDER BY pr.created_at DESC"
        reviews = conn.execute(q, params).fetchall()
        employees = conn.execute(
            "SELECT id, name FROM employees WHERE role='Employee' AND is_active=1 ORDER BY name"
        ).fetchall()
        status_counts = {}
        for s in ("Pending Self-Review", "Pending HR Review", "Completed"):
            status_counts[s] = conn.execute(
                "SELECT COUNT(*) FROM performance_reviews WHERE status=?", (s,)
            ).fetchone()[0]
    return templates.TemplateResponse(request, "shared/hr_reviews.html", {
        "user": user, "reviews": [dict(r) for r in reviews],
        "employees": [dict(e) for e in employees],
        "status": status, "status_counts": status_counts,
        "flash": get_flash(request), **shared_ctx(user, request),
    })


@app.post("/api/reviews/create-cycle")
async def create_review_cycle(
    request: Request,
    period: str = Form(...),
    period_start: str = Form(...),
    period_end: str = Form(...),
    emp_ids: list[int] = Form(default=[]),
):
    user = require_role(request, "HR Manager", "Admin")
    with get_db() as conn:
        if not emp_ids:
            emps = conn.execute(
                "SELECT id FROM employees WHERE role='Employee' AND is_active=1"
            ).fetchall()
            emp_ids = [e["id"] for e in emps]
        created = 0
        for eid in emp_ids:
            existing = conn.execute(
                "SELECT id FROM performance_reviews WHERE emp_id=? AND period=?",
                (eid, period)
            ).fetchone()
            if not existing:
                conn.execute(
                    """INSERT INTO performance_reviews
                       (emp_id, period, period_start, period_end, status, created_by)
                       VALUES (?,?,?,?,'Pending Self-Review',?)""",
                    (eid, period.strip(), period_start, period_end, user["id"])
                )
                push_notification(conn, eid, "📋 Performance Review",
                                  f"Your {period} review is ready.", "/my-review")
                created += 1
    flash(request, f"Created {created} review(s) for period '{period}'.")
    return RedirectResponse("/hr-reviews", status_code=302)


@app.post("/api/reviews/{review_id}/hr-rate")
async def hr_rate_review(
    request: Request,
    review_id: int,
    hr_rating: int = Form(...),
    hr_comments: str = Form(""),
):
    user = require_role(request, "HR Manager", "Admin")
    now_str = get_pht_now().strftime("%Y-%m-%d %H:%M:%S")
    with get_db() as conn:
        rev = conn.execute(
            "SELECT * FROM performance_reviews WHERE id=?", (review_id,)
        ).fetchone()
        if not rev:
            flash(request, "Review not found.", "error")
            return RedirectResponse("/hr-reviews", status_code=302)
        conn.execute(
            """UPDATE performance_reviews
               SET hr_rating=?, hr_comments=?, hr_reviewed_by=?, hr_reviewed_at=?, status='Completed'
               WHERE id=?""",
            (max(1, min(5, hr_rating)), hr_comments.strip(), user["name"], now_str, review_id)
        )
        push_notification(conn, rev["emp_id"], "✅ Review Completed",
                          f"Your {rev['period']} performance review has been rated.", "/my-review")
    flash(request, "Review completed.")
    return RedirectResponse("/hr-reviews", status_code=302)


# ══════════════════════════════════════════════════════════════════════════════
# ── SHIFT SCHEDULING ──────────────────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════

_SHIFT_TYPES = ["Morning", "Night", "WFH", "Off"]
_DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
_DAY_LABELS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


@app.get("/hr-schedule", response_class=HTMLResponse)
async def hr_schedule(request: Request, week: str = ""):
    user = require_role(request, "HR Manager", "Admin")
    ws, _ = get_week_range()
    week_start = week or ws

    # Build Mon–Sun dates for this week
    from datetime import date as _date
    try:
        base_date = _date.fromisoformat(week_start)
    except ValueError:
        base_date = _date.fromisoformat(ws)
        week_start = ws
    week_dates = [(base_date + timedelta(days=i)).isoformat() for i in range(7)]
    prev_week = (base_date - timedelta(days=7)).isoformat()
    next_week = (base_date + timedelta(days=7)).isoformat()

    with get_db() as conn:
        employees = conn.execute(
            "SELECT id, name, shift_type FROM employees WHERE role='Employee' AND is_active=1 ORDER BY name"
        ).fetchall()
        schedules = conn.execute(
            "SELECT * FROM schedules WHERE week_start=?", (week_start,)
        ).fetchall()

    sched_map = {s["emp_id"]: dict(s) for s in schedules}

    return templates.TemplateResponse(request, "shared/hr_schedule.html", {
        "user": user,
        "employees": [dict(e) for e in employees],
        "sched_map": sched_map,
        "week_start": week_start,
        "week_dates": week_dates,
        "prev_week": prev_week,
        "next_week": next_week,
        "days": _DAYS,
        "day_labels": _DAY_LABELS,
        "shift_types": _SHIFT_TYPES,
        "flash": get_flash(request),
        **shared_ctx(user, request),
    })


@app.post("/api/schedule/save")
async def save_schedule(request: Request):
    user = require_role(request, "HR Manager", "Admin")
    form = await request.form()
    week_start = form.get("week_start", "")
    now_str = get_pht_now().strftime("%Y-%m-%d %H:%M:%S")
    with get_db() as conn:
        employees = conn.execute(
            "SELECT id FROM employees WHERE role='Employee' AND is_active=1"
        ).fetchall()
        for emp in employees:
            eid = emp["id"]
            day_vals = {d: (form.get(f"{eid}_{d}") or "Morning") for d in _DAYS}
            conn.execute(
                """INSERT INTO schedules (emp_id, week_start, mon, tue, wed, thu, fri, sat, sun, created_by, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(emp_id, week_start) DO UPDATE SET
                   mon=excluded.mon, tue=excluded.tue, wed=excluded.wed,
                   thu=excluded.thu, fri=excluded.fri, sat=excluded.sat, sun=excluded.sun,
                   updated_at=excluded.updated_at""",
                (eid, week_start,
                 day_vals["mon"], day_vals["tue"], day_vals["wed"],
                 day_vals["thu"], day_vals["fri"], day_vals["sat"], day_vals["sun"],
                 user["id"], now_str)
            )
    flash(request, f"Schedule saved for week of {week_start}.")
    return RedirectResponse(f"/hr-schedule?week={week_start}", status_code=302)


@app.get("/my-schedule", response_class=HTMLResponse)
async def my_schedule(request: Request, week: str = ""):
    user = require_role(request, "Employee")
    ws, _ = get_week_range()
    week_start = week or ws

    from datetime import date as _date
    try:
        base_date = _date.fromisoformat(week_start)
    except ValueError:
        base_date = _date.fromisoformat(ws)
        week_start = ws
    week_dates = [(base_date + timedelta(days=i)).isoformat() for i in range(7)]
    prev_week = (base_date - timedelta(days=7)).isoformat()
    next_week = (base_date + timedelta(days=7)).isoformat()

    with get_db() as conn:
        sched = conn.execute(
            "SELECT * FROM schedules WHERE emp_id=? AND week_start=?",
            (user["id"], week_start)
        ).fetchone()

    schedule = dict(sched) if sched else None
    return templates.TemplateResponse(request, "employee/my_schedule.html", {
        "user": user,
        "schedule": schedule,
        "week_start": week_start,
        "week_dates": week_dates,
        "prev_week": prev_week,
        "next_week": next_week,
        "today": _date.today().isoformat(),
        "days": _DAYS,
        "day_labels": _DAY_LABELS,
        "flash": get_flash(request),
        **shared_ctx(user, request),
    })


# ---------------------------------------------------------------------------
# Global search
# ---------------------------------------------------------------------------

@app.get("/api/search")
async def global_search(request: Request, q: str = ""):
    user = require_role(request, "Employee", "HR Manager", "Admin")
    q = q.strip()
    if not q or len(q) < 2:
        return JSONResponse({"results": {}})

    like = f"%{q}%"
    results: dict = {}

    with get_db() as conn:
        # Employees (all roles can find people)
        emp_rows = conn.execute(
            """SELECT id, name, role, department, position
               FROM employees
               WHERE (name LIKE ? OR department LIKE ? OR position LIKE ?)
                 AND status='Active'
               LIMIT 8""",
            (like, like, like),
        ).fetchall()
        if emp_rows:
            results["Employees"] = [
                {"label": r["name"],
                 "sub": f"{r['role']} · {r['department'] or '—'}",
                 "url": f"/employees/{r['id']}" if user["role"] != "Employee" else "/directory"}
                for r in emp_rows
            ]

        # Announcements
        ann_rows = conn.execute(
            "SELECT id, title, body FROM announcements WHERE title LIKE ? OR body LIKE ? ORDER BY id DESC LIMIT 6",
            (like, like),
        ).fetchall()
        if ann_rows:
            results["Announcements"] = [
                {"label": r["title"],
                 "sub": (r["body"] or "")[:60],
                 "url": "/announcements"}
                for r in ann_rows
            ]

        # Clients (HR + Admin only)
        if user["role"] in ("HR Manager", "Admin"):
            cli_rows = conn.execute(
                """SELECT id, name, industry, contact_person FROM clients
                   WHERE name LIKE ? OR industry LIKE ? OR contact_person LIKE ? LIMIT 6""",
                (like, like, like),
            ).fetchall()
            if cli_rows:
                results["Clients"] = [
                    {"label": r["name"],
                     "sub": f"{r['industry'] or '—'} · {r['contact_person'] or ''}",
                     "url": f"/clients/{r['id']}"}
                    for r in cli_rows
                ]

        # Chat messages
        chat_rows = conn.execute(
            """SELECT m.body, m.created_at, e.name as sender
               FROM chat_messages m JOIN employees e ON e.id=m.sender_id
               WHERE m.body LIKE ? ORDER BY m.id DESC LIMIT 5""",
            (like,),
        ).fetchall()
        if chat_rows:
            results["Chat"] = [
                {"label": (r["body"] or "")[:60],
                 "sub": f"{r['sender']} · {(r['created_at'] or '')[:10]}",
                 "url": "/chat"}
                for r in chat_rows
            ]

    return JSONResponse({"q": q, "results": results})
