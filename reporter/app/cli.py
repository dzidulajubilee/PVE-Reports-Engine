"""Command line: make a report now, and manage accounts (first admin, lost access).

    python report.py run weekly|monthly|quarterly|custom [--start D] [--end D] [--no-email]
    python report.py user add NAME [--role admin|operator|viewer]
    python report.py user reset-password NAME
    python report.py user list
    python report.py logs backfill [--days 30]

Passwords are always asked for interactively and never taken as an argument.
"""

import argparse
import datetime as dt
import getpass
import logging
import sys

from . import config, users
from .build import build
from .config import load_settings
from .history import mark_sent, record_made
from .mail import deliver, describe_sends, recipients
from .notify import notify
from .periods import KINDS, period_end, period_start, report_path, report_title


def run(kind, end=None, send=True, start=None, to=None):
    end = end or period_end(kind, dt.datetime.now(config.TZ))
    remade = report_path(kind, start or period_start(kind, end), end).exists()
    pdfs, subject, lines, errors, pages = build(kind, end, start)
    issues = [i for style in ("full", "summary") for i in pages.get(style, [])]
    record_made(pdfs["full"].name, "cli", remade, issues)
    what = report_title(kind, pdfs["full"])
    sends = [("full", to)] if to else (recipients(kind) if send else [])
    results = deliver(pdfs, subject, lines, sends)
    if send and not to and kind != "custom" and end == period_end(kind, dt.datetime.now(config.TZ)):
        mark_sent(kind, end)
    failed = [(style, t, err) for style, t, err in results if err]
    sent = describe_sends(results)
    notify(f"{what} was made from the command line" + (f" and {sent}" if sent else ""), file=pdfs["full"].name)
    if issues:
        notify(f"{what} has a page that may look wrong: {issues[0]}. Check the PDF", "error", file=pdfs["full"].name)
    for style, t, err in failed:
        notify(f"Emailing the {style} {what[0].lower() + what[1:]} to {t} failed: {err}", "error",
               file=pdfs["full"].name, admins_only=True)
    print(pdfs["full"])
    if failed:
        raise SystemExit(f"{len(failed)} email(s) failed")
    return pdfs["full"]


def ask_password(username):
    if not sys.stdin.isatty():
        raise SystemExit("Run this in a terminal (docker compose exec reporter ...): the password is asked for, "
                         "never passed as an argument")
    for _ in range(3):
        pw = getpass.getpass(f"New password for {username} (at least {users.MIN_LENGTH} characters): ")
        why = users.password_problem(pw, username)
        if why:
            print(why)
            continue
        if getpass.getpass("Again: ") != pw:
            print("The two passwords don't match")
            continue
        return pw
    raise SystemExit("No password set")


def user_cmd(a):
    if a.action == "list":
        rows = users.all_users()
        if not rows:
            print("No accounts yet")
        for u in rows:
            state = "active" if u["active"] else "deactivated"
            print(f"{u['username']:<24} {users.ROLE_LABELS[u['role']]:<9} {state}")
        return
    if not a.name:
        raise SystemExit("Give a username")
    if a.action == "add":
        pw = ask_password(a.name)
        try:
            users.create(a.name, a.role, pw, actor="command line")
        except users.UserError as e:
            raise SystemExit(str(e)) from e
        print(f"Added {a.name} ({users.ROLE_LABELS[a.role]})")
    elif a.action == "reset-password":
        u = users.by_name(a.name)
        if not u:
            raise SystemExit(f"No account called {a.name}")
        pw = ask_password(u["username"])
        users.set_password(u["id"], pw, actor="command line")
        users.unlock(u["id"], "command line")
        print(f"Password changed for {u['username']}; their other sessions have ended")


def logs_cmd(a):
    from . import snapshot
    from .pve import ApiError, describe, pve_client

    try:
        client = pve_client(load_settings())
    except ApiError as e:
        raise SystemExit(f"The Proxmox connection isn't set up yet: {e}") from e
    print(f"Reading the last {a.days} days of logs from the cluster. This can take a few minutes")

    def progress(step, steps):
        print(f"\r  {100 * step // steps:3d}%", end="", flush=True)

    try:
        kept, problems = snapshot.backfill(client, a.days, progress)
    except Exception as e:  # noqa: BLE001 - a plain reason, not a traceback
        raise SystemExit(f"Couldn't read past logs: {describe(e)}") from None
    print(f"\nKept {kept} warning and error lines")
    for p in problems:
        print(f"  {p}")
    notify(f"Past logs read from the command line: {kept} lines from the last {a.days} days", admins_only=True)


def main():
    load_settings()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stdout)
    ap = argparse.ArgumentParser(prog="report.py")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="make a report now")
    r.add_argument("kind", choices=list(KINDS))
    r.add_argument("--end", help="last day to include, e.g. 2026-11-15; default: the last closed period")
    r.add_argument("--start", help="first day to include (custom reports only)")
    r.add_argument("--no-email", action="store_true")
    u = sub.add_parser("user", help="manage accounts")
    u.add_argument("action", choices=["add", "reset-password", "list"])
    u.add_argument("name", nargs="?")
    u.add_argument("--role", choices=list(users.ROLES), default="admin")
    lg = sub.add_parser("logs", help="read past logs from the cluster")
    lg.add_argument("action", choices=["backfill"])
    lg.add_argument("--days", type=int, default=30, choices=range(1, 366), metavar="DAYS")
    a = ap.parse_args()
    if a.cmd == "user":
        return user_cmd(a)
    if a.cmd == "logs":
        return logs_cmd(a)

    def day(x):
        return dt.datetime.combine(dt.date.fromisoformat(x), dt.time(), config.TZ)

    if a.kind == "custom":
        if not a.start:
            ap.error("custom reports need --start (and optionally --end, default today)")
        end = day(a.end) + dt.timedelta(days=1) if a.end else dt.datetime.now(config.TZ)
        run("custom", end, send=False, start=day(a.start))
    else:
        end = day(a.end) + dt.timedelta(days=1) if a.end else None
        if end and end != period_end(a.kind, end):
            ap.error(f"--end for a {a.kind} report must be the last day of a {KINDS[a.kind]}")
        run(a.kind, end, send=not a.no_email)
