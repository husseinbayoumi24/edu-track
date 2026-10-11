"""app.py — Edu-Track UI + routing (Streamlit), v2.

Design:
  * One look per role (student = indigo, doctor = teal, admin = amber/slate),
    each with its own home dashboard and its own menu.
  * Desktop (>= 992px): menu is a top bar with icons.
    Phones / tablets (< 992px): the same menu lives in the side drawer
    (the ☰ button opens and closes it).  Both are the same widget state,
    CSS decides which one is visible.
Run:  streamlit run app.py
"""
import html
import re
from datetime import datetime, time as dtime

import pandas as pd
import streamlit as st
from geopy.distance import geodesic

import config
import db
from i18n import t

try:  # lightweight browser-geolocation component
    from streamlit_js_eval import get_geolocation
    HAS_GPS = True
except ImportError:
    HAS_GPS = False

st.set_page_config(page_title="Edu-Track", page_icon="🎓", layout="wide",
                   initial_sidebar_state="auto")

# Material icon for every menu entry
ICONS = {
    "home": "home", "attendance": "location_on", "syllabus": "menu_book", "planner": "checklist",
    "chat": "chat", "complaints": "support_agent", "profile": "person",
    "syllabus_tracker": "edit_note", "attendance_control": "fact_check",
    "complaints_admin": "inbox", "analytics": "insights", "roster": "groups", "manage": "settings",
    "schedule": "calendar_month", "schedule_admin": "edit_calendar", "registered": "badge",
    "doctors_monitor": "monitoring", "dean_attendance": "bar_chart",
}
# (accent 1, accent 2, max content width) per context
THEMES = {
    "auth":    ("#4f46e5", "#7c3aed", "560px"),
    "student": ("#4f46e5", "#7c3aed", "860px"),
    "doctor":  ("#0d9488", "#059669", "1120px"),
    "dean":    ("#be123c", "#7e22ce", "1300px"),
    "admin":   ("#d97706", "#1e293b", "1400px"),
}


# ============================================================================
# Setup, theming, small UI helpers
# ============================================================================
@st.cache_resource
def _boot():
    db.init_db()
    return True


def init_state():
    st.session_state.setdefault("lang", "ar")
    st.session_state.setdefault("user", None)
    st.session_state.setdefault("onboard", None)


CSS = """
<style>
.block-container {max-width: __MAXW__; padding-top: 1rem; padding-bottom: 4rem;}
footer {visibility: hidden;}
.hero {background: linear-gradient(135deg, __A1__, __A2__); color: #fff; border-radius: 22px;
       padding: 1.4rem 1.5rem; margin-bottom: 1rem; box-shadow: 0 10px 28px rgba(0,0,0,.18);}
.hero h2 {margin: 0; padding: 0; color: #fff; font-size: 1.6rem;}
.hero p {margin: .3rem 0 0; opacity: .93;}
.chip {display: inline-block; background: rgba(255,255,255,.22); border-radius: 999px;
       padding: .15rem .75rem; margin: .6rem .4rem 0 0; font-size: .82rem;}
.alert-banner {background: #b3261e; color: #fff; padding: .85rem 1rem; border-radius: 14px;
               margin-bottom: .6rem; font-weight: 600; box-shadow: 0 4px 14px rgba(179,38,30,.35);}
.brand {font-size: 1.25rem; font-weight: 800; color: __A1__;}
[data-testid="stVerticalBlockBorderWrapper"] {border-radius: 18px;}
[data-testid="stMetric"] {text-align: center;}
[data-testid^="stBaseButton-primary"] {background: linear-gradient(135deg, __A1__, __A2__);
       border: none; color: #fff; border-radius: 14px; min-height: 2.8rem; font-weight: 700;}
[data-testid="stBaseButton-secondary"] {border-radius: 14px; min-height: 2.6rem;}
[data-testid="stBaseButton-pillsActive"] {background: __A1__ !important; border-color: __A1__ !important;
       color: #fff !important;}
.st-key-topbar {border-bottom: 1px solid rgba(128,128,128,.25); padding-bottom: .5rem; margin-bottom: .9rem;}

/* Desktop: menu on TOP, hide the side drawer. Phones/tablets: menu in the side drawer. */
@media (min-width: 992px) {
  [data-testid="stSidebar"], [data-testid="stSidebarCollapsedControl"],
  [data-testid="stExpandSidebarButton"], [data-testid="collapsedControl"] {display: none !important;}
}
@media (max-width: 991px) {
  .st-key-topbar {display: none !important;}
}
@media (max-width: 640px) {
  .block-container {padding-left: .7rem; padding-right: .7rem;}
  .hero {padding: 1.1rem 1.1rem; border-radius: 18px;} .hero h2 {font-size: 1.3rem;}
  .stButton > button, .stFormSubmitButton > button {width: 100%;}
  /* KPI tiles: two per row on phones */
  .st-key-kpis [data-testid="stHorizontalBlock"] {flex-wrap: wrap; gap: .5rem;}
  .st-key-kpis [data-testid="stColumn"], .st-key-kpis [data-testid="column"] {
      min-width: calc(50% - .5rem) !important; flex: 1 1 calc(50% - .5rem) !important;}
}
__AUTH__
__RTL__
</style>
"""


def inject_css(ctx):
    a1, a2, maxw = THEMES[ctx]
    auth = ('[data-testid="stSidebar"], [data-testid="stSidebarCollapsedControl"], '
            '[data-testid="stExpandSidebarButton"], [data-testid="collapsedControl"] {display:none !important;}'
            ) if ctx == "auth" else ""
    rtl = (".stApp, [data-testid='stSidebar'] {direction: rtl; text-align: right;} .stChatMessage {direction: rtl;}"
           ) if st.session_state.lang == "ar" else ""
    css = (CSS.replace("__A1__", a1).replace("__A2__", a2).replace("__MAXW__", maxw)
              .replace("__AUTH__", auth).replace("__RTL__", rtl))
    st.markdown(css, unsafe_allow_html=True)


def hero(title, sub, chips=()):
    chips_html = "".join(f'<span class="chip">{html.escape(c)}</span>' for c in chips)
    st.markdown(f'<div class="hero"><h2>{html.escape(title)}</h2><p>{html.escape(sub)}</p>{chips_html}</div>',
                unsafe_allow_html=True)


def kpis(items):
    """items: [(material_icon, label, value)] -> a row of bordered metric tiles."""
    with st.container(key="kpis"):
        for col, (icon, label, value) in zip(st.columns(len(items)), items):
            with col, st.container(border=True):
                st.metric(f":material/{icon}: {label}", value)


