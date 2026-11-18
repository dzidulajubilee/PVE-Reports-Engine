"""Reports portal: the reporter's own accounts, making and downloading reports, and (admins) the Proxmox
connection, email, the schedule, report looks and users.

One process runs the web app, a single worker that builds reports one at a time, the scheduler, which drops due
reports into the same queue, and the snapshot thread that records the cluster every 5 minutes.
"""

import datetime as dt
import itertools
import logging
import os
import queue
import re
import secrets
import sys
import threading
import time
from functools import wraps

from flask import (
    Flask,
    abort,
    flash,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    send_from_directory,
    session,
    url_for,
)

from . import api as R
from . import config, snapshot, users
from .pve import test as test_connection

log = logging.getLogger("portal")

app = Flask(
    __name__,
    static_folder=str(config.ROOT / "static"),
    static_url_path="/static",
    template_folder=str(config.ROOT / "templates" / "portal"),
)
IDLE = 30 * 60
ABSOLUTE = 12 * 3600


def _secret():
    """Session key kept in the output folder so logins survive restarts."""
    f = config.OUT / ".portal-secret"
    if not f.exists():
        config.OUT.mkdir(parents=True, exist_ok=True)
        f.write_text(secrets.token_hex(32))
        os.chmod(f, 0o600)
    return f.read_text().strip()


app.config.update(
    SECRET_KEY=_secret(),
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.environ.get("COOKIE_SECURE", "true").lower() != "false",  # nginx serves HTTPS
    PERMANENT_SESSION_LIFETIME=dt.timedelta(seconds=ABSOLUTE),
    MAX_CONTENT_LENGTH=4 * 1024 * 1024,
)


@app.get("/fonts/<path:name>")
def fonts(name):
    return send_from_directory(config.ROOT / "fonts", name, max_age=86400)


# ---------------------------------------------------------------- jobs

jobs = {}
work = queue.Queue()
_ids = itertools.count(1)
_lock = threading.Lock()


def enqueue(kind, start, end, by, sends=(), login=None):
    with _lock:
        jid = next(_ids)
        jobs[jid] = {"id": jid, "kind": kind, "start": start, "end": end, "by": by, "sends": list(sends),
                     "to": ", ".join(to for _, to in sends), "login": login, "status": "queued", "file": None,
                     "error": None, "queued_at": dt.datetime.now(config.TZ)}
        for old in sorted(jobs)[:-30]:
            jobs.pop(old)
    work.put(jid)
    return jid


def worker():
    while True:
        jid = work.get()
        job = jobs.get(jid)
        if not job:
            continue
        job["status"] = "running"
        kind, by, login = job["kind"], job["by"], job.get("login")
        what = R.report_title(kind, job["start"], job["end"])
        who = "automatically" if by == "schedule" else f"by {by}"
        old = R.report_path(kind, job["start"], job["end"])
        replaces = dt.datetime.fromtimestamp(old.stat().st_mtime, config.TZ) if old.exists() else None
        try:
            pdfs, subject, lines, errors, pages = R.build(kind, job["end"], job["start"] if kind == "custom" else None)
        except Exception as e:  # noqa: BLE001 - reported, worker keeps going
            log.exception("job %s failed", jid)
            job.update(status="failed", error=str(e) or e.__class__.__name__)
            R.notify(f"{what} could not be made ({who}): {job['error']}", "error", by=by)
            continue
        full = pdfs["full"].name
        job.update(status="done", file=full)
        if by == "schedule":
            R.mark_sent(kind, job["end"])
        issues = [i for style in ("full", "summary") for i in pages.get(style, [])]
        R.record_made(full, by, replaces is not None, issues)
        done = (f"{what} was remade {who}, replacing the copy from {replaces:%-d %b %H:%M}" if replaces
                else f"{what} was made {who}")
        if errors:
            R.notify(f"{done}, but {errors} figure{'s' * (errors != 1)} couldn't be fetched from Proxmox and show "
                     "as –. Check the connection and the token's permissions, then make it again", "error", by=by, file=full)
        if issues:
            R.notify(f"{what} has a page that may look wrong: {issues[0]}. Check the PDF", "error", by=by, file=full)
        results = R.deliver(pdfs, subject, lines, job["sends"]) if R.load_settings()["smtp"].get("host") else []
        sent = R.describe_sends(results)
        R.notify(done + (f" and {sent}" if sent else ""), by=by, file=full)
        for style, to, err in results:
            if err:
                job["error"] = f"Email failed: {err}"
                R.notify(f"Emailing the {style} {what[0].lower() + what[1:]} to {to} failed: {err}. The report is saved here",
                         "error", by=by, file=full, admins_only=True, for_user=login)


