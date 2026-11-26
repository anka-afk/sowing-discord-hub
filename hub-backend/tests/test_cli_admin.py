from __future__ import annotations

from pathlib import Path

from app.cli_admin import create_admin, list_admins, main, reset_admin_password, set_admin_active
from app.config import Settings
from app.db import Database
from app.security import verify_password


def test_cli_admin_lifecycle(tmp_path: Path):
    settings = Settings(database_path=str(tmp_path / "cli_admins.db"))
    db = Database(settings.database_path)

    # Create moderator via CLI
    assert main(["create", "--username", "mod_alice", "--password", "StrongPass123", "--role", "moderator"], settings=settings) == 0
    # Duplicate username rejected
    assert main(["create", "--username", "mod_alice", "--password", "StrongPass123"], settings=settings) == 2

    admins = list_admins(db)
    assert len(admins) == 1
    assert admins[0]["username"] == "mod_alice"
    assert admins[0]["role"] == "moderator"
    assert admins[0]["is_active"] == 1

    # Reset password and verify hash
    assert reset_admin_password(db, "mod_alice", "NewSecret456") is True
    with db.read() as conn:
        row = conn.execute("SELECT password_hash FROM admins WHERE username = 'mod_alice'").fetchone()
    assert verify_password(row["password_hash"], "NewSecret456")
    assert not verify_password(row["password_hash"], "StrongPass123")

    # Disable & re-enable
    assert set_admin_active(db, "mod_alice", False) is True
    assert list_admins(db)[0]["is_active"] == 0
    assert main(["set-active", "--username", "mod_alice", "--active", "true"], settings=settings) == 0
    assert list_admins(db)[0]["is_active"] == 1
    assert main(["list"], settings=settings) == 0