def nav_label(key):
    return f":material/{ICONS[key]}: {t('menu_' + key)}"


def go(page):
    st.session_state.page = page


def quick_actions(keys):
    st.markdown(f"#### {t('quick_actions')}")
    for col, k in zip(st.columns(len(keys)), keys):
        col.button(nav_label(k), key=f"qa_{k}", on_click=go, args=(k,), width="stretch")


def pick_course(courses, key):
    if not courses:
        st.info(t("no_courses"))
        return None
    by_id = {c["course_id"]: c for c in courses}
    cid = st.selectbox(
        t("course"), list(by_id), key=key,
        format_func=lambda i: f"{by_id[i]['course_name']} — {by_id[i]['academic_year']}",
    )
    return by_id[cid]


def tr_cols(df: pd.DataFrame) -> pd.DataFrame:
    return df.rename(columns={c: t(f"col_{c}") for c in df.columns})


def show_alerts(year):
    for a in db.active_alerts(year):
        st.markdown(
            f'<div class="alert-banner">🔔 {html.escape(a["course_name"])} — '
            f'{html.escape(t("quiz_alert"))}</div>', unsafe_allow_html=True)


def student_card(user):
    """Name / code / year / study type — exactly the data attendance sends."""
    with st.container(border=True):
        st.markdown(f"**{user['full_name']}**")
        c1, c2, c3 = st.columns(3)
        c1.caption(t("student_code")); c1.write(user["student_code"])
        c2.caption(t("academic_year")); c2.write(user["academic_year"])
        c3.caption(t("study_type")); c3.write(t(f"study_{user['study_type']}"))


# ---------------------------------------------------------------- navigation
def set_lang(lang):
    st.session_state.lang = lang


def do_logout():
    for k in ("user", "page", "onboard", "gps_request", "nav_top", "nav_side", "just_marked"):
        st.session_state.pop(k, None)


def _nav_changed(key):
    v = st.session_state.get(key)
    if v:                       # pills can be de-selected -> ignore None
        st.session_state.page = v


def lang_buttons(suffix):
    c1, c2 = st.columns(2)
    for col, code, label in ((c1, "ar", "العربية"), (c2, "en", "English")):
        col.button(label, key=f"lang_{code}_{suffix}", on_click=set_lang, args=(code,), width="stretch",
                   type="primary" if st.session_state.lang == code else "secondary")


def top_bar(user, pages):
    """Desktop menu: brand | icon pills | account popover."""
    with st.container(key="topbar"):
        c1, c2, c3 = st.columns([2, 8, 2], vertical_alignment="center")
        c1.markdown('<span class="brand">🎓 Edu-Track</span>', unsafe_allow_html=True)
        with c2:
            st.pills("menu", list(pages), format_func=nav_label, key="nav_top",
                     selection_mode="single", on_change=_nav_changed, args=("nav_top",),
                     label_visibility="collapsed")
        with c3.popover(user["full_name"].split()[0], icon=":material/account_circle:", width="stretch"):
            st.caption(f"{t('role_' + user['role'])} • {user['email']}")
            st.markdown(f"**{t('language')}**")
            lang_buttons("top")
            st.button(t("logout"), key="logout_top", on_click=do_logout, icon=":material/logout:",
                      width="stretch")


def side_bar(user, pages):
    """Phone/tablet menu: the drawer opened by the ☰ button."""
    with st.sidebar:
        st.markdown('<span class="brand">🎓 Edu-Track</span>', unsafe_allow_html=True)
        st.caption(f"👤 {user['full_name']} — {t('role_' + user['role'])}")
        st.radio("menu", list(pages), format_func=nav_label, key="nav_side",
                 on_change=_nav_changed, args=("nav_side",), label_visibility="collapsed")
        st.divider()
        lang_buttons("side")
        st.button(t("logout"), key="logout_side", on_click=do_logout, icon=":material/logout:",
                  width="stretch")


# ============================================================================
# Authentication & onboarding
# ============================================================================
def page_auth():
    _, c2 = st.columns([3, 2])
    with c2:
        lang_buttons("auth")
    hero("🎓 Edu-Track", t("welcome_title"))
    st.caption(t("auth_sub"))
    with st.container(border=True):
        tab_login, tab_first = st.tabs([t("login"), t("first_time")])
        with tab_login:
            with st.form("login_form", border=False):
                email = st.text_input(t("email"), placeholder="name@fcom.bu.edu.eg")
                pw = st.text_input(t("password"), type="password")
                go_ = st.form_submit_button(t("login"), type="primary", width="stretch")
            if go_:
                user = db.authenticate(email, pw)
                if user:
                    st.session_state.user = user
                    st.rerun()
                st.error(t("bad_credentials"))
        with tab_first:
            onboarding_flow()
    if config.SHOW_DEMO:                       # testing only (EDUTRACK_SHOW_DEMO=1)
        from seed_demo import DEMO_ACCOUNTS, DEMO_PASSWORD
        with st.expander(t("demo_accounts")):
            st.caption(t("demo_hint"))
            st.code("\n".join(f"{r:8s} {e}" for r, e in DEMO_ACCOUNTS) + f"\n\npassword: {DEMO_PASSWORD}",
                    language=None)


def onboarding_flow():
    ob = st.session_state.onboard
    if ob is None:
        with st.form("verify_form", border=False):
            code = st.text_input(t("student_code"))
            email = st.text_input(t("univ_email"), placeholder="name@fcom.bu.edu.eg")
            go_ = st.form_submit_button(t("verify"), type="primary", width="stretch")
        if go_:
            code, email = code.strip(), email.strip().lower()
            if not code or not re.match(config.EMAIL_REGEX, email, re.IGNORECASE):
                st.error(t("invalid_email_domain"))
                return
            status, row = db.verify_roster(code, email)
            if status != "ok":
                st.error(t(f"onb_{status}"))
                return
            st.session_state.onboard = row
            st.rerun()
        return

    st.success(t("welcome_student", name=ob["full_name"]))
    st.markdown(
        f"**{t('academic_year')}:** {ob['academic_year']}  \n"
        f"**{t('department')}:** {ob['department']}  \n"
        f"**{t('study_type')}:** {t('study_' + ob['study_type'])}"
    )
    with st.form("password_form", border=False):
        p1 = st.text_input(t("new_password"), type="password")
        p2 = st.text_input(t("confirm_password"), type="password")
        go_ = st.form_submit_button(t("create_account"), type="primary", width="stretch")
    if go_:
        if len(p1) < config.MIN_PASSWORD_LEN:
            st.error(t("pw_too_short", n=config.MIN_PASSWORD_LEN))
        elif len(p1.encode("utf-8")) > config.MAX_PASSWORD_BYTES:
            st.error(t("pw_too_long"))
        elif p1 != p2:
            st.error(t("pw_mismatch"))
        else:
            user = db.activate_student(ob["student_code"], ob["faculty_email"], p1)
            if user:
                st.session_state.onboard = None
                st.session_state.user = user
                st.rerun()
            st.error(t("onb_already_registered"))
    if st.button(t("cancel")):
        st.session_state.onboard = None
        st.rerun()


