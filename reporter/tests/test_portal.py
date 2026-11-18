"""Each route against each role: allowed, 403, or a redirect to sign in."""

import time

import pytest
from app import db, notify, users, web


@pytest.fixture(autouse=True)
def accounts():
    with db.tx() as c:
        c.execute("delete from users")
    ids = {r: users.create(r, r, "four unrelated words here")["id"] for r in users.ROLES}
    web.app.config.update(TESTING=True)
    yield ids


def as_role(role, ids):
    client = web.app.test_client()
    u = users.get(ids[role])
    with client.session_transaction() as s:
        s.update(uid=u["id"], ver=u["session_ver"], csrf="t", since=time.time(), seen=time.time())
    return client


ROUTES = [("/", "viewer"), ("/account", "viewer"), ("/notifications", "viewer"), ("/settings", "admin"),
          ("/users", "admin")]
RANK = {"viewer": 0, "operator": 1, "admin": 2}


@pytest.mark.parametrize("path,least", ROUTES)
@pytest.mark.parametrize("role", list(users.ROLES))
def test_route_permissions(path, least, role, accounts):
    r = as_role(role, accounts).get(path)
    assert r.status_code == (200 if RANK[role] >= RANK[least] else 403)


def test_sign_in_required(accounts):
    r = web.app.test_client().get("/")
    assert r.status_code == 302 and "/login" in r.headers["Location"]


def test_setup_page_only_without_accounts(accounts):
    assert web.app.test_client().get("/setup").status_code == 302
    with db.tx() as c:
        c.execute("delete from users")
    assert web.app.test_client().get("/").headers["Location"].endswith("/setup")


def test_viewers_cannot_make_reports_and_operators_cannot_email(accounts):
    assert as_role("viewer", accounts).post("/generate", data={"kind": "weekly", "csrf": "t"}).status_code == 403
    before = len(web.jobs)
    as_role("operator", accounts).post("/generate", data={"kind": "weekly", "csrf": "t", "email": "1", "to": "x@example.com"})
    job = web.jobs[max(web.jobs)]
    assert len(web.jobs) == before + 1 and job["sends"] == []


def test_posts_need_the_csrf_token(accounts):
    assert as_role("admin", accounts).post("/generate", data={"kind": "weekly"}).status_code == 400


def test_role_change_ends_the_session(accounts):
    client = as_role("operator", accounts)
    users.set_role(accounts["operator"], "viewer", accounts["admin"], "admin")
    assert client.get("/").status_code == 302


def test_idle_sessions_end(accounts):
    client = as_role("viewer", accounts)
    with client.session_transaction() as s:
        s["seen"] = time.time() - 31 * 60
    assert client.get("/").status_code == 302


def test_downloads_only_serve_report_files(accounts):
    assert as_role("viewer", accounts).get("/download/..%2fsettings.json").status_code == 404


def test_login_flow_and_safe_redirect(accounts):
    c = web.app.test_client()
    r = c.post("/login?next=//evil.example", data={"username": "viewer", "password": "four unrelated words here"})
    assert r.status_code == 302 and r.headers["Location"] == "/"


def test_admin_only_notes(accounts):
    notify.notify("mail failed", "error", admins_only=True, for_user="operator")
    v = as_role("viewer", accounts).get("/notifications").get_json()
    assert "mail failed" not in [n["text"] for n in v["items"]]
    o = as_role("operator", accounts).get("/notifications").get_json()
    assert "mail failed" in [n["text"] for n in o["items"]]
