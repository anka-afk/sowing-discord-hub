"""CLI utility for managing Sowing Discord Hub admin/moderator accounts.

Usage:
    python -m app.cli_admin list
    python -m app.cli_admin create --username alice --password <secret> --role moderator
    python -m app.cli_admin set-active --username alice --active false
    python -m app.cli_admin reset-password --username alice --password <new_secret>
"""

from __future__ import annotations

import argparse
import sys
from typing import Optional

from app.config import Settings
from app.db import Database, utc_now_iso
from app.security import hash_password
from app.ulid import generate_prefixed_id


def create_admin(
    db: Database,
    username: str,
    password: str,
    role: str = "moderator",
) -> str:
    if role not in ("moderator", "super_admin"):
        raise ValueError("role must be 'moderator' or 'super_admin'")
    if len(username.strip()) < 2:
        raise ValueError("username must be at least 2 characters")
    if len(password) < 8:
        raise ValueError("password must be at least 8 characters")

    admin_id = generate_prefixed_id("adm")
    pwd_hash = hash_password(password)
    with db.write() as conn:
        existing = conn.execute(
            "SELECT id FROM admins WHERE username = ?", (username.strip(),)
        ).fetchone()
        if existing is not None:
            raise ValueError(f"Admin username '{username}' already exists")
        conn.execute(
            """
            INSERT INTO admins (id, username, password_hash, role, is_active, created_at)
            VALUES (?, ?, ?, ?, 1, ?)
            """,
            (admin_id, username.strip(), pwd_hash, role, utc_now_iso()),
        )
    return admin_id


def list_admins(db: Database) -> list[dict]:
    with db.read() as conn:
        rows = conn.execute(
            "SELECT id, username, role, is_active, created_at FROM admins ORDER BY created_at ASC"
        ).fetchall()
    return [dict(r) for r in rows]


def set_admin_active(db: Database, username: str, active: bool) -> bool:
    with db.write() as conn:
        cur = conn.execute(
            "UPDATE admins SET is_active = ? WHERE username = ?",
            (1 if active else 0, username.strip()),
        )
        return cur.rowcount > 0


def reset_admin_password(db: Database, username: str, password: str) -> bool:
    if len(password) < 8:
        raise ValueError("password must be at least 8 characters")
    pwd_hash = hash_password(password)
    with db.write() as conn:
        cur = conn.execute(
            "UPDATE admins SET password_hash = ? WHERE username = ?",
            (pwd_hash, username.strip()),
        )
        return cur.rowcount > 0


def main(argv: Optional[list[str]] = None, settings: Optional[Settings] = None) -> int:
    parser = argparse.ArgumentParser(description="Sowing Discord Hub admin account CLI")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list", help="List all admin/moderator accounts")

    p_create = sub.add_parser("create", help="Create a new admin or moderator")
    p_create.add_argument("--username", required=True)
    p_create.add_argument("--password", required=True)
    p_create.add_argument("--role", default="moderator", choices=["moderator", "super_admin"])

    p_active = sub.add_parser("set-active", help="Enable or disable an admin account")
    p_active.add_argument("--username", required=True)
    p_active.add_argument("--active", required=True, choices=["true", "false"])

    p_reset = sub.add_parser("reset-password", help="Reset an admin's password")
    p_reset.add_argument("--username", required=True)
    p_reset.add_argument("--password", required=True)

    args = parser.parse_args(argv)
    cfg = settings or Settings()
    db = Database(cfg.database_path)

    try:
        if args.cmd == "list":
            admins = list_admins(db)
            if not admins:
                print("No admin accounts found.")
            for a in admins:
                status = "active" if a["is_active"] else "disabled"
                print(f"{a['id']}  {a['username']:<16}  {a['role']:<11}  {status:<8}  {a['created_at']}")
            return 0
        if args.cmd == "create":
            aid = create_admin(db, args.username, args.password, role=args.role)
            print(f"Created admin {args.username} ({args.role}) id={aid}")
            return 0
        if args.cmd == "set-active":
            ok = set_admin_active(db, args.username, active=(args.active == "true"))
            if not ok:
                print(f"Admin '{args.username}' not found.", file=sys.stderr)
                return 1
            print(f"Updated {args.username} active={args.active}")
            return 0
        if args.cmd == "reset-password":
            ok = reset_admin_password(db, args.username, args.password)
            if not ok:
                print(f"Admin '{args.username}' not found.", file=sys.stderr)
                return 1
            print(f"Password updated for {args.username}")
            return 0
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
