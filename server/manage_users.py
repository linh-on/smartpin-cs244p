"""Add and manage dashboard accounts.

Usernames and passwords are for PEOPLE who log in to the dashboard.
Bins never use them: a bin proves who it is by signing its reports (see DEVICE_KEYS).
Each account is linked to the bins it is allowed to see.

  python manage_users.py add <username> <device_id> [<device_id> ...]
  python manage_users.py passwd <username>
  python manage_users.py devices <username> <device_id> [<device_id> ...]
  python manage_users.py remove <username>
  python manage_users.py list

Passwords are typed when asked (they are not shown and never go in a file).

On the cloud server, run the same commands inside the app container, from deploy/:
  sudo docker compose exec app python manage_users.py add linh bin-2
"""
import argparse
import getpass
import re
import sys
import time

from sqlmodel import Session, delete, select

from config import load_settings
from db import User, UserDevice, devices_of, make_engine
from passwords import MIN_PASSWORD_LENGTH, hash_password

USERNAME_PATTERN = re.compile(r"^[a-z0-9_.-]{2,32}$")
DEVICE_ID_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{1,32}$")


class UserError(Exception):
    """A problem the person running the command can fix (bad name, user missing, ...)."""


def clean_username(username: str) -> str:
    username = username.strip().lower()
    if not USERNAME_PATTERN.match(username):
        raise UserError("username must be 2-32 characters: lowercase letters, digits, . _ -")
    return username


def check_password(password: str) -> None:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise UserError(f"password must be at least {MIN_PASSWORD_LENGTH} characters")


def check_device_ids(device_ids: list[str]) -> list[str]:
    if not device_ids:
        raise UserError("give at least one device id, for example bin-1")
    for device_id in device_ids:
        if not DEVICE_ID_PATTERN.match(device_id):
            raise UserError(f"bad device id {device_id!r}")
    return sorted(set(device_ids))


def add_user(engine, username: str, password: str, device_ids: list[str]) -> None:
    username = clean_username(username)
    check_password(password)
    device_ids = check_device_ids(device_ids)
    with Session(engine) as session:
        if session.get(User, username):
            raise UserError(f"user {username!r} already exists")
        session.add(User(username=username, password_hash=hash_password(password), created_at=time.time()))
        for device_id in device_ids:
            session.add(UserDevice(username=username, device_id=device_id))
        session.commit()


def set_password(engine, username: str, password: str) -> None:
    username = clean_username(username)
    check_password(password)
    with Session(engine) as session:
        user = session.get(User, username)
        if user is None:
            raise UserError(f"no user {username!r}")
        user.password_hash = hash_password(password)
        session.add(user)
        session.commit()


def set_devices(engine, username: str, device_ids: list[str]) -> None:
    username = clean_username(username)
    device_ids = check_device_ids(device_ids)
    with Session(engine) as session:
        if session.get(User, username) is None:
            raise UserError(f"no user {username!r}")
        session.exec(delete(UserDevice).where(UserDevice.username == username))
        for device_id in device_ids:
            session.add(UserDevice(username=username, device_id=device_id))
        session.commit()


def remove_user(engine, username: str) -> None:
    username = clean_username(username)
    with Session(engine) as session:
        user = session.get(User, username)
        if user is None:
            raise UserError(f"no user {username!r}")
        session.exec(delete(UserDevice).where(UserDevice.username == username))
        session.delete(user)
        session.commit()


def list_users(engine) -> list[tuple[str, list[str]]]:
    with Session(engine) as session:
        users = session.exec(select(User).order_by(User.username)).all()
        return [(user.username, devices_of(session, user.username)) for user in users]


def ask_new_password() -> str:
    password = getpass.getpass("New password: ")
    if password != getpass.getpass("Type it again: "):
        raise UserError("the two passwords do not match")
    return password


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Manage SmartBin dashboard accounts.")
    commands = parser.add_subparsers(dest="command", required=True)
    add = commands.add_parser("add", help="create an account linked to one or more bins")
    add.add_argument("username")
    add.add_argument("device_ids", nargs="+", metavar="device_id")
    passwd = commands.add_parser("passwd", help="change a password")
    passwd.add_argument("username")
    devices = commands.add_parser("devices", help="replace the bins an account can see")
    devices.add_argument("username")
    devices.add_argument("device_ids", nargs="+", metavar="device_id")
    remove = commands.add_parser("remove", help="delete an account (its bins' data stays)")
    remove.add_argument("username")
    commands.add_parser("list", help="show every account and its bins")
    args = parser.parse_args(argv)

    settings = load_settings()
    engine = make_engine(settings.database_url)
    try:
        if args.command == "add":
            add_user(engine, args.username, ask_new_password(), args.device_ids)
            print(f"Added {clean_username(args.username)}.")
        elif args.command == "passwd":
            set_password(engine, args.username, ask_new_password())
            print("Password changed.")
        elif args.command == "devices":
            set_devices(engine, args.username, args.device_ids)
            print("Bins updated.")
        elif args.command == "remove":
            remove_user(engine, args.username)
            print("Removed. That person is logged out on their next request.")
        else:
            rows = list_users(engine)
            if not rows:
                print("No accounts yet. Add one with: python manage_users.py add <username> <device_id>")
            for username, device_ids in rows:
                print(f"{username:20} {', '.join(device_ids) or '(no bins)'}")
    except UserError as problem:
        print(f"Error: {problem}", file=sys.stderr)
        return 1

    if args.command in ("add", "devices"):
        unknown = sorted(set(args.device_ids) - set(settings.device_keys))
        if unknown:
            print(f"Note: {', '.join(unknown)} not in DEVICE_KEYS yet, so the server will "
                  "reject reports from it until you add its key.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
