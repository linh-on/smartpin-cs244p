"""Database tables. SQLite by default; set DATABASE_URL to use Postgres later."""
import time

from sqlalchemy import UniqueConstraint, event
from sqlalchemy.engine import Engine
from sqlmodel import Field, Session, SQLModel, create_engine, select


class Reading(SQLModel, table=True):
    __tablename__ = "readings"
    __table_args__ = (UniqueConstraint("boot_id", "seq"),)

    id: int | None = Field(default=None, primary_key=True)
    device_id: str
    boot_id: str
    seq: int
    created_at: float = Field(index=True)
    received_at: float
    hand_cm: float | None = None
    fill_cm: float | None = None
    fill_percent: float | None = None
    lid_open: bool = False
    bin_full: bool = False
    wifi_rssi: int | None = None
    uptime_s: int | None = None


class Event(SQLModel, table=True):
    __tablename__ = "events"
    __table_args__ = (UniqueConstraint("boot_id", "seq"),)

    id: int | None = Field(default=None, primary_key=True)
    device_id: str
    boot_id: str
    seq: int | None = None  # None for events made by the dashboard
    created_at: float = Field(index=True)
    received_at: float
    name: str = Field(index=True)
    detail: str = ""


class Boot(SQLModel, table=True):
    """One row per power-up of a device, used to spot reboots and lost messages."""
    __tablename__ = "boots"

    boot_id: str = Field(primary_key=True)
    device_id: str
    first_seen: float
    last_seen: float
    highest_seq: int
    received_count: int
    dropped_on_device: int = 0


class Setting(SQLModel, table=True):
    __tablename__ = "settings"

    name: str = Field(primary_key=True)
    value: str
    updated_at: float


class Message(SQLModel, table=True):
    """Append-only log of every accepted device report and its signature.

    Lets anyone re-check later that a stored reading was really signed by the device.
    """
    __tablename__ = "messages"

    id: int | None = Field(default=None, primary_key=True)
    device_id: str = Field(index=True)
    boot_id: str
    received_at: float = Field(index=True)
    auth_method: str
    signature: str
    body: str


def make_engine(database_url: str) -> Engine:
    is_sqlite = database_url.startswith("sqlite")
    engine = create_engine(
        database_url,
        connect_args={"check_same_thread": False, "timeout": 10} if is_sqlite else {},
    )
    if is_sqlite:
        @event.listens_for(engine, "connect")
        def use_wal(connection, _record):
            connection.execute("PRAGMA journal_mode=WAL")
    SQLModel.metadata.create_all(engine)
    return engine


def get_setting(session: Session, name: str, default: str | None = None) -> str | None:
    row = session.get(Setting, name)
    return row.value if row else default


def save_setting(session: Session, name: str, value) -> None:
    row = session.get(Setting, name)
    if row is None:
        row = Setting(name=name, value=str(value), updated_at=time.time())
    else:
        row.value, row.updated_at = str(value), time.time()
    session.add(row)


def existing_seqs(session: Session, table, boot_id: str, seqs: list[int]) -> set[int]:
    if not seqs:
        return set()
    query = select(table.seq).where(table.boot_id == boot_id, table.seq.in_(seqs))
    return set(session.exec(query).all())
