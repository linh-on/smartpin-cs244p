import hashlib
import hmac
import json

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from fastapi.testclient import TestClient
from sqlmodel import Session

from app import create_app
from config import Settings
from db import Boot, get_setting, make_engine, save_setting
from device_auth import parse_device_keys
from manage_users import UserError, add_user, list_users, remove_user, set_devices, set_password
from passwords import hash_password, password_matches

HMAC_SECRET = "test-secret"
LINH_PASSWORD = "correct horse"
SAM_PASSWORD = "battery staple"

# A stand-in for an ATECC608A: an ordinary P-256 key pair.
ECC_PRIVATE_KEY = ec.generate_private_key(ec.SECP256R1())
_numbers = ECC_PRIVATE_KEY.public_key().public_numbers()
ECC_PUBLIC_HEX = (_numbers.x.to_bytes(32, "big") + _numbers.y.to_bytes(32, "big")).hex()


@pytest.fixture
def app(tmp_path):
    settings = Settings(
        session_secret="x" * 32,
        session_cookie_secure=False,
        database_url=f"sqlite:///{tmp_path / 'test.db'}",
        host="127.0.0.1",
        port=0,
        device_keys=parse_device_keys(f"bin-1=hmac:{HMAC_SECRET},bin-2=ecdsa:{ECC_PUBLIC_HEX}"),
    )
    app = create_app(settings)
    # Two people, one bin each.
    add_user(app.state.engine, "linh", LINH_PASSWORD, ["bin-1"])
    add_user(app.state.engine, "sam", SAM_PASSWORD, ["bin-2"])
    return app


@pytest.fixture
def client(app):
    with TestClient(app) as test_client:
        yield test_client


def report(device_id="bin-1", boot_id="boot-a", seq=0, fill_percent=40.0, lid_opens=0):
    items = [{"type": "reading", "seq": seq, "fill_cm": 9.0, "fill_percent": fill_percent,
              "hand_cm": 70.1, "lid_open": False, "bin_full": False, "wifi_rssi": -55, "uptime_s": 12}]
    for i in range(lid_opens):
        items.append({"type": "event", "seq": seq + 1 + i, "name": "lid_open", "detail": "hand"})
    return json.dumps({"device_id": device_id, "boot_id": boot_id, "full_level_percent": 80,
                       "empty_distance_cm": 15.5, "items": items}).encode()


def hmac_sign(body, secret=HMAC_SECRET):
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def ecdsa_sign(body):
    r, s = decode_dss_signature(ECC_PRIVATE_KEY.sign(body, ec.ECDSA(hashes.SHA256())))
    return (r.to_bytes(32, "big") + s.to_bytes(32, "big")).hex()


def send(client, body, signature):
    headers = {"Content-Type": "application/json"}
    if signature is not None:
        headers["X-Signature"] = signature
    return client.post("/api/device/report", content=body, headers=headers)


def send_bin_1(client, **kwargs):
    body = report(device_id="bin-1", **kwargs)
    return send(client, body, hmac_sign(body))


def send_bin_2(client, **kwargs):
    body = report(device_id="bin-2", boot_id="boot-b", **kwargs)
    return send(client, body, ecdsa_sign(body))


def log_in(client, username="linh", password=LINH_PASSWORD):
    reply = client.post("/login", data={"username": username, "password": password}, follow_redirects=False)
    assert reply.status_code == 303


# ---------- device reports ----------

def test_hmac_signed_report_is_saved(client):
    reply = send_bin_1(client, lid_opens=2)
    assert reply.status_code == 200
    # The firmware looks for exactly this text in the reply.
    assert '"full_level_percent":80' in reply.text
    assert reply.json()["saved"] == 3


def test_ecdsa_signed_report_is_saved(client):
    assert send_bin_2(client).status_code == 200


@pytest.mark.parametrize("signature, reason", [
    (None, "missing X-Signature header"),
    ("zz", "bad signature"),
    ("00" * 32, "bad signature"),
])
def test_unsigned_or_badly_signed_report_is_rejected(client, signature, reason):
    reply = send(client, report(), signature)
    assert reply.status_code == 401
    assert reply.json()["error"] == reason


def test_changed_report_is_rejected(client):
    body = report(fill_percent=40.0)
    signature = hmac_sign(body)
    tampered = body.replace(b"40.0", b"99.0")
    assert send(client, tampered, signature).status_code == 401


def test_unknown_device_is_rejected(client):
    body = report(device_id="bin-9")
    assert send(client, body, hmac_sign(body)).status_code == 401


def test_replayed_report_is_not_stored_twice(client):
    assert send_bin_1(client, lid_opens=1).json()["saved"] == 2
    assert send_bin_1(client, lid_opens=1).json()["saved"] == 0


def test_out_of_range_value_is_rejected(client):
    reply = send_bin_1(client, fill_percent=150)
    assert reply.status_code == 400
    assert "fill_percent" in reply.json()["error"]


# ---------- logging in ----------

def test_dashboard_needs_login(client):
    assert client.get("/api/status?device=bin-1").status_code == 401
    assert client.get("/api/devices").status_code == 401
    assert client.get("/", follow_redirects=False).headers["location"] == "/login"


@pytest.mark.parametrize("username, password", [
    ("linh", "wrong password"),
    ("nobody", LINH_PASSWORD),
    ("linh", ""),
    ("", ""),
])
def test_wrong_login_is_rejected(client, username, password):
    reply = client.post("/login", data={"username": username, "password": password})
    assert reply.status_code == 401
    assert "Wrong username or password" in reply.text


def test_username_is_not_case_sensitive(client):
    log_in(client, "  Linh ", LINH_PASSWORD)
    assert client.get("/api/devices").json()["username"] == "linh"


