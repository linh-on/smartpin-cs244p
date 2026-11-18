"""Server settings, read from environment variables (and server/.env if present)."""
import os
import secrets
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

from device_auth import DeviceKey, parse_device_keys

SERVER_FOLDER = Path(__file__).resolve().parent


@dataclass(frozen=True)
class Settings:
    session_secret: str
    session_cookie_secure: bool
    database_url: str
    host: str
    port: int
    device_keys: dict[str, DeviceKey]


def load_settings() -> Settings:
    load_dotenv(SERVER_FOLDER / ".env")
    return Settings(
        # Without a fixed secret, everyone is logged out when the server restarts.
        session_secret=os.environ.get("SESSION_SECRET") or secrets.token_hex(32),
        # Set to true once the dashboard is served over HTTPS.
        session_cookie_secure=os.environ.get("SESSION_COOKIE_SECURE", "false").lower() == "true",
        database_url=os.environ.get("DATABASE_URL", f"sqlite:///{SERVER_FOLDER / 'trash_can.db'}"),
        host=os.environ.get("HOST", "0.0.0.0"),
        port=int(os.environ.get("PORT", "5001")),
        device_keys=parse_device_keys(os.environ.get("DEVICE_KEYS", "")),
    )
