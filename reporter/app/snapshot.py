"""Every 5 minutes: record node, guest and storage state and quorum, and read new log lines.

Proxmox keeps only coarse averages and no outage history, its cluster log has no time filter, and node journals
last only as long as journald keeps them. Recording as we go is what gives a report accurate outages, restarts
and the whole period's logs.
"""

import re
import time

from . import db
from .config import load_settings, log
from .pve import pve_client

INTERVAL = 300

# journal lines kept: warnings and errors from the services that matter for a cluster
JOURNAL_UNITS = re.compile(r"^(pve[\w-]*|corosync|ceph[\w-]*|zfs[\w-]*|zed|kernel|qmeventd|lxc[\w-]*|watchdog-mux|"
                           r"pmxcfs|spiceproxy|proxmox-[\w-]*|smartd)$")
BAD_WORDS = re.compile(r"\b(error|failed|failure|fail|fence|fencing|degraded|unable|timeout|timed out|crit|"
                       r"critical|panic|i/o error|segfault|out of memory|oom|link down|lost|retransmit)\b", re.I)
JOURNAL_LINE = re.compile(r"^(?P<ts>\w{3} +\d+ [\d:]+) (?P<host>\S+) (?P<unit>[\w.@-]+?)(?:\[\d+\])?: (?P<msg>.*)$")


def take(client, now=None):
    """One snapshot. Returns the number of rows written."""
    now = int(now or time.time())
    rows = 0
    status = client.get("/cluster/status") or []
    res = client.get("/cluster/resources") or []
    cluster = next((s for s in status if s.get("type") == "cluster"), None)
    nodes = [s for s in status if s.get("type") == "node"]
    with db.tx() as c:
        quorate = 1 if (cluster or {}).get("quorate", 1 if len(nodes) <= 1 else 0) else 0
        c.execute(
            "insert or replace into snap_cluster values (?, ?, ?, ?)",
            (now, quorate, sum(1 for n in nodes if n.get("online")), len(nodes)),
        )
        for r in res:
            t = r.get("type")
            if t == "node":
                c.execute(
                    "insert or replace into snap_node values (?, ?, ?, ?, ?, ?, ?)",
                    (now, r["node"], 1 if r.get("status") == "online" else 0, r.get("uptime"), r.get("cpu"),
                     r.get("mem"), r.get("maxmem")),
                )
            elif t in ("qemu", "lxc"):
                if r.get("template"):
                    continue
                c.execute(
                    "insert or replace into snap_guest values (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (now, r["vmid"], r.get("node"), t, r.get("name"), r.get("status"), r.get("uptime"),
                     r.get("maxcpu"), r.get("maxmem")),
                )
            elif t == "storage":
                c.execute(
                    "insert or replace into snap_storage values (?, ?, ?, ?, ?, ?, ?)",
                    (now, r["storage"], "" if r.get("shared") else r.get("node", ""), 1 if r.get("shared") else 0,
                     r.get("plugintype"), r.get("disk"), r.get("maxdisk")),
                )
            else:
                continue
            rows += 1
    rows += read_logs(client, [n["name"] for n in nodes if n.get("online")], now)
    return rows


def read_logs(client, nodes, now):
    """New cluster log entries and new journal lines since the saved cursor, deduplicated."""
    added = 0
    entries = client.get("/cluster/log", max=500)
    if entries:
        with db.tx() as c:
            for e in entries:
                pri = int(e.get("pri", 6))
                level = "error" if pri <= 3 else "warning" if pri == 4 else "info"
                cur = c.execute(
                    "insert or ignore into logs values (?, 'cluster', ?, ?, ?, ?)",
                    (int(e.get("time", now)), e.get("node", ""), level, e.get("tag", ""), str(e.get("msg", ""))[:500]),
                )
                added += cur.rowcount
    for node in nodes:
        key = f"journal_cursor:{node}"
        cursor = db.get_state(key)
        try:
            if cursor:
                lines = client.get(f"/nodes/{node}/journal", startcursor=cursor)
            else:
                lines = client.get(f"/nodes/{node}/journal", since=now - 2 * INTERVAL)
        except Exception as e:  # noqa: BLE001 - e.g. no Sys.Syslog: logs are optional
            log.info("journal of %s not read: %s", node, e)
            continue
        if not lines:
            continue
        # the API returns the start cursor first and the end cursor last
        end_cursor = lines[-1] if len(lines) > 1 else None
        with db.tx() as c:
            for line in lines[1:-1]:
                m = JOURNAL_LINE.match(line)
                if not m:
                    continue
                unit = m["unit"].split("@")[0]
                if not JOURNAL_UNITS.match(unit) or not BAD_WORDS.search(m["msg"]):
                    continue
                level = "error" if re.search(r"error|fail|panic|fenc|oom|out of memory|crit", m["msg"], re.I) else "warning"
                cur = c.execute(
                    "insert or ignore into logs values (?, 'journal', ?, ?, ?, ?)",
                    (_journal_time(m["ts"], now), node, level, unit, m["msg"][:500]),
                )
                added += cur.rowcount
        if end_cursor:
            db.set_state(key, end_cursor)
    return added


def _journal_time(ts, now):
    """'Nov 15 03:12:01' (no year) -> epoch, taking the year that puts it closest before now."""
    import datetime as dt

    from . import config

    ref = dt.datetime.fromtimestamp(now, config.TZ)
    for year in (ref.year, ref.year - 1):
        try:
            t = dt.datetime.strptime(f"{year} {ts}", "%Y %b %d %H:%M:%S").replace(tzinfo=config.TZ)
        except ValueError:
            return now
        if t.timestamp() <= now + 300:
            return int(t.timestamp())
    return now


def loop():
    """The snapshot thread: runs forever, one failed round never stops it."""
    last_prune = 0
    while True:
        started = time.time()
        try:
            s = load_settings()
            if s["pve"]["hosts"] and s["pve"]["token_id"]:
                n = take(pve_client(s))
                log.debug("snapshot: %d rows", n)
                db.set_state("last_snapshot", int(started))
            if started - last_prune > 86400:
                db.prune(started)
                last_prune = started
        except Exception as e:  # noqa: BLE001
            log.warning("snapshot failed: %s", e)
            db.set_state("last_snapshot_error", f"{int(started)} {e}")
        time.sleep(max(30, INTERVAL - (time.time() - started)))