# ============================================================================
# STUDENT  (indigo, narrow, mobile-first)
# ============================================================================
def page_home_student(user):
    hero(t("greeting", name=user["full_name"].split()[0]), t("home_student_sub"),
         [user["academic_year"], user["department"], t(f"study_{user['study_type']}")])
    rows = db.student_attendance_summary(user["student_code"], user["academic_year"])
    total = sum(r["total"] for r in rows)
    present = sum(r["present"] for r in rows)
    pct = round(100 * present / total, 1) if total else None
    open_tasks = sum(1 for x in db.list_tasks(user["student_code"]) if not x["is_completed"])
    kpis([("how_to_reg", t("kpi_attendance"), f"{pct}%" if pct is not None else "—"),
          ("checklist", t("kpi_open_tasks"), open_tasks),
          ("menu_book", t("kpi_courses"), len(rows))])
    if pct is not None and pct < config.ATTENDANCE_THRESHOLD:
        st.warning(t("below_threshold", n=config.ATTENDANCE_THRESHOLD))
    quick_actions(["attendance", "planner", "chat"])


def page_profile(user):
    hero(user["full_name"], user["email"],
         [user["academic_year"], user["department"], t(f"study_{user['study_type']}")])
    student_card(user)
    st.markdown(f"#### {t('attendance_per_course')}")
    rows = db.student_attendance_summary(user["student_code"], user["academic_year"])
    if not rows:
        st.info(t("no_courses"))
    for r in rows:
        with st.container(border=True):
            st.write(f"**{r['course_name']}**")
            if r["total"] == 0:
                st.caption(t("no_lectures_yet"))
                continue
            pct = round(100 * r["present"] / r["total"], 1)
            st.progress(min(pct / 100, 1.0))
            st.caption(f"{r['present']}/{r['total']} — {pct}%")
            if pct < config.ATTENDANCE_THRESHOLD:
                st.warning(t("below_threshold", n=config.ATTENDANCE_THRESHOLD))


def acquire_position():
    """ONE TAP: the button itself triggers the location check, nothing else to do.
    The browser asks for location permission the first time (this cannot and should not
    be bypassed). Coordinates are used for the distance check and then discarded."""
    sim = None
    if config.ALLOW_SIMULATION:
        with st.expander(t("dev_mode")):
            if st.toggle(t("dev_mode"), value=False, key="dev_toggle"):
                lat = st.number_input(t("latitude"), value=config.FACULTY_COORDS[0], format="%.6f")
                lon = st.number_input(t("longitude"), value=config.FACULTY_COORDS[1], format="%.6f")
                sim = (lat, lon)

    clicked = st.button(t("att_one_tap"), type="primary", width="stretch", key="att_btn")
    if sim is not None:
        return sim if clicked else None
    if not HAS_GPS:
        st.error(t("gps_missing"))
        return None
    if clicked:
        st.session_state.gps_request = True
    if st.session_state.get("gps_request"):
        loc = get_geolocation()            # None until the browser answers
        if loc and "coords" in loc:
            st.session_state.gps_request = False
            return (loc["coords"]["latitude"], loc["coords"]["longitude"])
        if loc and "error" in loc:
            st.session_state.gps_request = False
            st.error(t("gps_denied"))
        else:
            st.info(t("gps_waiting"))
    return None


def page_attendance(user):
    hero(t("menu_attendance"), t("home_student_sub"))
    course = pick_course(db.list_courses(year=user["academic_year"]), "att_course")
    if not course:
        return
    lec = db.latest_lecture(course["course_id"])
    if not lec:
        st.info(t("att_no_lecture"))
        return
    with st.container(border=True):
        st.markdown(f"**{t('lecture')} {lec['lecture_number']}** — {lec['topic_covered']}")

    rec = db.attendance_record(user["student_code"], lec["lecture_id"])
    if rec and rec["status"] == "Present":            # receipt of what was sent
        if st.session_state.pop("just_marked", False):
            st.balloons()
        st.success(t("att_receipt"))
        student_card(user)
        st.caption(f"{t('att_time')}: {rec['timestamp']}")
        return
    if lec["attendance_closed"]:
        st.warning(t("att_closed"))
        return

    st.caption(t("att_will_send"))
    student_card(user)
    pos = acquire_position()
    st.caption(t("att_privacy"))
    if pos is None:
        return
    if geodesic(config.FACULTY_COORDS, pos).meters < config.MAX_DISTANCE_M:
        res = db.mark_present(user["student_code"], lec["lecture_id"])
        if res == "ok":
            st.session_state.just_marked = True
            st.rerun()
        st.warning(t(f"att_{res}"))
    else:
        st.error(t("att_outside"))       # no coordinates / distance shown or stored


def page_syllabus(user):
    hero(t("menu_syllabus"), user["academic_year"])
    courses = db.list_courses(year=user["academic_year"])
    if not courses:
        st.info(t("no_courses"))
    for c in courses:
        with st.container(border=True):
            st.markdown(f"### {c['course_name']}")
            st.caption(f"{t('doctor')}: {c['doctor_name']}")
            lec = db.latest_lecture(c["course_id"])
            if not lec:
                st.caption(t("no_lectures_yet"))
                continue
            st.write(f"**{t('current_lecture')}:** {lec['lecture_number']} — {lec['topic_covered']}")
            with st.expander(t("all_lectures")):
                df = pd.DataFrame(db.list_lectures(c["course_id"]))[
                    ["lecture_number", "topic_covered", "date_created"]]
                st.dataframe(tr_cols(df), width="stretch", hide_index=True)


