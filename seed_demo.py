"""seed_demo.py — fills the database with TEST data and one demo account per role.

    python seed_demo.py            # add demo data (safe to re-run: skips if already seeded)
    python seed_demo.py --reset    # delete edutrack.db first, then seed

!! For testing only. Every demo account shares one well-known password, so never
!! use this on a database that holds real students.
"""
import os
import random
import sys
from datetime import datetime, timedelta

import pandas as pd

import config
import db

DEMO_PASSWORD = "Demo@12345"
DEMO_ACCOUNTS = [   # (role label, email)
    ("admin", "admin.demo@fcom.bu.edu.eg"),
    ("dean", "dean.demo@fcom.bu.edu.eg"),
    ("doctor", "dr.ahmed@fcom.bu.edu.eg"),
    ("doctor", "dr.mona@fcom.bu.edu.eg"),
    ("doctor", "dr.khaled@fcom.bu.edu.eg"),
    ("doctor", "dr.sara@fcom.bu.edu.eg"),
    ("student", "student1@fcom.bu.edu.eg"),   # 3rd year, regular
    ("student", "student2@fcom.bu.edu.eg"),   # 3rd year, affiliated
    ("student", "student3@fcom.bu.edu.eg"),   # 1st year
    ("student", "student4@fcom.bu.edu.eg"),   # 4th year
]
# Not activated on purpose: use the "First time? Activate account" tab with
# code D005 + student5@fcom.bu.edu.eg to test onboarding.

DEPTS = ["المحاسبة", "إدارة الأعمال", "الاقتصاد"]
FIRST = ["أحمد", "محمد", "محمود", "مصطفى", "عمر", "يوسف", "كريم", "علي", "سارة", "منى", "نور", "هدى", "ياسمين", "آية"]
LAST = ["حسن", "السيد", "عبد الله", "إبراهيم", "فتحي", "جمال", "رمضان", "الشناوي", "سليمان", "عطية"]

COURSES = [  # (name, doctor email, year)
    ("مبادئ المحاسبة", "dr.ahmed@fcom.bu.edu.eg", "1st Year"),
    ("مبادئ الإدارة", "dr.mona@fcom.bu.edu.eg", "1st Year"),
    ("محاسبة التكاليف", "dr.ahmed@fcom.bu.edu.eg", "2nd Year"),
    ("مبادئ الاقتصاد", "dr.khaled@fcom.bu.edu.eg", "2nd Year"),
    ("المحاسبة المتوسطة", "dr.sara@fcom.bu.edu.eg", "3rd Year"),
    ("السلوك التنظيمي", "dr.mona@fcom.bu.edu.eg", "3rd Year"),
    ("المراجعة", "dr.sara@fcom.bu.edu.eg", "4th Year"),
    ("الإدارة الاستراتيجية", "dr.khaled@fcom.bu.edu.eg", "4th Year"),
]


