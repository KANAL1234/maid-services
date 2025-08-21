import base64
import datetime as dt
import hashlib
import hmac
import json
import os
from typing import List, Optional, Tuple

import requests
import streamlit as st
from dateutil import tz

st.set_page_config(page_title="Maid Services (Streamlit)", page_icon="🧹", layout="wide")

# ---- Secrets ----
GITHUB = st.secrets.get("github", {})
EMAIL = st.secrets.get("email", {})

REPO_OWNER = GITHUB.get("owner")
REPO_NAME = GITHUB.get("repo")
REPO_BRANCH = GITHUB.get("branch", "main")
GITHUB_TOKEN = GITHUB.get("token")

DATA_USERS = "data/users.json"
DATA_WORKERS = "data/workers.json"
DATA_BOOKINGS = "data/bookings.json"

LOCAL_TZ = tz.gettz("Asia/Kolkata")
HALF_HOUR = dt.timedelta(minutes=30)

# ================= Password hashing =================
def hash_password(password: str, salt: Optional[bytes] = None) -> Tuple[str, str]:
    if salt is None:
        salt = os.urandom(16)
    pwd_hash = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 200_000)
    return base64.b64encode(salt).decode(), base64.b64encode(pwd_hash).decode()

def verify_password(password: str, salt_b64: str, hash_b64: str) -> bool:
    salt = base64.b64decode(salt_b64.encode())
    calc = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 200_000)
    return hmac.compare_digest(base64.b64encode(calc).decode(), hash_b64)

# ================= GitHub Content API =================
def gh_headers():
    if not GITHUB_TOKEN:
        raise RuntimeError("Missing GitHub token in secrets.")
    return {"Authorization": f"token {GITHUB_TOKEN}", "Accept": "application/vnd.github+json"}

def gh_get(path: str):
    url = f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/contents/{path}"
    params = {"ref": REPO_BRANCH}
    resp = requests.get(url, headers=gh_headers(), params=params)
    if resp.status_code == 200:
        data = resp.json()
        content = base64.b64decode(data["content"]).decode("utf-8")
        try:
            return (json.loads(content) if content else {}), data.get("sha")
        except json.JSONDecodeError:
            return {}, data.get("sha")
    if resp.status_code == 404:
        return {}, None
    raise RuntimeError(f"GitHub GET failed: {resp.status_code} {resp.text}")

def gh_put(path: str, obj: dict, message: str, sha: Optional[str]) -> None:
    url = f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/contents/{path}"
    content_str = json.dumps(obj, indent=2, ensure_ascii=False)
    payload = {
        "message": message,
        "content": base64.b64encode(content_str.encode("utf-8")).decode(),
        "branch": REPO_BRANCH,
    }
    if sha is not None:
        payload["sha"] = sha
    resp = requests.put(url, headers=gh_headers(), json=payload)
    if resp.status_code not in (200, 201):
        raise RuntimeError(f"GitHub PUT failed: {resp.status_code} {resp.text}")

def load_table(path: str):
    data, sha = gh_get(path)
    if not isinstance(data, dict) or "rows" not in data:
        data = {"rows": []}
    return data, sha

def save_table(path: str, data: dict, sha: Optional[str], message: str):
    gh_put(path, data, message, sha)

def ensure_files():
    for path in (DATA_USERS, DATA_WORKERS, DATA_BOOKINGS):
        data, sha = load_table(path)
        if sha is None:
            gh_put(path, data, f"Initialize {os.path.basename(path)}", None)

# ================= Models / helpers =================
def now_iso() -> str:
    return dt.datetime.now(tz=LOCAL_TZ).isoformat(timespec="seconds")

def get_user(username: str) -> Optional[dict]:
    users, _ = load_table(DATA_USERS)
    for u in users["rows"]:
        if u["username"].lower() == username.lower():
            return u
    return None

