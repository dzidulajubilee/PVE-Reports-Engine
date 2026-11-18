"""One SQLite file (/out/reporter.db, mode 600) for users, the audit log, the 5-minute snapshots and the logs
collected as they happen. Standard library only; a connection per call keeps it safe across threads."""

import contextlib
import os
import sqlite3
import threading

from . import config

KEEP_DAYS = 460  # about 15 months of snapshots and logs

SCHEMA = """
create table if not exists users (
  id integer primary key,
  username text not null unique collate nocase,
  display text not null default '',
  email text not null default '',
  role text not null,
  pw_hash text not null,
  active integer not null default 1,
  must_change integer not null default 0,
  created real not null,
  last_login real,
  fails integer not null default 0,
  lock_until real not null default 0,
  session_ver integer not null default 1
);
create table if not exists audit (
  id integer primary key, t real not null, actor text not null, action text not null, target text not null default ''
);
create table if not exists snap_cluster (t integer primary key, quorate integer, nodes_online integer, nodes_total integer);
create table if not exists snap_node (
  t integer not null, node text not null, online integer, uptime integer, cpu real, mem real, maxmem real,
  primary key (t, node)
);
create table if not exists snap_guest (
  t integer not null, vmid integer not null, node text, type text, name text, status text, uptime integer,
  cpus real, maxmem real, primary key (t, vmid)
);
create table if not exists snap_storage (
  t integer not null, storage text not null, node text not null, shared integer, type text, used real, total real,
  primary key (t, storage, node)
);
create table if not exists logs (
  t integer not null, source text not null, node text not null default '', level text not null default '',
  unit text not null default '', message text not null,
  unique (t, source, node, message)
);
create index if not exists logs_t on logs (t);
create table if not exists unreachable (t integer primary key);  -- rounds the reporter couldn't reach Proxmox
create table if not exists state (key text primary key, value text);
"""

_init_lock = threading.Lock()
_ready = set()


def connect():
    path = config.OUT / "reporter.db"
    with _init_lock:
        if str(path) not in _ready:
            config.OUT.mkdir(parents=True, exist_ok=True)
            new = not path.exists()
            c = sqlite3.connect(path)
            if new:
                os.chmod(path, 0o600)
            c.executescript(SCHEMA)
            c.execute("pragma journal_mode=wal")
            c.commit()
            c.close()
            _ready.add(str(path))
    c = sqlite3.connect(path, timeout=30)
    c.row_factory = sqlite3.Row
    return c


@contextlib.contextmanager
def tx():
    c = connect()
    try:
        yield c
        c.commit()
    finally:
        c.close()


def query(sql, *args):
    with tx() as c:
        return [dict(r) for r in c.execute(sql, args).fetchall()]


def one(sql, *args):
    rows = query(sql, *args)
    return rows[0] if rows else None


def get_state(key, default=None):
    r = one("select value from state where key = ?", key)
    return r["value"] if r else default


def set_state(key, value):
    with tx() as c:
        c.execute("insert into state(key, value) values(?, ?) on conflict(key) do update set value = excluded.value",
                  (key, str(value)))


def prune(now):
    cut = int(now - KEEP_DAYS * 86400)
    with tx() as c:
        for t in ("snap_cluster", "snap_node", "snap_guest", "snap_storage", "logs", "unreachable"):
            c.execute(f"delete from {t} where t < ?", (cut,))  # noqa: S608 - fixed table names
        # AppArmor audit lines kept by earlier versions (see snapshot.NOISE)
        c.execute("delete from logs where unit = 'kernel' and message like 'audit: type=%apparmor=%'")
