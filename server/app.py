"""SmartBin server: receives signed reports from the bin and serves the dashboard.

Run with:  python app.py
"""
import asyncio
import json
import logging
import math
import time
from datetime import datetime
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, Form, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import func
from sqlmodel import Session, select
from starlette.concurrency import run_in_threadpool
from starlette.middleware.sessions import SessionMiddleware

from config import SERVER_FOLDER, Settings, load_settings
from db import (Boot, Event, Message, Reading, Setting, User, devices_of, existing_seqs,
                get_setting, make_engine, save_setting)
from device_auth import signature_is_valid
from passwords import UNKNOWN_USER_HASH, password_matches

log = logging.getLogger("smartbin")

DEFAULT_FULL_LEVEL = 80
LOWEST_FULL_LEVEL = 10
HIGHEST_FULL_LEVEL = 100
OFFLINE_AFTER_SECONDS = 45
MAX_CHART_POINTS = 400
MAX_ITEMS_PER_REPORT = 100
MAX_BODY_BYTES = 64 * 1024
MAX_MESSAGE_AGE_MS = 7 * 24 * 3600 * 1000


# ---------- what the device sends ----------

class ReadingItem(BaseModel):
    model_config = ConfigDict(extra="ignore")
    type: Literal["reading"]
    seq: int = Field(ge=0, le=2**32)
    age_ms: float = Field(default=0, ge=0, le=MAX_MESSAGE_AGE_MS)
    hand_cm: float | None = Field(default=None, ge=0, le=1000)
    fill_cm: float | None = Field(default=None, ge=0, le=1000)
    fill_percent: float | None = Field(default=None, ge=0, le=100)
    lid_open: bool = False
    bin_full: bool = False
    wifi_rssi: int | None = Field(default=None, ge=-150, le=0)
    uptime_s: int | None = Field(default=None, ge=0, le=2**32)


class EventItem(BaseModel):
    model_config = ConfigDict(extra="ignore")
    type: Literal["event"]
    seq: int = Field(ge=0, le=2**32)
    age_ms: float = Field(default=0, ge=0, le=MAX_MESSAGE_AGE_MS)
    name: str = Field(min_length=1, max_length=32)
    detail: str = Field(default="", max_length=64)


class Report(BaseModel):
    model_config = ConfigDict(extra="ignore")
    device_id: str = Field(min_length=1, max_length=32)
    boot_id: str = Field(min_length=1, max_length=40)
    items: list[Annotated[ReadingItem | EventItem, Field(discriminator="type")]] = Field(
        max_length=MAX_ITEMS_PER_REPORT)
    full_level_percent: int | None = Field(default=None, ge=0, le=100)
    empty_distance_cm: float | None = Field(default=None, ge=0, le=500)
    dropped_messages: int = Field(default=0, ge=0)


class SettingsUpdate(BaseModel):
    device_id: str = Field(min_length=1, max_length=32)
    full_level_percent: int = Field(ge=LOWEST_FULL_LEVEL, le=HIGHEST_FULL_LEVEL)


# ---------- helpers ----------

def error(status: int, message: str) -> JSONResponse:
    return JSONResponse({"ok": False, "error": message}, status_code=status)


def setting_name(name: str, device_id: str) -> str:
    """Each bin has its own settings, stored as e.g. "full_level_percent:bin-1"."""
    return f"{name}:{device_id}"


def full_level(session: Session, device_id: str) -> int:
    value = get_setting(session, setting_name("full_level_percent", device_id), DEFAULT_FULL_LEVEL)
    try:
        return int(value)
    except ValueError:
        return DEFAULT_FULL_LEVEL


def move_old_shared_settings(engine) -> None:
    """Servers from before accounts kept one shared copy of each setting.
    Give that value to every bin the server has already heard from, then delete it,
    so new bins start from the defaults instead of someone else's settings."""
    with Session(engine) as session:
        known_devices = set(session.exec(select(Boot.device_id)).all())
        for name in ("full_level_percent", "level_on_bin", "empty_distance_cm"):
            old = session.get(Setting, name)
            if old is None:
                continue
            for device_id in known_devices:
                if get_setting(session, setting_name(name, device_id)) is None:
                    save_setting(session, setting_name(name, device_id), old.value)
            session.delete(old)
        session.commit()


