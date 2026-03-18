# EventToday - Event Attendance Management System

A Flask-based web application for creating events, generating QR codes for attendance, and tracking submissions. Designed for organizations that need a simple way to manage event check-ins.

## Features

- **User Authentication** — Login system with SHA-256 hashed passwords (supports both UTF-8 and UTF-16LE encodings for backward compatibility)
- **Event Management** — Create, edit, and delete events with start/end times
- **QR Code Generation** — Auto-generates a QR code for each event's attendance URL
- **Attendance Tracking** — Public-facing form lets attendees submit their name, employee ID, and department
- **CSV Export** — Export attendance records per event as `.csv` files
- **Dashboard** — View events you've created (with attendance counts) and events you've joined
- **Responsive Design** — Single set of templates for desktop and mobile

## Tech Stack

- **Backend:** Python 3, Flask
- **Database:** Microsoft SQL Server (via `pyodbc`)
- **QR Codes:** `qrcode` library
- **Frontend:** HTML templates (Jinja2)

## Prerequisites

- Python 3.8+
- Microsoft SQL Server instance
- ODBC Driver for SQL Server

## Installation

1. **Clone the repository:**

   ```bash
   git clone https://github.com/<your-username>/eventtoday.git
   cd eventtoday
   ```

2. **Install dependencies:**

   ```bash
   pip install flask pyodbc qrcode[pil]
   ```

3. **Configure the database connection:**

   Copy `.env.example` to `.env` and fill in your database credentials (see [Configuration](#configuration) below).

4. **Set up the database:**

   Create a SQL Server database named `EventTodayDB` with the following tables:

   ```sql
   CREATE TABLE Users (
       UserID INT PRIMARY KEY IDENTITY,
       Username NVARCHAR(100) UNIQUE NOT NULL,
       Password NVARCHAR(256) NOT NULL,
       EmployeeID NVARCHAR(50)
   );

   CREATE TABLE Events (
       EventID INT PRIMARY KEY IDENTITY,
       EventName NVARCHAR(200) NOT NULL,
       Description NVARCHAR(MAX),
       StartTime DATETIME NOT NULL,
       EndTime DATETIME NOT NULL,
       URL NVARCHAR(500),
       CreatorID INT FOREIGN KEY REFERENCES Users(UserID)
   );

   CREATE TABLE Submissions (
       SubmissionID INT PRIMARY KEY IDENTITY,
       EventID INT FOREIGN KEY REFERENCES Events(EventID),
       UserID INT NULL FOREIGN KEY REFERENCES Users(UserID),
       Name NVARCHAR(200),
       EmployeeID NVARCHAR(50),
       Department NVARCHAR(100),
       Timestamp DATETIME
   );
   ```

5. **Run the application:**

   ```bash
   python app.py
   ```

   The app will be available at `http://localhost:80`.

## Configuration

All sensitive configuration should be set via environment variables — **never hardcode credentials in source code.**

| Variable        | Description                    |
| --------------- | ------------------------------ |
| `DB_DRIVER`     | ODBC driver name               |
| `DB_SERVER`     | SQL Server hostname            |
| `DB_NAME`       | Database name (`EventTodayDB`) |
| `DB_UID`        | Database username              |
| `DB_PWD`        | Database password              |
| `SECRET_KEY`    | Flask session secret key       |

## Project Structure

```
eventtoday/
├── app.py                 # Main Flask application
├── hash.py                # Utility script for generating password hashes
├── static/
│   └── qrcodes/           # Generated QR code images
├── templates/
│   ├── login.html
│   ├── dashboard.html
│   ├── create_event.html
│   ├── event_created.html
│   ├── edit_event.html
│   └── attendance_form.html
└── README.md
```

## API Endpoints

| Method | Route                              | Description                  | Auth Required |
| ------ | ---------------------------------- | ---------------------------- | ------------- |
| GET/POST | `/`                              | Login page                   | No            |
| GET    | `/dashboard`                       | User dashboard               | Yes           |
| GET    | `/logout`                          | Logout                       | Yes           |
| GET/POST | `/create`                        | Create a new event           | Yes           |
| GET/POST | `/edit/<event_id>`               | Edit an existing event       | Yes           |
| POST   | `/delete/<event_id>`               | Delete an event              | Yes           |
| GET    | `/export/<event_id>`               | Export attendance as CSV     | Yes           |
| GET    | `/event/<event_id>/submissions`    | Get submissions as JSON      | Yes           |
| GET/POST | `/event/<event_url>`             | Public attendance form       | No            |