def register_user(username: str, email: str, password: str, role: str):
    users, sha = load_table(DATA_USERS)
    if any(u["username"].lower() == username.lower() for u in users["rows"]):
        return False, "Username already exists."
    salt_b64, hash_b64 = hash_password(password)
    users["rows"].append({
        "username": username,
        "email": email,
        "role": role,
        "pwd_salt": salt_b64,
        "pwd_hash": hash_b64,
        "created_at": now_iso(),
    })
    save_table(DATA_USERS, users, sha, f"Add user {username}")
    return True, "Account created."

def login_user(username: str, password: str):
    u = get_user(username)
    if not u:
        return False, "User not found.", None
    if verify_password(password, u["pwd_salt"], u["pwd_hash"]):
        return True, "Welcome back!", u
    return False, "Invalid password.", None

def get_worker(username: str) -> Optional[dict]:
    workers, _ = load_table(DATA_WORKERS)
    for w in workers["rows"]:
        if w["username"].lower() == username.lower():
            return w
    return None

def upsert_worker(profile: dict):
    workers, sha = load_table(DATA_WORKERS)
    for i, w in enumerate(workers["rows"]):
        if w["username"].lower() == profile["username"].lower():
            workers["rows"][i] = profile
            save_table(DATA_WORKERS, workers, sha, f"Update worker {profile['username']}")
            return
    workers["rows"].append(profile)
    save_table(DATA_WORKERS, workers, sha, f"Add worker {profile['username']}")

def list_workers(filters: dict) -> List[dict]:
    workers, _ = load_table(DATA_WORKERS)
    rows = workers["rows"]
    city = (filters.get("city") or "").strip().lower()
    skill = (filters.get("skill") or "").strip().lower()
    if city:
        rows = [w for w in rows if city in (w.get("city","").lower())]
    if skill:
        rows = [w for w in rows if any(skill in s.lower() for s in w.get("skills", []))]
    return rows

def worker_daily_range(worker: dict):
    start = worker.get("daily_start", "09:00")
    end = worker.get("daily_end", "18:00")
    s_h, s_m = map(int, start.split(":"))
    e_h, e_m = map(int, end.split(":"))
    return dt.time(s_h, s_m), dt.time(e_h, e_m)

def generate_slots(worker: dict, date: dt.date) -> List[dt.time]:
    start_t, end_t = worker_daily_range(worker)
    slots = []
    cur_dt = dt.datetime.combine(date, start_t)
    end_dt = dt.datetime.combine(date, end_t)
    while cur_dt <= end_dt - HALF_HOUR:
        slots.append(cur_dt.time())
        cur_dt += HALF_HOUR
    return slots

def is_overlap(s1: dt.time, e1: dt.time, s2: dt.time, e2: dt.time) -> bool:
    return max(s1, s2) < min(e1, e2)

def load_bookings():
    return load_table(DATA_BOOKINGS)

def worker_booked_spans(worker_username: str, date: dt.date):
    bookings, _ = load_bookings()
    spans = []
    day = date.isoformat()
    for b in bookings["rows"]:
        if b["worker"].lower() == worker_username.lower() and b["date"] == day and b["status"] != "cancelled":
            sh, sm = map(int, b["start"].split(":"))
            eh, em = map(int, b["end"].split(":"))
            spans.append((dt.time(sh, sm), dt.time(eh, em)))
    return spans

def available_start_slots(worker: dict, date: dt.date, duration_hrs: float) -> List[dt.time]:
    dur = dt.timedelta(minutes=int(duration_hrs * 60))
    slots = generate_slots(worker, date)
    spans = worker_booked_spans(worker["username"], date)
    avail = []
    day_start, day_end = worker_daily_range(worker)
    for t in slots:
        start_dt = dt.datetime.combine(date, t)
        end_dt = start_dt + dur
        st_t = start_dt.time()
        en_t = end_dt.time()
        if st_t < day_start or en_t > day_end:
            continue
        if not any(is_overlap(st_t, en_t, s, e) for (s, e) in spans):
            avail.append(t)
    return avail