def page_planner(user):
    """Strictly private: every call passes the SESSION's student_code to the db layer."""
    hero(t("menu_planner"), t("planner_private"))
    with st.container(border=True):
        with st.form("task_form", clear_on_submit=True, border=False):
            title = st.text_input(t("new_task"))
            add = st.form_submit_button(t("add"), type="primary", width="stretch")
        if add and title.strip():
            db.add_task(user["student_code"], title)
            st.rerun()
    for tk in db.list_tasks(user["student_code"]):
        with st.container(border=True):
            c1, c2 = st.columns([8, 2], vertical_alignment="center")
            done = c1.checkbox(tk["task_title"], value=bool(tk["is_completed"]), key=f"task_{tk['task_id']}")
            if done != bool(tk["is_completed"]):
                db.set_task_done(user["student_code"], tk["task_id"], done)
                st.rerun()
            if c2.button("", icon=":material/delete:", key=f"del_{tk['task_id']}"):
                db.delete_task(user["student_code"], tk["task_id"])
                st.rerun()


def render_chat(msgs, me):
    for m in msgs:
        with st.chat_message("user" if m["sender_code"] == me else "assistant"):
            st.write(m["message_text"])
            st.caption(m["timestamp"])


def page_chat_student(user):
    hero(t("menu_chat"), t("home_doctor_sub"))
    course = pick_course(db.list_courses(year=user["academic_year"]), "chat_course")
    if not course:
        return
    if not course["doctor_id"]:
        st.info(t("no_doctor_assigned"))
        return
    dr = db.doctor_code(course["doctor_id"])
    is_open = db.is_chat_open(course["course_id"])
    st.button(t("refresh"), key="chat_refresh")
    render_chat(db.get_conversation(course["course_id"], user["code"], dr), user["code"])
    if not is_open:
        st.info(t("chat_closed"))
    msg = st.chat_input(t("type_message"), disabled=not is_open, max_chars=1000)
    if msg and db.send_message(user["code"], dr, course["course_id"], msg):
        st.rerun()


def page_complaints_student(user):
    hero(t("menu_complaints"), t("submit_anonymously"))
    with st.container(border=True):
        with st.form("complaint_form", clear_on_submit=True, border=False):
            ctype = st.selectbox(t("complaint_type"), config.COMPLAINT_TYPES,
                                 format_func=lambda x: t(f"type_{x}"))
            content = st.text_area(t("complaint_content"), max_chars=2000)
            anon = st.checkbox(t("submit_anonymously"))
            go_ = st.form_submit_button(t("submit"), type="primary", width="stretch")
        if go_:
            if len(content.strip()) < 10:
                st.error(t("complaint_too_short"))
            else:
                db.add_complaint(None if anon else user["student_code"], ctype, content)
                st.success(t("complaint_sent"))


# ============================================================================
# DOCTOR / TA  (teal, wider, dashboard-style)
# ============================================================================
def page_home_doctor(user):
    s = db.doctor_stats(user["id"])
    hero(t("greeting", name=user["full_name"]), t("home_doctor_sub"), [t("role_doctor")])
    kpis([("school", t("kpi_courses"), s["courses"]), ("groups", t("kpi_students"), s["students"]),
          ("menu_book", t("kpi_lectures"), s["lectures"]), ("how_to_reg", t("kpi_today_present"), s["today_present"])])
    quick_actions(["syllabus_tracker", "attendance_control", "chat"])


def page_syllabus_doctor(user):
    hero(t("menu_syllabus_tracker"), t("quiz_alert"))
    course = pick_course(db.list_courses(doctor_id=user["id"]), "doc_course")
    if not course:
        return
    latest = db.latest_lecture(course["course_id"])
    next_no = latest["lecture_number"] + 1 if latest else 1
    with st.container(border=True):
        with st.form("lecture_form", border=False):
            c1, c2 = st.columns([1, 3])
            num = c1.number_input(t("lecture_number"), min_value=1, value=next_no, step=1)
            topic = c2.text_input(t("topic_covered"))
            go_ = st.form_submit_button(t("save"), type="primary", width="stretch")
    if go_:
        if not topic.strip():
            st.error(t("topic_required"))
        else:
            _, quiz = db.upsert_lecture(course["course_id"], int(num), topic)
            st.success(t("lecture_saved"))
            if quiz:   # lecture 4, 8, 12 ... -> automatic system-wide alert
                st.warning(t("quiz_posted"))
    rows = db.list_lectures(course["course_id"])
    if rows:
        df = pd.DataFrame(rows)[["lecture_number", "topic_covered", "date_created", "present_count"]]
        st.dataframe(tr_cols(df), width="stretch", hide_index=True)


@st.fragment(run_every="10s")
def live_attendees(lecture_id):
    """Refreshes itself every 10 s: doctor sees students appear as they tap the button."""
    rows = db.lecture_attendees(lecture_id)
    st.markdown(f"#### {t('live_attendance')}")
    c = st.columns(3)
    c[0].metric(t("present_total"), len(rows))
    c[1].metric(t("study_Regular"), sum(1 for r in rows if r["study_type"] == "Regular"))
    c[2].metric(t("study_Affiliated"), sum(1 for r in rows if r["study_type"] == "Affiliated"))
    if not rows:
        st.info(t("attendees_none"))
        return
    df = pd.DataFrame(rows)
    df["study_type"] = df["study_type"].map(lambda s: t(f"study_{s}"))
    st.dataframe(tr_cols(df[["full_name", "student_code", "academic_year", "study_type", "timestamp"]]),
                 width="stretch", hide_index=True)


def page_attendance_doctor(user):
    hero(t("menu_attendance_control"), t("live_attendance"))
    course = pick_course(db.list_courses(doctor_id=user["id"]), "attc_course")
    if not course:
        return
    rows = db.list_lectures(course["course_id"])
    if not rows:
        st.info(t("no_lectures_yet"))
        return
    by_id = {r["lecture_id"]: r for r in rows}      # newest first
    lid = st.selectbox(t("pick_lecture"), list(by_id), key=f"ac_lec_{course['course_id']}",
                       format_func=lambda i: f"{by_id[i]['lecture_number']} — {by_id[i]['topic_covered']}")
    with st.container(border=True):
        live_attendees(lid)
    if by_id[lid]["attendance_closed"]:
        st.caption("✔ " + t("att_closed"))
        return
    with st.container(border=True):
        st.warning(t("close_attendance_warning"))
        sure = st.checkbox(t("confirm"), key=f"sure_{lid}")
        if st.button(t("close_attendance"), disabled=not sure, key=f"close_{lid}", type="primary"):
            n = db.close_lecture(lid)
            st.success(t("attendance_closed_msg", n=n))
            st.rerun()


