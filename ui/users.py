"""
ui/users.py

Staff accounts for the console. Passwords are stored as salted PBKDF2-SHA256 hashes in ui/users.json,
never in plain text. Create or reset the accounts with:  python -m ui.seed_users

Roles decide what a person can do:
  analyst   view audits, evidence and telemetry
  reviewer  everything an analyst can do, and record rulings on escalations
  administrator  everything a reviewer can do, and run audits
"""
from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
from typing import Optional

USERS_PATH = Path(__file__).with_name("users.json")
ITERATIONS = 200_000

ROLE_LABEL = {"analyst": "Analyst", "reviewer": "Reviewer", "admin": "Administrator"}
PERMISSIONS = {"analyst": set(), "reviewer": {"rule"}, "admin": {"rule", "run"}}


def hash_password(password: str, salt_hex: str, iterations: int = ITERATIONS) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), iterations).hex()


def load_users() -> list:
    try:
        return json.loads(USERS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return []


def authenticate(email: str, password: str) -> Optional[dict]:
    """Return the account (without secrets) if the email and password match, otherwise None."""
    email = (email or "").strip().lower()
    users = load_users()
    match = next((u for u in users if u["email"].lower() == email), None)
    # Do the same amount of work whether or not the email exists.
    salt = match["salt"] if match else "00" * 16
    expected = match["hash"] if match else "0" * 64
    ok = hmac.compare_digest(hash_password(password or "", salt), expected)
    if match and ok:
        return {k: v for k, v in match.items() if k not in ("salt", "hash")}
    return None


def can(role: str, action: str) -> bool:
    return action in PERMISSIONS.get(role, set())
