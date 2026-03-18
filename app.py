import os
import re
import csv
import logging
from io import StringIO
from datetime import datetime
from functools import wraps

from flask import (
    Flask, render_template, request, redirect,
    url_for, flash, session, jsonify, Response
)
from flask_wtf import CSRFProtect
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from werkzeug.security import generate_password_hash, check_password_hash
import pyodbc
import hashlib
import qrcode

# ---------------------------------------------------------------------------
# App Setup
# ---------------------------------------------------------------------------
app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "CHANGE-ME-BEFORE-PRODUCTION")

if app.secret_key == "CHANGE-ME-BEFORE-PRODUCTION":
    logging.warning(
        "SECRET_KEY is not set! Generate one with: "
        "python -c \"import secrets; print(secrets.token_hex(32))\""
    )

# CSRF protection for all forms
csrf = CSRFProtect(app)

# Rate limiter (uses client IP by default)
limiter = Limiter(
    key_func=get_remote_address,
    app=app,
    default_limits=["200 per day", "50 per hour"],
    storage_uri="memory://",
)

# ---------------------------------------------------------------------------
# Database – per-request connections (thread-safe)
# ---------------------------------------------------------------------------
DB_DRIVER = os.environ.get("DB_DRIVER", "{ODBC Driver 18 for SQL Server}")
DB_SERVER = os.environ.get("DB_SERVER", "")
DB_NAME = os.environ.get("DB_NAME", "EventTodayDB")
DB_UID = os.environ.get("DB_UID", "")
DB_PWD = os.environ.get("DB_PWD", "")

CONN_STR = (
    f"DRIVER={DB_DRIVER};"
    f"SERVER={DB_SERVER};"
    f"DATABASE={DB_NAME};"
    f"UID={DB_UID};"
    f"PWD={DB_PWD};"
    "Encrypt=yes;"
    "TrustServerCertificate=yes;"
)


def get_db():
    """Return a new connection for the current request."""
    return pyodbc.connect(CONN_STR)


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------
def login_required(f):
    """Decorator that redirects to login when session is missing."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if "username" not in session:
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated


def _verify_password(stored_hash: str, password: str) -> bool:
    """
    Check a plaintext password against a stored hash.

    Supports (in order):
      1. Werkzeug (pbkdf2 / scrypt) – new standard
      2. Legacy SHA-256 UTF-16LE
      3. Legacy SHA-256 UTF-8
    """
    # Werkzeug hashes always start with a method prefix
    if stored_hash.startswith(("pbkdf2:", "scrypt:")):
        return check_password_hash(stored_hash, password)

    # Legacy SHA-256 (uppercase hex)
    sha_utf16 = hashlib.sha256(password.encode("utf-16le")).hexdigest().upper()
    sha_utf8 = hashlib.sha256(password.encode("utf-8")).hexdigest().upper()
    return stored_hash in (sha_utf16, sha_utf8)


def _upgrade_password_hash(conn, user_id: int, password: str):
    """Re-hash a legacy SHA-256 password to Werkzeug (pbkdf2)."""
    new_hash = generate_password_hash(password)
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE Users SET Password=? WHERE UserID=?", (new_hash, user_id)
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Routes – Login / Logout
# ---------------------------------------------------------------------------
@app.route("/", methods=["GET", "POST"])
@limiter.limit("10 per minute", methods=["POST"])   # brute-force protection
def login():
    if request.method == "POST":
        username = request.form["username"].strip()
        password = request.form["password"].strip()

        conn = get_db()
        try:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT UserID, Password FROM Users WHERE Username=?",
                (username,),
            )
            row = cursor.fetchone()

            if row and _verify_password(row.Password, password):
                user_id = row.UserID

                # Transparently upgrade legacy hashes
                if not row.Password.startswith(("pbkdf2:", "scrypt:")):
                    _upgrade_password_hash(conn, user_id, password)

                session["username"] = username
                session["user_id"] = user_id
                return redirect(url_for("dashboard"))
        finally:
            conn.close()

        return render_template("login.html", error="Invalid credentials")

    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------
@app.route("/dashboard")
@login_required
def dashboard():
    user_id = session["user_id"]
    conn = get_db()
    try:
        cursor = conn.cursor()

        cursor.execute("""
            SELECT E.EventID, E.EventName, E.Description, E.StartTime, E.EndTime,
                   COUNT(S.SubmissionID) AS AttendanceCount
            FROM Events E
            LEFT JOIN Submissions S ON E.EventID = S.EventID
            WHERE E.CreatorID=?
            GROUP BY E.EventID, E.EventName, E.Description, E.StartTime, E.EndTime
            ORDER BY E.StartTime DESC
        """, (user_id,))
        created_events = cursor.fetchall()

        cursor.execute("""
            SELECT DISTINCT E.EventID, E.EventName, E.Description, E.StartTime, E.EndTime
            FROM Events E
            INNER JOIN Submissions S ON E.EventID = S.EventID
            WHERE S.UserID=?
            ORDER BY E.StartTime DESC
        """, (user_id,))
        joined_events = cursor.fetchall()
    finally:
        conn.close()

    return render_template(
        "dashboard.html",
        username=session["username"],
        created_events=created_events,
        joined_events=joined_events,
    )


# ---------------------------------------------------------------------------
# Create Event
# ---------------------------------------------------------------------------
@app.route("/create", methods=["GET", "POST"])
@login_required
def create_event():
    if request.method == "POST":
        name = request.form["event_name"]
        description = request.form["description"]
        start_time = datetime.strptime(request.form["start_time"], "%Y-%m-%dT%H:%M")
        end_time = datetime.strptime(request.form["end_time"], "%Y-%m-%dT%H:%M")

        if end_time <= start_time:
            return render_template("create_event.html", error="End time must be after start time")

        safe_name = re.sub(r'\W+', '_', name)
        safe_time = start_time.strftime("%Y%m%d_%H%M")
        event_path = f"/event/{safe_name}_{safe_time}"
        full_url = request.host_url.rstrip("/") + event_path

        conn = get_db()
        try:
            cursor = conn.cursor()

            cursor.execute(
                "SELECT UserID FROM Users WHERE Username=?",
                (session["username"],),
            )
            user_row = cursor.fetchone()
            if not user_row:
                return "User not found", 404
            creator_id = user_row.UserID

            # Generate QR code
            qr_dir = os.path.join("static", "qrcodes")
            os.makedirs(qr_dir, exist_ok=True)
            qr_filename = f"{safe_name}_{safe_time}.png"
            qr_path = os.path.join(qr_dir, qr_filename)
            img = qrcode.make(full_url)
            img.save(qr_path)

            cursor.execute("""
                INSERT INTO Events (EventName, Description, StartTime, EndTime, URL, CreatorID)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (name, description, start_time, end_time, event_path, creator_id))
            conn.commit()
        finally:
            conn.close()

        qr_url = url_for("static", filename=f"qrcodes/{qr_filename}")
        return render_template(
            "event_created.html",
            event_name=name,
            event_url=event_path,
            qr_url=qr_url,
        )

    return render_template("create_event.html")


