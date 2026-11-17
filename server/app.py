import hmac
import math
import os
import secrets
import sqlite3
import time
from datetime import datetime
from functools import wraps

from dotenv import load_dotenv
from flask import (Flask, g, jsonify, redirect, render_template, request,
                   session, url_for)

SERVER_FOLDER = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(SERVER_FOLDER, ".env"))

DASHBOARD_PASSWORD = os.environ.get("DASHBOARD_PASSWORD", "")
DATABASE_FILE = os.path.join(SERVER_FOLDER, os.environ.get("DATABASE_FILE", "trash_can.db"))
PORT = int(os.environ.get("PORT", "5000"))
EXAMPLE_PASSWORD = "change-me"

DEFAULT_FULL_LEVEL = 80
LOWEST_FULL_LEVEL = 10
HIGHEST_FULL_LEVEL = 100
OFFLINE_AFTER_SECONDS = 45
MAX_CHART_POINTS = 400

app = Flask(__name__)
app.config.update(
    SECRET_KEY=os.environ.get("FLASK_SECRET_KEY") or secrets.token_hex(32),
    SESSION_COOKIE_SECURE=False,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Strict",
    MAX_CONTENT_LENGTH=64 * 1024,
)


DATABASE_TABLES = """
CREATE TABLE IF NOT EXISTS readings (
    id INTEGER PRIMARY KEY,
    device_id TEXT NOT NULL,
    boot_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    created_at REAL NOT NULL,
    received_at REAL NOT NULL,
    hand_cm REAL,
    fill_cm REAL,
    fill_percent REAL,
    lid_open INTEGER,
    bin_full INTEGER,
    wifi_rssi INTEGER,
    uptime_s INTEGER,
    UNIQUE (boot_id, seq)
);
CREATE INDEX IF NOT EXISTS readings_by_time ON readings (created_at);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY,
    device_id TEXT NOT NULL,
    boot_id TEXT NOT NULL,
    seq INTEGER,
    created_at REAL NOT NULL,
    received_at REAL NOT NULL,
    name TEXT NOT NULL,
    detail TEXT,
    UNIQUE (boot_id, seq)
);
CREATE INDEX IF NOT EXISTS events_by_time ON events (created_at);
CREATE INDEX IF NOT EXISTS events_by_name ON events (name, created_at);

CREATE TABLE IF NOT EXISTS boots (
    boot_id TEXT PRIMARY KEY,
    device_id TEXT NOT NULL,
    first_seen REAL NOT NULL,
    last_seen REAL NOT NULL,
    highest_seq INTEGER NOT NULL,
    received_count INTEGER NOT NULL,
    dropped_on_device INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS settings (
    name TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at REAL NOT NULL
);
"""


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DATABASE_FILE, timeout=10)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(error):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def create_tables():
    db = sqlite3.connect(DATABASE_FILE)
    db.execute("PRAGMA journal_mode=WAL")
    db.executescript(DATABASE_TABLES)
    db.commit()
    db.close()


def get_setting(name, default=None):
    row = get_db().execute("SELECT value FROM settings WHERE name = ?", (name,)).fetchone()
    return row["value"] if row else default


def save_setting(name, value):
    get_db().execute(
        "INSERT INTO settings (name, value, updated_at) VALUES (?, ?, ?) "
        "ON CONFLICT(name) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
        (name, str(value), time.time()),
    )


def get_full_level():
    try:
        return int(get_setting("full_level_percent", DEFAULT_FULL_LEVEL))
    except ValueError:
        return DEFAULT_FULL_LEVEL


def number_or_none(value, lowest, highest):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = float(value)
    if math.isnan(value) or value < lowest or value > highest:
        return None
    return value


def whole_number_or_none(value, lowest, highest):
    number = number_or_none(value, lowest, highest)
    return None if number is None else int(number)


def dashboard_login_required(view):
    @wraps(view)
    def check(*args, **kwargs):
        if not session.get("logged_in"):
            if request.path.startswith("/api/"):
                return jsonify(ok=False, error="please log in"), 401
            return redirect(url_for("login_page"))
        return view(*args, **kwargs)
    return check


@app.route("/login", methods=["GET", "POST"])
def login_page():
    error = None
    if request.method == "POST":
        typed = request.form.get("password", "")
        if DASHBOARD_PASSWORD and hmac.compare_digest(typed, DASHBOARD_PASSWORD):
            session.clear()
            session["logged_in"] = True
            session.permanent = True
            return redirect(url_for("dashboard_page"))
        time.sleep(1)
        error = "Wrong password"
    return render_template("login.html", error=error)


@app.post("/logout")
def logout():
    session.clear()
    return redirect(url_for("login_page"))


@app.get("/")
@dashboard_login_required
def dashboard_page():
    return render_template("dashboard.html")


def start_of_today():
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    return today.timestamp()


