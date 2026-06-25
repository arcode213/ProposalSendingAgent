"""
RoamDigi Proposal Sending Agent — Flask web app.

Loads the verified Pakistan travel agencies, lets you personalize a RoamDigi
proposal, and sends it to each agency one-by-one over Gmail SMTP with paced,
spam-safe delivery, live progress, and crash-safe state in SQLite.

Run:   python app.py     then open http://127.0.0.1:5000
"""
import os
import re
import sys
import ssl
import json
import time
import secrets
import sqlite3
import smtplib
import threading
import urllib.request
import urllib.error
from datetime import timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.utils import formataddr, formatdate, make_msgid

from flask import Flask, request, jsonify, render_template, session, redirect, url_for
from werkzeug.security import generate_password_hash, check_password_hash

try:
    from dotenv import load_dotenv
except ImportError:  # dotenv optional; on Vercel the vars come from the dashboard
    def load_dotenv(*a, **k):
        return False

import proposals

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# DB_PATH is overridable so it can live on a persistent disk in production
# (e.g. Render mounts a disk at /var/data → set DB_PATH=/var/data/outreach.db).
DB_PATH = os.environ.get("DB_PATH", os.path.join(BASE_DIR, "outreach.db"))
XLSX_PATH = os.path.join(BASE_DIR, "pk_travel_agencies_VALID_EMAILS_v2.xlsx")

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# Load secrets / locked config from .env (local) or process env (Vercel).
load_dotenv(os.path.join(BASE_DIR, ".env"))

# These settings come from the environment and are LOCKED in the web UI —
# the server always uses these values, ignoring any DB or browser changes.
ENV_SETTINGS = {
    "smtp_host":   os.environ.get("SMTP_HOST", "smtp-relay.brevo.com"),
    "smtp_port":   os.environ.get("SMTP_PORT", "587"),
    "smtp_user":   os.environ.get("SMTP_USER", ""),
    "smtp_pass":   os.environ.get("SMTP_PASS", ""),
    "from_email":  os.environ.get("FROM_EMAIL", ""),
    "reply_to":    os.environ.get("REPLY_TO", ""),
    "sender_name": os.environ.get("SENDER_NAME", "RoamDigi"),
    "title":       os.environ.get("SENDER_TITLE", "Partner Program"),
}
LOCKED_KEYS = set(ENV_SETTINGS)

DEFAULT_SETTINGS = {
    **ENV_SETTINGS,
    "commission": "4",         # [X]%
    "partner_commission": "partner commission program",  # [partner commission]
    "greeting_fallback": "Sir/Madam",   # used in the formal auto-greeting
    # Custom proposal composed at run time from the UI (used when draft_id=custom).
    "custom_subject": "",
    "custom_body": "",
    "app_store_url": "https://apps.apple.com/pk/app/roamdigi/id6758890011",
    "play_store_url": "https://play.google.com/store/apps/details?id=com.roamdigi",
    "draft_id": proposals.DEFAULT_DRAFT,
    "gap_seconds": "10",        # pause between sends (slow & safe)
    "daily_cap": "300",         # max sends per calendar day
}

app = Flask(__name__)

# ---------------------------------------------------------------------------
# Authentication / session security
# ---------------------------------------------------------------------------
# Signing key for session cookies. MUST be stable in production, or every
# restart logs everyone out. If unset we generate a throwaway one and warn.
_secret = os.environ.get("SECRET_KEY")
if not _secret:
    _secret = secrets.token_hex(32)
    print("WARNING: SECRET_KEY not set — using a temporary key (sessions reset on "
          "restart). Set SECRET_KEY in the environment for production.")
app.secret_key = _secret