def scheduler():
    while True:
        try:
            busy = {(j["kind"], j["end"]) for j in jobs.values() if j["status"] in ("queued", "running")}
            for kind, end in R.due_reports():
                if (kind, end) not in busy:
                    enqueue(kind, R.period_start(kind, end), end, "schedule", R.recipients(kind))
        except Exception:  # noqa: BLE001
            log.exception("scheduler tick failed")
        time.sleep(300)


# ---------------------------------------------------------------- sessions and permissions

_fails = {}  # ip -> [timestamps]


def current_user():
    """The signed-in user, checked against the database on every request: still active, same session version,
    not idle too long. None otherwise (and the session is cleared)."""
    uid = session.get("uid")
    if not uid:
        return None
    now = time.time()
    u = users.get(uid)
    if (not u or not u["active"] or u["session_ver"] != session.get("ver")
            or now - session.get("seen", 0) > IDLE or now - session.get("since", 0) > ABSOLUTE):
        session.clear()
        return None
    session["seen"] = now
    return u


def requires(permission=None):
    def deco(fn):
        @wraps(fn)
        def wrapper(*a, **kw):
            if users.count() == 0:
                return redirect(url_for("setup"))
            u = current_user()
            if not u:
                return redirect(url_for("login", next=request.path))
            g.user = u
            if u["must_change"] and request.endpoint not in ("account", "logout", "fonts"):
                return redirect(url_for("account"))
            if permission and not users.can(u["role"], permission):
                abort(403)
            if request.method == "POST" and not secrets.compare_digest(request.form.get("csrf", ""), session.get("csrf", "")):
                abort(400)
            return fn(*a, **kw)

        return wrapper

    return deco


def me():
    return g.user["username"]


def can(permission):
    u = getattr(g, "user", None)
    return bool(u) and users.can(u["role"], permission)


@app.context_processor
def common():
    st = R.load_settings()
    u = getattr(g, "user", None)
    return {"user": (u["display"] or u["username"]) if u else None, "role": u["role"] if u else None, "can": can,
            "csrf": session.get("csrf"), "name": R.display_name(st), "custom_name": bool(st["general"]["name"].strip()),
            "tz": str(config.TZ)}


@app.after_request
def headers(r):
    r.headers.setdefault("X-Content-Type-Options", "nosniff")
    r.headers.setdefault("X-Frame-Options", "DENY")
    r.headers.setdefault("Referrer-Policy", "same-origin")
    return r


def _start_session(u):
    session.clear()  # a fresh session at every sign-in
    session.permanent = True
    now = time.time()
    session.update(uid=u["id"], ver=u["session_ver"], csrf=secrets.token_hex(16), since=now, seen=now)


def _safe_next():
    nxt = request.args.get("next", "")
    return nxt if nxt.startswith("/") and not nxt.startswith("//") and "\\" not in nxt else url_for("index")


@app.route("/setup", methods=["GET", "POST"])
def setup():
    """First start: no accounts yet. The setup code from the container log creates the first admin."""
    if users.count():
        return redirect(url_for("login"))
    users.setup_code()
    error = None
    if request.method == "POST":
        ip = request.remote_addr
        recent = [t for t in _fails.get(ip, []) if t > time.time() - 300]
        f = request.form
        if len(recent) >= 5:
            error = "Too many attempts. Try again in 5 minutes"
        elif not users.check_setup_code(f.get("code")):
            _fails[ip] = recent + [time.time()]
            error = "That setup code isn't right. It's in the container log: docker compose logs reporter"
        elif f.get("password") != f.get("password2"):
            error = "The two passwords don't match"
        else:
            try:
                u = users.create(f.get("username", ""), "admin", f.get("password", ""), f.get("display", ""),
                                 f.get("email", ""), actor="setup")
                _start_session(u)
                return redirect(url_for("settings"))
            except users.UserError as e:
                error = str(e)
    return render_template("setup.html", error=error)