def page_chat_doctor(user):
    hero(t("menu_chat"), t("chat_open_toggle"))
    course = pick_course(db.list_courses(doctor_id=user["id"]), "dchat_course")
    if not course:
        return
    cid = course["course_id"]
    is_open = st.toggle(t("chat_open_toggle"), value=db.is_chat_open(cid), key=f"open_{cid}")
    if is_open != db.is_chat_open(cid):
        db.set_chat_open(cid, is_open)
        st.rerun()
    partners = db.chat_partners(cid, user["code"])
    if not partners:
        st.info(t("no_messages"))
        return
    by_code = {p["code"]: p["name"] for p in partners}
    stu = st.selectbox(t("student"), list(by_code), format_func=lambda c: f"{by_code[c]} ({c})")
    st.button(t("refresh"), key="dchat_refresh")
    render_chat(db.get_conversation(cid, user["code"], stu), user["code"])
    reply = st.chat_input(t("type_message"), max_chars=1000)
    if reply and db.send_message(user["code"], stu, cid, reply, from_student=False):
        st.rerun()


# ============================================================================
# ADMIN  (amber/slate, full width, data-heavy)
# ============================================================================
def page_home_admin(user):
    s = db.admin_stats()
    at_risk = db.early_warning(config.ATTENDANCE_THRESHOLD, 1)["student_code"].nunique()
    hero(t("greeting", name=user["full_name"]), t("home_admin_sub"), [t("role_admin")])
    kpis([("groups", t("kpi_roster"), s["roster"]), ("verified_user", t("kpi_activated"), s["activated"]),
          ("inbox", t("kpi_open_complaints"), s["open_complaints"]),
          ("insights", t("kpi_avg_rate"), f"{s['avg_rate']}%" if s["avg_rate"] is not None else "—"),
          ("warning", t("kpi_at_risk"), at_risk)])
    quick_actions(["registered", "roster", "schedule_admin", "analytics"])


def page_complaints_admin(user):
    hero(t("menu_complaints_admin"), t("update_status"))
    kind = st.radio(t("filter"), ["all", "named", "anonymous"], horizontal=True,
                    format_func=lambda k: t(f"filter_{k}"))
    rows = db.list_complaints(kind)
    if not rows:
        st.info(t("no_data"))
        return
    view = pd.DataFrame([{
        "complaint_id": r["complaint_id"], "type": t(f"type_{r['type']}"), "content": r["content"],
        "student": f"{r['student_code']} — {r['full_name']}" if r["student_code"] else t("anonymous"),
        "status": t(f"status_{r['status']}"), "timestamp": r["timestamp"],
    } for r in rows])
    st.dataframe(tr_cols(view), width="stretch", hide_index=True)
    with st.container(border=True):
        st.markdown(f"#### {t('update_status')}")
        with st.form("status_form", border=False):
            c1, c2 = st.columns(2)
            cid = c1.selectbox(t("col_complaint_id"), [r["complaint_id"] for r in rows])
            new = c2.selectbox(t("col_status"), config.COMPLAINT_STATUSES, format_func=lambda s: t(f"status_{s}"))
            if st.form_submit_button(t("save"), type="primary"):
                db.set_complaint_status(cid, new)
                st.rerun()


def page_analytics(user):
    hero(t("menu_analytics"), t("tab_warning"))
    tab1, tab2, tab3 = st.tabs([t("tab_by_group"), t("tab_trend"), t("tab_warning")])
    with tab1:
        df = db.attendance_by_group()
        if df.empty:
            st.info(t("no_data"))
        else:
            df["group"] = df["department"] + " — " + df["academic_year"]
            st.bar_chart(df.set_index("group")[["present", "absent"]])
            st.dataframe(tr_cols(df[["academic_year", "department", "present", "absent", "rate"]]),
                         width="stretch", hide_index=True)
    with tab2:
        year = st.selectbox(t("academic_year"), [None] + config.YEARS,
                            format_func=lambda y: t("all_years") if y is None else y)
        tdf = db.attendance_trend(year)
        if tdf.empty:
            st.info(t("no_data"))
        else:
            st.line_chart(tdf.set_index("lecture_date")["rate"])
    with tab3:
        c1, c2 = st.columns(2)
        thr = c1.slider(t("threshold"), 10, 100, config.ATTENDANCE_THRESHOLD)
        minl = c2.number_input(t("min_lectures"), min_value=1, value=3, step=1)
        wdf = db.early_warning(thr, int(minl))
        if wdf.empty:
            st.success(t("no_warnings"))
        else:
            st.error(t("warning_count", n=wdf["student_code"].nunique()))
            wdf["study_type"] = wdf["study_type"].map(lambda s: t(f"study_{s}"))
            shown = tr_cols(wdf)
            st.dataframe(shown, width="stretch", hide_index=True)
            st.download_button(t("download_csv"), shown.to_csv(index=False).encode("utf-8-sig"),
                               "restriction_list.csv", "text/csv")


ROSTER_COLS = ["student_code", "full_name", "faculty_email", "academic_year", "department", "study_type"]
STUDY_MAP = {"regular": "Regular", "منتظم": "Regular", "affiliated": "Affiliated", "منتسب": "Affiliated"}


def roster_year_tabs():
    """Everyone who is in the system (uploaded roster), split into 1st/2nd/3rd/4th year."""
    st.markdown(f"#### {t('students_in_system')}")
    data = {y: db.roster_by_year(y) for y in config.YEARS}
    for y, tab in zip(config.YEARS, st.tabs([f"{y} ({len(data[y])})" for y in config.YEARS])):
        with tab:
            if not data[y]:
                st.info(t("no_data"))
                continue
            df = pd.DataFrame(data[y])
            df["study_type"] = df["study_type"].map(lambda v: t(f"study_{v}"))
            df["registered"] = df["registered"].map(lambda v: "✔" if v else "—")
            st.dataframe(tr_cols(df[["student_code", "full_name", "faculty_email", "department",
                                     "study_type", "registered"]]), width="stretch", hide_index=True)


