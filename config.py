"""config.py — central settings for Edu-Track.

Everything environment-specific lives here (or in environment variables)
so you never have to edit business logic to change a path or a coordinate.
"""
import os

# --- Database ---------------------------------------------------------------
DB_PATH = os.getenv("EDUTRACK_DB", "edutrack.db")

# --- Auth -------------------------------------------------------------------
# Student must use the official faculty email domain.
EMAIL_REGEX = r"^[A-Za-z0-9._%+-]+@fcom\.bu\.edu\.eg$"
MIN_PASSWORD_LEN = 8
MAX_PASSWORD_BYTES = 72  # bcrypt ignores/rejects anything beyond 72 bytes

# --- GPS attendance ---------------------------------------------------------
# !! VERIFY these coordinates on Google Maps (right-click the faculty building
# !! -> copy lat/lon). The defaults below are only a rough placeholder for Benha.
FACULTY_COORDS = (
    float(os.getenv("EDUTRACK_LAT", "30.4660")),
    float(os.getenv("EDUTRACK_LON", "31.1850")),
)
MAX_DISTANCE_M = 50
# Simulation mode lets you test without a phone. SET TO "0" IN PRODUCTION.
ALLOW_SIMULATION = os.getenv("EDUTRACK_ALLOW_SIM", "1") == "1"

# --- Academic data ----------------------------------------------------------
ATTENDANCE_THRESHOLD = 75  # % below which a student is flagged
YEARS = ["1st Year", "2nd Year", "3rd Year", "4th Year"]
SEMESTERS = ["First", "Second"]
COMPLAINT_TYPES = ["Academic", "Administrative", "Technical", "Suggestion"]
COMPLAINT_STATUSES = ["Pending", "Under Review", "Resolved"]

STUDY_TYPES = ["Regular", "Affiliated"]   # منتظم / منتسب

# Show a "demo accounts" box on the login page (testing only!). Run seed_demo.py first.
SHOW_DEMO = os.getenv("EDUTRACK_SHOW_DEMO", "0") == "1"
DAYS = [0, 1, 2, 3, 4, 5]   # Saturday .. Thursday