def create_booking(user: dict, worker: dict, date: dt.date, start: dt.time, duration_hrs: float):
    dur = dt.timedelta(minutes=int(duration_hrs * 60))
    end_dt = dt.datetime.combine(date, start) + dur
    end_t = end_dt.time()

    if start not in available_start_slots(worker, date, duration_hrs):
        return False, "Selected slot is no longer available."

    bookings, sha = load_bookings()
    booking_id = "bk_" + dt.datetime.utcnow().strftime("%Y%m%d%H%M%S%f")
    row = {
        "id": booking_id,
        "user": user["username"],
        "worker": worker["username"],
        "date": date.isoformat(),
        "start": start.strftime("%H:%M"),
        "end": end_t.strftime("%H:%M"),
        "created_at": now_iso(),
        "status": "confirmed",
    }
    bookings["rows"].append(row)
    save_table(DATA_BOOKINGS, bookings, sha, f"Add booking {booking_id}")

    subject = f"Booking Confirmed: {worker.get('name', worker['username'])} on {row['date']} {row['start']}-{row['end']}"
    body = (
        f"Hello {user['username']},\n\n"
        f"Your booking is confirmed.\n\n"
        f"Worker: {worker.get('name', worker['username'])}\n"
        f"Date:   {row['date']}\n"
        f"Time:   {row['start']} - {row['end']}\n"
        f"City:   {worker.get('city','')}\n\n"
        f"Booking ID: {booking_id}\n\n"
        f"Thanks for using Maid Services!"
    )
    sent = False
    if user.get("email"):
        try:
            import smtplib
            from email.mime.text import MIMEText
            host = EMAIL.get("host")
            port = int(EMAIL.get("port", 587))
            username = EMAIL.get("username")
            password = EMAIL.get("password")
            sender_name = EMAIL.get("sender_name", "Maid Services")
            sender_email = EMAIL.get("sender_email", username)
            use_tls = bool(EMAIL.get("use_tls", True))
            if host and username and password and sender_email:
                msg = MIMEText(body, "plain", "utf-8")
                msg["Subject"] = subject
                msg["From"] = f"{sender_name} <{sender_email}>"
                msg["To"] = user["email"]
                with smtplib.SMTP(host, port) as server:
                    if use_tls:
                        server.starttls()
                    server.login(username, password)
                    server.sendmail(sender_email, [user["email"]], msg.as_string())
                sent = True
        except Exception:
            sent = False

    msg = "Booking created successfully."
    if not sent:
        msg += " (Email not sent; check SMTP settings in secrets.toml.)"
    return True, msg

# ================= UI =================
def sidebar_auth():
    st.sidebar.header("Account")
    if "auth_user" not in st.session_state:
        st.session_state["auth_user"] = None

    if st.session_state["auth_user"]:
        u = st.session_state["auth_user"]
        st.sidebar.success(f"Logged in as **{u['username']}** ({u['role']})")
        if st.sidebar.button("Log out", use_container_width=True):
            st.session_state["auth_user"] = None
            st.rerun()
        return

    tabs = st.sidebar.tabs(["Log in", "Sign up"])
    with tabs[0]:
        u_name = st.text_input("Username", key="login_user")
        pwd = st.text_input("Password", type="password", key="login_pwd")
        if st.button("Log in", use_container_width=True):
            try:
                ok, msg, user = login_user(u_name, pwd)
            except Exception as e:
                st.error(f"Login failed: {e}")
                st.stop()
            if ok:
                st.session_state["auth_user"] = user
                st.success(msg)
                st.rerun()
            else:
                st.error(msg)
    with tabs[1]:
        u_new = st.text_input("Username (new)")
        email = st.text_input("Email")
        role = st.selectbox("Role", ["customer", "worker"], index=0)
        p1 = st.text_input("Password", type="password")
        p2 = st.text_input("Confirm Password", type="password")
        if st.button("Create account", use_container_width=True):
            if not u_new or not email or not p1:
                st.error("Please fill all required fields.")
            elif p1 != p2:
                st.error("Passwords do not match.")
            else:
                try:
                    ok, msg = register_user(u_new, email, p1, role)
                except Exception as e:
                    st.error(f"Sign-up failed: {e}")
                    st.stop()
                if ok:
                    st.success(msg + " You can now log in.")
                else:
                    st.error(msg)