def page_roster(user):
    hero(t("menu_roster"), t("roster_hint", years=", ".join(config.YEARS)))
    template = pd.DataFrame([["20260001", "Ahmed Ali", "ahmed.ali@fcom.bu.edu.eg",
                              "3rd Year", "Business Administration", "Regular"]], columns=ROSTER_COLS)
    st.download_button(t("download_template"), template.to_csv(index=False).encode("utf-8-sig"),
                       "roster_template.csv", "text/csv")
    up = st.file_uploader("CSV", type=["csv"])
    if not up:
        roster_year_tabs()
        return
    try:
        df = pd.read_csv(up, dtype=str, encoding="utf-8-sig").fillna("")
    except Exception:
        st.error(t("csv_unreadable"))
        return
    if not set(ROSTER_COLS).issubset(df.columns):
        st.error(t("csv_missing_cols", cols=", ".join(ROSTER_COLS)))
        return
    df = df[ROSTER_COLS].apply(lambda s: s.str.strip())
    df["study_type"] = df["study_type"].str.lower().map(STUDY_MAP).fillna("")   # Arabic or English
    ok = (df["student_code"] != "") & (df["full_name"] != "") & (df["department"] != "") \
        & (df["study_type"] != "") \
        & df["faculty_email"].str.match(config.EMAIL_REGEX, case=False) \
        & df["academic_year"].isin(config.YEARS) \
        & ~df["student_code"].duplicated(keep=False) & ~df["faculty_email"].str.lower().duplicated(keep=False)
    st.write(t("rows_valid", ok=int(ok.sum()), bad=int((~ok).sum())))
    if (~ok).any():
        st.warning(t("rows_invalid_hint"))
        st.dataframe(df[~ok], width="stretch", hide_index=True)
    st.dataframe(df[ok].head(20), width="stretch", hide_index=True)
    if ok.any() and st.button(t("import_valid"), type="primary"):
        st.success(t("imported", n=db.import_roster(df[ok])))
    roster_year_tabs()


def page_manage(user):
    hero(t("menu_manage"), t("create_staff"))
    with st.container(border=True):
        st.markdown(f"#### {t('create_staff')}")
        with st.form("staff_form", clear_on_submit=True, border=False):
            name = st.text_input(t("full_name"))
            email = st.text_input(t("email"))
            pw = st.text_input(t("password"), type="password")
            role = st.selectbox(t("role"), ["doctor", "dean", "admin"], format_func=lambda r: t(f"role_{r}"))
            go_ = st.form_submit_button(t("create_account"), type="primary", width="stretch")
        if go_:
            if not (name.strip() and "@" in email and len(pw) >= config.MIN_PASSWORD_LEN
                    and len(pw.encode("utf-8")) <= config.MAX_PASSWORD_BYTES):
                st.error(t("staff_invalid"))
            else:
                res = db.create_staff(email, name, pw, role)
                (st.success if res == "ok" else st.error)(t("staff_created") if res == "ok" else t("staff_exists"))

    with st.container(border=True):
        st.markdown(f"#### {t('create_course')}")
        doctors = db.list_doctors()
        if not doctors:
            st.info(t("need_doctor_first"))
        else:
            by_id = {d["id"]: d for d in doctors}
            with st.form("course_form", clear_on_submit=True, border=False):
                cname = st.text_input(t("course_name"))
                did = st.selectbox(t("doctor"), list(by_id), format_func=lambda i: by_id[i]["full_name"])
                c1, c2, c3 = st.columns(3)
                year = c1.selectbox(t("academic_year"), config.YEARS)
                sem = c2.selectbox(t("semester"), config.SEMESTERS)
                planned = c3.number_input(t("planned_lectures"), min_value=1, max_value=40, value=14)
                go_ = st.form_submit_button(t("save"), type="primary", width="stretch")
            if go_ and cname.strip():
                db.create_course(cname, did, by_id[did]["full_name"], year, sem, int(planned))
                st.success(t("course_created"))
    courses = db.list_courses()
    if courses:
        st.dataframe(tr_cols(pd.DataFrame(courses)[["course_name", "doctor_name", "academic_year", "semester", "planned_lectures"]]),
                     width="stretch", hide_index=True)


# ============================================================================
# Shared: timetable
# ============================================================================
SLOT_MSG = {"ok": "slot_ok", "time_invalid": "time_invalid", "no_course": "slot_no_course",
            "hall_conflict": "slot_hall_conflict", "doctor_conflict": "slot_doctor_conflict",
            "year_conflict": "slot_year_conflict"}


def render_schedule(rows):
    """Day tabs (Sat..Thu), one card per session."""
    if not rows:
        st.info(t("no_sessions"))
        return
    for d, tab in zip(config.DAYS, st.tabs([t(f"day_{d}") for d in config.DAYS])):
        with tab:
            day_rows = [r for r in rows if r["day_of_week"] == d]
            if not day_rows:
                st.caption(t("no_sessions"))
            for r in day_rows:
                with st.container(border=True):
                    is_lec = r["session_type"] == "Lecture"
                    grp = f" — {t('section_no')} {r['group_label']}" if not is_lec and r["group_label"] else ""
                    st.markdown(f"**{r['start_time']} – {r['end_time']}** · {'🏛️' if is_lec else '👥'} "
                                f"{t('session_' + r['session_type'])}{grp}")
                    st.write(f"{r['course_name']} — {r['doctor_name']}")
                    st.caption(f"{t('hall')}: {r['hall_name']} • {r['academic_year']}")


def schedule_filters():
    c1, c2, c3 = st.columns(3)
    year = c1.selectbox(t("academic_year"), [None] + config.YEARS, key="sf_year",
                        format_func=lambda y: t("all") if y is None else y)
    docs = {d["id"]: d["full_name"] for d in db.list_doctors()}
    did = c2.selectbox(t("doctor"), [None] + list(docs), key="sf_doc",
                       format_func=lambda i: t("all") if i is None else docs[i])
    halls = {h["hall_id"]: h["name"] for h in db.list_halls()}
    hid = c3.selectbox(t("hall"), [None] + list(halls), key="sf_hall",
                       format_func=lambda i: t("all") if i is None else halls[i])
    return {"year": year, "doctor_id": did, "hall_id": hid}


def page_schedule_view(user):
    """Read-only timetable. Student: own year. Doctor: own sessions. Dean: everything + filters."""
    hero(t("menu_schedule"), t("weekly_schedule"))
    if user["role"] == "student":
        rows = db.list_schedule(year=user["academic_year"])
    elif user["role"] == "doctor":
        rows = db.list_schedule(doctor_id=user["id"])
    else:
        rows = db.list_schedule(**schedule_filters())
    render_schedule(rows)


# ============================================================================
# DEAN  (rose/purple) — monitors doctors and attendance
# ============================================================================
STALE_DAYS = 14   # syllabus not updated for this long -> flagged


