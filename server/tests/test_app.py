import hashlib
import hmac
import json

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from fastapi.testclient import TestClient

from app import create_app
from config import Settings
from device_auth import parse_device_keys

HMAC_SECRET = "test-secret"
PASSWORD = "correct horse"

# A stand-in for an ATECC608A: an ordinary P-256 key pair.
ECC_PRIVATE_KEY = ec.generate_private_key(ec.SECP256R1())
_numbers = ECC_PRIVATE_KEY.public_key().public_numbers()
ECC_PUBLIC_HEX = (_numbers.x.to_bytes(32, "big") + _numbers.y.to_bytes(32, "big")).hex()


@pytest.fixture
def client(tmp_path):
    settings = Settings(
        dashboard_password=PASSWORD,
        session_secret="x" * 32,
        session_cookie_secure=False,
        database_url=f"sqlite:///{tmp_path / 'test.db'}",
        host="127.0.0.1",
        port=0,
        device_keys=parse_device_keys(f"bin-1=hmac:{HMAC_SECRET},bin-2=ecdsa:{ECC_PUBLIC_HEX}"),
    )
    with TestClient(create_app(settings)) as test_client:
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


def log_in(client):
    assert client.post("/login", data={"password": PASSWORD}, follow_redirects=False).status_code == 303


def test_hmac_signed_report_is_saved(client):
    body = report(lid_opens=2)
    reply = send(client, body, hmac_sign(body))
    assert reply.status_code == 200
    # The firmware looks for exactly this text in the reply.
    assert '"full_level_percent":80' in reply.text
    assert reply.json()["saved"] == 3


def test_ecdsa_signed_report_is_saved(client):
    body = report(device_id="bin-2")
    assert send(client, body, ecdsa_sign(body)).status_code == 200


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
    body = report(lid_opens=1)
    assert send(client, body, hmac_sign(body)).json()["saved"] == 2
    assert send(client, body, hmac_sign(body)).json()["saved"] == 0


def test_out_of_range_value_is_rejected(client):
    body = report(fill_percent=150)
    reply = send(client, body, hmac_sign(body))
    assert reply.status_code == 400
    assert "fill_percent" in reply.json()["error"]


def test_dashboard_needs_login(client):
    assert client.get("/api/status").status_code == 401
    assert client.get("/", follow_redirects=False).headers["location"] == "/login"
    assert client.post("/login", data={"password": "wrong"}).status_code == 401


def test_status_and_history_after_reports(client):
    for seq in (0, 2):
        body = report(seq=seq, fill_percent=20.0 + seq * 10, lid_opens=1)
        send(client, body, hmac_sign(body))
    log_in(client)

    status = client.get("/api/status").json()
    assert status["online"] is True
    assert status["latest"]["fill_percent"] == 40.0
    assert status["opens"]["today"] == 2
    assert status["level_on_bin"] == 80
    assert status["empty_distance_cm"] == 15.5
    assert status["missing_messages"] == 0

    history = client.get("/api/history?hours=1").json()
    assert [p["fill_percent"] for p in history["points"]] == [20.0, 40.0]
    assert sum(history["opens"]["counts"]) == 2


def test_dashboard_page_renders(client):
    log_in(client)
    page = client.get("/")
    assert page.status_code == 200
    assert "/static/dashboard.js" in page.text


def test_full_level_setting_reaches_the_bin(client):
    log_in(client)
    assert client.post("/api/settings", json={"full_level_percent": 5}).status_code == 400
    assert client.post("/api/settings", json={"full_level_percent": 70}).json()["full_level_percent"] == 70

    body = report()
    assert send(client, body, hmac_sign(body)).json()["full_level_percent"] == 70
    events = client.get("/api/events").json()["events"]
    assert events[0]["name"] == "full_level_set"


def test_bad_device_keys_config_is_reported():
    with pytest.raises(ValueError):
        parse_device_keys("bin-1=rsa:abc")
    with pytest.raises(ValueError):
        parse_device_keys("bin-1=ecdsa:abcd")