# Admin credentials. Prefer a pre-computed hash (ADMIN_PASSWORD_HASH); accept a
# plaintext ADMIN_PASSWORD for convenience; otherwise mint a random one-time
# password and print it (never ship a guessable default).
ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "admin")
_pw_hash = os.environ.get("ADMIN_PASSWORD_HASH")
if not _pw_hash:
    _pw_plain = os.environ.get("ADMIN_PASSWORD")
    if not _pw_plain:
        _pw_plain = secrets.token_urlsafe(12)
        print("=" * 64)
        print("  No ADMIN_PASSWORD / ADMIN_PASSWORD_HASH set. Temporary login:")
        print(f"      username: {ADMIN_USERNAME}")
        print(f"      password: {_pw_plain}")
        print("  Set ADMIN_PASSWORD_HASH in the environment for production.")
        print("=" * 64)
    _pw_hash = generate_password_hash(_pw_plain)
ADMIN_PASSWORD_HASH = _pw_hash

# Cookie hardening. Render terminates TLS, so Secure cookies are safe there;
# locally over http we leave Secure off so login still works.
_is_prod = bool(os.environ.get("RENDER") or os.environ.get("SECURE_COOKIES"))
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,      # JS can't read the cookie (XSS mitigation)
    SESSION_COOKIE_SAMESITE="Lax",     # blocks cross-site POST cookies (CSRF mitigation)
    SESSION_COOKIE_SECURE=_is_prod,    # HTTPS-only cookie in production
    PERMANENT_SESSION_LIFETIME=timedelta(hours=12),
)

# Simple in-memory brute-force throttle (single worker → fine). ip -> [fails, locked_until]
_login_fails = {}
LOGIN_MAX_FAILS = 5
LOGIN_LOCK_SECONDS = 300

PUBLIC_ENDPOINTS = {"login", "logout", "healthz", "static"}


@app.before_request
def _require_login():
    if request.endpoint in PUBLIC_ENDPOINTS:
        return
    if not session.get("user"):
        if request.path.startswith("/api/"):
            return jsonify({"ok": False, "error": "Authentication required."}), 401
        return redirect(url_for("login", next=request.path))


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------
_db_lock = threading.Lock()


