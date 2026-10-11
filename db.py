"""db.py — SQLite data layer for Edu-Track.

Rules followed everywhere in this file:
  * ALL SQL uses ? placeholders (never string formatting) -> no SQL injection.
  * Every private-data query (planner, profile) filters by the student_code
    that comes from the logged-in session, never from user-typed input.
  * This module does not import streamlit, so it can be tested on its own.
"""
import os
import secrets
import sqlite3
from contextlib import contextmanager

import bcrypt
import pandas as pd

import config

# ----------------------------------------------------------------------------
# Schema
# ----------------------------------------------------------------------------
SCHEMA = """
CREATE TABLE IF NOT EXISTS university_roster (
    student_code  TEXT PRIMARY KEY,
    full_name     TEXT NOT NULL,
    faculty_email TEXT NOT NULL UNIQUE COLLATE NOCASE,
    academic_year TEXT NOT NULL,
    department    TEXT NOT NULL,
    study_type    TEXT NOT NULL DEFAULT 'Regular' CHECK (study_type IN ('Regular','Affiliated'))
);

-- student_code is NULL for doctors/admins (they are not in the roster).
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    student_code  TEXT UNIQUE REFERENCES university_roster(student_code) ON DELETE CASCADE,
    email         TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password_hash TEXT NOT NULL,
    role          TEXT NOT NULL CHECK (role IN ('student','doctor','dean','admin')),
    full_name     TEXT,                     -- used for doctor/dean/admin accounts
    must_change_password INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS courses (
    course_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    course_name   TEXT NOT NULL,
    doctor_name   TEXT NOT NULL,
    doctor_id     INTEGER REFERENCES users(id) ON DELETE SET NULL,
    academic_year TEXT NOT NULL,
    semester      TEXT NOT NULL,
    planned_lectures INTEGER NOT NULL DEFAULT 14 CHECK (planned_lectures > 0)
);

CREATE TABLE IF NOT EXISTS lectures (
    lecture_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    course_id         INTEGER NOT NULL REFERENCES courses(course_id) ON DELETE CASCADE,
    lecture_number    INTEGER NOT NULL CHECK (lecture_number > 0),
    topic_covered     TEXT NOT NULL,
    date_created      TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    attendance_closed INTEGER NOT NULL DEFAULT 0,
    UNIQUE (course_id, lecture_number)
);

CREATE TABLE IF NOT EXISTS attendance (
    attendance_id INTEGER PRIMARY KEY AUTOINCREMENT,
    student_code  TEXT NOT NULL REFERENCES university_roster(student_code) ON DELETE CASCADE,
    lecture_id    INTEGER NOT NULL REFERENCES lectures(lecture_id) ON DELETE CASCADE,
    status        TEXT NOT NULL CHECK (status IN ('Present','Absent')),
    timestamp     TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    UNIQUE (student_code, lecture_id)       -- one record per student per lecture
);

CREATE TABLE IF NOT EXISTS private_planner (
    task_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    student_code  TEXT NOT NULL REFERENCES university_roster(student_code) ON DELETE CASCADE,
    task_title    TEXT NOT NULL,
    is_completed  INTEGER NOT NULL DEFAULT 0,
    date_created  TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

-- student_code NULL = anonymous complaint (we deliberately store no identity).
CREATE TABLE IF NOT EXISTS complaints (
    complaint_id  INTEGER PRIMARY KEY AUTOINCREMENT,
    student_code  TEXT REFERENCES university_roster(student_code) ON DELETE SET NULL,
    type          TEXT NOT NULL,
    content       TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'Pending'
                  CHECK (status IN ('Pending','Under Review','Resolved')),
    timestamp     TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

-- sender/receiver are identity codes: student_code for students, 'DR<id>' for doctors.
CREATE TABLE IF NOT EXISTS chat_messages (
    message_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    sender_code   TEXT NOT NULL,
    receiver_code TEXT NOT NULL,
    course_id     INTEGER NOT NULL REFERENCES courses(course_id) ON DELETE CASCADE,
    message_text  TEXT NOT NULL,
    timestamp     TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

-- Extra tables (needed by the features, not in the original spec):
CREATE TABLE IF NOT EXISTS alerts (
    alert_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    course_id  INTEGER NOT NULL REFERENCES courses(course_id) ON DELETE CASCADE,
    lecture_id INTEGER NOT NULL REFERENCES lectures(lecture_id) ON DELETE CASCADE,
    alert_type TEXT NOT NULL DEFAULT 'quiz',
    priority   TEXT NOT NULL DEFAULT 'high',
    timestamp  TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    UNIQUE (lecture_id, alert_type)         -- editing a lecture never duplicates the alert
);
CREATE TABLE IF NOT EXISTS course_settings (
    course_id INTEGER PRIMARY KEY REFERENCES courses(course_id) ON DELETE CASCADE,
    chat_open INTEGER NOT NULL DEFAULT 1
);

-- Halls (amphitheaters / section rooms) and the weekly timetable.
-- day_of_week: 0=Saturday ... 5=Thursday. Times are 'HH:MM' (zero-padded, so they sort as text).
CREATE TABLE IF NOT EXISTS halls (
    hall_id  INTEGER PRIMARY KEY AUTOINCREMENT,
    name     TEXT NOT NULL UNIQUE,
    kind     TEXT NOT NULL DEFAULT 'Amphitheater' CHECK (kind IN ('Amphitheater','Section Room')),
    capacity INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS schedule (
    slot_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    course_id    INTEGER NOT NULL REFERENCES courses(course_id) ON DELETE CASCADE,
    hall_id      INTEGER NOT NULL REFERENCES halls(hall_id) ON DELETE RESTRICT,
    session_type TEXT NOT NULL CHECK (session_type IN ('Lecture','Section')),
    group_label  TEXT NOT NULL DEFAULT '',          -- section number for sections
    day_of_week  INTEGER NOT NULL CHECK (day_of_week BETWEEN 0 AND 6),
    start_time   TEXT NOT NULL,
    end_time     TEXT NOT NULL,
    CHECK (start_time < end_time)
);
CREATE INDEX IF NOT EXISTS idx_schedule_hall   ON schedule(hall_id, day_of_week);
CREATE INDEX IF NOT EXISTS idx_schedule_course ON schedule(course_id);

-- Who did what (e.g. an admin resetting a student's password).
CREATE TABLE IF NOT EXISTS audit_log (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    actor     TEXT NOT NULL,
    action    TEXT NOT NULL,
    target    TEXT NOT NULL DEFAULT '',
    timestamp TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

-- Indices for the queries the app runs most often.
CREATE INDEX IF NOT EXISTS idx_roster_year_dept ON university_roster(academic_year, department);
CREATE INDEX IF NOT EXISTS idx_courses_year     ON courses(academic_year);
CREATE INDEX IF NOT EXISTS idx_courses_doctor   ON courses(doctor_id);
CREATE INDEX IF NOT EXISTS idx_att_lecture      ON attendance(lecture_id);
CREATE INDEX IF NOT EXISTS idx_planner_student  ON private_planner(student_code);
CREATE INDEX IF NOT EXISTS idx_chat_lookup      ON chat_messages(course_id, sender_code, receiver_code);
CREATE INDEX IF NOT EXISTS idx_complaints_stat  ON complaints(status);

-- VIEW
-- Every (student x closed lecture) pair; a missing attendance row counts as Absent.
-- Analytics and the early-warning list all read from this single view.
CREATE VIEW IF NOT EXISTS v_attendance_matrix AS
SELECT r.student_code, r.full_name, r.department, r.academic_year, r.study_type,
       c.course_id, c.course_name,
       l.lecture_id, l.lecture_number,
       date(l.date_created)           AS lecture_date,
       COALESCE(a.status, 'Absent')   AS status
FROM courses c
JOIN university_roster r ON r.academic_year = c.academic_year
JOIN lectures l          ON l.course_id = c.course_id AND l.attendance_closed = 1
LEFT JOIN attendance a   ON a.lecture_id = l.lecture_id AND a.student_code = r.student_code;
"""

