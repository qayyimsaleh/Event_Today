from flask import Flask, render_template, request, redirect, url_for, flash, get_flashed_messages, session, jsonify, Response
import pyodbc
import hashlib
from datetime import datetime
import re
import csv
from io import StringIO
import qrcode
import os

app = Flask(__name__)
app.secret_key = "SecretKey"


def is_mobile():
    ua = request.user_agent.string.lower()
    return any(keyword in ua for keyword in ['iphone', 'android', 'ipad', 'mobile'])


# MSSQL connection
conn_str = (
    r"DRIVER={};"
    r"SERVER="
    r"DATABASE=EventTodayDB;"
    r"UID=;"
    r"PWD="
    r"Encrypt=yes;"
    r"TrustServerCertificate=yes;"
)

def get_db_connection():
    return pyodbc.connect(conn_str)

# Note: you currently use a global connection/cursor — OK for small/test setups
conn = pyodbc.connect(conn_str)
cursor = conn.cursor()

# --------------------- Login ---------------------
@app.route("/", methods=["GET", "POST"])
def login():
    template = "login.html"  # single responsive template for all devices

    if request.method == "POST":
        username = request.form["username"].strip()
        password = request.form["password"].strip()
        
        # Compute both encodings to support old + new users
        hashed_utf16 = hashlib.sha256(password.encode("utf-16le")).hexdigest().upper()
        hashed_utf8 = hashlib.sha256(password.encode("utf-8")).hexdigest().upper()

        # Debugging output to confirm which hash matches
        print(f"DEBUG LOGIN: username={username}, password={password}")
        print(f"  UTF-16LE hash: {hashed_utf16}")
        print(f"  UTF-8 hash   : {hashed_utf8}")

        # Try both hash types for backward compatibility
        cursor.execute("""
            SELECT UserID FROM Users 
            WHERE Username=? AND (Password=? OR Password=?)
        """, (username, hashed_utf16, hashed_utf8))
        row = cursor.fetchone()

        if row:
            session["username"] = username
            session["user_id"] = row.UserID if hasattr(row, "UserID") else row[0]
            return redirect(url_for("dashboard"))
        else:
            return render_template(template, error="Invalid credentials")

    return render_template(template)


# --------------------- Dashboard ---------------------
@app.route("/dashboard")
def dashboard():
    if "username" not in session:
        return redirect(url_for("login"))

    user_id = session["user_id"]

    # Events created by this user
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

    # Events the user joined
    cursor.execute("""
        SELECT DISTINCT E.EventID, E.EventName, E.Description, E.StartTime, E.EndTime
        FROM Events E
        INNER JOIN Submissions S ON E.EventID = S.EventID
        WHERE S.UserID=?
        ORDER BY E.StartTime DESC
    """, (user_id,))
    joined_events = cursor.fetchall()

    return render_template("dashboard.html",
                           username=session["username"],
                           created_events=created_events,
                           joined_events=joined_events)