def db():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with db() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY, value TEXT
            );
            CREATE TABLE IF NOT EXISTS recipients (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                agency_name TEXT,
                city TEXT,
                email TEXT UNIQUE,
                contact TEXT,
                business_type TEXT,
                reliability TEXT,
                notes TEXT,
                first_name TEXT DEFAULT '',
                included INTEGER DEFAULT 1,
                status TEXT DEFAULT 'pending',      -- pending|sent|failed|skipped
                error TEXT DEFAULT '',
                draft_used TEXT DEFAULT '',
                sent_at TEXT DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT, level TEXT, message TEXT
            );
            """
        )
        # seed settings
        for k, v in DEFAULT_SETTINGS.items():
            conn.execute("INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)", (k, v))
        conn.commit()


def get_settings():
    with db() as conn:
        rows = conn.execute("SELECT key,value FROM settings").fetchall()
    s = {r["key"]: r["value"] for r in rows}
    for k, v in DEFAULT_SETTINGS.items():
        s.setdefault(k, v)
    s.update(ENV_SETTINGS)  # locked keys are always authoritative & current
    return s


def save_settings(updates):
    with db() as conn:
        for k, v in updates.items():
            if k in DEFAULT_SETTINGS and k not in LOCKED_KEYS:
                conn.execute(
                    "INSERT INTO settings(key,value) VALUES(?,?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (k, str(v)),
                )
        conn.commit()


def log(level, message):
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    with db() as conn:
        conn.execute("INSERT INTO log(ts,level,message) VALUES(?,?,?)", (ts, level, message))
        conn.commit()
    line = f"[{ts}] {level.upper()}: {message}"
    try:
        print(line)
    except UnicodeEncodeError:
        enc = (sys.stdout.encoding or "utf-8")
        print(line.encode(enc, "replace").decode(enc, "replace"))


# ---------------------------------------------------------------------------
# Import agencies from the verified Excel sheet
# ---------------------------------------------------------------------------
def import_recipients():
    with db() as conn:
        n = conn.execute("SELECT COUNT(*) c FROM recipients").fetchone()["c"]
    if n > 0:
        return  # already imported
    if not os.path.exists(XLSX_PATH):
        log("error", f"Agencies file not found: {XLSX_PATH}")
        return

    import openpyxl

    wb = openpyxl.load_workbook(XLSX_PATH, read_only=True, data_only=True)
    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))

    # find header row (the one containing "Email")
    header_idx = None
    for i, r in enumerate(rows[:5]):
        cells = [str(c).strip().lower() if c is not None else "" for c in r]
        if "email" in cells and "name" in cells:
            header_idx = i
            break
    if header_idx is None:
        log("error", "Could not locate header row in spreadsheet.")
        return

    header = [str(c).strip().lower() if c is not None else "" for c in rows[header_idx]]

    def col(*names):
        for nm in names:
            if nm in header:
                return header.index(nm)
        return None

    ci = {
        "name": col("name"),
        "city": col("city"),
        "email": col("email"),
        "contact": col("contact", "phone"),
        "btype": col("business type", "business"),
        "rel": col("reliability"),
        "notes": col("web verified / notes", "notes"),
    }

    added, skipped = 0, 0
    seen = set()
    with db() as conn:
        for r in rows[header_idx + 1:]:
            if not r:
                continue

            def g(key):
                idx = ci[key]
                if idx is None or idx >= len(r):
                    return ""
                return str(r[idx]).strip() if r[idx] is not None else ""

            email = g("email").lower()
            name = g("name")
            if not EMAIL_RE.match(email) or email in seen:
                skipped += 1
                continue
            seen.add(email)
            first = proposals.first_name_from(name, email)
            try:
                conn.execute(
                    """INSERT OR IGNORE INTO recipients
                       (agency_name, city, email, contact, business_type, reliability, notes, first_name)
                       VALUES (?,?,?,?,?,?,?,?)""",
                    (proposals.clean_agency_name(name), g("city"), email, g("contact"),
                     g("btype"), g("rel"), g("notes"), first),
                )
                added += 1
            except sqlite3.IntegrityError:
                skipped += 1
        conn.commit()
    log("info", f"Imported {added} agencies ({skipped} skipped: invalid/duplicate email).")


# ---------------------------------------------------------------------------
# Sender engine (single background thread, state machine)
# ---------------------------------------------------------------------------
class Sender:
    def __init__(self):
        self.status = "idle"          # idle|running|paused|stopping
        self.lock = threading.Lock()
        self.thread = None
        self.current = None           # email currently being sent
        self.wake = threading.Event()

    # ---- helpers ----
    def sent_today(self):
        today = time.strftime("%Y-%m-%d")
        with db() as conn:
            row = conn.execute(
                "SELECT COUNT(*) c FROM recipients WHERE status='sent' AND substr(sent_at,1,10)=?",
                (today,),
            ).fetchone()
        return row["c"]

    def next_pending(self):
        with db() as conn:
            return conn.execute(
                "SELECT * FROM recipients WHERE included=1 AND status='pending' ORDER BY id LIMIT 1"
            ).fetchone()

    def counts(self):
        with db() as conn:
            rows = conn.execute(
                "SELECT status, COUNT(*) c FROM recipients GROUP BY status"
            ).fetchall()
            total = conn.execute("SELECT COUNT(*) c FROM recipients").fetchone()["c"]
            included = conn.execute(
                "SELECT COUNT(*) c FROM recipients WHERE included=1"
            ).fetchone()["c"]
        by = {r["status"]: r["c"] for r in rows}
        return {
            "total": total,
            "included": included,
            "sent": by.get("sent", 0),
            "failed": by.get("failed", 0),
            "pending": by.get("pending", 0),
            "skipped": by.get("skipped", 0),
            "sent_today": self.sent_today(),
        }

    # ---- Brevo HTTP API (port 443) — used when BREVO_API_KEY is set ----
    # Many hosts (e.g. Railway) block outbound SMTP ports (25/465/587), so SMTP
    # sends time out. The Brevo transactional API goes over normal HTTPS (443),
    # which is never blocked. This is preferred in production; SMTP is the fallback
    # for local development.
    def _send_via_brevo_api(self, api_key, from_email, sender_name, to_email,
                            subject, text_body, html_body, reply_to):
        payload = {
            "sender": {"email": from_email, "name": sender_name or "RoamDigi"},
            "to": [{"email": to_email}],
            "subject": subject,
            "htmlContent": html_body,
            "textContent": text_body,
        }
        if reply_to:
            payload["replyTo"] = {"email": reply_to}
        req = urllib.request.Request(
            "https://api.brevo.com/v3/smtp/email",
            data=json.dumps(payload).encode("utf-8"),
            method="POST",
            headers={
                "api-key": api_key,
                "content-type": "application/json",
                "accept": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                resp.read()
            return True, ""
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")
            return False, f"Brevo API {e.code}: {body}"
        except Exception as e:
            return False, f"{type(e).__name__}: {e}"

    # ---- core send of one message ----
    def send_one(self, recipient, settings):
        """Send to one recipient row (sqlite Row or dict). Returns (ok, error)."""
        rec = dict(recipient)
        rendered = proposals.render(settings.get("draft_id"), rec, settings)
        if rendered["unresolved"]:
            return False, "Unresolved placeholders: " + ", ".join(rendered["unresolved"])

        from_email = (settings.get("from_email") or settings.get("smtp_user")).strip()

        # Prefer the HTTP API when a key is configured (works where SMTP is blocked).
        api_key = os.environ.get("BREVO_API_KEY", "").strip()
        if api_key:
            return self._send_via_brevo_api(
                api_key, from_email, settings.get("sender_name"), rec["email"],
                rendered["subject"], rendered["body"], rendered["html"],
                settings.get("reply_to"),
            )

        msg = MIMEMultipart("alternative")
        msg["Subject"] = rendered["subject"]
        msg["From"] = formataddr((settings.get("sender_name") or "RoamDigi", from_email))
        msg["To"] = rec["email"]
        if settings.get("reply_to"):
            msg["Reply-To"] = settings["reply_to"]
        msg["Date"] = formatdate(localtime=True)
        msg["Message-ID"] = make_msgid(domain=from_email.split("@")[-1] if "@" in from_email else "roamdigi.com")
        msg.attach(MIMEText(rendered["body"], "plain", "utf-8"))
        msg.attach(MIMEText(rendered["html"], "html", "utf-8"))

        host = settings.get("smtp_host", "smtp.gmail.com")
        port = int(settings.get("smtp_port") or 465)
        user = settings.get("smtp_user", "").strip()
        pw = settings.get("smtp_pass", "")
        ctx = ssl.create_default_context()
        try:
            if port == 465:
                with smtplib.SMTP_SSL(host, port, context=ctx, timeout=30) as server:
                    server.login(user, pw)
                    server.sendmail(from_email, [rec["email"]], msg.as_string())
            else:
                with smtplib.SMTP(host, port, timeout=30) as server:
                    server.starttls(context=ctx)
                    server.login(user, pw)
                    server.sendmail(from_email, [rec["email"]], msg.as_string())
            return True, ""
        except Exception as e:
            return False, f"{type(e).__name__}: {e}"

    def _mark(self, rid, status, error="", draft=""):
        with db() as conn:
            conn.execute(
                "UPDATE recipients SET status=?, error=?, draft_used=?, sent_at=? WHERE id=?",
                (status, error, draft, time.strftime("%Y-%m-%d %H:%M:%S") if status == "sent" else "", rid),
            )
            conn.commit()

    # ---- public actions ----
    def validate_ready(self, settings):
        problems = []
        if not settings.get("smtp_user") or not EMAIL_RE.match(settings.get("smtp_user", "")):
            problems.append("Gmail address (SMTP user) is missing or invalid.")
        if not settings.get("smtp_pass"):
            problems.append("Gmail App Password is missing.")
        if not settings.get("sender_name"):
            problems.append("Your Name is required (used in the email body & signature).")
        if not settings.get("title"):
            problems.append("Your Title is required (used in the signature).")
        if not (settings.get("custom_subject") or "").strip():
            problems.append("Proposal subject is empty — write it in the Compose tab.")
        if not (settings.get("custom_body") or "").strip():
            problems.append("Proposal body is empty — write it in the Compose tab.")
        return problems

    def send_to(self, rid):
        """Send the proposal to one specific recipient now (synchronous)."""
        with self.lock:
            if self.status == "running":
                return {"ok": False, "error": "Auto-send is running; pause it first."}
        settings = get_settings()
        problems = self.validate_ready(settings)
        if problems:
            return {"ok": False, "error": " ".join(problems)}
        with db() as conn:
            rec = conn.execute("SELECT * FROM recipients WHERE id=?", (rid,)).fetchone()
        if not rec:
            return {"ok": False, "error": "Recipient not found."}
        if self.sent_today() >= int(settings.get("daily_cap") or 120):
            return {"ok": False, "error": "Daily cap reached."}
        self.current = rec["email"]
        ok, err = self.send_one(rec, settings)
        self._mark(rec["id"], "sent" if ok else "failed", err, settings.get("draft_id"))
        self.current = None
        log("info" if ok else "error",
            f"{'Sent to ' + rec['agency_name'] + ' <' + rec['email'] + '>' if ok else 'FAILED ' + rec['email'] + ' — ' + err}")
        return {"ok": ok, "email": rec["email"], "error": err}

    def send_next_manual(self):
        """Send exactly one pending email synchronously (manual mode)."""
        with self.lock:
            if self.status == "running":
                return {"ok": False, "error": "Auto-send is running; pause it first."}
        settings = get_settings()
        problems = self.validate_ready(settings)
        if problems:
            return {"ok": False, "error": " ".join(problems)}
        rec = self.next_pending()
        if not rec:
            return {"ok": False, "error": "No pending recipients."}
        if self.sent_today() >= int(settings.get("daily_cap") or 120):
            return {"ok": False, "error": "Daily cap reached."}
        self.current = rec["email"]
        ok, err = self.send_one(rec, settings)
        self._mark(rec["id"], "sent" if ok else "failed", err, settings.get("draft_id"))
        self.current = None
        log("info" if ok else "error",
            f"{'Sent to' if ok else 'FAILED ' + rec['email'] + ' — '}{rec['email'] if ok else err}")
        return {"ok": ok, "email": rec["email"], "error": err}

    def start(self):
        with self.lock:
            if self.status == "running":
                return {"ok": False, "error": "Already running."}
            settings = get_settings()
            problems = self.validate_ready(settings)
            if problems:
                return {"ok": False, "error": " ".join(problems)}
            if not self.next_pending():
                return {"ok": False, "error": "No pending recipients to send."}
            self.status = "running"
            self.wake.set()
            if not self.thread or not self.thread.is_alive():
                self.thread = threading.Thread(target=self._run, daemon=True)
                self.thread.start()
        log("info", "Campaign started.")
        return {"ok": True}

    def pause(self):
        with self.lock:
            if self.status == "running":
                self.status = "paused"
        log("info", "Campaign paused.")
        return {"ok": True}

    def resume(self):
        with self.lock:
            if self.status == "paused":
                self.status = "running"
                self.wake.set()
        log("info", "Campaign resumed.")
        return {"ok": True}

    def stop(self):
        with self.lock:
            if self.status in ("running", "paused"):
                self.status = "stopping"
                self.wake.set()
        log("info", "Stopping campaign…")
        return {"ok": True}

    def _interruptible_sleep(self, seconds):
        """Sleep but wake immediately on stop."""
        end = time.time() + seconds
        while time.time() < end:
            if self.status == "stopping":
                return
            time.sleep(min(1.0, end - time.time()))

    def _run(self):
        while True:
            with self.lock:
                st = self.status
            if st == "stopping":
                break
            if st == "paused":
                self.wake.wait(timeout=1.0)
                self.wake.clear()
                continue
            if st != "running":
                break

            settings = get_settings()
            cap = int(settings.get("daily_cap") or 120)
            if self.sent_today() >= cap:
                log("warn", f"Daily cap of {cap} reached. Pausing until tomorrow / manual resume.")
                with self.lock:
                    self.status = "paused"
                continue

            rec = self.next_pending()
            if not rec:
                log("info", "All recipients processed. Campaign finished.")
                with self.lock:
                    self.status = "idle"
                break

            self.current = rec["email"]
            ok, err = self.send_one(rec, settings)
            self._mark(rec["id"], "sent" if ok else "failed", err, settings.get("draft_id"))
            self.current = None
            if ok:
                log("info", f"Sent to {rec['agency_name']} <{rec['email']}>  "
                            f"({self.sent_today()}/{cap} today)")
            else:
                log("error", f"FAILED {rec['email']} — {err}")

            gap = float(settings.get("gap_seconds") or 45)
            self._interruptible_sleep(gap)

        with self.lock:
            if self.status == "stopping":
                self.status = "idle"


sender = Sender()


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@app.route("/healthz")
def healthz():
    """Public, unauthenticated health probe for the platform."""
    return jsonify({"ok": True})


@app.route("/login", methods=["GET", "POST"])
def login():
    if session.get("user"):
        return redirect(url_for("index"))
    error = None
    if request.method == "POST":
        ip = request.remote_addr or "?"
        now = time.time()
        rec = _login_fails.get(ip)
        if rec and rec[1] > now:
            wait = int(rec[1] - now)
            return render_template("login.html",
                                   error=f"Too many attempts. Try again in {wait}s."), 429
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""
        if username == ADMIN_USERNAME and check_password_hash(ADMIN_PASSWORD_HASH, password):
            session.clear()
            session["user"] = username
            session.permanent = True
            _login_fails.pop(ip, None)
            log("info", f"Admin login from {ip}.")
            nxt = request.args.get("next") or url_for("index")
            if not nxt.startswith("/") or nxt.startswith("//"):  # block open redirect
                nxt = url_for("index")
            return redirect(nxt)
        cnt = (rec[0] if rec else 0) + 1
        locked = now + LOGIN_LOCK_SECONDS if cnt >= LOGIN_MAX_FAILS else 0
        _login_fails[ip] = [cnt, locked]
        log("warn", f"Failed admin login from {ip} (attempt {cnt}).")
        error = "Invalid username or password."
    return render_template("login.html", error=error)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/state")
def api_state():
    s = get_settings()
    safe = dict(s)
    safe["smtp_pass"] = "********" if s.get("smtp_pass") else ""  # never echo the password
    return jsonify({
        "status": sender.status,
        "current": sender.current,
        "counts": sender.counts(),
        "settings": safe,
        "drafts": proposals.draft_list(s),
        "ready_problems": sender.validate_ready(s),
        "locked": sorted(LOCKED_KEYS),
    })


@app.route("/api/settings", methods=["POST"])
def api_settings():
    data = request.get_json(force=True) or {}
    # Don't overwrite the stored password with the masked placeholder.
    if data.get("smtp_pass") in ("********", None):
        data.pop("smtp_pass", None)
    save_settings(data)
    return jsonify({"ok": True})


@app.route("/api/recipients")
def api_recipients():
    q = (request.args.get("q") or "").strip().lower()
    status = request.args.get("status") or ""
    sql = "SELECT * FROM recipients WHERE 1=1"
    args = []
    if q:
        sql += " AND (lower(agency_name) LIKE ? OR lower(email) LIKE ? OR lower(city) LIKE ?)"
        args += [f"%{q}%", f"%{q}%", f"%{q}%"]
    if status:
        sql += " AND status=?"
        args.append(status)
    sql += " ORDER BY id"
    with db() as conn:
        rows = [dict(r) for r in conn.execute(sql, args).fetchall()]
    return jsonify(rows)


# Columns the UI is allowed to write on a recipient row.
EDITABLE_RECIPIENT_FIELDS = (
    "agency_name", "first_name", "email", "city", "contact",
    "business_type", "reliability", "notes", "included",
)


@app.route("/api/recipient/add", methods=["POST"])
def api_recipient_add():
    """Create a new agency record from the UI."""
    data = request.get_json(force=True) or {}
    email = (data.get("email") or "").strip().lower()
    if not EMAIL_RE.match(email):
        return jsonify({"ok": False, "error": "A valid email address is required."})
    agency = proposals.clean_agency_name(data.get("agency_name") or "")
    if not agency:
        return jsonify({"ok": False, "error": "Agency name is required."})
    first = (data.get("first_name") or "").strip() or proposals.first_name_from(agency, email)
    with db() as conn:
        exists = conn.execute("SELECT 1 FROM recipients WHERE email=?", (email,)).fetchone()
        if exists:
            return jsonify({"ok": False, "error": "An agency with that email already exists."})
        included = 0 if data.get("included") in (0, "0", False) else 1
        cur = conn.execute(
            """INSERT INTO recipients
               (agency_name, city, email, contact, business_type, reliability,
                notes, first_name, included, status)
               VALUES (?,?,?,?,?,?,?,?,?,'pending')""",
            (agency, (data.get("city") or "").strip(), email,
             (data.get("contact") or "").strip(), (data.get("business_type") or "").strip(),
             (data.get("reliability") or "").strip(), (data.get("notes") or "").strip(),
             first, included),
        )
        conn.commit()
        rid = cur.lastrowid
    log("info", f"Agency added: {agency} <{email}>")
    return jsonify({"ok": True, "id": rid})


@app.route("/api/recipient/<int:rid>", methods=["POST"])
def api_recipient_update(rid):
    data = request.get_json(force=True) or {}
    fields = {}
    for k in EDITABLE_RECIPIENT_FIELDS:
        if k in data:
            if k == "agency_name":
                fields[k] = proposals.clean_agency_name(data[k] or "")
            elif k == "email":
                em = (data[k] or "").strip().lower()
                if not EMAIL_RE.match(em):
                    return jsonify({"ok": False, "error": "Invalid email address."})
                fields[k] = em
            elif k == "included":
                fields[k] = 1 if data[k] else 0
            else:
                fields[k] = (data[k] or "").strip() if isinstance(data[k], str) else data[k]
    if not fields:
        return jsonify({"ok": False, "error": "nothing to update"})
    sets = ", ".join(f"{k}=?" for k in fields)
    args = list(fields.values()) + [rid]
    with db() as conn:
        try:
            conn.execute(f"UPDATE recipients SET {sets} WHERE id=?", args)
            conn.commit()
        except sqlite3.IntegrityError:
            return jsonify({"ok": False, "error": "That email is already used by another agency."})
    return jsonify({"ok": True})


@app.route("/api/recipient/<int:rid>/delete", methods=["POST"])
def api_recipient_delete(rid):
    with db() as conn:
        cur = conn.execute("DELETE FROM recipients WHERE id=?", (rid,))
        conn.commit()
    if cur.rowcount:
        log("info", f"Agency record #{rid} deleted.")
    return jsonify({"ok": True, "deleted": cur.rowcount})


@app.route("/api/recipient/<int:rid>/reset", methods=["POST"])
def api_recipient_reset(rid):
    with db() as conn:
        conn.execute("UPDATE recipients SET status='pending', error='', sent_at='' WHERE id=?", (rid,))
        conn.commit()
    return jsonify({"ok": True})


@app.route("/api/reset_failed", methods=["POST"])
def api_reset_failed():
    with db() as conn:
        cur = conn.execute("UPDATE recipients SET status='pending', error='' WHERE status='failed'")
        conn.commit()
    return jsonify({"ok": True, "reset": cur.rowcount})


@app.route("/api/recipient/<int:rid>/send", methods=["POST"])
def api_recipient_send(rid):
    """Send the proposal to one specific recipient on demand."""
    return jsonify(sender.send_to(rid))


@app.route("/api/preview", methods=["POST"])
def api_preview():
    data = request.get_json(force=True) or {}
    s = get_settings()
    draft_id = data.get("draft_id") or s.get("draft_id")
    rid = data.get("recipient_id")
    if rid:
        with db() as conn:
            row = conn.execute("SELECT * FROM recipients WHERE id=?", (rid,)).fetchone()
        rec = dict(row) if row else {}
    else:
        rec = {"agency_name": "Sample Travel Agency", "first_name": "", "email": "sample@example.com"}
    rendered = proposals.render(draft_id, rec, s)
    rendered["to"] = rec.get("email", "")
    rendered["agency"] = proposals.clean_agency_name(rec.get("agency_name", ""))
    return jsonify(rendered)


@app.route("/api/test", methods=["POST"])
def api_test():
    data = request.get_json(force=True) or {}
    s = get_settings()
    problems = sender.validate_ready(s)
    if problems:
        return jsonify({"ok": False, "error": " ".join(problems)})
    # Default to a REAL mailbox. Never default to smtp_user — with Brevo that's a
    # login like "xxxx@smtp-brevo.com" which is not a deliverable inbox, so the
    # send "succeeds" but you never receive it.
    to = (data.get("to") or s.get("reply_to") or s.get("from_email") or "").strip().lower()
    if not EMAIL_RE.match(to):
        return jsonify({"ok": False, "error": "Enter a real email address to send the test to."})
    if to.endswith("@smtp-brevo.com") or to.endswith("@smtp-relay.brevo.com"):
        return jsonify({"ok": False, "error": "That's your Brevo SMTP login, not an inbox. "
                                              "Enter a real address (e.g. your Gmail)."})
    rec = {
        "agency_name": data.get("agency") or "Your Test Agency",
        "first_name": "", "email": to,
    }
    ok, err = sender.send_one(rec, s)
    log("info" if ok else "error", f"Test email to {to}: {'OK' if ok else err}")
    return jsonify({"ok": ok, "error": err, "to": to})


@app.route("/api/start", methods=["POST"])
def api_start():
    return jsonify(sender.start())


@app.route("/api/pause", methods=["POST"])
def api_pause():
    return jsonify(sender.pause())


@app.route("/api/resume", methods=["POST"])
def api_resume():
    return jsonify(sender.resume())


@app.route("/api/stop", methods=["POST"])
def api_stop():
    return jsonify(sender.stop())


@app.route("/api/send_next", methods=["POST"])
def api_send_next():
    return jsonify(sender.send_next_manual())


@app.route("/api/log")
def api_log():
    limit = int(request.args.get("limit") or 60)
    with db() as conn:
        rows = conn.execute(
            "SELECT ts,level,message FROM log ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    return jsonify([dict(r) for r in rows][::-1])


def bootstrap():
    init_db()
    import_recipients()
    # Make the active send path obvious in the logs. If this says SMTP in
    # production (e.g. Railway), BREVO_API_KEY is not set in the environment and
    # SMTP sends will time out because the platform blocks outbound SMTP ports.
    mode = "Brevo HTTP API (port 443)" if os.environ.get("BREVO_API_KEY", "").strip() else "SMTP"
    log("info", f"Email send mode: {mode}")


# Run setup at import time so it also works under a WSGI server (gunicorn),
# which imports `app:app` and never executes the __main__ block below.
bootstrap()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    print(f"\n  RoamDigi Proposal Sending Agent running ->  http://127.0.0.1:{port}\n")
    app.run(host="127.0.0.1", port=port, debug=False, threaded=True)