# SQLite cannot change a CHECK constraint in place, so old databases get the table rebuilt
# (official procedure: create new -> copy -> drop old -> rename).
_USERS_REBUILD = """
PRAGMA foreign_keys = OFF;
BEGIN;
CREATE TABLE users_new (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    student_code  TEXT UNIQUE REFERENCES university_roster(student_code) ON DELETE CASCADE,
    email         TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password_hash TEXT NOT NULL,
    role          TEXT NOT NULL CHECK (role IN ('student','doctor','dean','admin')),
    full_name     TEXT,
    must_change_password INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
INSERT INTO users_new(id, student_code, email, password_hash, role, full_name)
    SELECT id, student_code, email, password_hash, role, full_name FROM users;
DROP TABLE users;
ALTER TABLE users_new RENAME TO users;
COMMIT;
PRAGMA foreign_keys = ON;
"""

_DUMMY_HASH = bcrypt.hashpw(b"dummy", bcrypt.gensalt())  # equalises login timing


# ----------------------------------------------------------------------------
# Connection helpers
# ----------------------------------------------------------------------------
@contextmanager
def get_conn():
    """Open a connection, commit on success, rollback on error, always close."""
    conn = sqlite3.connect(config.DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")   # SQLite ignores FKs unless asked
    conn.execute("PRAGMA journal_mode = WAL")  # readers don't block the writer
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _rows(sql, params=()):
    with get_conn() as c:
        return [dict(r) for r in c.execute(sql, params).fetchall()]


def _row(sql, params=()):
    with get_conn() as c:
        r = c.execute(sql, params).fetchone()
        return dict(r) if r else None


def _df(sql, params=()):
    with get_conn() as c:
        return pd.read_sql_query(sql, c, params=params)


def init_db():
    """Create tables/indices/view and make sure one admin exists."""
    tables_sql, view_sql = SCHEMA.split("-- VIEW", 1)
    with get_conn() as c:
        c.executescript(tables_sql)
        # migration for databases created before study_type existed
        cols = [r["name"] for r in c.execute("PRAGMA table_info(university_roster)")]
        if "study_type" not in cols:
            c.execute("ALTER TABLE university_roster ADD COLUMN study_type TEXT NOT NULL DEFAULT 'Regular'")
        ccols = [r["name"] for r in c.execute("PRAGMA table_info(courses)")]
        if "planned_lectures" not in ccols:
            c.execute("ALTER TABLE courses ADD COLUMN planned_lectures INTEGER NOT NULL DEFAULT 14")
        users_sql = c.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='users'").fetchone()["sql"]
        if "'dean'" not in users_sql:          # old CHECK constraint -> rebuild the table
            c.executescript(_USERS_REBUILD)
        c.execute("DROP VIEW IF EXISTS v_attendance_matrix")   # recreate: definition may have changed
        c.executescript(view_sql)
        if not c.execute("SELECT 1 FROM users WHERE role='admin' LIMIT 1").fetchone():
            email = os.getenv("EDUTRACK_ADMIN_EMAIL", "admin@fcom.bu.edu.eg").lower()
            pw = os.getenv("EDUTRACK_ADMIN_PASSWORD") or secrets.token_urlsafe(9)
            c.execute(
                "INSERT INTO users(email, password_hash, role, full_name) VALUES (?,?,?,?)",
                (email, hash_password(pw), "admin", "System Admin"),
            )
            # Shown once in the terminal. Change it after first login.
            print(f"[Edu-Track] First admin created -> {email} / {pw}")


# ----------------------------------------------------------------------------
# Passwords & authentication
# ----------------------------------------------------------------------------
def hash_password(pw: str) -> str:
    return bcrypt.hashpw(pw.encode("utf-8")[:72], bcrypt.gensalt()).decode()


def _check_password(pw: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(pw.encode("utf-8")[:72], hashed.encode())
    except ValueError:
        return False


def _user_dict(row):
    role = row["role"]
    if role == "student":
        code = row["student_code"]
    else:
        code = f"{ {'doctor': 'DR', 'dean': 'DN', 'admin': 'AD'}[role] }{row['id']}"
    return {
        "id": row["id"], "role": role, "email": row["email"], "code": code,
        "student_code": row["student_code"], "full_name": row["full_name"],
        "academic_year": row["academic_year"], "department": row["department"],
        "study_type": row["study_type"], "must_change": bool(row["must_change_password"]),
    }


_USER_SQL = """
SELECT u.id, u.email, u.role, u.student_code, u.password_hash, u.must_change_password,
       COALESCE(u.full_name, r.full_name) AS full_name,
       r.academic_year, r.department, r.study_type
FROM users u LEFT JOIN university_roster r ON r.student_code = u.student_code
"""


def authenticate(email: str, password: str):
    """Return the user dict, or None. Same work done whether or not the email exists."""
    row = _row(_USER_SQL + " WHERE u.email = ?", (email.strip().lower(),))
    if row is None:
        bcrypt.checkpw(b"dummy", _DUMMY_HASH)
        return None
    return _user_dict(row) if _check_password(password, row["password_hash"]) else None


def verify_roster(code: str, email: str):
    """First-time check. Returns (status, roster_row)."""
    r = _row("SELECT * FROM university_roster WHERE student_code = ?", (code,))
    if r is None:
        return "not_found", None
    if r["faculty_email"].lower() != email.lower():
        return "email_mismatch", None
    if _row("SELECT 1 AS x FROM users WHERE student_code = ?", (code,)):
        return "already_registered", None
    return "ok", r


def activate_student(code: str, email: str, password: str):
    """Create the student's account (re-verifies on the server side). Returns user or None."""
    status, _ = verify_roster(code, email)
    if status != "ok":
        return None
    with get_conn() as c:
        c.execute(
            "INSERT INTO users(student_code, email, password_hash, role) VALUES (?,?,?,'student')",
            (code, email.lower(), hash_password(password)),
        )
    return authenticate(email, password)


def create_staff(email, full_name, password, role):
    if role not in ("doctor", "dean", "admin"):
        return "bad_role"
    try:
        with get_conn() as c:
            c.execute(
                "INSERT INTO users(email, password_hash, role, full_name) VALUES (?,?,?,?)",
                (email.strip().lower(), hash_password(password), role, full_name.strip()),
            )
        return "ok"
    except sqlite3.IntegrityError:
        return "exists"


def list_doctors():
    return _rows("SELECT id, full_name, email FROM users WHERE role='doctor' ORDER BY full_name")


# ----------------------------------------------------------------------------
# Roster
# ----------------------------------------------------------------------------
def import_roster(df: pd.DataFrame) -> int:
    """Upsert roster rows. Expected columns: student_code, full_name, faculty_email,
    academic_year, department."""
    cols = ["student_code", "full_name", "faculty_email", "academic_year", "department", "study_type"]
    data = [tuple(str(x).strip() for x in row) for row in df[cols].itertuples(index=False)]
    with get_conn() as c:
        c.executemany(
            """INSERT INTO university_roster(student_code, full_name, faculty_email, academic_year, department, study_type)
               VALUES (?,?,?,?,?,?)
               ON CONFLICT(student_code) DO UPDATE SET
                 full_name=excluded.full_name, faculty_email=excluded.faculty_email,
                 academic_year=excluded.academic_year, department=excluded.department,
                 study_type=excluded.study_type""",
            data,
        )
    return len(data)


# ----------------------------------------------------------------------------
# Courses, lectures, syllabus, alerts
# ----------------------------------------------------------------------------
def create_course(name, doctor_id, doctor_name, year, semester, planned_lectures=14):
    with get_conn() as c:
        cur = c.execute(
            """INSERT INTO courses(course_name, doctor_name, doctor_id, academic_year, semester, planned_lectures)
               VALUES (?,?,?,?,?,?)""",
            (name.strip(), doctor_name, doctor_id, year, semester, int(planned_lectures)),
        )
        c.execute("INSERT INTO course_settings(course_id, chat_open) VALUES (?, 1)", (cur.lastrowid,))


def list_courses(year=None, doctor_id=None):
    sql, where, params = "SELECT * FROM courses", [], []
    if year is not None:
        where.append("academic_year = ?"); params.append(year)
    if doctor_id is not None:
        where.append("doctor_id = ?"); params.append(doctor_id)
    if where:
        sql += " WHERE " + " AND ".join(where)
    return _rows(sql + " ORDER BY course_name", tuple(params))


def latest_lecture(course_id):
    return _row(
        "SELECT * FROM lectures WHERE course_id = ? ORDER BY lecture_number DESC LIMIT 1",
        (course_id,),
    )


def list_lectures(course_id):
    return _rows(
        """SELECT l.lecture_id, l.lecture_number, l.topic_covered, l.date_created, l.attendance_closed,
                  (SELECT COUNT(*) FROM attendance a
                    WHERE a.lecture_id = l.lecture_id AND a.status='Present') AS present_count
           FROM lectures l WHERE l.course_id = ? ORDER BY l.lecture_number DESC""",
        (course_id,),
    )


def upsert_lecture(course_id, number, topic):
    """Create/update a lecture. If number is a multiple of 4 -> auto-post a quiz alert.
    Returns (lecture_id, quiz_alert_posted)."""
    number = int(number)
    quiz = number > 0 and number % 4 == 0
    with get_conn() as c:
        c.execute(
            """INSERT INTO lectures(course_id, lecture_number, topic_covered) VALUES (?,?,?)
               ON CONFLICT(course_id, lecture_number) DO UPDATE SET topic_covered = excluded.topic_covered""",
            (course_id, number, topic.strip()),
        )
        lid = c.execute(
            "SELECT lecture_id FROM lectures WHERE course_id=? AND lecture_number=?",
            (course_id, number),
        ).fetchone()[0]
        if quiz:
            c.execute(
                "INSERT OR IGNORE INTO alerts(course_id, lecture_id, alert_type) VALUES (?,?,'quiz')",
                (course_id, lid),
            )
    return lid, quiz


def active_alerts(year):
    """An alert is active only while its lecture is the course's LATEST lecture,
    so it disappears automatically once the doctor moves on to the next lecture."""
    return _rows(
        """SELECT a.alert_id, c.course_name, l.lecture_number, a.alert_type
           FROM alerts a
           JOIN courses c  ON c.course_id  = a.course_id
           JOIN lectures l ON l.lecture_id = a.lecture_id
           WHERE c.academic_year = ?
             AND l.lecture_number = (SELECT MAX(lecture_number) FROM lectures WHERE course_id = a.course_id)
           ORDER BY a.timestamp DESC""",
        (year,),
    )


# ----------------------------------------------------------------------------
# Attendance
# ----------------------------------------------------------------------------
def has_attended(student_code, lecture_id) -> bool:
    return _row(
        "SELECT 1 AS x FROM attendance WHERE student_code=? AND lecture_id=?",
        (student_code, lecture_id),
    ) is not None


def mark_present(student_code, lecture_id) -> str:
    """Returns: ok | already | closed | not_enrolled | no_lecture."""
    lec = _row(
        """SELECT l.attendance_closed, c.academic_year FROM lectures l
           JOIN courses c ON c.course_id = l.course_id WHERE l.lecture_id = ?""",
        (lecture_id,),
    )
    if lec is None:
        return "no_lecture"
    if lec["attendance_closed"]:
        return "closed"
    stu = _row("SELECT academic_year FROM university_roster WHERE student_code=?", (student_code,))
    if stu is None or stu["academic_year"] != lec["academic_year"]:
        return "not_enrolled"
    try:
        with get_conn() as c:
            c.execute(
                "INSERT INTO attendance(student_code, lecture_id, status) VALUES (?,?,'Present')",
                (student_code, lecture_id),
            )
        return "ok"
    except sqlite3.IntegrityError:
        return "already"


def close_lecture(lecture_id) -> int:
    """Doctor closes attendance: everyone enrolled without a record becomes 'Absent'."""
    with get_conn() as c:
        cur = c.execute(
            """INSERT OR IGNORE INTO attendance(student_code, lecture_id, status)
               SELECT r.student_code, l.lecture_id, 'Absent'
               FROM lectures l
               JOIN courses c ON c.course_id = l.course_id
               JOIN university_roster r ON r.academic_year = c.academic_year
               WHERE l.lecture_id = ?""",
            (lecture_id,),
        )
        c.execute("UPDATE lectures SET attendance_closed = 1 WHERE lecture_id = ?", (lecture_id,))
        return cur.rowcount


def student_attendance_summary(student_code, year):
    """Per-course attendance for ONE student (courses with no closed lectures show total=0)."""
    return _rows(
        """SELECT c.course_id, c.course_name,
                  COUNT(m.lecture_id) AS total,
                  COALESCE(SUM(m.status = 'Present'), 0) AS present
           FROM courses c
           LEFT JOIN v_attendance_matrix m
                  ON m.course_id = c.course_id AND m.student_code = ?
           WHERE c.academic_year = ?
           GROUP BY c.course_id ORDER BY c.course_name""",
        (student_code, year),
    )


# ----------------------------------------------------------------------------
# Private planner  (every function requires the owner's student_code)
# ----------------------------------------------------------------------------
def add_task(student_code, title):
    with get_conn() as c:
        c.execute("INSERT INTO private_planner(student_code, task_title) VALUES (?,?)",
                  (student_code, title.strip()))


def list_tasks(student_code):
    return _rows(
        "SELECT * FROM private_planner WHERE student_code=? ORDER BY is_completed, task_id DESC",
        (student_code,),
    )


def set_task_done(student_code, task_id, done):
    with get_conn() as c:   # student_code in WHERE = nobody can touch another student's task
        c.execute("UPDATE private_planner SET is_completed=? WHERE task_id=? AND student_code=?",
                  (int(done), task_id, student_code))


def delete_task(student_code, task_id):
    with get_conn() as c:
        c.execute("DELETE FROM private_planner WHERE task_id=? AND student_code=?",
                  (task_id, student_code))


# ----------------------------------------------------------------------------
# Complaints
# ----------------------------------------------------------------------------
def add_complaint(student_code, ctype, content):
    """student_code=None -> anonymous (no identity is stored at all)."""
    with get_conn() as c:
        c.execute("INSERT INTO complaints(student_code, type, content) VALUES (?,?,?)",
                  (student_code, ctype, content.strip()))


def list_complaints(kind="all"):
    where = {"all": "", "anonymous": "WHERE c.student_code IS NULL",
             "named": "WHERE c.student_code IS NOT NULL"}[kind]
    return _rows(
        f"""SELECT c.complaint_id, c.type, c.content, c.status, c.timestamp,
                   c.student_code, r.full_name
            FROM complaints c LEFT JOIN university_roster r ON r.student_code = c.student_code
            {where} ORDER BY c.complaint_id DESC"""   # `where` comes from the fixed dict above
    )


def set_complaint_status(complaint_id, status):
    if status not in config.COMPLAINT_STATUSES:
        return
    with get_conn() as c:
        c.execute("UPDATE complaints SET status=? WHERE complaint_id=?", (status, complaint_id))


# ----------------------------------------------------------------------------
# Chat (office hours)
# ----------------------------------------------------------------------------
def doctor_code(doctor_id) -> str:
    return f"DR{doctor_id}"


def is_chat_open(course_id) -> bool:
    r = _row("SELECT chat_open FROM course_settings WHERE course_id=?", (course_id,))
    return bool(r["chat_open"]) if r else True


def set_chat_open(course_id, is_open):
    with get_conn() as c:
        c.execute(
            """INSERT INTO course_settings(course_id, chat_open) VALUES (?,?)
               ON CONFLICT(course_id) DO UPDATE SET chat_open = excluded.chat_open""",
            (course_id, int(is_open)),
        )


def send_message(sender, receiver, course_id, text, from_student=True) -> bool:
    """Server-side rule: students cannot post while the chat is closed."""
    text = text.strip()[:1000]
    if not text or (from_student and not is_chat_open(course_id)):
        return False
    with get_conn() as c:
        c.execute(
            "INSERT INTO chat_messages(sender_code, receiver_code, course_id, message_text) VALUES (?,?,?,?)",
            (sender, receiver, course_id, text),
        )
    return True


def get_conversation(course_id, a, b, limit=100):
    return _rows(
        """SELECT * FROM (
             SELECT * FROM chat_messages
             WHERE course_id = ? AND ((sender_code=? AND receiver_code=?) OR (sender_code=? AND receiver_code=?))
             ORDER BY message_id DESC LIMIT ?
           ) ORDER BY message_id""",
        (course_id, a, b, b, a, limit),
    )


def chat_partners(course_id, dr_code):
    """Students who have written to this doctor in this course."""
    return _rows(
        """SELECT DISTINCT m.sender_code AS code, COALESCE(r.full_name, m.sender_code) AS name
           FROM chat_messages m LEFT JOIN university_roster r ON r.student_code = m.sender_code
           WHERE m.course_id = ? AND m.receiver_code = ?""",
        (course_id, dr_code),
    )


# ----------------------------------------------------------------------------
# Analytics (all read from v_attendance_matrix)
# ----------------------------------------------------------------------------
def attendance_by_group() -> pd.DataFrame:
    return _df(
        """SELECT academic_year, department,
                  SUM(status='Present') AS present, SUM(status='Absent') AS absent,
                  ROUND(100.0 * SUM(status='Present') / COUNT(*), 1) AS rate
           FROM v_attendance_matrix GROUP BY academic_year, department
           ORDER BY academic_year, department"""
    )


def attendance_trend(year=None) -> pd.DataFrame:
    return _df(
        """SELECT lecture_date, ROUND(100.0 * SUM(status='Present') / COUNT(*), 1) AS rate
           FROM v_attendance_matrix WHERE (? IS NULL OR academic_year = ?)
           GROUP BY lecture_date ORDER BY lecture_date""",
        (year, year),
    )


def early_warning(threshold=75, min_lectures=1) -> pd.DataFrame:
    """Students below `threshold`% in any course (with at least `min_lectures` closed lectures)."""
    return _df(
        """SELECT student_code, full_name, academic_year, department, study_type, course_name,
                  SUM(status='Present') AS present, COUNT(*) AS total,
                  ROUND(100.0 * SUM(status='Present') / COUNT(*), 1) AS rate
           FROM v_attendance_matrix
           GROUP BY student_code, course_id
           HAVING total >= ? AND rate < ?
           ORDER BY rate, full_name""",
        (min_lectures, threshold),
    )


# ----------------------------------------------------------------------------
# Live attendance + dashboard statistics
# ----------------------------------------------------------------------------
def attendance_record(student_code, lecture_id):
    return _row("SELECT status, timestamp FROM attendance WHERE student_code=? AND lecture_id=?",
                (student_code, lecture_id))


def lecture_attendees(lecture_id):
    """Who has marked present so far: name, code, year, study type (Regular/Affiliated)."""
    return _rows(
        """SELECT r.full_name, r.student_code, r.academic_year, r.study_type, a.timestamp
           FROM attendance a JOIN university_roster r ON r.student_code = a.student_code
           WHERE a.lecture_id = ? AND a.status = 'Present'
           ORDER BY a.timestamp DESC""",
        (lecture_id,),
    )


def doctor_stats(doctor_id):
    one = lambda sql: _row(sql, (doctor_id,))["n"]
    return {
        "courses": one("SELECT COUNT(*) AS n FROM courses WHERE doctor_id = ?"),
        "students": one("""SELECT COUNT(DISTINCT r.student_code) AS n FROM courses c
                           JOIN university_roster r ON r.academic_year = c.academic_year
                           WHERE c.doctor_id = ?"""),
        "lectures": one("""SELECT COUNT(*) AS n FROM lectures l JOIN courses c ON c.course_id = l.course_id
                           WHERE c.doctor_id = ?"""),
        "today_present": one("""SELECT COUNT(*) AS n FROM attendance a
                                JOIN lectures l ON l.lecture_id = a.lecture_id
                                JOIN courses c ON c.course_id = l.course_id
                                WHERE c.doctor_id = ? AND a.status = 'Present'
                                  AND date(a.timestamp) = date('now','localtime')"""),
    }


def admin_stats():
    n = lambda sql: _row(sql)["n"]
    avg = _row("SELECT ROUND(100.0 * SUM(status='Present') / COUNT(*), 1) AS n FROM v_attendance_matrix")["n"]
    return {
        "roster": n("SELECT COUNT(*) AS n FROM university_roster"),
        "activated": n("SELECT COUNT(*) AS n FROM users WHERE role='student'"),
        "open_complaints": n("SELECT COUNT(*) AS n FROM complaints WHERE status != 'Resolved'"),
        "avg_rate": avg,
    }


# ----------------------------------------------------------------------------
# Password management, registered students, audit log
# ----------------------------------------------------------------------------
# NOTE: passwords are stored as bcrypt hashes and can NEVER be read back.
# "Forgot password" = admin generates a temporary one (shown once) and the user must change it.
def change_password(user_id, new_pw):
    row = _row("SELECT password_hash FROM users WHERE id=?", (user_id,))
    if row is None:
        return "not_found"
    if _check_password(new_pw, row["password_hash"]):
        return "same"
    with get_conn() as c:
        c.execute("UPDATE users SET password_hash=?, must_change_password=0 WHERE id=?",
                  (hash_password(new_pw), user_id))
    return "ok"


def reset_student_password(student_code, actor):
    """Returns the temporary password (None if the student has no account)."""
    u = _row("SELECT id FROM users WHERE student_code=? AND role='student'", (student_code,))
    if not u:
        return None
    temp = secrets.token_urlsafe(8)
    with get_conn() as c:
        c.execute("UPDATE users SET password_hash=?, must_change_password=1 WHERE id=?",
                  (hash_password(temp), u["id"]))
        c.execute("INSERT INTO audit_log(actor, action, target) VALUES (?, 'reset_password', ?)",
                  (actor, student_code))
    return temp


def list_audit(limit=20):
    return _rows("SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (limit,))


def registered_students(year=None):
    return _rows(
        """SELECT r.student_code, r.full_name, r.academic_year, r.department, r.study_type,
                  u.email, u.created_at
           FROM users u JOIN university_roster r ON r.student_code = u.student_code
           WHERE u.role = 'student' AND (? IS NULL OR r.academic_year = ?)
           ORDER BY r.academic_year, r.full_name""",
        (year, year),
    )


def roster_by_year(year):
    return _rows(
        """SELECT r.student_code, r.full_name, r.faculty_email, r.department, r.study_type,
                  (u.id IS NOT NULL) AS registered
           FROM university_roster r LEFT JOIN users u ON u.student_code = r.student_code
           WHERE r.academic_year = ? ORDER BY r.full_name""",
        (year,),
    )


# ----------------------------------------------------------------------------
# Halls & timetable
# ----------------------------------------------------------------------------
def add_hall(name, kind, capacity):
    try:
        with get_conn() as c:
            c.execute("INSERT INTO halls(name, kind, capacity) VALUES (?,?,?)",
                      (name.strip(), kind, int(capacity)))
        return "ok"
    except sqlite3.IntegrityError:
        return "exists"


def list_halls():
    return _rows("SELECT * FROM halls ORDER BY kind, name")


def add_slot(course_id, hall_id, session_type, group_label, day, start, end):
    """Insert a timetable slot after checking three kinds of clash.
    Returns: ok | time_invalid | no_course | hall_conflict | doctor_conflict | year_conflict"""
    if start >= end:
        return "time_invalid"
    course = _row("SELECT doctor_id, academic_year FROM courses WHERE course_id=?", (course_id,))
    if course is None:
        return "no_course"
    overlap = "s.day_of_week = ? AND s.start_time < ? AND s.end_time > ?"
    with get_conn() as c:
        if c.execute(f"SELECT 1 FROM schedule s WHERE s.hall_id = ? AND {overlap}",
                     (hall_id, day, end, start)).fetchone():
            return "hall_conflict"
        if course["doctor_id"] and c.execute(
                f"SELECT 1 FROM schedule s JOIN courses k ON k.course_id = s.course_id "
                f"WHERE k.doctor_id = ? AND {overlap}", (course["doctor_id"], day, end, start)).fetchone():
            return "doctor_conflict"
        if session_type == "Lecture" and c.execute(
                f"SELECT 1 FROM schedule s JOIN courses k ON k.course_id = s.course_id "
                f"WHERE k.academic_year = ? AND s.session_type = 'Lecture' AND {overlap}",
                (course["academic_year"], day, end, start)).fetchone():
            return "year_conflict"
        c.execute(
            """INSERT INTO schedule(course_id, hall_id, session_type, group_label, day_of_week, start_time, end_time)
               VALUES (?,?,?,?,?,?,?)""",
            (course_id, hall_id, session_type, str(group_label or ""), day, start, end),
        )
    return "ok"


def list_schedule(year=None, doctor_id=None, hall_id=None):
    return _rows(
        """SELECT s.slot_id, s.day_of_week, s.start_time, s.end_time, s.session_type, s.group_label,
                  c.course_id, c.course_name, c.doctor_name, c.academic_year,
                  h.name AS hall_name, h.kind AS hall_kind
           FROM schedule s
           JOIN courses c ON c.course_id = s.course_id
           JOIN halls h   ON h.hall_id = s.hall_id
           WHERE (? IS NULL OR c.academic_year = ?)
             AND (? IS NULL OR c.doctor_id = ?)
             AND (? IS NULL OR s.hall_id = ?)
           ORDER BY s.day_of_week, s.start_time""",
        (year, year, doctor_id, doctor_id, hall_id, hall_id),
    )


def delete_slot(slot_id):
    with get_conn() as c:
        c.execute("DELETE FROM schedule WHERE slot_id = ?", (slot_id,))


# ----------------------------------------------------------------------------
# Dean analytics: doctors' progress + attendance by department / doctor
# ----------------------------------------------------------------------------
def doctor_overview() -> pd.DataFrame:
    """One row per course with syllabus progress (last lecture / planned) and attendance."""
    df = _df(
        """SELECT c.course_id, c.course_name, c.doctor_id, c.doctor_name, c.academic_year, c.planned_lectures,
                  COALESCE((SELECT MAX(lecture_number) FROM lectures l WHERE l.course_id = c.course_id), 0) AS last_lecture,
                  (SELECT topic_covered FROM lectures l WHERE l.course_id = c.course_id
                    ORDER BY lecture_number DESC LIMIT 1) AS last_topic,
                  CAST(julianday('now','localtime') -
                       julianday((SELECT MAX(date_created) FROM lectures l WHERE l.course_id = c.course_id))
                       AS INTEGER) AS days_since,
                  (SELECT ROUND(100.0 * SUM(status='Present') / COUNT(*), 1)
                     FROM v_attendance_matrix m WHERE m.course_id = c.course_id) AS attendance_rate
           FROM courses c ORDER BY c.doctor_name, c.course_name"""
    )
    df["progress"] = (100.0 * df["last_lecture"] / df["planned_lectures"]).clip(upper=100).round(1)
    return df


def attendance_by_department(year=None) -> pd.DataFrame:
    return _df(
        """SELECT department, SUM(status='Present') AS present, SUM(status='Absent') AS absent,
                  ROUND(100.0 * SUM(status='Present') / COUNT(*), 1) AS rate
           FROM v_attendance_matrix WHERE (? IS NULL OR academic_year = ?)
           GROUP BY department ORDER BY department""",
        (year, year),
    )


def attendance_by_doctor() -> pd.DataFrame:
    return _df(
        """SELECT c.doctor_name, SUM(m.status='Present') AS present, SUM(m.status='Absent') AS absent,
                  ROUND(100.0 * SUM(m.status='Present') / COUNT(*), 1) AS rate
           FROM v_attendance_matrix m JOIN courses c ON c.course_id = m.course_id
           GROUP BY c.doctor_id, c.doctor_name ORDER BY c.doctor_name"""
    )


def attendance_doctor_department() -> pd.DataFrame:
    return _df(
        """SELECT c.doctor_name, m.department,
                  ROUND(100.0 * SUM(m.status='Present') / COUNT(*), 1) AS rate
           FROM v_attendance_matrix m JOIN courses c ON c.course_id = m.course_id
           GROUP BY c.doctor_id, c.doctor_name, m.department"""
    )