def find_user(engine, username: str) -> User | None:
    with Session(engine) as session:
        return session.get(User, username) if username else None


def count_users(engine) -> int:
    with Session(engine) as session:
        return session.exec(select(func.count()).select_from(User)).one()


def start_of_today() -> float:
    return datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()


def save_report(engine, report: Report, body: bytes, signature: str, method: str) -> tuple[int, int]:
    """Stores a verified report. Returns (new rows saved, full level for the bin)."""
    now = time.time()
    with Session(engine) as session:
        readings_seen = existing_seqs(session, Reading, report.boot_id,
                                      [i.seq for i in report.items if i.type == "reading"])
        events_seen = existing_seqs(session, Event, report.boot_id,
                                    [i.seq for i in report.items if i.type == "event"])
        new_rows = 0
        for item in report.items:
            created_at = now - item.age_ms / 1000
            common = dict(device_id=report.device_id, boot_id=report.boot_id, seq=item.seq,
                          created_at=created_at, received_at=now)
            if item.type == "reading" and item.seq not in readings_seen:
                readings_seen.add(item.seq)
                session.add(Reading(**common, hand_cm=item.hand_cm, fill_cm=item.fill_cm,
                                    fill_percent=item.fill_percent, lid_open=item.lid_open,
                                    bin_full=item.bin_full, wifi_rssi=item.wifi_rssi,
                                    uptime_s=item.uptime_s))
                new_rows += 1
            elif item.type == "event" and item.seq not in events_seen:
                events_seen.add(item.seq)
                session.add(Event(**common, name=item.name, detail=item.detail))
                new_rows += 1

        highest_seq = max((i.seq for i in report.items), default=-1)
        boot = session.get(Boot, report.boot_id)
        if boot is None:
            boot = Boot(boot_id=report.boot_id, device_id=report.device_id, first_seen=now,
                        last_seen=now, highest_seq=highest_seq, received_count=0)
        boot.last_seen = now
        boot.highest_seq = max(boot.highest_seq, highest_seq)
        boot.received_count += new_rows
        boot.dropped_on_device = report.dropped_messages
        session.add(boot)

        if report.full_level_percent is not None:
            save_setting(session, setting_name("level_on_bin", report.device_id), report.full_level_percent)
        if report.empty_distance_cm is not None:
            save_setting(session, setting_name("empty_distance_cm", report.device_id),
                         round(report.empty_distance_cm, 1))

        session.add(Message(device_id=report.device_id, boot_id=report.boot_id, received_at=now,
                            auth_method=method, signature=signature,
                            body=body.decode("utf-8", errors="replace")))
        session.commit()
        return new_rows, full_level(session, report.device_id)


# ---------- the app ----------