# ---------------------------------------------------------------------------
# Edit Event  (owner-only)
# ---------------------------------------------------------------------------
@app.route("/event/<int:event_id>/submissions")
@login_required
def get_event_submissions(event_id):
    conn = get_db()
    try:
        cursor = conn.cursor()

        # Verify ownership
        cursor.execute(
            "SELECT CreatorID FROM Events WHERE EventID=?", (event_id,)
        )
        event = cursor.fetchone()
        if not event or event.CreatorID != session["user_id"]:
            return jsonify({"error": "Forbidden"}), 403

        cursor.execute("""
            SELECT SubmissionID, Name, EmployeeID, Department, Timestamp
            FROM Submissions WHERE EventID=?
            ORDER BY Timestamp DESC
        """, (event_id,))
        rows = cursor.fetchall()
    finally:
        conn.close()

    submissions = [
        {
            "SubmissionID": r.SubmissionID,
            "Name": r.Name,
            "EmployeeID": r.EmployeeID,
            "Department": r.Department,
            "Timestamp": r.Timestamp.strftime("%Y-%m-%d %H:%M:%S"),
        }
        for r in rows
    ]
    return jsonify(submissions)


@app.route("/edit/<int:event_id>", methods=["GET", "POST"])
@login_required
def edit_event(event_id):
    conn = get_db()
    try:
        cursor = conn.cursor()

        cursor.execute("SELECT * FROM Events WHERE EventID=?", (event_id,))
        event = cursor.fetchone()
        if not event:
            return "Event not found", 404

        # Authorization: only the creator can edit
        if event.CreatorID != session["user_id"]:
            return "Forbidden", 403

        if request.method == "POST":
            name = request.form["event_name"]
            desc = request.form["description"]
            start = datetime.strptime(request.form["start_time"], "%Y-%m-%dT%H:%M")
            end = datetime.strptime(request.form["end_time"], "%Y-%m-%dT%H:%M")

            if end <= start:
                flash("End time must be after start time")
                return render_template("edit_event.html", event=event)

            cursor.execute("""
                UPDATE Events SET EventName=?, Description=?, StartTime=?, EndTime=?
                WHERE EventID=?
            """, (name, desc, start, end, event_id))
            conn.commit()
            flash("Event updated successfully!")
            return redirect(url_for("edit_event", event_id=event_id))
    finally:
        conn.close()

    # Compute QR path
    safe_name = re.sub(r'\W+', '_', event.EventName)
    safe_time = event.StartTime.strftime("%Y%m%d_%H%M")
    qr_filename = f"{safe_name}_{safe_time}.png"
    qr_path = os.path.join("static", "qrcodes", qr_filename)
    qr_url = url_for("static", filename=f"qrcodes/{qr_filename}") if os.path.exists(qr_path) else None

    return render_template("edit_event.html", event=event, qr_url=qr_url)


