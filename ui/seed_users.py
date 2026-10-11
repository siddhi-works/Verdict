"""
ui/seed_users.py

Creates ui/users.json with fictional staff accounts. The shared password comes from the SLA_UI_PASSWORD
environment variable; if it is not set, a default for local demos is used (printed below, change it for anything shared).

    python -m ui.seed_users
"""
import json
import os
import secrets

from ui.users import USERS_PATH, hash_password

DEFAULT_PASSWORD = "Fleet-Audit-2026"

PEOPLE = [
    {"email": "priya.deshmukh@michelin-sla.example", "name": "Priya Deshmukh", "role": "analyst",
     "title": "Commercial analyst, Pune"},
    {"email": "arjun.kulkarni@michelin-sla.example", "name": "Arjun Kulkarni", "role": "reviewer",
     "title": "Contract reviewer, Pune"},
    {"email": "camille.laurent@michelin-sla.example", "name": "Camille Laurent", "role": "admin",
     "title": "Fleet services finance lead"},
]


def main() -> None:
    password = os.getenv("SLA_UI_PASSWORD") or DEFAULT_PASSWORD
    users = []
    for p in PEOPLE:
        salt = secrets.token_hex(16)
        users.append({**p, "salt": salt, "hash": hash_password(password, salt)})
    USERS_PATH.write_text(json.dumps(users, indent=2), encoding="utf-8")
    print(f"Wrote {len(users)} accounts to {USERS_PATH.name}")
    print("Sign in with any of these emails:")
    for p in PEOPLE:
        print(f"  {p['email']}  ({p['role']})")
    print("Password: " + ("the value of SLA_UI_PASSWORD" if os.getenv("SLA_UI_PASSWORD") else f"{DEFAULT_PASSWORD}  (default, local demo only)"))


if __name__ == "__main__":
    main()
