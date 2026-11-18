"""Accounts: hashing, lockout, the last-admin guard, sessions ending, the setup code and the CLI."""

import subprocess
import sys
from pathlib import Path

import pytest
from app import db, users


@pytest.fixture(autouse=True)
def fresh():
    with db.tx() as c:
        c.execute("delete from users")
    users._setup_code = None
    yield


def test_hash_and_verify():
    h = users.hash_password("correct horse battery")
    assert h.startswith("scrypt$32768$8$1$") and "correct" not in h
    assert users.verify_password("correct horse battery", h)
    assert not users.verify_password("wrong horse battery", h)


def test_password_rules():
    assert users.password_problem("short") is not None
    assert users.password_problem("ama-is-the-best-admin", "ama") is not None
    assert users.password_problem("password1234") is not None
    assert users.password_problem("four unrelated words here") is None


def test_lockout_after_five_failures_and_same_message():
    users.create("ama", "viewer", "four unrelated words here")
    msgs = {users.authenticate("ama", "nope nope nope")[1] for _ in range(5)}
    assert msgs == {"Wrong username or password"}
    assert users.authenticate("nobody", "nope nope nope")[1] == "Wrong username or password"
    u, why = users.authenticate("ama", "four unrelated words here")
    assert u is None and "locked" in why
    users.unlock(users.by_name("ama")["id"], "test")
    assert users.authenticate("ama", "four unrelated words here")[0]


def test_last_admin_guard():
    a = users.create("admin1", "admin", "four unrelated words here")
    b = users.create("op", "operator", "four unrelated words here")
    with pytest.raises(users.UserError):
        users.set_role(a["id"], "viewer", b["id"], "op")
    with pytest.raises(users.UserError):
        users.set_active(a["id"], False, b["id"], "op")
    with pytest.raises(users.UserError):
        users.delete(a["id"], b["id"], "op")
    with pytest.raises(users.UserError):
        users.set_role(a["id"], "viewer", a["id"], "admin1")  # your own admin role


def test_password_or_role_change_ends_sessions():
    u = users.create("kofi", "operator", "four unrelated words here")
    a = users.create("boss", "admin", "four unrelated words here")
    ver = users.get(u["id"])["session_ver"]
    users.set_role(u["id"], "viewer", a["id"], "boss")
    assert users.get(u["id"])["session_ver"] == ver + 1
    users.set_password(u["id"], "another four word phrase", actor="boss")
    assert users.get(u["id"])["session_ver"] == ver + 2


def test_setup_code_only_without_users():
    code = users.setup_code()
    assert len(code) == 24 and users.check_setup_code("-".join(code[i:i + 4] for i in range(0, 24, 4)).lower())
    users.create("first", "admin", "four unrelated words here")
    assert users.setup_code() is None and not users.check_setup_code(code)


def test_cli_never_takes_a_password_argument():
    root = Path(__file__).resolve().parent.parent
    r = subprocess.run([sys.executable, "report.py", "user", "add", "x", "--password", "secret secret"], cwd=root,
                       capture_output=True, text=True)
    assert r.returncode != 0 and "unrecognized arguments" in r.stderr
    r = subprocess.run([sys.executable, "report.py", "user", "add", "x"], cwd=root, capture_output=True, text=True,
                       stdin=subprocess.DEVNULL)
    assert r.returncode != 0 and "terminal" in (r.stderr + r.stdout)