# ---------------------------------------------------------------------------
# Delete Event  (owner-only)
# ---------------------------------------------------------------------------
@app.route("/delete/<int:event_id>", methods=["POST"])
@login_required
def delete_event(event_id):
    conn = get_db()
    try:
        cursor = conn.cursor()

        # Authorization: only the creator can delete
        cursor.execute(
            "SELECT CreatorID FROM Events WHERE EventID=?", (event_id,)
        )
        event = cursor.fetchone()
        if not event or event.CreatorID != session["user_id"]:
            return "Forbidden", 403

        cursor.execute("DELETE FROM Submissions WHERE EventID=?", (event_id,))
        cursor.execute("DELETE FROM Events WHERE EventID=?", (event_id,))
        conn.commit()
    finally:
        conn.close()

    return redirect(url_for("dashboard"))


# ---------------------------------------------------------------------------
# Export CSV  (owner-only)
# ---------------------------------------------------------------------------
@app.route("/export/<int:event_id>")
@login_required
def export_csv(event_id):
    conn = get_db()
    try:
        cursor = conn.cursor()

        # Authorization
        cursor.execute(
            "SELECT CreatorID FROM Events WHERE EventID=?", (event_id,)
        )
        event = cursor.fetchone()
        if not event or event.CreatorID != session["user_id"]:
            return "Forbidden", 403

        cursor.execute(
            "SELECT Name, EmployeeID, Department, Timestamp "
            "FROM Submissions WHERE EventID=?",
            (event_id,),
        )
        submissions = cursor.fetchall()
    finally:
        conn.close()

    si = StringIO()
    cw = csv.writer(si)
    cw.writerow(["Name", "EmployeeID", "Department", "Timestamp"])
    for row in submissions:
        cw.writerow([row.Name, row.EmployeeID, row.Department, row.Timestamp])

    return Response(
        si.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename=event_{event_id}_attendance.csv"},
    )


# ---------------------------------------------------------------------------
# Public Attendance Form  (no login required, CSRF-exempt)
# ---------------------------------------------------------------------------
@csrf.exempt                       # public form – no session token available
@app.route("/event/<path:event_url>", methods=["GET", "POST"])
@limiter.limit("20 per minute")    # prevent spam submissions
def event_form(event_url):
    conn = get_db()
    try:
        cursor = conn.cursor()

        cursor.execute(
            "SELECT EventID, EventName, Description, StartTime, EndTime "
            "FROM Events WHERE URL=?",
            (f"/event/{event_url}",),
        )
        event = cursor.fetchone()
        if not event:
            return "Event not found", 404

        template_ctx = dict(
            event_name=event.EventName,
            event_description=event.Description,
            event_start=event.StartTime,
            event_end=event.EndTime,
        )

        if request.method == "POST":
            name = request.form["name"]
            employee_id = request.form["employee_id"]
            department = request.form["department"]
            timestamp = datetime.now()

            cursor.execute(
                "SELECT UserID FROM Users WHERE Username=? OR EmployeeID=?",
                (name, employee_id),
            )
            user_row = cursor.fetchone()
            user_id = user_row.UserID if user_row else None

            cursor.execute("""
                INSERT INTO Submissions (EventID, UserID, Name, EmployeeID, Department, Timestamp)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (event.EventID, user_id, name, employee_id, department, timestamp))
            conn.commit()

            template_ctx["success"] = "Attendance submitted!"
    finally:
        conn.close()

    return render_template("attendance_form.html", **template_ctx)


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    debug_mode = os.environ.get("FLASK_DEBUG", "false").lower() == "true"
    port = int(os.environ.get("PORT", 80))
    app.run(host="0.0.0.0", port=port, debug=debug_mode)