def seed():
    rnd = random.Random(42)
    db.init_db()
    if db._row("SELECT 1 AS x FROM users WHERE email = ?", ("dean.demo@fcom.bu.edu.eg",)):
        print("Demo data already present — nothing to do (use --reset to rebuild).")
        return

    # ---- staff
    db.create_staff("admin.demo@fcom.bu.edu.eg", "مدير النظام (تجريبي)", DEMO_PASSWORD, "admin")
    db.create_staff("dean.demo@fcom.bu.edu.eg", "أ.د. العميد (تجريبي)", DEMO_PASSWORD, "dean")
    names = {"dr.ahmed": "د. أحمد فؤاد", "dr.mona": "د. منى السعيد",
             "dr.khaled": "د. خالد مراد", "dr.sara": "د. سارة نبيل"}
    for key, name in names.items():
        db.create_staff(f"{key}@fcom.bu.edu.eg", name, DEMO_PASSWORD, "doctor")
    doctors = {d["email"]: d for d in db.list_doctors()}

    # ---- roster: 5 demo students + 36 random ones spread over years 1..4
    rows = [
        ["D001", "طالب تجريبي ١", "student1@fcom.bu.edu.eg", "3rd Year", DEPTS[0], "Regular"],
        ["D002", "طالب تجريبي ٢", "student2@fcom.bu.edu.eg", "3rd Year", DEPTS[1], "Affiliated"],
        ["D003", "طالب تجريبي ٣", "student3@fcom.bu.edu.eg", "1st Year", DEPTS[2], "Regular"],
        ["D004", "طالب تجريبي ٤", "student4@fcom.bu.edu.eg", "4th Year", DEPTS[0], "Regular"],
        ["D005", "طالب تجريبي ٥ (غير مفعّل)", "student5@fcom.bu.edu.eg", "2nd Year", DEPTS[1], "Regular"],
    ]
    for i in range(1, 37):
        code = f"2026{i:03d}"
        rows.append([code, f"{rnd.choice(FIRST)} {rnd.choice(LAST)}", f"s{code}@fcom.bu.edu.eg",
                     config.YEARS[i % 4], rnd.choice(DEPTS), "Affiliated" if rnd.random() < 0.25 else "Regular"])
    db.import_roster(pd.DataFrame(rows, columns=["student_code", "full_name", "faculty_email",
                                                 "academic_year", "department", "study_type"]))
    for r in rows[:4] + rows[5:13]:        # D001-D004 + 8 random students get active accounts
        db.activate_student(r[0], r[2], DEMO_PASSWORD)

    # ---- courses (Dr. Khaled is deliberately "behind" so the dean's monitor flags him)
    for name, email, year in COURSES:
        d = doctors[email]
        db.create_course(name, d["id"], d["full_name"], year, "First", 14)
    courses = db.list_courses()

    now = datetime.now()
    risky = {"D002", "2026005"}            # poor attendance -> shows in the early-warning list
    for course in courses:
        lazy = course["doctor_name"] == "د. خالد مراد"
        n_lectures = rnd.randint(2, 3) if lazy else rnd.randint(6, 11)
        shift = 35 if lazy else 0           # days: pushes the lazy doctor's last update far back
        students = [r["student_code"] for r in db.roster_by_year(course["academic_year"])]
        for n in range(1, n_lectures + 1):
            lid, _ = db.upsert_lecture(course["course_id"], n, f"الموضوع رقم {n}")   # own connection
            when = (now - timedelta(days=(n_lectures - n) * 7 + shift)).strftime("%Y-%m-%d %H:%M:%S")
            last_open = (n == n_lectures and not lazy)
            batch = []
            for sc in students:
                if last_open and rnd.random() > 0.3:
                    continue                # current lecture: only some students tapped so far
                p = 0.35 if sc in risky else (0.9 if rnd.random() < 0.85 else 0.55)
                batch.append((sc, lid, "Present" if rnd.random() < p else "Absent", when))
            with db.get_conn() as c:        # one short write transaction per lecture
                c.execute("UPDATE lectures SET date_created = ?, attendance_closed = ? WHERE lecture_id = ?",
                          (when, 0 if last_open else 1, lid))
                c.executemany("INSERT OR IGNORE INTO attendance(student_code, lecture_id, status, timestamp) "
                              "VALUES (?,?,?,?)", batch)

    # ---- halls + timetable (add_slot refuses clashes, so we just search for a free spot)
    for name, kind, cap in [("مدرج 1", "Amphitheater", 500), ("مدرج 2", "Amphitheater", 400),
                            ("مدرج 3", "Amphitheater", 300), ("قاعة سكشن A", "Section Room", 40),
                            ("قاعة سكشن B", "Section Room", 40)]:
        db.add_hall(name, kind, cap)
    halls = db.list_halls()
    amphi = [h for h in halls if h["kind"] == "Amphitheater"]
    rooms = [h for h in halls if h["kind"] == "Section Room"]
    times = [("09:00", "10:30"), ("10:45", "12:15"), ("12:30", "14:00"), ("14:15", "15:45")]
    for course in courses:
        for stype, pool, labels in (("Lecture", amphi, [""]), ("Section", rooms, ["1", "2"])):
            for label in labels:
                done = False
                for day in rnd.sample(config.DAYS, len(config.DAYS)):
                    for start, end in times:
                        for h in pool:
                            if db.add_slot(course["course_id"], h["hall_id"], stype, label, day, start, end) == "ok":
                                done = True
                                break
                        if done:
                            break
                    if done:
                        break

    # ---- a few complaints + one chat message
    db.add_complaint(None, "Technical", "الواي فاي في المدرج الثالث ضعيف جدًا ومحتاج تحسين.")
    db.add_complaint("D001", "Academic", "عايز أستفسر عن موعد امتحان منتصف الفصل لمادة المحاسبة المتوسطة.")
    db.add_complaint("D002", "Suggestion", "ياريت يتم إضافة ملخصات المحاضرات على التطبيق.")
    ahmed = doctors["dr.ahmed@fcom.bu.edu.eg"]
    first_course = db.list_courses(doctor_id=ahmed["id"])[0]
    db.send_message("D003", db.doctor_code(ahmed["id"]), first_course["course_id"],
                    "السلام عليكم دكتور، ممكن مرجع للمادة؟")

    print("\nDemo data created. Password for ALL demo accounts:", DEMO_PASSWORD)
    for role, email in DEMO_ACCOUNTS:
        print(f"  {role:8s} {email}")
    print("  (not activated, for onboarding test) code D005 + student5@fcom.bu.edu.eg")


if __name__ == "__main__":
    if "--reset" in sys.argv:
        for suffix in ("", "-wal", "-shm"):
            if os.path.exists(config.DB_PATH + suffix):
                os.remove(config.DB_PATH + suffix)
        print("Old database removed.")
    seed()