@app.get("/api/status")
@dashboard_login_required
def status():
    db = get_db()
    now = time.time()
    latest = db.execute("SELECT * FROM readings ORDER BY created_at DESC LIMIT 1").fetchone()
    last_message = db.execute("SELECT MAX(last_seen) AS t FROM boots").fetchone()["t"]

    def count_opens(since, reason=None):
        sql = "SELECT COUNT(*) AS n FROM events WHERE name = 'lid_open' AND created_at >= ?"
        values = [since]
        if reason:
            sql += " AND detail = ?"
            values.append(reason)
        return db.execute(sql, values).fetchone()["n"]

    boot_rows = db.execute("SELECT * FROM boots ORDER BY first_seen").fetchall()
    missing = sum(max(0, row["highest_seq"] + 1 - row["received_count"]) for row in boot_rows)

    level_on_bin = get_setting("level_on_bin")
    return jsonify(
        ok=True,
        server_time=now,
        online=bool(last_message and now - last_message < OFFLINE_AFTER_SECONDS),
        last_message_at=last_message,
        full_level_percent=get_full_level(),
        level_on_bin=int(level_on_bin) if level_on_bin is not None else None,
        empty_distance_cm=get_setting("empty_distance_cm"),
        latest=dict(latest) if latest else None,
        opens={
            "today": count_opens(start_of_today()),
            "today_by_hand": count_opens(start_of_today(), "hand"),
            "today_by_button": count_opens(start_of_today(), "button"),
            "last_24h": count_opens(now - 24 * 3600),
            "all_time": count_opens(0),
        },
        reboots=max(0, len(boot_rows) - 1),
        missing_messages=missing,
        first_seen=boot_rows[0]["first_seen"] if boot_rows else None,
    )


@app.get("/api/history")
@dashboard_login_required
def history():
    hours = request.args.get("hours", default=24, type=float)
    hours = min(max(hours, 0.25), 24 * 7)
    since = time.time() - hours * 3600
    rows = get_db().execute(
        "SELECT created_at, fill_percent, hand_cm, lid_open FROM readings "
        "WHERE created_at >= ? ORDER BY created_at",
        (since,),
    ).fetchall()

    step = max(1, math.ceil(len(rows) / MAX_CHART_POINTS))
    points = [dict(row) for row in rows[::step]]

    bucket_seconds = max(900, int(hours * 3600 / 24))
    open_rows = get_db().execute(
        "SELECT CAST((created_at - ?) / ? AS INTEGER) AS bucket, COUNT(*) AS n "
        "FROM events WHERE name = 'lid_open' AND created_at >= ? GROUP BY bucket",
        (since, bucket_seconds, since),
    ).fetchall()
    bucket_count = math.ceil(hours * 3600 / bucket_seconds)
    opens = [0] * bucket_count
    for row in open_rows:
        if 0 <= row["bucket"] < bucket_count:
            opens[row["bucket"]] = row["n"]

    return jsonify(
        ok=True, since=since, hours=hours, points=points,
        opens={"bucket_seconds": bucket_seconds, "counts": opens},
        full_level_percent=get_full_level(),
    )


@app.get("/api/events")
@dashboard_login_required
def recent_events():
    limit = min(max(request.args.get("limit", default=25, type=int), 1), 200)
    rows = get_db().execute(
        "SELECT created_at, name, detail, boot_id FROM events ORDER BY created_at DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return jsonify(ok=True, events=[dict(row) for row in rows])


@app.post("/api/settings")
@dashboard_login_required
def change_settings():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify(ok=False, error="expected JSON"), 400
    level = whole_number_or_none(data.get("full_level_percent"), LOWEST_FULL_LEVEL, HIGHEST_FULL_LEVEL)
    if level is None:
        return jsonify(ok=False, error=f"full level must be {LOWEST_FULL_LEVEL} to {HIGHEST_FULL_LEVEL}"), 400

    db = get_db()
    old_level = get_full_level()
    save_setting("full_level_percent", level)
    if level != old_level:
        now = time.time()
        db.execute(
            "INSERT INTO events (device_id, boot_id, seq, created_at, received_at, name, detail) "
            "VALUES ('dashboard', 'dashboard', NULL, ?, ?, 'full_level_set', ?)",
            (now, now, f"{old_level}% to {level}%"),
        )
    db.commit()
    return jsonify(ok=True, full_level_percent=level)


if __name__ == "__main__":
    if not DASHBOARD_PASSWORD:
        raise SystemExit("Missing password. Copy .env.example to .env and type your own DASHBOARD_PASSWORD.")
    if DASHBOARD_PASSWORD == EXAMPLE_PASSWORD:
        raise SystemExit("Please change DASHBOARD_PASSWORD in .env. Do not keep the example password.")
    create_tables()
    print(f"Website: http://127.0.0.1:{PORT}/  (log in with the password from .env)")
    app.run(host="127.0.0.1", port=PORT, threaded=True)
