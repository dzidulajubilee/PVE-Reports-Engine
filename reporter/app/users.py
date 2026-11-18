"""The reporter's own accounts: roles, password hashing, lockout, the first-admin setup code and the audit log.

Passwords are hashed with scrypt from the standard library and compared in constant time. Nothing here logs or
stores a password or a hash anywhere but the users table.
"""

import hashlib
import hmac
import os
import secrets
import string
import time

from . import db
from .config import log

# Every route checks a named permission, never a role name, so a role can change here in one place.
ROLES = {
    "viewer": {"reports.view"},
    "operator": {"reports.view", "reports.make"},
    "admin": {"reports.view", "reports.make", "reports.email", "reports.delete", "settings", "users"},
}
ROLE_LABELS = {"viewer": "Viewer", "operator": "Operator", "admin": "Admin"}

N, R, P = 2**15, 8, 1
MIN_LENGTH = 12
LOCK_AFTER, LOCK_SECONDS = 5, 15 * 60
COMMON = {
    "password1234", "123456789012", "qwertyuiopas", "passwordpassword", "letmeinletmein", "administrator",
    "welcome12345", "iloveyou1234", "proxmox12345", "proxmoxproxmox", "changeme1234", "password123!",
    "qwerty123456", "1q2w3e4r5t6y", "abc123abc123", "trustno1trustno1", "summer2026!!", "winter2026!!",
}
USERNAME_OK = set(string.ascii_letters + string.digits + "._-@")


class UserError(Exception):
    """A plain-language reason an account change was refused."""


def can(role, permission):
    return permission in ROLES.get(role, set())


# ---------------------------------------------------------------- passwords


def hash_password(password):
    salt = os.urandom(16)
    h = hashlib.scrypt(password.encode(), salt=salt, n=N, r=R, p=P, maxmem=64 * 1024 * 1024, dklen=32)
    return f"scrypt${N}${R}${P}${salt.hex()}${h.hex()}"


def verify_password(password, stored):
    try:
        kind, n, r, p, salt, h = stored.split("$")
        if kind != "scrypt":
            return False
        got = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=int(n), r=int(r), p=int(p),
                             maxmem=64 * 1024 * 1024, dklen=len(bytes.fromhex(h)))
        return hmac.compare_digest(got, bytes.fromhex(h))
    except (ValueError, TypeError):
        return False


_DUMMY = None


def _dummy_hash():
    """Hashed once, so an unknown username costs as much time as a wrong password."""
    global _DUMMY
    if _DUMMY is None:
        _DUMMY = hash_password(secrets.token_hex(8))
    return _DUMMY


def password_problem(password, username=""):
    """None when the password is acceptable, else why not."""
    if len(password) < MIN_LENGTH:
        return f"Use at least {MIN_LENGTH} characters"
    low = password.lower()
    if username and username.lower() in low:
        return "The password can't contain the username"
    if low in COMMON or len(set(low)) < 4:
        return "That password is too common or too simple. Try a few unrelated words"
    return None


def temporary_password():
    """A one-time password an admin hands over: 4 groups of 4 easy-to-read characters."""
    alphabet = "abcdefghjkmnpqrstuvwxyz23456789"
    return "-".join("".join(secrets.choice(alphabet) for _ in range(4)) for _ in range(4))


# ---------------------------------------------------------------- reading


def count():
    return db.one("select count(*) as n from users")["n"]


def get(uid):
    return db.one("select * from users where id = ?", uid)


def by_name(username):
    return db.one("select * from users where username = ? collate nocase", username.strip())


def all_users():
    return db.query("select id, username, display, email, role, active, must_change, created, last_login, fails, "
                    "lock_until from users order by username collate nocase")


def active_admins(excluding=None):
    rows = db.query("select id from users where role = 'admin' and active = 1")
    return [r["id"] for r in rows if r["id"] != excluding]


# ---------------------------------------------------------------- audit


def audit(actor, action, target=""):
    with db.tx() as c:
        c.execute("insert into audit(t, actor, action, target) values (?, ?, ?, ?)", (time.time(), actor, action, target))
    log.info("audit: %s %s %s", actor, action, target)
    try:
        from .notify import notify

        notify(f"{actor} {action}" + (f" {target}" if target else ""), by=actor, admins_only=True)
    except Exception:  # noqa: BLE001 - the audit row is what counts
        pass


def audit_log(limit=100):
    return db.query("select t, actor, action, target from audit order by t desc limit ?", limit)


# ---------------------------------------------------------------- changes


def create(username, role, password, display="", email="", must_change=False, actor="setup"):
    username = username.strip()
    if not username or len(username) > 64 or not set(username) <= USERNAME_OK:
        raise UserError("Usernames use letters, digits and . _ - @ only, up to 64 characters")
    if role not in ROLES:
        raise UserError("Unknown role")
    if by_name(username):
        raise UserError("That username is taken")
    why = password_problem(password, username)
    if why:
        raise UserError(why)
    with db.tx() as c:
        c.execute(
            "insert into users(username, display, email, role, pw_hash, must_change, created) values (?, ?, ?, ?, ?, ?, ?)",
            (username, display.strip()[:80], email.strip()[:120], role, hash_password(password), int(must_change), time.time()),
        )
    audit(actor, "added user", f"{username} ({ROLE_LABELS[role]})")
    return by_name(username)