def page_home_dean(user):
    ov = db.doctor_overview()
    avg = db.admin_stats()["avg_rate"]
    at_risk = db.early_warning(config.ATTENDANCE_THRESHOLD, 1)["student_code"].nunique()
    hero(t("greeting", name=user["full_name"]), t("home_dean_sub"), [t("role_dean")])
    kpis([("school", t("kpi_doctors"), int(ov["doctor_id"].nunique()) if not ov.empty else 0),
          ("trending_up", t("kpi_progress"), f"{round(ov['progress'].mean(), 1)}%" if not ov.empty else "—"),
          ("insights", t("kpi_avg_rate"), f"{avg}%" if avg is not None else "—"),
          ("warning", t("kpi_at_risk"), at_risk)])
    quick_actions(["doctors_monitor", "dean_attendance", "schedule"])


def page_doctors_monitor(user):
    hero(t("menu_doctors_monitor"), t("kpi_progress"))
    ov = db.doctor_overview()
    if ov.empty:
        st.info(t("no_courses"))
        return
    g = ov.groupby("doctor_name").agg(courses=("course_id", "count"), progress=("progress", "mean"),
                                      attendance=("attendance_rate", "mean"),
                                      days_since=("days_since", "min")).reset_index()
    g[["progress", "attendance"]] = g[["progress", "attendance"]].round(1)
    g = g.rename(columns={"doctor_name": t("col_doctor"), "courses": t("col_courses"),
                          "progress": t("col_progress"), "attendance": t("col_attendance"),
                          "days_since": t("col_days_since")})
    pc = st.column_config
    st.dataframe(g, width="stretch", hide_index=True, column_config={
        t("col_progress"): pc.ProgressColumn(t("col_progress"), min_value=0, max_value=100, format="%.0f%%"),
        t("col_attendance"): pc.ProgressColumn(t("col_attendance"), min_value=0, max_value=100, format="%.0f%%"),
    })
    for doc, part in ov.groupby("doctor_name"):
        last = part["days_since"].min()
        stale = bool(pd.isna(last) or last > STALE_DAYS)
        with st.expander(f"{doc} — {round(part['progress'].mean())}%" + ("  ⚠️" if stale else "")):
            if stale:
                st.warning(t("never_updated") if pd.isna(last) else t("stale_warning"))
            for _, r in part.iterrows():
                with st.container(border=True):
                    st.markdown(f"**{r['course_name']}** — {r['academic_year']}")
                    st.progress(min(float(r["progress"]) / 100, 1.0))
                    topic = f" • {r['last_topic']}" if r["last_topic"] else ""
                    st.caption(f"{t('col_last_lecture')}: {int(r['last_lecture'])} / {int(r['planned_lectures'])}{topic}")
                    if pd.notna(r["attendance_rate"]):
                        st.caption(f"{t('col_attendance')}: {r['attendance_rate']}%")


def page_dean_attendance(user):
    hero(t("menu_dean_attendance"), t("tab_warning"))
    tabs = st.tabs([t("by_department"), t("by_doctor"), t("doctor_dept_matrix"), t("tab_warning")])
    with tabs[0]:
        year = st.selectbox(t("academic_year"), [None] + config.YEARS, key="dean_year",
                            format_func=lambda y: t("all_years") if y is None else y)
        df = db.attendance_by_department(year)
        if df.empty:
            st.info(t("no_data"))
        else:
            st.bar_chart(df.set_index("department")["rate"])
            st.dataframe(tr_cols(df), width="stretch", hide_index=True)
    with tabs[1]:
        df = db.attendance_by_doctor()
        if df.empty:
            st.info(t("no_data"))
        else:
            st.bar_chart(df.set_index("doctor_name")["rate"])
            st.dataframe(tr_cols(df), width="stretch", hide_index=True)
    with tabs[2]:
        df = db.attendance_doctor_department()
        if df.empty:
            st.info(t("no_data"))
        else:
            st.dataframe(df.pivot(index="doctor_name", columns="department", values="rate"), width="stretch")
    with tabs[3]:
        thr = st.slider(t("threshold"), 10, 100, config.ATTENDANCE_THRESHOLD, key="dean_thr")
        wdf = db.early_warning(thr, 3)
        if wdf.empty:
            st.success(t("no_warnings"))
        else:
            st.error(t("warning_count", n=wdf["student_code"].nunique()))
            wdf["study_type"] = wdf["study_type"].map(lambda v: t(f"study_{v}"))
            st.dataframe(tr_cols(wdf), width="stretch", hide_index=True)


# ============================================================================
# ADMIN additions: registered students + timetable management
# ============================================================================
def page_registered(user):
    hero(t("menu_registered"), t("reset_hint"))
    c1, c2 = st.columns([1, 2])
    year = c1.selectbox(t("academic_year"), [None] + config.YEARS, key="reg_year",
                        format_func=lambda y: t("all_years") if y is None else y)
    q = c2.text_input(t("reg_search"), key="reg_q").strip().lower()
    rows = db.registered_students(year)
    if q:
        rows = [r for r in rows if q in r["full_name"].lower() or q in r["student_code"].lower()]
    s = db.admin_stats()
    kpis([("verified_user", t("reg_count"), s["activated"]),
          ("person_off", t("reg_not_yet"), max(s["roster"] - s["activated"], 0))])
    if not rows:
        st.info(t("no_data"))
        return
    df = pd.DataFrame(rows)
    df["study_type"] = df["study_type"].map(lambda v: t(f"study_{v}"))
    st.dataframe(tr_cols(df[["full_name", "student_code", "academic_year", "department",
                             "study_type", "email", "created_at"]]), width="stretch", hide_index=True)
    with st.container(border=True):
        st.markdown(f"#### {t('reset_title')}")
        by_code = {r["student_code"]: r for r in rows}
        code = st.selectbox(t("reset_pick"), list(by_code), key="reset_pick",
                            format_func=lambda c: f"{by_code[c]['full_name']} ({c})")
        if st.button(t("reset_btn"), key="reset_btn", type="primary", icon=":material/lock_reset:"):
            temp = db.reset_student_password(code, user["email"])
            if temp:                       # shown ONCE, never stored in readable form
                st.success(t("reset_done"))
                st.code(temp, language=None)
    with st.expander(t("audit_log")):
        audit = db.list_audit()
        if audit:
            st.dataframe(pd.DataFrame(audit)[["timestamp", "actor", "action", "target"]],
                         width="stretch", hide_index=True)
        else:
            st.caption(t("no_data"))


