from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from ui.users import USERS_PATH, authenticate, can, hash_password, load_users

APP = str(Path(__file__).resolve().parents[1] / "ui" / "app.py")
PASSWORD = "Fleet-Audit-2026"  # default from ui/seed_users.py


def test_accounts_file_has_hashes_and_no_plaintext_passwords():
    raw = USERS_PATH.read_text(encoding="utf-8")
    assert PASSWORD not in raw
    users = load_users()
    assert {u["role"] for u in users} == {"analyst", "reviewer", "admin"}
    assert all(len(u["hash"]) == 64 and len(u["salt"]) == 32 for u in users)


def test_authentication_accepts_the_right_password_only():
    email = load_users()[0]["email"]
    assert authenticate(email.upper(), PASSWORD)["email"] == email  # email is not case sensitive
    assert "hash" not in authenticate(email, PASSWORD) and "salt" not in authenticate(email, PASSWORD)
    assert authenticate(email, "wrong") is None
    assert authenticate("nobody@example.com", PASSWORD) is None
    assert authenticate("", "") is None


def test_hashes_are_salted():
    assert hash_password("same", "00" * 16) != hash_password("same", "11" * 16)


def test_role_permissions():
    assert not can("analyst", "rule") and not can("analyst", "run")
    assert can("reviewer", "rule") and not can("reviewer", "run")
    assert can("admin", "rule") and can("admin", "run")


def sign_in(email: str, password: str) -> AppTest:
    at = AppTest.from_file(APP, default_timeout=120)
    at.run()
    at.text_input[0].set_value(email)
    at.text_input[1].set_value(password)
    at.button[0].click()
    at.run()
    return at


def email_for(role: str) -> str:
    return next(u["email"] for u in load_users() if u["role"] == role)


def test_wrong_password_shows_an_error_and_stays_signed_out():
    at = sign_in(email_for("analyst"), "nope")
    assert any("incorrect" in e.value for e in at.error)
    assert not at.session_state["authenticated"]


@pytest.mark.parametrize("role", ["analyst", "reviewer", "admin"])
def test_signing_in_opens_the_console_without_errors(role):
    at = sign_in(email_for(role), PASSWORD)
    assert not at.exception
    assert at.session_state["authenticated"] and at.session_state["account"]["role"] == role


def test_only_administrators_see_the_audit_run_controls():
    labels = lambda at: [b.label for b in at.button]  # noqa: E731
    assert "Run audit" not in labels(sign_in(email_for("analyst"), PASSWORD))
    assert "Run audit" not in labels(sign_in(email_for("reviewer"), PASSWORD))
    assert "Run audit" in labels(sign_in(email_for("admin"), PASSWORD))


def test_sign_in_is_prefilled_and_one_click_signs_in():
    at = AppTest.from_file(APP, default_timeout=120)
    at.run()
    assert at.text_input[0].value in {u["email"] for u in load_users()}  # email filled
    assert at.text_input[1].value == PASSWORD  # password filled (masked in the browser)
    at.button[0].click()
    at.run()
    assert at.session_state["authenticated"] and not at.exception


def test_choosing_another_person_fills_their_email():
    at = AppTest.from_file(APP, default_timeout=120)
    at.run()
    target = next(u for u in load_users() if u["role"] == "admin")
    at.selectbox(key="signin_as").select(next(o for o in at.selectbox(key="signin_as").options if target["name"] in o))
    at.run()
    assert at.text_input[0].value == target["email"]


def test_prefill_can_be_switched_off(monkeypatch):
    monkeypatch.setenv("SLA_UI_AUTOFILL", "0")
    at = AppTest.from_file(APP, default_timeout=120)
    at.run()
    assert at.text_input[0].value == "" and at.text_input[1].value == ""