@app.route("/login", methods=["GET", "POST"])
def login():
    if users.count() == 0:
        return redirect(url_for("setup"))
    error = None
    if request.method == "POST":
        ip = request.remote_addr
        recent = [t for t in _fails.get(ip, []) if t > time.time() - 300]
        if len(recent) >= 5:
            error = "Too many failed attempts. Try again in 5 minutes"
        else:
            u, error = users.authenticate(request.form.get("username", ""), request.form.get("password", ""))
            if u:
                _fails.pop(ip, None)
                _start_session(u)
                return redirect(url_for("account") if u["must_change"] else _safe_next())
            _fails[ip] = recent + [time.time()]
    return render_template("login.html", error=error)


@app.post("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ---------------------------------------------------------------- account


@app.route("/account", methods=["GET", "POST"])
@requires()
def account():
    u = g.user
    if request.method == "POST":
        f = request.form
        if f.get("action") == "everywhere":
            users.sign_out_everywhere(u["id"], me())
            session.clear()
            return redirect(url_for("login"))
        if f.get("action") == "profile":
            users.update_profile(u["id"], f.get("display", ""), f.get("email", ""), me())
            flash("Saved")
            return redirect(url_for("account"))
        if not users.verify_password(f.get("current", ""), u["pw_hash"]):
            flash("Your current password isn't right")
        elif f.get("new") != f.get("new2"):
            flash("The two new passwords don't match")
        else:
            try:
                users.set_password(u["id"], f.get("new", ""), actor=u["username"])
                _start_session(users.get(u["id"]))  # this session continues; others end
                flash("Password changed. You've been signed out everywhere else")
                return redirect(url_for("index"))
            except users.UserError as e:
                flash(str(e))
        return redirect(url_for("account"))
    return render_template("account.html", u=u, role_label=users.ROLE_LABELS[u["role"]])


# ---------------------------------------------------------------- users (admins)


@app.route("/users", methods=["GET", "POST"])
@requires("users")
def users_page():
    shown = None
    if request.method == "POST":
        f = request.form
        action = f.get("action")
        try:
            uid = int(f.get("uid") or 0)
            if action == "add":
                temp = f.get("password") or ""
                one_time = not temp
                if one_time:
                    temp = users.temporary_password()
                users.create(f.get("username", ""), f.get("role", "viewer"), temp, f.get("display", ""),
                             f.get("email", ""), must_change=True, actor=me())
                if one_time:
                    shown = (f.get("username", "").strip(), temp)
                else:
                    flash(f"Added {f.get('username', '').strip()}. They choose a new password at first sign-in")
            elif action == "role":
                users.set_role(uid, f.get("role"), g.user["id"], me())
                flash("Role changed")
            elif action in ("deactivate", "activate"):
                users.set_active(uid, action == "activate", g.user["id"], me())
                flash("Saved")
            elif action == "reset":
                temp = users.temporary_password()
                users.set_password(uid, temp, must_change=True, actor=me())
                shown = (users.get(uid)["username"], temp)
            elif action == "unlock":
                users.unlock(uid, me())
                flash("Unlocked")
            elif action == "delete":
                users.delete(uid, g.user["id"], me())
                flash("Deleted")
            elif action == "everywhere":
                users.sign_out_everywhere(uid, me())
                flash("Signed out everywhere")
        except users.UserError as e:
            flash(str(e))
        except (ValueError, TypeError):
            flash("Something in the form wasn't right")
        if not shown:
            return redirect(url_for("users_page"))
    return render_template("users.html", rows=users.all_users(), roles=users.ROLE_LABELS, shown=shown,
                           audit=users.audit_log(40), now=time.time(), me_id=g.user["id"],
                           fmt=lambda t: dt.datetime.fromtimestamp(t, config.TZ).strftime("%-d %b %Y %H:%M") if t else "never")


# ---------------------------------------------------------------- reports

NAME = re.compile(r"^(weekly|monthly|quarterly|custom)-(\d{4}-\d{2}-\d{2})(?:-to-(\d{4}-\d{2}-\d{2}))?(-summary)?\.pdf$")


def describe(kind, start, last):
    if kind == "custom":
        return "Custom", f"{start:%-d %b %Y}" if start == last else f"{start:%-d %b} – {last:%-d %b %Y}"
    end = last + dt.timedelta(days=1)
    s = dt.datetime.combine(start, dt.time(), config.TZ)
    e = dt.datetime.combine(end, dt.time(), config.TZ)
    title, dates = R.period_label(kind, s, e)
    return kind.capitalize(), f"{title} · {dates}"


def remade_note(entry):
    if not entry or not entry.get("remade"):
        return None
    by = entry.get("by")
    return {"schedule": "remade automatically", "cli": "remade from the command line"}.get(by, f"remade by {by}" if by else "remade")


def report_note(entry):
    parts = [n for n in (remade_note(entry), "pages need a look" if entry and entry.get("layout") else None) if n]
    return " · ".join(parts) or None


def _period_last(kind, start):
    if kind == "weekly":
        return (start + dt.timedelta(days=6)).date()
    months = 1 if kind == "monthly" else 3
    m = start.month + months
    nxt = start.replace(year=start.year + (m - 1) // 12, month=(m - 1) % 12 + 1)
    return (nxt - dt.timedelta(days=1)).date()


def archive():
    out, made = [], R.made()
    for f in config.OUT.glob("*.pdf"):
        m = NAME.match(f.name)
        if not m or m[4]:
            continue
        kind, start = m[1], dt.date.fromisoformat(m[2])
        last = dt.date.fromisoformat(m[3]) if m[3] else _period_last(kind, dt.datetime.combine(start, dt.time(), config.TZ))
        label, period = describe(kind, start, last)
        st = f.stat()
        summary = f.with_name(f.stem + "-summary.pdf")
        out.append({"file": f.name, "summary": summary.name if summary.exists() else None, "kind": label, "period": period,
                    "start": start, "made": dt.datetime.fromtimestamp(st.st_mtime, config.TZ),
                    "note": report_note(made.get(f.name)), "layout": (made.get(f.name) or {}).get("layout"),
                    "size": st.st_size})
    return sorted(out, key=lambda r: (r["start"], r["made"]), reverse=True)


def job_view(j):
    label, period = (describe(j["kind"], j["start"].date(), (j["end"] - dt.timedelta(days=1)).date())
                     if j["kind"] != "custom" or j["end"].time() == dt.time() else ("Custom", f"{j['start']:%-d %b} – now"))
    return {"id": j["id"], "kind": label, "period": period, "status": j["status"], "file": j["file"], "error": j["error"],
            "by": j["by"], "to": j["to"], "queued_at": j["queued_at"].strftime("%H:%M")}


def period_choices():
    now = dt.datetime.now(config.TZ)
    out = {}
    for kind in ("weekly", "monthly", "quarterly"):
        end = R.period_end(kind, now)
        last = R.period_start(kind, end)
        before = R.period_start(kind, last)

        def label(s, e, kind=kind):
            title, dates = R.period_label(kind, s, e)
            return f"{title} · {dates}" if kind in ("weekly", "quarterly") else title

        out[kind] = {"last": label(last, end), "previous": label(before, last), "current": label(end, R._next_period(kind, end))}
    return out


def schedule_text(sch):
    when = {"weekly": "weekly reports every Monday", "monthly": "monthly reports on the 1st",
            "quarterly": "quarterly reports on the first day of each quarter"}
    parts = [when[k] for k in ("weekly", "monthly", "quarterly") if k in sch.get("kinds", [])]
    if not sch.get("enabled") or not parts:
        return "Automatic reports are off"
    text = parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " and " + parts[-1]
    return f"Made automatically: {text}, at {int(sch.get('hour', 7)):02d}:00"


def active_jobs():
    return [job_view(j) for j in reversed(jobs.values()) if j["status"] in ("queued", "running")]


def connection_state(s):
    if not s["pve"]["hosts"] or not s["pve"]["token_id"]:
        return "unset"
    last = R.db.get_state("last_snapshot")
    if last and time.time() - int(last) < 900:
        return "ok"
    return "stale"


@app.get("/")
@requires("reports.view")
def index():
    s = R.load_settings()
    today = dt.datetime.now(config.TZ).date()
    return render_template("reports.html", reports=archive(), jobs=active_jobs(), periods=period_choices(),
                           recipients=s["recipients"], recipients_summary=s["recipients_summary"], today=today,
                           schedule=schedule_text(s["schedule"]), week_ago=today - dt.timedelta(days=7),
                           mail_ready=bool(s["smtp"].get("host")), connection=connection_state(s))


@app.post("/generate")
@requires("reports.make")
def generate():
    f = request.form
    kind = f.get("kind")
    R.load_settings()
    now = dt.datetime.now(config.TZ)

    def day(x):
        return dt.datetime.combine(dt.date.fromisoformat(x), dt.time(), config.TZ)

    try:
        if kind == "custom":
            start, last = day(f["start"]), day(f["end"])
            if last < start:
                raise ValueError("the end date is before the start date")
            if (last - start).days > 366:
                raise ValueError("pick a range of a year or less")
            end = min(last + dt.timedelta(days=1), now)
        elif kind in ("weekly", "monthly", "quarterly"):
            end = R.period_end(kind, now)
            if f.get("which") == "previous":
                end = R.period_start(kind, end)
            start = R.period_start(kind, end)
        else:
            raise ValueError("unknown report type")
    except (KeyError, ValueError) as e:
        flash(f"Can't make that report: {e}")
        return redirect(url_for("index"))
    # only admins can email reports: anyone else could send the cluster's details to any address
    to = f.get("to", "").strip() if f.get("email") and can("reports.email") else ""
    if to and not R.load_settings()["smtp"].get("host"):
        flash("Email isn't set up yet, so the report will only be saved here")
        to = ""
    style = "summary" if f.get("style") == "summary" else "full"
    enqueue(kind, start, end, g.user["display"] or me(), [(style, to)] if to else [], login=me())
    return redirect(url_for("index"))


@app.get("/jobs")
@requires("reports.view")
def jobs_json():
    return jsonify(active_jobs())


# ---------------------------------------------------------------- notifications


def ago(iso):
    t = dt.datetime.fromisoformat(iso).astimezone(config.TZ)
    secs = (dt.datetime.now(config.TZ) - t).total_seconds()
    if secs < 60:
        return "just now"
    if secs < 3600:
        return f"{int(secs // 60)} min ago"
    if secs < 6 * 3600:
        return f"{int(secs // 3600)} h ago"
    return t.strftime("%-d %b %H:%M")


@app.get("/notifications")
@requires("reports.view")
def notifications():
    notes = R.notes_for(me(), can("users"))
    return jsonify({"unread": sum(not n["read"] for n in notes),
                    "items": [{"id": n["id"], "text": n["text"], "level": n["level"], "read": n["read"], "when": ago(n["t"]),
                               "file": n["file"] if n.get("file") and NAME.match(n["file"]) and (config.OUT / n["file"]).exists() else None}
                              for n in notes[:50]]})


@app.post("/notifications/read")
@requires("reports.view")
def notifications_read():
    ids = [n["id"] for n in R.notes_for(me(), can("users"))] if request.form.get("all") else request.form.getlist("id")
    R.mark_read(me(), ids)
    return notifications()


@app.post("/notifications/clear")
@requires("reports.view")
def notifications_clear():
    R.clear(me(), [n["id"] for n in R.notes_for(me(), can("users"))])
    return notifications()


@app.get("/download/<name>")
@requires("reports.view")
def download(name):
    if not NAME.match(name) or not (config.OUT / name).exists():
        abort(404)
    return send_from_directory(config.OUT, name, as_attachment=request.args.get("view") is None)


@app.post("/delete/<name>")
@requires("reports.delete")
def delete(name):
    if NAME.match(name) and (config.OUT / name).exists():
        (config.OUT / name).unlink()
        twin = config.OUT / (name[:-4] + "-summary.pdf")
        if twin.exists():
            twin.unlink()
        R.forget(name)
        flash(f"Deleted {name}")
        R.notify(f"{me()} deleted {name}", by=me(), admins_only=True)
    return redirect(url_for("index"))


# ---------------------------------------------------------------- settings (admins)


def _hosts(text):
    hosts = [h.strip().rstrip("/") for h in re.split(r"[,\s]+", text or "") if h.strip()]
    for h in hosts:
        if not re.match(r"^(https://)?(\[[0-9a-fA-F:]+\]|[A-Za-z0-9.-]+)(:\d{1,5})?$", h):
            return None, f"{h!r} isn't a node address. Use a name or IP, optionally with :port"
    return hosts[:8], None


def _pem(text):
    t = (text or "").strip()
    if t and not (t.startswith("-----BEGIN CERTIFICATE-----") and t.rstrip().endswith("-----END CERTIFICATE-----")):
        return None, "The CA must be a PEM certificate (the contents of /etc/pve/pve-root-ca.pem)"
    return t, None


def _fp(text):
    t = (text or "").strip()
    fps = [f for f in re.split(r"[,\s]+", t) if f]
    for f in fps:
        if not re.match(r"^([0-9A-Fa-f]{2}:?){31}[0-9A-Fa-f]{2}$", f):
            return None, "A fingerprint is SHA-256: 32 pairs of hex digits separated by colons. Separate several with commas"
    return ", ".join(fps), None


def connection_from(form, saved, prefix):
    """The Proxmox or PBS block of the Settings form; a blank secret keeps the saved one. (block, problem)."""
    out = dict(saved)
    if prefix == "pve":
        hosts, why = _hosts(form.get("pve_hosts"))
        if why:
            return None, why
        out["hosts"] = hosts
    else:
        url = form.get("pbs_url", "").strip().rstrip("/")
        if url and not re.match(r"^https://[^\s/]+$", url):
            return None, "The backup server address must look like https://pbs.example:8007"
        out["url"] = url
        out["enabled"] = bool(form.get("pbs_enabled")) and bool(url)
    tid = form.get(f"{prefix}_token_id", "").strip()
    if tid and not re.match(r"^[^@\s!]+@[^@\s!]+![A-Za-z0-9._-]+$", tid):
        return None, "The token ID looks like user@realm!tokenname, for example reporter@pve!reports"
    out["token_id"] = tid
    if form.get(f"{prefix}_token_secret"):
        out["token_secret"] = form[f"{prefix}_token_secret"].strip()
    pem, why = _pem(form.get(f"{prefix}_ca_pem"))
    if why:
        return None, why
    fp, why = _fp(form.get(f"{prefix}_fingerprint"))
    if why:
        return None, why
    out["ca_pem"], out["fingerprint"] = pem, fp
    return out, None


@app.post("/settings/test/<product>")
@requires("settings")
def settings_test(product):
    """Test the Proxmox or PBS connection from the form without saving it."""
    if product not in ("pve", "pbs"):
        abort(404)
    s = R.load_settings()
    block, why = connection_from(request.form, s[product], product)
    if why:
        return jsonify(ok=False, message=why)
    if product == "pbs":
        block["enabled"] = True
    s[product] = block
    why = test_connection(s, product)
    return jsonify(ok=not why, message=("Connected" if not why else why[0].upper() + why[1:]))


_backfill_lock = threading.Lock()


def backfill_state():
    import json

    try:
        return json.loads(R.db.get_state("backfill") or "null")
    except ValueError:
        return None


def _run_backfill(days, by):
    import json

    from .pve import pve_client

    def save(**kw):
        st = backfill_state() or {}
        st.update(kw)
        R.db.set_state("backfill", json.dumps(st))

    try:
        save(status="running", days=days, step=0, steps=1, started=time.time(), by=by, problems=[])
        kept, problems = snapshot.backfill(pve_client(R.load_settings()), days, lambda a, b: save(step=a, steps=b))
        save(status="done", kept=kept, problems=problems[:5], finished=time.time())
        R.notify(f"Past logs read ({days} days): {kept} warning and error lines kept"
                 + (f". {problems[0]}" if problems else ""), by=by, admins_only=True)
    except Exception as e:  # noqa: BLE001
        log.exception("reading past logs failed")
        from .pve import describe

        save(status="failed", error=describe(e), finished=time.time())
        R.notify(f"Reading past logs failed: {describe(e)}", "error", by=by, admins_only=True)
    finally:
        _backfill_lock.release()


@app.post("/settings/backfill")
@requires("settings")
def settings_backfill():
    try:
        days = int(request.form.get("days", 30))
        if not 1 <= days <= 365:
            raise ValueError
    except ValueError:
        flash("Pick how many days of logs to read")
        return redirect(url_for("settings"))
    if not _backfill_lock.acquire(blocking=False):
        flash("Past logs are already being read")
        return redirect(url_for("settings"))
    threading.Thread(target=_run_backfill, args=(days, me()), daemon=True).start()
    flash(f"Reading the last {days} days of logs. The bell tells you when it's done")
    return redirect(url_for("settings"))


@app.get("/settings/backfill")
@requires("settings")
def settings_backfill_state():
    return jsonify(backfill_state() or {})


@app.route("/settings", methods=["GET", "POST"])
@requires("settings")
def settings():
    s = R.load_settings()
    if request.method == "POST":
        f = request.form
        try:
            port = int(f.get("port") or 587)
            hour = int(f.get("hour") or 7)
            warn = int(f.get("storage_warn") or 80)
            snap_days = int(f.get("snapshot_days") or 30)
            if not 0 <= hour <= 23 or not 50 <= warn <= 99 or not 1 <= snap_days <= 3650:
                raise ValueError
        except ValueError:
            flash("Port, send hour (0 to 23), storage warning (50 to 99%) and snapshot age must be numbers")
            return redirect(url_for("settings"))
        pve, why = connection_from(f, s["pve"], "pve")
        pbs, why2 = connection_from(f, s["pbs"], "pbs")
        if why or why2:
            flash(why or why2)
            return redirect(url_for("settings"))
        if pve["hosts"] and not pve["ca_pem"] and not pve["fingerprint"]:
            flash("Paste the cluster CA or a certificate fingerprint, so the reporter can check it's talking to your cluster")
            return redirect(url_for("settings"))
        logo, problem = logo_from(request.files.get("logo"))
        if problem:
            flash(problem)
            return redirect(url_for("settings"))
        tz = f.get("timezone", "").strip()
        if tz not in R.timezones():
            flash(f"Unknown time zone {tz!r}. Pick one from the list, e.g. Europe/London or UTC")
            return redirect(url_for("settings"))
        purl = f.get("pve_url", "").strip().rstrip("/")
        if purl and not re.match(r"^https://[^\s/]+$", purl):
            flash("The Proxmox web address must look like https://pve.example:8006")
            return redirect(url_for("settings"))
        template = f.get("template") if f.get("template") in config.TEMPLATES else config.DEFAULT_TEMPLATE
        accent = f.get("accent", "").strip()
        if accent and not config.HEX.match(accent):
            flash("The accent colour must look like #0e7c7b")
            return redirect(url_for("settings"))
        g_ = s["general"]
        g_.update(name=f.get("name", "").strip()[:60], timezone=tz, pve_url=purl, pve_links=bool(f.get("pve_links")),
                  template=template, storage_warn=warn, snapshot_days=snap_days)
        accents = dict(g_.get("accents") or {})
        if accent and accent.lower() != config.TEMPLATES[template]["accent"]:
            accents[template] = accent.lower()
        else:
            accents.pop(template, None)
        g_["accents"] = accents
        s["pve"], s["pbs"] = pve, pbs
        s["sections"] = {k: bool(f.get(f"sec_{k}")) for k in config.SECTION_KEYS}
        s["sections_summary"] = {k: bool(f.get(f"sum_{k}")) for k in config.SECTION_KEYS}
        s["smtp"].update(host=f.get("host", "").strip(), port=port, security=f.get("security", "starttls"),
                         verify=bool(f.get("smtp_verify")), user=f.get("user", "").strip(), **{"from": f.get("from", "").strip()})
        if f.get("password"):
            s["smtp"]["password"] = f["password"]
        if f.get("clear_password"):
            s["smtp"]["password"] = ""
        for k in ("weekly", "monthly", "quarterly"):
            s["recipients"][k] = ", ".join(R.split_addresses(f.get(f"to_{k}", "")))
            s["recipients_summary"][k] = ", ".join(R.split_addresses(f.get(f"to_s_{k}", "")))
        s["schedule"].update(enabled=bool(f.get("enabled")), hour=hour,
                             kinds=[k for k in ("weekly", "monthly", "quarterly") if f.get(f"auto_{k}")])
        R.save_settings(s)
        if logo or f.get("remove_logo"):
            save_logo(logo)
        users.audit(me(), "changed settings")
        flash("Settings saved")
        if f.get("action") == "test":
            to = f.get("test_to", "").strip()
            try:
                R.send_mail(to, f"{R.display_name()}: test email from the reports portal",
                            "This is a test from the reports portal. Email is working.")
                flash(f"Test email sent to {to}")
            except Exception as e:  # noqa: BLE001
                flash(f"Test email failed: {R.mail_error(e)}")
        return redirect(url_for("settings"))
    lk = config.look(s)
    return render_template(
        "settings.html", s=s, has_password=bool(s["smtp"].get("password")),
        has_pve_secret=bool(s["pve"].get("token_secret")), has_pbs_secret=bool(s["pbs"].get("token_secret")),
        sections=config.SECTIONS, timezones=R.timezones(), templates=config.TEMPLATES, template=lk["template"],
        accent=lk["accent"], accents=config.ACCENTS, all_accents={t: (s["general"].get("accents") or {}).get(t, x["accent"])
                                                                   for t, x in config.TEMPLATES.items()},
        logo_version=int(lp.stat().st_mtime) if (lp := config.logo_path()) else None,
        last_snapshot=R.db.get_state("last_snapshot"), snapshot_error=R.db.get_state("last_snapshot_error"),
        backfill=backfill_state(), logs_from=R.db.get_state("logs_from"),
        fmt=lambda t: dt.datetime.fromtimestamp(int(t), config.TZ).strftime("%-d %b %H:%M") if t else "never",
    )


def logo_from(upload):
    if not upload or not upload.filename:
        return None, None
    data = upload.read(config.LOGO_MAX_BYTES + 1)
    if len(data) > config.LOGO_MAX_BYTES:
        return None, "The logo is larger than 1 MB. Use a smaller PNG, JPEG or SVG"
    ext = config.logo_type(data)
    if not ext:
        return None, "The logo must be a PNG, JPEG or SVG file"
    return (data, ext), None


def save_logo(logo):
    for old in config.LOGO_TYPES:
        (config.OUT / f"logo.{old}").unlink(missing_ok=True)
    if logo:
        data, ext = logo
        tmp = config.OUT / f"logo.{ext}.part"
        tmp.write_bytes(data)
        tmp.replace(config.OUT / f"logo.{ext}")


@app.get("/settings/logo")
@requires("settings")
def logo_preview():
    p = config.logo_path()
    if not p:
        abort(404)
    r = send_file(p, mimetype=config.LOGO_TYPES[p.suffix[1:]], max_age=0)
    r.headers["Content-Security-Policy"] = "default-src 'none'; style-src 'unsafe-inline'; img-src data:"
    return r


# ---------------------------------------------------------------- main


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s", stream=sys.stdout)
    R.load_settings()
    users.setup_code()  # logs the code once when there are no accounts yet
    for target in (worker, scheduler, snapshot.loop):
        threading.Thread(target=target, daemon=True).start()
    from waitress import serve

    port = int(os.environ.get("PORTAL_PORT", 8088))
    log.info("portal on :%d", port)
    serve(app, host="0.0.0.0", port=port, threads=8)


if __name__ == "__main__":
    main()