def page_schedule_admin(user):
    hero(t("menu_schedule_admin"), t("weekly_schedule"))
    tab_view, tab_add, tab_halls = st.tabs([t("weekly_schedule"), t("add_session"), t("halls")])

    with tab_view:
        rows = db.list_schedule(**schedule_filters())
        render_schedule(rows)
        if rows:
            with st.container(border=True):
                by_id = {r["slot_id"]: r for r in rows}
                sid = st.selectbox(t("delete"), list(by_id), key="del_slot_sel", format_func=lambda i: (
                    f"{t('day_' + str(by_id[i]['day_of_week']))} {by_id[i]['start_time']} — "
                    f"{by_id[i]['course_name']} ({t('session_' + by_id[i]['session_type'])})"))
                if st.button(t("delete"), key="del_slot_btn", icon=":material/delete:"):
                    db.delete_slot(sid)
                    st.rerun()

    with tab_add:
        courses, halls = db.list_courses(), db.list_halls()
        if not courses:
            st.info(t("need_course_first"))
        elif not halls:
            st.info(t("need_hall_first"))
        else:
            stype = st.radio(t("session_type"), ["Lecture", "Section"], horizontal=True, key="new_slot_type",
                             format_func=lambda x: t(f"session_{x}"))
            by_c, by_h = {c["course_id"]: c for c in courses}, {h["hall_id"]: h for h in halls}
            with st.form("slot_form", border=False):
                cid = st.selectbox(t("course"), list(by_c), format_func=lambda i: (
                    f"{by_c[i]['course_name']} — {by_c[i]['doctor_name']} ({by_c[i]['academic_year']})"))
                hid = st.selectbox(t("hall"), list(by_h), format_func=lambda i: (
                    f"{by_h[i]['name']} ({t('kind_' + by_h[i]['kind'])}, {by_h[i]['capacity']})"))
                c1, c2, c3 = st.columns(3)
                day = c1.selectbox(t("day"), config.DAYS, format_func=lambda d: t(f"day_{d}"))
                start = c2.time_input(t("start_time"), value=dtime(9, 0), step=900)
                end = c3.time_input(t("end_time"), value=dtime(10, 30), step=900)
                group = st.number_input(t("section_no"), 1, 30, 1) if stype == "Section" else ""
                go_ = st.form_submit_button(t("add_session"), type="primary", width="stretch")
            if go_:
                res = db.add_slot(cid, hid, stype, group, day, start.strftime("%H:%M"), end.strftime("%H:%M"))
                (st.success if res == "ok" else st.error)(t(SLOT_MSG[res]))

    with tab_halls:
        with st.container(border=True):
            with st.form("hall_form", clear_on_submit=True, border=False):
                name = st.text_input(t("hall_name"))
                c1, c2 = st.columns(2)
                kind = c1.selectbox(t("hall_kind"), ["Amphitheater", "Section Room"],
                                    format_func=lambda k: t(f"kind_{k}"))
                cap = c2.number_input(t("capacity"), 0, 5000, 100, step=10)
                go_ = st.form_submit_button(t("save"), type="primary", width="stretch")
            if go_ and name.strip():
                res = db.add_hall(name, kind, cap)
                (st.success if res == "ok" else st.error)(t("hall_added") if res == "ok" else t("hall_exists"))
        halls = db.list_halls()
        if halls:
            hd = pd.DataFrame(halls)[["name", "kind", "capacity"]]
            hd["kind"] = hd["kind"].map(lambda k: t(f"kind_{k}"))
            st.dataframe(hd.rename(columns={"name": t("hall_name"), "kind": t("hall_kind"),
                                            "capacity": t("capacity")}), width="stretch", hide_index=True)


def page_force_password(user):
    """After an admin reset, the user must pick their own password before doing anything else."""
    _, c2 = st.columns([3, 2])
    with c2:
        lang_buttons("force")
    hero(t("must_change_title"), t("must_change_hint"))
    with st.container(border=True):
        with st.form("force_pw", border=False):
            p1 = st.text_input(t("new_password"), type="password")
            p2 = st.text_input(t("confirm_password"), type="password")
            go_ = st.form_submit_button(t("save"), type="primary", width="stretch")
        if go_:
            if len(p1) < config.MIN_PASSWORD_LEN:
                st.error(t("pw_too_short", n=config.MIN_PASSWORD_LEN))
            elif len(p1.encode("utf-8")) > config.MAX_PASSWORD_BYTES:
                st.error(t("pw_too_long"))
            elif p1 != p2:
                st.error(t("pw_mismatch"))
            elif db.change_password(user["id"], p1) == "same":
                st.error(t("pw_same"))
            else:
                st.session_state.user["must_change"] = False
                st.rerun()
    st.button(t("logout"), key="logout_force", on_click=do_logout)


# ============================================================================
# Router
# ============================================================================
PAGES = {
    "student": {"home": page_home_student, "attendance": page_attendance, "syllabus": page_syllabus,
                "schedule": page_schedule_view, "planner": page_planner, "chat": page_chat_student,
                "complaints": page_complaints_student, "profile": page_profile},
    "doctor": {"home": page_home_doctor, "syllabus_tracker": page_syllabus_doctor,
               "attendance_control": page_attendance_doctor, "schedule": page_schedule_view,
               "chat": page_chat_doctor},
    "dean": {"home": page_home_dean, "doctors_monitor": page_doctors_monitor,
             "dean_attendance": page_dean_attendance, "schedule": page_schedule_view},
    "admin": {"home": page_home_admin, "complaints_admin": page_complaints_admin, "analytics": page_analytics,
              "roster": page_roster, "registered": page_registered, "schedule_admin": page_schedule_admin,
              "manage": page_manage},
}


def main():
    init_state()
    _boot()
    user = st.session_state.user
    inject_css(user["role"] if user else "auth")
    if not user:
        page_auth()
        return

    if user.get("must_change"):              # admin reset the password -> force a new one first
        page_force_password(user)
        return

    pages = PAGES[user["role"]]
    page = st.session_state.get("page")
    if page not in pages:
        page = next(iter(pages))
    st.session_state.page = page
    st.session_state["nav_top"] = page       # keep both menus in sync with the single source of truth
    st.session_state["nav_side"] = page

    top_bar(user, pages)
    side_bar(user, pages)
    if user["role"] == "student":
        show_alerts(user["academic_year"])
    pages[page](user)


main()