def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()
    engine = make_engine(settings.database_url)
    move_old_shared_settings(engine)
    if not settings.device_keys:
        log.warning("DEVICE_KEYS is empty: every device report will be rejected")
    if count_users(engine) == 0:
        log.warning("No dashboard accounts yet: add one with  python manage_users.py add <username> <device_id>")

    app = FastAPI(title="SmartBin", docs_url="/docs", redoc_url=None)
    app.state.settings = settings
    app.state.engine = engine
    app.add_middleware(SessionMiddleware, secret_key=settings.session_secret,
                       same_site="strict", https_only=settings.session_cookie_secure)
    app.mount("/static", StaticFiles(directory=SERVER_FOLDER / "static"), name="static")
    templates = Jinja2Templates(directory=SERVER_FOLDER / "templates")

    @app.exception_handler(HTTPException)
    async def http_error(_request, exc: HTTPException):
        return error(exc.status_code, str(exc.detail))

    @app.exception_handler(RequestValidationError)
    async def validation_error(_request, exc: RequestValidationError):
        first = exc.errors()[0]
        where = ".".join(str(p) for p in first["loc"] if p != "body")
        return error(400, f"{where}: {first['msg']}" if where else first["msg"])

    def db_session():
        with Session(engine) as session:
            yield session

    DB = Annotated[Session, Depends(db_session)]

    def logged_in_user(request: Request, session: DB) -> str:
        """The username of whoever is logged in. Checked against the database on every
        request, so removing an account logs that person out right away."""
        username = request.session.get("username")
        if not username or session.get(User, username) is None:
            raise HTTPException(401, "please log in")
        return username

    LoggedInUser = Annotated[str, Depends(logged_in_user)]

    def check_device(session: Session, username: str, device_id: str) -> None:
        # Same answer for "bin does not exist" and "not your bin", so nobody can
        # discover other teams' bin names by guessing.
        if device_id not in devices_of(session, username):
            raise HTTPException(404, "no such bin on your account")

    # ----- device -----

    @app.post("/api/device/report")
    async def device_report(request: Request):
        declared_size = request.headers.get("content-length")
        if declared_size and declared_size.isdigit() and int(declared_size) > MAX_BODY_BYTES:
            return error(413, "report too large")
        body = await request.body()
        if len(body) > MAX_BODY_BYTES:
            return error(413, "report too large")

        try:
            data = json.loads(body)
        except ValueError:
            return error(400, "body is not valid JSON")
        device_id = data.get("device_id") if isinstance(data, dict) else None
        key = settings.device_keys.get(device_id) if isinstance(device_id, str) else None
        if key is None:
            return error(401, "unknown device")
        signature = request.headers.get("x-signature", "")
        if not signature:
            return error(401, "missing X-Signature header")
        if not signature_is_valid(key, body, signature):
            log.warning("rejected report with a bad signature for device %s", device_id)
            return error(401, "bad signature")

        try:
            report = Report.model_validate(data)
        except ValidationError as exc:
            first = exc.errors()[0]
            return error(400, f"{'.'.join(map(str, first['loc']))}: {first['msg']}")

        saved, level = await run_in_threadpool(save_report, engine, report, body,
                                               signature.strip().lower(), key.method)
        return {"ok": True, "saved": saved, "full_level_percent": level}

    # ----- login -----

    @app.get("/login", name="login_page")
    def login_page(request: Request):
        return templates.TemplateResponse(request, "login.html", {"error": None, "username": ""})

    @app.post("/login")
    async def login(request: Request, username: Annotated[str, Form()] = "",
                    password: Annotated[str, Form()] = ""):
        username = username.strip().lower()
        user = await run_in_threadpool(find_user, engine, username)
        # Always check a hash, even for an unknown username, so both take the same time.
        stored_hash = user.password_hash if user else UNKNOWN_USER_HASH
        correct = await run_in_threadpool(password_matches, password, stored_hash)
        if user is not None and correct:
            request.session.clear()
            request.session["username"] = user.username
            return RedirectResponse("/", status_code=303)
        await asyncio.sleep(1)
        no_accounts = await run_in_threadpool(count_users, engine) == 0
        message = ("No accounts yet: run  python manage_users.py add <username> <device_id>"
                   if no_accounts else "Wrong username or password")
        return templates.TemplateResponse(request, "login.html",
                                          {"error": message, "username": username}, status_code=401)

    @app.post("/logout", name="logout")
    def logout(request: Request):
        request.session.clear()
        return RedirectResponse("/login", status_code=303)

    @app.get("/")
    def dashboard_page(request: Request, session: DB):
        username = request.session.get("username")
        if not username or session.get(User, username) is None:
            return RedirectResponse("/login", status_code=303)
        return templates.TemplateResponse(request, "dashboard.html", {"username": username})

    # ----- dashboard API -----
    # Every route below needs a logged-in user and only shows bins linked to that user.

    @app.get("/api/devices")
    def my_devices(username: LoggedInUser, session: DB):
        return {"ok": True, "username": username, "devices": devices_of(session, username)}

    @app.get("/api/status")
    def status(username: LoggedInUser, session: DB, device: str):
        check_device(session, username, device)
        now = time.time()
        latest = session.exec(select(Reading).where(Reading.device_id == device)
                              .order_by(Reading.created_at.desc()).limit(1)).first()
        last_message = session.exec(select(func.max(Boot.last_seen)).where(Boot.device_id == device)).one()

        def count_opens(since: float, reason: str | None = None) -> int:
            query = select(func.count()).select_from(Event).where(
                Event.device_id == device, Event.name == "lid_open", Event.created_at >= since)
            if reason:
                query = query.where(Event.detail == reason)
            return session.exec(query).one()

        boots = session.exec(select(Boot).where(Boot.device_id == device).order_by(Boot.first_seen)).all()
        level_on_bin = get_setting(session, setting_name("level_on_bin", device))
        empty_distance = get_setting(session, setting_name("empty_distance_cm", device))
        today = start_of_today()
        return {
            "ok": True,
            "device_id": device,
            "server_time": now,
            "online": bool(last_message and now - last_message < OFFLINE_AFTER_SECONDS),
            "last_message_at": last_message,
            "full_level_percent": full_level(session, device),
            "level_on_bin": int(level_on_bin) if level_on_bin is not None else None,
            "empty_distance_cm": float(empty_distance) if empty_distance is not None else None,
            "latest": latest.model_dump() if latest else None,
            "opens": {
                "today": count_opens(today),
                "today_by_hand": count_opens(today, "hand"),
                "today_by_button": count_opens(today, "button"),
                "last_24h": count_opens(now - 24 * 3600),
                "all_time": count_opens(0),
            },
            "reboots": max(0, len(boots) - 1),
            "missing_messages": sum(max(0, b.highest_seq + 1 - b.received_count) for b in boots),
            "first_seen": boots[0].first_seen if boots else None,
        }

    @app.get("/api/history")
    def history(username: LoggedInUser, session: DB, device: str, hours: float = 24):
        check_device(session, username, device)
        hours = min(max(hours, 0.25), 24 * 7)
        since = time.time() - hours * 3600
        rows = session.exec(
            select(Reading.created_at, Reading.fill_percent, Reading.hand_cm, Reading.lid_open)
            .where(Reading.device_id == device, Reading.created_at >= since).order_by(Reading.created_at)
        ).all()
        step = max(1, math.ceil(len(rows) / MAX_CHART_POINTS))
        points = [{"created_at": r[0], "fill_percent": r[1], "hand_cm": r[2], "lid_open": r[3]}
                  for r in rows[::step]]

        bucket_seconds = max(900, int(hours * 3600 / 24))
        bucket_count = math.ceil(hours * 3600 / bucket_seconds)
        counts = [0] * bucket_count
        open_times = session.exec(
            select(Event.created_at).where(Event.device_id == device, Event.name == "lid_open",
                                           Event.created_at >= since)
        ).all()
        for t in open_times:
            index = int((t - since) // bucket_seconds)
            if 0 <= index < bucket_count:
                counts[index] += 1

        return {"ok": True, "device_id": device, "since": since, "hours": hours, "points": points,
                "opens": {"bucket_seconds": bucket_seconds, "counts": counts},
                "full_level_percent": full_level(session, device)}

    @app.get("/api/events")
    def recent_events(username: LoggedInUser, session: DB, device: str,
                      limit: Annotated[int, Query(ge=1, le=200)] = 25):
        check_device(session, username, device)
        rows = session.exec(select(Event).where(Event.device_id == device)
                            .order_by(Event.created_at.desc()).limit(limit)).all()
        return {"ok": True, "events": [
            {"created_at": e.created_at, "name": e.name, "detail": e.detail, "boot_id": e.boot_id}
            for e in rows]}

    @app.post("/api/settings")
    def change_settings(update: SettingsUpdate, username: LoggedInUser, session: DB):
        device = update.device_id
        check_device(session, username, device)
        old_level = full_level(session, device)
        save_setting(session, setting_name("full_level_percent", device), update.full_level_percent)
        if update.full_level_percent != old_level:
            now = time.time()
            session.add(Event(device_id=device, boot_id="dashboard", seq=None, created_at=now,
                              received_at=now, name="full_level_set",
                              detail=f"{username}: {old_level}% to {update.full_level_percent}%"))
        session.commit()
        return {"ok": True, "device_id": device, "full_level_percent": update.full_level_percent}

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    logging.basicConfig(level=logging.INFO)
    uvicorn.run(app, host=app.state.settings.host, port=app.state.settings.port,
                proxy_headers=True, forwarded_allow_ips="127.0.0.1")