def _bump(c, uid):
    c.execute("update users set session_ver = session_ver + 1 where id = ?", (uid,))


def set_role(uid, role, actor_id, actor):
    u = get(uid)
    if not u or role not in ROLES:
        raise UserError("Unknown user or role")
    if u["role"] == "admin" and role != "admin":
        if uid == actor_id:
            raise UserError("You can't remove your own admin role")
        if u["active"] and not active_admins(excluding=uid):
            raise UserError("This is the last active admin")
    with db.tx() as c:
        c.execute("update users set role = ? where id = ?", (role, uid))
        _bump(c, uid)
    audit(actor, "changed the role of", f"{u['username']} to {ROLE_LABELS[role]}")


def set_active(uid, active, actor_id, actor):
    u = get(uid)
    if not u:
        raise UserError("Unknown user")
    if not active:
        if uid == actor_id:
            raise UserError("You can't deactivate yourself")
        if u["role"] == "admin" and not active_admins(excluding=uid):
            raise UserError("This is the last active admin")
    with db.tx() as c:
        c.execute("update users set active = ? where id = ?", (int(active), uid))
        _bump(c, uid)
    audit(actor, "reactivated" if active else "deactivated", u["username"])


def delete(uid, actor_id, actor):
    u = get(uid)
    if not u:
        raise UserError("Unknown user")
    if uid == actor_id:
        raise UserError("You can't delete yourself")
    if u["role"] == "admin" and u["active"] and not active_admins(excluding=uid):
        raise UserError("This is the last active admin")
    with db.tx() as c:
        c.execute("delete from users where id = ?", (uid,))
    audit(actor, "deleted user", u["username"])


def set_password(uid, password, must_change=False, actor=None):
    u = get(uid)
    if not u:
        raise UserError("Unknown user")
    why = password_problem(password, u["username"])
    if why:
        raise UserError(why)
    with db.tx() as c:
        c.execute("update users set pw_hash = ?, must_change = ?, fails = 0, lock_until = 0 where id = ?",
                  (hash_password(password), int(must_change), uid))
        _bump(c, uid)  # every other session of this user ends
    audit(actor or u["username"], "reset the password of" if actor and actor != u["username"] else "changed the password of",
          u["username"])


def unlock(uid, actor):
    u = get(uid)
    with db.tx() as c:
        c.execute("update users set fails = 0, lock_until = 0 where id = ?", (uid,))
    audit(actor, "unlocked", u["username"] if u else str(uid))


def sign_out_everywhere(uid, actor):
    with db.tx() as c:
        _bump(c, uid)
    u = get(uid)
    audit(actor, "signed out every session of", u["username"] if u else str(uid))


def update_profile(uid, display, email, actor):
    with db.tx() as c:
        c.execute("update users set display = ?, email = ? where id = ?", (display.strip()[:80], email.strip()[:120], uid))
    u = get(uid)
    audit(actor, "updated the profile of", u["username"] if u else str(uid))


# ---------------------------------------------------------------- sign-in


def authenticate(username, password, now=None):
    """(user, None) or (None, reason). The reason is the same for an unknown user and a wrong password."""
    now = now or time.time()
    wrong = "Wrong username or password"
    u = by_name(username or "")
    if not u:
        verify_password(password, _dummy_hash())
        return None, wrong
    if u["lock_until"] > now:
        verify_password(password, _dummy_hash())
        return None, "Too many failed attempts. This account is locked for 15 minutes"
    if not verify_password(password, u["pw_hash"]):
        fails = u["fails"] + 1
        lock = now + LOCK_SECONDS if fails >= LOCK_AFTER else 0
        with db.tx() as c:
            c.execute("update users set fails = ?, lock_until = ? where id = ?", (0 if lock else fails, lock, u["id"]))
        if lock:
            log.warning("account %s locked after %d failed sign-ins", u["username"], fails)
        return None, wrong
    if not u["active"]:
        return None, wrong
    with db.tx() as c:
        c.execute("update users set fails = 0, lock_until = 0, last_login = ? where id = ?", (now, u["id"]))
    return get(u["id"]), None


# ---------------------------------------------------------------- first admin

_setup_code = None


def setup_code():
    """While there are no users: a 24-character code, logged once and kept in memory only."""
    global _setup_code
    if count():
        _setup_code = None
        return None
    if _setup_code is None:
        alphabet = string.ascii_uppercase + string.digits
        _setup_code = "".join(secrets.choice(alphabet) for _ in range(24))
        log.warning("No accounts yet. Open the portal and enter this setup code to create the first admin: %s",
                    "-".join(_setup_code[i:i + 4] for i in range(0, 24, 4)))
    return _setup_code


def check_setup_code(code):
    want = setup_code()
    got = (code or "").replace("-", "").replace(" ", "").upper()
    return bool(want) and hmac.compare_digest(got.encode(), want.encode())