def page_home():
    st.title("🧹 Maid Services")
    st.caption("Book trusted domestic help in half-hour slots — inspired by UrbanClap, built with Streamlit.")
    c1, c2, c3 = st.columns(3)
    with c1:
        st.metric("Workers", len(load_table(DATA_WORKERS)[0]["rows"]))
    with c2:
        st.metric("Users", len(load_table(DATA_USERS)[0]["rows"]))
    with c3:
        st.metric("Bookings", len(load_table(DATA_BOOKINGS)[0]["rows"]))

def page_browse_and_book():
    if "auth_user" not in st.session_state or not st.session_state["auth_user"]:
        st.warning("Please log in to continue.")
        st.stop()
    user = st.session_state["auth_user"]
    st.title("🔎 Browse & Book")

    colf1, colf2 = st.columns([2, 2])
    with colf1:
        city = st.text_input("Filter by city (optional)")
    with colf2:
        skill = st.text_input("Filter by skill (e.g., cleaning, cooking, babysitting)")

    workers = list_workers({"city": city, "skill": skill})
    if not workers:
        st.info("No workers match the filters yet.")
        return

    selected = st.selectbox(
        "Select a worker",
        options=workers,
        format_func=lambda w: f"{w.get('name') or w['username']} — {w.get('city','')} | ₹{w.get('rate_per_hour','?')}/hr | Skills: {', '.join(w.get('skills',[]))}",
    )

    if selected:
        with st.expander("Worker details", expanded=True):
            st.write(f"**Name:** {selected.get('name') or selected['username']}")
            st.write(f"**City:** {selected.get('city','')}")
            st.write(f"**Skills:** {', '.join(selected.get('skills', [])) or '—'}")
            st.write(f"**Rate:** ₹{selected.get('rate_per_hour','?')}/hr")
            st.write(f"**Daily Hours:** {selected.get('daily_start','09:00')} - {selected.get('daily_end','18:00')}")
            st.write(selected.get("bio",""))

        c1, c2 = st.columns([2, 1])
        with c1:
            date = st.date_input("Choose a date", min_value=dt.date.today())
        with c2:
            duration = st.selectbox("Duration (hours)", [0.5, 1, 1.5, 2, 3, 4, 5, 6], index=1)

        avail = available_start_slots(selected, date, float(duration))
        if not avail:
            st.warning("No available start times for this duration on the chosen date.")
            return
        pretty = [t.strftime("%H:%M") for t in avail]
        start_str = st.selectbox("Start time", pretty)
        start_time = dt.datetime.strptime(start_str, "%H:%M").time()

        if st.button("Book", type="primary", use_container_width=True):
            ok, msg = create_booking(user, selected, date, start_time, float(duration))
            if ok:
                st.success(msg)
            else:
                st.error(msg)

def page_my_bookings():
    if "auth_user" not in st.session_state or not st.session_state["auth_user"]:
        st.warning("Please log in to continue.")
        st.stop()
    user = st.session_state["auth_user"]
    st.title("📅 My Bookings")

    bookings, sha = load_bookings()
    my_role = user["role"]
    if my_role == "customer":
        rows = [b for b in bookings["rows"] if b["user"].lower() == user["username"].lower()]
    elif my_role == "worker":
        rows = [b for b in bookings["rows"] if b["worker"].lower() == user["username"].lower()]
    else:
        rows = bookings["rows"]

    if not rows:
        st.info("No bookings yet.")
        return

    rows_sorted = sorted(rows, key=lambda b: (b["date"], b["start"]))
    for b in rows_sorted:
        with st.container(border=True):
            st.write(f"**Booking ID:** {b['id']}  |  **Status:** {b['status']}")
            st.write(f"**Date:** {b['date']}  |  **Time:** {b['start']} - {b['end']}")
            st.write(f"**Customer:** {b['user']}  |  **Worker:** {b['worker']}")

