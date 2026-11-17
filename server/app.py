import hmac
import math
import os
import secrets
import socket
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
MAX_ITEMS_PER_REPORT = 100
MAX_MESSAGE_AGE_SECONDS = 7 * 24 * 3600

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


def short_text(value, max_length):
    return str(value)[:max_length] if value is not None else ""


def dashboard_login_required(view):
    @wraps(view)
    def check(*args, **kwargs):
        if not session.get("logged_in"):
            if request.path.startswith("/api/"):
                return jsonify(ok=False, error="please log in"), 401
            return redirect(url_for("login_page"))
        return view(*args, **kwargs)
    return check


@app.post("/api/device/report")
def device_report():
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or not isinstance(data.get("items"), list):
        return jsonify(ok=False, error="expected JSON with an items list"), 400
    items = data["items"][:MAX_ITEMS_PER_REPORT]

    now = time.time()
    device_id = short_text(data.get("device_id", "bin"), 32)
    boot_id = short_text(data.get("boot_id", "unknown"), 40)
    db = get_db()
    new_rows = 0
    highest_seq = -1

    for item in items:
        if not isinstance(item, dict):
            continue
        seq = whole_number_or_none(item.get("seq"), 0, 2**32)
        if seq is None:
            continue
        age_seconds = (number_or_none(item.get("age_ms"), 0, MAX_MESSAGE_AGE_SECONDS * 1000) or 0) / 1000
        created_at = now - age_seconds
        highest_seq = max(highest_seq, seq)

        if item.get("type") == "reading":
            cursor = db.execute(
                "INSERT OR IGNORE INTO readings (device_id, boot_id, seq, created_at, received_at, "
                "hand_cm, fill_cm, fill_percent, lid_open, bin_full, wifi_rssi, uptime_s) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    device_id, boot_id, seq, created_at, now,
                    number_or_none(item.get("hand_cm"), 0, 1000),
                    number_or_none(item.get("fill_cm"), 0, 1000),
                    number_or_none(item.get("fill_percent"), 0, 100),
                    1 if item.get("lid_open") is True else 0,
                    1 if item.get("bin_full") is True else 0,
                    whole_number_or_none(item.get("wifi_rssi"), -150, 0),
                    whole_number_or_none(item.get("uptime_s"), 0, 2**32),
                ),
            )
        elif item.get("type") == "event":
            cursor = db.execute(
                "INSERT OR IGNORE INTO events (device_id, boot_id, seq, created_at, received_at, name, detail) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (device_id, boot_id, seq, created_at, now,
                 short_text(item.get("name"), 32), short_text(item.get("detail"), 64)),
            )
        else:
            continue
        new_rows += cursor.rowcount

    dropped = whole_number_or_none(data.get("dropped_messages"), 0, 2**32) or 0
    db.execute(
        "INSERT INTO boots (boot_id, device_id, first_seen, last_seen, highest_seq, received_count, dropped_on_device) "
        "VALUES (?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(boot_id) DO UPDATE SET last_seen = excluded.last_seen, "
        "highest_seq = MAX(highest_seq, excluded.highest_seq), "
        "received_count = received_count + excluded.received_count, "
        "dropped_on_device = excluded.dropped_on_device",
        (boot_id, device_id, now, now, highest_seq, new_rows, dropped),
    )

    level_on_bin = whole_number_or_none(data.get("full_level_percent"), 0, 100)
    if level_on_bin is not None:
        save_setting("level_on_bin", level_on_bin)
    empty_distance = number_or_none(data.get("empty_distance_cm"), 0, 500)
    if empty_distance is not None:
        save_setting("empty_distance_cm", round(empty_distance, 1))

    db.commit()
    return jsonify(ok=True, saved=new_rows, full_level_percent=get_full_level())


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
        return