def test_dashboard_page_renders(client):
    log_in(client)
    page = client.get("/")
    assert page.status_code == 200
    assert "/static/dashboard.js" in page.text
    assert "linh" in page.text


def test_log_out(client):
    log_in(client)
    client.post("/logout")
    assert client.get("/api/devices").status_code == 401


def test_removed_user_is_logged_out_right_away(app, client):
    log_in(client)
    remove_user(app.state.engine, "linh")
    assert client.get("/api/devices").status_code == 401


# ---------- each user only sees their own bins ----------

def test_user_sees_only_their_devices(client):
    log_in(client, "sam", SAM_PASSWORD)
    assert client.get("/api/devices").json()["devices"] == ["bin-2"]


def test_user_cannot_see_or_change_another_users_bin(client):
    send_bin_2(client)
    log_in(client)  # linh owns bin-1 only
    for url in ("/api/status?device=bin-2", "/api/history?device=bin-2", "/api/events?device=bin-2"):
        assert client.get(url).status_code == 404
    reply = client.post("/api/settings", json={"device_id": "bin-2", "full_level_percent": 50})
    assert reply.status_code == 404
    # A bin that does not exist gets the same answer, so names can't be guessed.
    assert client.get("/api/status?device=bin-9").json()["error"] == "no such bin on your account"


def test_status_and_history_only_include_my_bin(client):
    for seq in (0, 2):
        send_bin_1(client, seq=seq, fill_percent=20.0 + seq * 10, lid_opens=1)
    send_bin_2(client, fill_percent=90.0, lid_opens=1)
    log_in(client)

    status = client.get("/api/status?device=bin-1").json()
    assert status["online"] is True
    assert status["latest"]["fill_percent"] == 40.0
    assert status["opens"]["today"] == 2
    assert status["level_on_bin"] == 80
    assert status["empty_distance_cm"] == 15.5
    assert status["missing_messages"] == 0

    history = client.get("/api/history?device=bin-1&hours=1").json()
    assert [p["fill_percent"] for p in history["points"]] == [20.0, 40.0]
    assert sum(history["opens"]["counts"]) == 2


def test_full_level_is_set_per_bin(client):
    log_in(client)
    assert client.post("/api/settings", json={"device_id": "bin-1", "full_level_percent": 5}).status_code == 400
    reply = client.post("/api/settings", json={"device_id": "bin-1", "full_level_percent": 70})
    assert reply.json()["full_level_percent"] == 70

    assert send_bin_1(client).json()["full_level_percent"] == 70
    assert send_bin_2(client).json()["full_level_percent"] == 80  # sam's bin is untouched

    events = client.get("/api/events?device=bin-1").json()["events"]
    assert events[0]["name"] == "full_level_set"
    assert events[0]["detail"].startswith("linh:")


def test_old_shared_settings_move_to_existing_bins_only(tmp_path):
    # A database from before accounts: bin-1 has reported, and one shared level was saved.
    database_url = f"sqlite:///{tmp_path / 'old.db'}"
    engine = make_engine(database_url)
    with Session(engine) as session:
        session.add(Boot(boot_id="old-boot", device_id="bin-1", first_seen=0, last_seen=0,
                         highest_seq=0, received_count=1))
        save_setting(session, "full_level_percent", 65)
        session.commit()

    settings = Settings(session_secret="x" * 32, session_cookie_secure=False, database_url=database_url,
                        host="127.0.0.1", port=0,
                        device_keys=parse_device_keys(f"bin-1=hmac:{HMAC_SECRET},bin-2=ecdsa:{ECC_PUBLIC_HEX}"))
    upgraded = create_app(settings)
    with TestClient(upgraded) as client:
        assert send_bin_1(client).json()["full_level_percent"] == 65  # kept its level
        assert send_bin_2(client).json()["full_level_percent"] == 80  # new bin gets the default
    with Session(upgraded.state.engine) as session:
        assert get_setting(session, "full_level_percent") is None  # old shared copy is gone


def test_teammates_can_share_a_bin(app, client):
    set_devices(app.state.engine, "sam", ["bin-1", "bin-2"])
    log_in(client, "sam", SAM_PASSWORD)
    assert client.get("/api/devices").json()["devices"] == ["bin-1", "bin-2"]
    assert client.get("/api/status?device=bin-1").status_code == 200


# ---------- accounts and passwords ----------

def test_passwords_are_hashed_not_stored():
    stored = hash_password("correct horse")
    assert "correct horse" not in stored
    assert hash_password("correct horse") != stored  # random salt each time
    assert password_matches("correct horse", stored)
    assert not password_matches("correct horsE", stored)
    assert not password_matches("correct horse", "not-a-hash")


def test_account_rules(app):
    engine = app.state.engine
    with pytest.raises(UserError):
        add_user(engine, "linh", "another password", ["bin-1"])  # already exists
    with pytest.raises(UserError):
        add_user(engine, "kim", "short", ["bin-1"])  # password too short
    with pytest.raises(UserError):
        add_user(engine, "kim", "long enough", [])  # no bins
    with pytest.raises(UserError):
        add_user(engine, "bad name!", "long enough", ["bin-1"])
    assert list_users(engine) == [("linh", ["bin-1"]), ("sam", ["bin-2"])]


def test_changed_password_works(app, client):
    set_password(app.state.engine, "linh", "a new password")
    reply = client.post("/login", data={"username": "linh", "password": LINH_PASSWORD})
    assert reply.status_code == 401
    log_in(client, "linh", "a new password")


def test_bad_device_keys_config_is_reported():
    with pytest.raises(ValueError):
        parse_device_keys("bin-1=rsa:abc")
    with pytest.raises(ValueError):
        parse_device_keys("bin-1=ecdsa:abcd")