def page_worker_dashboard():
    if "auth_user" not in st.session_state or not st.session_state["auth_user"]:
        st.warning("Please log in to continue.")
        st.stop()
    user = st.session_state["auth_user"]
    if user["role"] not in ("worker", "admin"):
        st.warning("Worker dashboard is for workers.")
        return

    st.title("🧰 Worker Dashboard")
    existing = get_worker(user["username"]) or {
        "username": user["username"],
        "name": "",
        "city": "",
        "skills": [],
        "rate_per_hour": 300,
        "bio": "",
        "daily_start": "09:00",
        "daily_end": "18:00",
    }

    name = st.text_input("Display name", value=existing.get("name","") or user["username"])
    city = st.text_input("City", value=existing.get("city",""))
    skills_csv = st.text_input("Skills (comma-separated)", value=", ".join(existing.get("skills", [])))
    rate = st.number_input("Rate per hour (₹)", min_value=100, max_value=10000, value=int(existing.get("rate_per_hour", 300)), step=50)
    c1, c2 = st.columns(2)
    with c1:
        start = st.time_input("Daily start", dt.datetime.strptime(existing.get("daily_start","09:00"), "%H:%M").time())
    with c2:
        end = st.time_input("Daily end", dt.datetime.strptime(existing.get("daily_end","18:00"), "%H:%M").time())
    bio = st.text_area("Short bio", value=existing.get("bio",""), height=120)

    if st.button("Save Profile", type="primary"):
        skills = [s.strip() for s in skills_csv.split(",") if s.strip()]
        profile = {
            "username": user["username"],
            "name": name.strip() or user["username"],
            "city": city.strip(),
            "skills": skills,
            "rate_per_hour": int(rate),
            "bio": bio.strip(),
            "daily_start": start.strftime("%H:%M"),
            "daily_end": end.strftime("%H:%M"),
        }
        try:
            upsert_worker(profile)
            st.success("Profile saved.")
        except Exception as e:
            st.error(f"Failed to save profile: {e}")

def page_admin():
    if "auth_user" not in st.session_state or not st.session_state["auth_user"]:
        st.warning("Please log in to continue.")
        st.stop()
    user = st.session_state["auth_user"]
    if user["role"] != "admin":
        st.warning("Admins only.")
        return
    st.title("🛡️ Admin")
    c1, c2, c3 = st.columns(3)
    with c1:
        st.subheader("Users")
        st.json(load_table(DATA_USERS)[0])
    with c2:
        st.subheader("Workers")
        st.json(load_table(DATA_WORKERS)[0])
    with c3:
        st.subheader("Bookings")
        st.json(load_table(DATA_BOOKINGS)[0])

def main():
    if not all([REPO_OWNER, REPO_NAME, REPO_BRANCH, GITHUB_TOKEN]):
        st.error("GitHub secrets not configured. Please set [github] in .streamlit/secrets.toml")
        st.stop()
    ensure_files()
    sidebar_auth()

    pages = {
        "Home": page_home,
        "Browse & Book": page_browse_and_book,
        "My Bookings": page_my_bookings,
        "Worker Dashboard": page_worker_dashboard,
        "Admin": page_admin,
    }

    visible = ["Home", "Browse & Book"]
    if st.session_state.get("auth_user"):
        role = st.session_state["auth_user"]["role"]
        visible.append("My Bookings")
        if role in ("worker", "admin"):
            visible.append("Worker Dashboard")
        if role == "admin":
            visible.append("Admin")

    choice = st.sidebar.radio("Navigate", visible, index=0)
    pages[choice]()

if __name__ == "__main__":
    main()