# --------------------- Logout ---------------------
@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# --------------------- Create Event ---------------------
@app.route("/create", methods=["GET", "POST"])
def create_event():
    if "username" not in session:
        return redirect(url_for("login"))

    if request.method == "POST":
        name = request.form["event_name"]
        description = request.form["description"]

        start_time_str = request.form["start_time"]
        end_time_str = request.form["end_time"]

        start_time = datetime.strptime(start_time_str, "%Y-%m-%dT%H:%M")
        end_time = datetime.strptime(end_time_str, "%Y-%m-%dT%H:%M")

        if end_time <= start_time:
            return render_template("create_event.html", error="End time must be after start time")

        safe_name = re.sub(r'\W+', '_', name)
        safe_time = start_time.strftime("%Y%m%d_%H%M")
        event_path = f"/event/{safe_name}_{safe_time}"
        full_url = request.host_url.rstrip("/") + event_path

        # fetch creator id properly (tuple param)
        cursor.execute("SELECT UserID FROM Users WHERE Username=?", (session["username"],))
        user_row = cursor.fetchone()
        if not user_row:
            return "User not found", 404
        creator_id = user_row.UserID if hasattr(user_row, "UserID") else user_row[0]

        # === Generate QR Code ===
        qr_dir = os.path.join("static", "qrcodes")
        os.makedirs(qr_dir, exist_ok=True)

        qr_filename = f"{safe_name}_{safe_time}.png"
        qr_path = os.path.join(qr_dir, qr_filename)
        qr_relative_path = f"qrcodes/{qr_filename}"  # store relative path

        img = qrcode.make(full_url)
        img.save(qr_path)

        # Insert event data into database
        cursor.execute(
            """
            INSERT INTO Events (EventName, Description, StartTime, EndTime, URL, CreatorID)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (name, description, start_time, end_time, event_path, creator_id)
        )
        conn.commit()

        qr_url = url_for("static", filename=qr_relative_path)

        return render_template(
            "event_created.html",
            event_name=name,
            event_url=event_path,
            qr_url=qr_url
        )

    return render_template("create_event.html")


# --------------------- Edit Event ---------------------
@app.route("/event/<int:event_id>/submissions")
def get_event_submissions(event_id):
    if "username" not in session:
        return jsonify({"error": "Unauthorized"}), 403

    cursor.execute("""
        SELECT SubmissionID, Name, EmployeeID, Department, Timestamp
        FROM Submissions
        WHERE EventID = ?
        ORDER BY Timestamp DESC
    """, (event_id,))
    rows = cursor.fetchall()

    submissions = [
        {
            "SubmissionID": r.SubmissionID,
            "Name": r.Name,
            "EmployeeID": r.EmployeeID,
            "Department": r.Department,
            "Timestamp": r.Timestamp.strftime("%Y-%m-%d %H:%M:%S")
        }
        for r in rows
    ]

    return jsonify(submissions)


@app.route("/edit/<int:event_id>", methods=["GET", "POST"])
def edit_event(event_id):
    if "username" not in session:
        return redirect(url_for("login"))

    cursor.execute("SELECT * FROM Events WHERE EventID=?", (event_id,))
    event = cursor.fetchone()
    if not event:
        return "Event not found", 404

    if request.method == "POST":
        # Update logic (same as before)
        name = request.form["event_name"]
        desc = request.form["description"]
        start = datetime.strptime(request.form["start_time"], "%Y-%m-%dT%H:%M")
        end = datetime.strptime(request.form["end_time"], "%Y-%m-%dT%H:%M")

        if end <= start:
            flash("End time must be after start time")
            return render_template("edit_event.html", event=event)

        cursor.execute("""
            UPDATE Events SET EventName=?, Description=?, StartTime=?, EndTime=? WHERE EventID=?
        """, (name, desc, start, end, event_id))
        conn.commit()
        flash("✅ Event updated successfully!")
        return redirect(url_for("edit_event", event_id=event_id))

    # compute QR path
    safe_name = re.sub(r'\W+', '_', event.EventName)
    safe_time = event.StartTime.strftime("%Y%m%d_%H%M")
    qr_filename = f"{safe_name}_{safe_time}.png"
    qr_path = os.path.join("static", "qrcodes", qr_filename)
    qr_url = url_for("static", filename=f"qrcodes/{qr_filename}") if os.path.exists(qr_path) else None

    return render_template("edit_event.html", event=event, qr_url=qr_url)


# --------------------- Delete Event ---------------------
@app.route("/delete/<int:event_id>", methods=["POST"])
def delete_event(event_id):
    if "username" not in session:
        return redirect(url_for("login"))

    # Delete submissions first
    cursor.execute("DELETE FROM Submissions WHERE EventID=?", (event_id,))
    cursor.execute("DELETE FROM Events WHERE EventID=?", (event_id,))
    conn.commit()
    return redirect(url_for("dashboard"))


# --------------------- Export CSV ---------------------
@app.route("/export/<int:event_id>")
def export_csv(event_id):
    if "username" not in session:
        return redirect(url_for("login"))

    cursor.execute("SELECT Name, EmployeeID, Department, Timestamp FROM Submissions WHERE EventID=?", (event_id,))
    submissions = cursor.fetchall()
    si = StringIO()
    cw = csv.writer(si)
    cw.writerow(["Name", "EmployeeID", "Department", "Timestamp"])
    for row in submissions:
        cw.writerow([row.Name, row.EmployeeID, row.Department, row.Timestamp])

    output = si.getvalue()
    return Response(output, mimetype="text/csv",
                    headers={"Content-Disposition": f"attachment; filename=event_{event_id}_attendance.csv"})


# --------------------- Public Event Form ---------------------
@app.route("/event/<path:event_url>", methods=["GET", "POST"])
def event_form(event_url):
    cursor.execute(
        "SELECT EventID, EventName, Description, StartTime, EndTime FROM Events WHERE URL=?",
        (f"/event/{event_url}",)
    )
    event = cursor.fetchone()
    if not event:
        return "Event not found", 404

    template = "attendance_form.html"  # single template for all devices

    if request.method == "POST":
        name = request.form["name"]
        employee_id = request.form["employee_id"]
        department = request.form["department"]
        timestamp = datetime.now()

        cursor.execute(
            "SELECT UserID FROM Users WHERE Username=? OR EmployeeID=?",
            (name, employee_id)
        )
        user_row = cursor.fetchone()
        user_id = user_row.UserID if user_row and hasattr(user_row, "UserID") else (user_row[0] if user_row else None)

        cursor.execute("""
            INSERT INTO Submissions (EventID, UserID, Name, EmployeeID, Department, Timestamp)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (event.EventID, user_id, name, employee_id, department, timestamp))
        conn.commit()

        return render_template(
            template,
            event_name=event.EventName,
            event_description=event.Description,
            event_start=event.StartTime,
            event_end=event.EndTime,
            success="Attendance submitted!"
        )

    return render_template(
        template,
        event_name=event.EventName,
        event_description=event.Description,
        event_start=event.StartTime,
        event_end=event.EndTime
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=80, debug=True)
