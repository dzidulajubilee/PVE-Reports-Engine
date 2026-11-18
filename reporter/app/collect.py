"""Figures for a report period, from the Proxmox API and the reporter's own snapshots, as plain dicts and lists.

Every figure is computed from data. When something can't be read it is None (shown as –) and the failure is
counted by the Source; when the snapshots don't cover the period the report says so instead of guessing.
"""

import datetime as dt
import math
import re
import time
from collections import defaultdict

from . import config, db

DAY = 86400
TIMEFRAMES = [("hour", 3600), ("day", DAY), ("week", 7 * DAY), ("month", 31 * DAY), ("year", 366 * DAY),
              ("decade", 3660 * DAY)]
GIB = 1024**3

# tasks that mean a person or Proxmox itself started, stopped or moved a guest on purpose
PLANNED = {"qmstart", "qmstop", "qmshutdown", "qmreboot", "qmreset", "qmigrate", "qmrollback", "qmrestore",
           "vzstart", "vzstop", "vzshutdown", "vzreboot", "vzmigrate", "vzrollback", "vzrestore", "vzdump",
           "hamigrate", "harelocate", "hastart", "hastop", "qmresume", "qmsuspend"}
CHANGES = {
    "qmigrate": "moved", "vzmigrate": "moved", "hamigrate": "moved", "harelocate": "moved",
    "qmcreate": "created", "vzcreate": "created", "qmclone": "cloned", "qmdestroy": "removed",
    "vzdestroy": "removed", "qmrestore": "restored", "vzrestore": "restored", "qmtemplate": "made a template",
}


# ---------------------------------------------------------------- small helpers


def ts(d):
    return int(d.timestamp())


def timeframe_for(start, now=None):
    span = (now or time.time()) - start
    for name, secs in TIMEFRAMES:
        if secs >= span * 1.02:
            return name
    return "decade"


def rrd(S, path, start, end, cf="AVERAGE", now=None, timeframe=None):
    """RRD points of path/rrddata inside [start, end), oldest first. None when the call failed."""
    data = S.get(path + "/rrddata", timeframe=timeframe or timeframe_for(start, now), cf=cf)
    if data is None:
        return None
    return sorted((p for p in data if start <= p.get("time", 0) < end), key=lambda p: p["time"])


def vals(points, key):
    return [p[key] for p in points or [] if isinstance(p.get(key), (int, float)) and not math.isnan(p[key])]


def mean(xs):
    return sum(xs) / len(xs) if xs else None


def pctl(xs, q):
    if not xs:
        return None
    xs = sorted(xs)
    i = min(len(xs) - 1, max(0, int(round(q * (len(xs) - 1)))))
    return xs[i]


def regression(points):
    """Least-squares slope per second and r² of [(t, y)]; (None, None) with fewer than 3 points."""
    if len(points) < 3:
        return None, None
    n = len(points)
    mx = sum(t for t, _ in points) / n
    my = sum(y for _, y in points) / n
    sxx = sum((t - mx) ** 2 for t, _ in points)
    syy = sum((y - my) ** 2 for _, y in points)
    if sxx == 0:
        return None, None
    sxy = sum((t - mx) * (y - my) for t, y in points)
    slope = sxy / sxx
    r2 = (sxy * sxy) / (sxx * syy) if syy else 1.0
    return slope, r2


def buckets(start, end):
    """The columns of a report's timeline and backup grid: days up to a month, weeks beyond."""
    days = max(1, round((end - start).total_seconds() / DAY))
    out = []
    if days <= 31:
        d = start
        while d < end:
            nxt = min(d + dt.timedelta(days=1), end)
            out.append({"start": ts(d), "end": ts(nxt), "label": f"{d:%a} {d.day}" if days <= 8 else f"{d.day}",
                        "short": f"{d:%a}"[0], "wide": days <= 8})
            d = nxt
    else:
        d = start
        while d < end:
            nxt = min(d + dt.timedelta(days=7), end)
            out.append({"start": ts(d), "end": ts(nxt), "label": f"W{d.isocalendar().week}",
                        "short": f"{d.isocalendar().week}", "wide": False})
            d = nxt
    return out


def bucket_of(t, bks):
    for i, b in enumerate(bks):
        if b["start"] <= t < b["end"]:
            return i
    return None


def kernel_key(release):
    """'6.14.11-1-pve' -> (6, 14, 11, 1) for comparing kernels."""
    return tuple(int(x) for x in re.findall(r"\d+", release or "")[:4])


def newest_kernel(versions):
    """The newest installed kernel release from /apt/versions, e.g. '6.14.11-1-pve', or None."""
    best = None
    for p in versions or []:
        m = re.match(r"^(?:proxmox|pve)-kernel-(\d+\.\d+\.\d+-\d+-pve)(?:-signed)?$", p.get("Package", ""))
        if m and p.get("CurrentState", "Installed") == "Installed":
            if best is None or kernel_key(m[1]) > kernel_key(best):
                best = m[1]
    return best


def running_kernel(versions, status):
    for p in versions or []:
        if p.get("RunningKernel"):
            return p["RunningKernel"]
    cur = (status or {}).get("current-kernel") or {}
    if cur.get("release"):
        return cur["release"]
    m = re.search(r"\d+\.\d+\.\d+-\d+-pve", (status or {}).get("kversion", ""))
    return m[0] if m else None


VZ_START = re.compile(r"Starting Backup of VM (\d+)")
VZ_DONE = re.compile(r"Finished Backup of VM (\d+) \((\d+):(\d+):(\d+)\)")
VZ_FAIL = re.compile(r"(?:ERROR: )?Backup of VM (\d+) failed - (.*)")
VZ_AT = re.compile(r"Backup started at (\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})")
VZ_SIZE = re.compile(r"(?:archive file size|transferred)[: ]+([\d.]+) ?([KMGT]i?B)", re.I)
UNITS = {"KB": 1e3, "MB": 1e6, "GB": 1e9, "TB": 1e12, "KIB": 1024, "MIB": 1024**2, "GIB": 1024**3, "TIB": 1024**4}


def parse_vzdump(lines, task_start, tz):
    """Per-guest results of one vzdump task log: [{vmid, ok, reason, t, seconds, size}]."""
    out, cur = {}, None
    for line in lines:
        if m := VZ_START.search(line):
            cur = int(m[1])
            out[cur] = {"vmid": cur, "ok": None, "reason": None, "t": task_start, "seconds": None, "size": None}
        elif m := VZ_AT.search(line):
            if cur in out:
                try:
                    out[cur]["t"] = int(dt.datetime.strptime(m[1], "%Y-%m-%d %H:%M:%S").replace(tzinfo=tz).timestamp())
                except ValueError:
                    pass
        elif m := VZ_FAIL.search(line):
            v = int(m[1])
            out.setdefault(v, {"vmid": v, "t": task_start, "seconds": None, "size": None})
            out[v].update(ok=False, reason=m[2].strip())
        elif m := VZ_DONE.search(line):
            v = int(m[1])
            out.setdefault(v, {"vmid": v, "t": task_start, "size": None, "reason": None})
            out[v].update(ok=True, seconds=int(m[2]) * 3600 + int(m[3]) * 60 + int(m[4]))
        elif (m := VZ_SIZE.search(line)) and cur in out:
            out[cur]["size"] = float(m[1]) * UNITS.get(m[2].upper(), 1)
    for r in out.values():
        if r["ok"] is None:  # started but neither finished nor failed: the task was cut off
            r.update(ok=False, reason=r.get("reason") or "the backup didn't finish")
    return list(out.values())


def plain_reason(reason):
    """Shorter, plainer wording for common vzdump failures."""
    r = reason or ""
    rules = [
        (r"no space left|not enough space|ENOSPC", "not enough space for the backup"),
        (r"timeout|timed out|got timeout", "the backup storage didn't answer in time"),
        (r"unable to (?:activate|access) storage|storage .* is not online|not available",
         "the backup storage wasn't reachable"),
        (r"connection refused|connection reset|broken pipe", "the connection to the backup storage dropped"),
        (r"lock|locked", "the guest was locked by another task"),
        (r"interrupted|aborted|received signal", "the run was interrupted"),
        (r"guest-agent|fs-freeze|qga", "the guest agent didn't answer the freeze request"),
    ]
    for pat, text in rules:
        if re.search(pat, r, re.I):
            return text
    return r[:140] or "unknown reason"


# ---------------------------------------------------------------- snapshots


def snapshot_coverage(start, end):
    """Share of [start, end) with a 5-minute snapshot, 0..1."""
    rows = db.query("select t from snap_cluster where t >= ? and t < ? order by t", start, end)
    if not rows:
        return 0.0
    covered, prev = 0, None
    for r in rows:
        if prev is not None:
            covered += min(r["t"] - prev, 600)  # a gap longer than two rounds means the reporter was down
        prev = r["t"]
    covered += 300
    return max(0.0, min(1.0, covered / max(1, end - start)))


def intervals(times_flags, step=300):
    """[(t, bad)] in time order -> [(from, to)] runs where bad is true."""
    out, run = [], None
    for t, bad in times_flags:
        if bad and run is None:
            run = [t, t + step]
        elif bad:
            run[1] = t + step
        elif run is not None:
            out.append(tuple(run))
            run = None
    if run:
        out.append(tuple(run))
    return out


def snap_cluster(start, end):
    rows = db.query("select t, quorate from snap_cluster where t >= ? and t < ? order by t", start, end)
    lost = intervals([(r["t"], not r["quorate"]) for r in rows])
    return {"quorum_lost": lost, "rows": len(rows)}


def snap_nodes(start, end):
    """{node: {'down': [(from, to)], 'reboots': [t]}} from the snapshots."""
    out = defaultdict(lambda: {"down": [], "reboots": []})
    rows = db.query("select t, node, online, uptime from snap_node where t >= ? and t < ? order by node, t", start, end)
    by = defaultdict(list)
    for r in rows:
        by[r["node"]].append(r)
    for node, rs in by.items():
        out[node]["down"] = intervals([(r["t"], not r["online"]) for r in rs])
        for a, b in zip(rs, rs[1:], strict=False):
            if a["uptime"] and b["uptime"] is not None and b["online"] and b["uptime"] < a["uptime"]:
                out[node]["reboots"].append(b["t"] - b["uptime"])
    return out


def snap_guests(start, end):
    """{vmid: {'restarts': [t], 'running_share', 'last_running', 'nodes': [..], 'alloc': [(t, cpus, maxmem)]}}."""
    rows = db.query(
        "select t, vmid, node, status, uptime, cpus, maxmem, name from snap_guest where t >= ? and t < ? "
        "order by vmid, t", start, end)
    by = defaultdict(list)
    for r in rows:
        by[r["vmid"]].append(r)
    out = {}
    for vmid, rs in by.items():
        restarts = []
        for a, b in zip(rs, rs[1:], strict=False):
            if b["status"] == "running" and b["uptime"] is not None:
                if a["status"] != "running" or (a["uptime"] and b["uptime"] < a["uptime"]):
                    restarts.append(b["t"] - b["uptime"])
        running = sum(1 for r in rs if r["status"] == "running")
        alloc = []
        for r in rs:
            if not alloc or (r["cpus"], r["maxmem"]) != alloc[-1][1:]:
                alloc.append((r["t"], r["cpus"], r["maxmem"]))
        nodes = []
        for r in rs:
            if not nodes or nodes[-1][1] != r["node"]:
                nodes.append((r["t"], r["node"]))
        out[vmid] = {"restarts": restarts, "running_share": running / len(rs), "rows": len(rs),
                     "running_at_end": rs[-1]["status"] == "running", "alloc": alloc, "nodes": nodes,
                     "name": rs[-1]["name"]}
    return out


def last_running(vmid, before):
    r = db.one("select max(t) as t from snap_guest where vmid = ? and status = 'running' and t < ?", vmid, before)
    return r["t"] if r and r["t"] else None


def running_at(t):
    """Guests running in the snapshot closest before t, or None without one within 15 minutes."""
    r = db.one("select max(t) as t from snap_cluster where t <= ? and t > ?", t, t - 900)
    if not r or not r["t"]:
        return None
    rows = db.query("select status from snap_guest where t = ?", r["t"])
    return {"running": sum(1 for x in rows if x["status"] == "running"), "total": len(rows)}


def recorded_intervals(start, end):
    """[(from, to)] stretches of [start, end) the reporter recorded, a gap over 10 minutes ending a stretch."""
    out = []
    for r in db.query("select t from snap_cluster where t >= ? and t < ? order by t", start, end):
        if out and r["t"] - out[-1][1] <= 600:
            out[-1][1] = min(r["t"] + 300, end)
        else:
            out.append([r["t"], min(r["t"] + 300, end)])
    return [tuple(x) for x in out]


QUORUM_LOST = re.compile(r"lost quorum|non-primary component|not quorate", re.I)
QUORUM_BACK = re.compile(r"has quorum|within the primary component|quorum regained|is quorate", re.I)
FENCED = re.compile(r"fenc", re.I)


def logged_events(start, end, recorded=()):
    """Quorum and fencing events exactly as the nodes logged them, outside the recorded stretches (where the
    recordings are the record). The same event logged by several nodes within a minute counts once."""
    rows = db.query("select t, node, message from logs where t >= ? and t < ? and (message like '%quorum%' or "
                    "message like '%primary component%' or message like '%fenc%') order by t", start, end)
    out = []
    for r in rows:
        if any(a <= r["t"] < b for a, b in recorded):
            continue
        msg = r["message"]
        kind = "fence" if FENCED.search(msg) else "lost" if QUORUM_LOST.search(msg) else \
            "back" if QUORUM_BACK.search(msg) else None
        if not kind:
            continue
        if out and out[-1]["kind"] == kind and r["t"] - out[-1]["t"] <= 60:
            if r["node"] and r["node"] not in out[-1]["nodes"]:
                out[-1]["nodes"].append(r["node"])
            continue
        out.append({"t": r["t"], "kind": kind, "nodes": [r["node"]] if r["node"] else [], "message": msg[:160]})
    return out


def unreachable_between(start, end):
    """[(from, to)] stretches when the reporter tried and no node answered."""
    rows = db.query("select t from unreachable where t >= ? and t < ? order by t", start, end)
    return intervals([(r["t"], True) for r in rows]) if rows else []


def logs_between(start, end, limit=20):
    """Warnings and errors from the collected logs, the same message on the same node counted once."""
    rows = db.query("select t, source, node, level, unit, message from logs where t >= ? and t < ? and level != 'info' "
                    "order by t", start, end)
    groups = {}
    for r in rows:
        norm = re.sub(r"\d+", "#", r["message"])[:120]
        k = (r["node"], r["unit"], norm)
        g = groups.setdefault(k, {**r, "count": 0, "first": r["t"]})
        g["count"] += 1
        g["t"] = r["t"]
        if r["level"] == "error":
            g["level"] = "error"
    out = sorted(groups.values(), key=lambda g: (g["level"] != "error", -g["count"], -g["t"]))
    return out[:limit], len(rows)


# ---------------------------------------------------------------- the collection


def collect(S, start, end, settings, full=True, now=None):
    """All figures for [start, end). full=False collects only what comparisons need (the period before)."""
    now = now or time.time()
    tz = config.TZ
    s_, e_ = ts(start), ts(end)
    d = {"start": s_, "end": e_, "days": max(1, round((e_ - s_) / DAY)), "buckets": buckets(start, end),
         "now": int(now)}  # node uptimes are counted back from this moment
    bks = d["buckets"]
    d["coverage"] = snapshot_coverage(s_, e_)

    res = S.get("/cluster/resources") or []
    status = S.get("/cluster/status") or []
    storage_cfg = {c["storage"]: c for c in (S.get("/storage") or []) if "storage" in c}
    nodes_res = sorted((r for r in res if r.get("type") == "node"), key=lambda r: r["node"])
    guests_res = [r for r in res if r.get("type") in ("qemu", "lxc") and not r.get("template")]
    stores_res = [r for r in res if r.get("type") == "storage"]
    online = [n["node"] for n in nodes_res if n.get("status") == "online"]
    cluster = next((x for x in status if x.get("type") == "cluster"), {})
    d["cluster_name"] = cluster.get("name")
    # what kind of setup this is decides wording, availability and which checks apply
    d["topology"] = {
        "cluster": bool(cluster),  # a standalone node has no cluster entry
        "nodes": len(nodes_res),
        "shared": any(r.get("shared") and "backup" != (storage_cfg.get(r["storage"], {}).get("content") or "backup")
                      for r in stores_res),
        "node_ips": {x.get("name"): x.get("ip") for x in status if x.get("type") == "node"},
    }
    if not d["topology"]["cluster"] and not d["cluster_name"]:
        d["cluster_name"] = nodes_res[0]["node"] if len(nodes_res) == 1 else None
    d["api_ok"] = bool(res)

    # ---- availability: quorum and nodes, from the snapshots
    sc = snap_cluster(s_, e_)
    sn = snap_nodes(s_, e_)
    lost = sum(b - a for a, b in sc["quorum_lost"])
    d["quorum_lost"] = sc["quorum_lost"]
    d["available_pct"] = (100 * (1 - lost / max(1, (e_ - s_) * d["coverage"]))) if d["coverage"] > 0 else None
    # facts for the time the reporter didn't record: only what the nodes logged, never an estimate
    d["recorded"] = recorded_intervals(s_, e_)
    lf = db.get_state("logs_from")
    d["logs_cover"] = bool(lf) and int(lf) <= s_
    d["logged"] = logged_events(s_, e_, d["recorded"])
    d["unreachable"] = unreachable_between(s_, e_)

    # ---- backups (tasks are kept by Proxmox, so this works for past periods too)
    tasks = []
    for n in online:
        t = S.get(f"/nodes/{n}/tasks", since=s_, until=e_, limit=10000, source="all")
        for x in t or []:
            x.setdefault("node", n)
            tasks.append(x)
    d["tasks_read"] = bool(online)
    backups = collect_backups(S, tasks, guests_res, stores_res, storage_cfg, bks, s_, e_, tz, full, online)
    d["backups"] = backups

    # ---- storage (RRD history works for past periods too)
    d["storage"] = collect_storage(S, stores_res, storage_cfg, start, end, now, settings)

    if not full:
        d["guests_running"] = running_at(e_ - 1)
        return d

    # ---- nodes
    nodes = []
    vm_by_node = defaultdict(list)
    for g in guests_res:
        vm_by_node[g.get("node")].append(g)
    for n in nodes_res:
        name = n["node"]
        st = S.get(f"/nodes/{name}/status") if name in online else None
        vers = S.get(f"/nodes/{name}/apt/versions") if name in online else None
        avg = rrd(S, f"/nodes/{name}", s_, e_, "AVERAGE", now)
        mx = rrd(S, f"/nodes/{name}", s_, e_, "MAX", now)
        cpu = [v * 100 for v in vals(avg, "cpu")]
        mem = [100 * p["memused"] / p["memtotal"] for p in avg or [] if p.get("memtotal") and p.get("memused") is not None]
        memx = [(100 * p["memused"] / p["memtotal"], p["time"]) for p in mx or []
                if p.get("memtotal") and p.get("memused") is not None]
        cpux = [v * 100 for v in vals(mx, "cpu")]
        running = [g for g in vm_by_node[name] if g.get("status") == "running"]
        kr = running_kernel(vers, st)
        kn = newest_kernel(vers)
        locals_ = [x for x in d["storage"] if x["node"] == name and x["pct"] is not None and not x["backup_only"]]
        nd = {
            "name": name,
            "online": name in online,
            "uptime": n.get("uptime") or (st or {}).get("uptime"),
            "cpu_avg": mean(cpu), "cpu_p95": pctl(cpu, 0.95), "cpu_max": max(cpux) if cpux else (max(cpu) if cpu else None),
            "mem_avg": mean(mem), "mem_p95": pctl(mem, 0.95),
            "mem_max": max(memx)[0] if memx else (max(mem) if mem else None),
            "mem_max_at": max(memx)[1] if memx else None,
            "cores": n.get("maxcpu") or ((st or {}).get("cpuinfo") or {}).get("cpus"),
            "ram_total": n.get("maxmem"),
            "vcpu": sum(g.get("maxcpu") or 0 for g in running),
            "ram_given": sum(g.get("maxmem") or 0 for g in running),
            "guests": len(vm_by_node[name]),
            "guests_running": len(running),
            "local_pct": max((x["pct"] for x in locals_), default=None),
            "pve": _pve_version((st or {}).get("pveversion")),
            "kernel": kr, "kernel_new": kn if kn and kr and kernel_key(kn) > kernel_key(kr) else None,
            "down": sn[name]["down"] if name in sn else [],
            "reboots": [t for t in (sn[name]["reboots"] if name in sn else []) if s_ <= t < e_],
            "heat": heat_cells(avg, start, end),
            "disks": [],
        }
        nd["reboot"] = nd["kernel_new"] is not None
        if nd["uptime"] and not nd["reboots"] and s_ <= now - nd["uptime"] < e_:
            nd["reboots"] = [int(now - nd["uptime"])]
        nd["down_s"] = sum(b - a for a, b in nd["down"])
        nodes.append(nd)
    d["nodes"] = nodes

    # ---- guests
    d["guests"] = collect_guests(S, guests_res, tasks, s_, e_, now, settings, bks)
    current = {"running": sum(1 for g in guests_res if g.get("status") == "running"), "total": len(guests_res)}
    d["guests_running"] = current if e_ >= now - 900 else running_at(e_ - 1)
    if d["guests_running"] is None:  # a period before the reporter recorded anything: say what's running now
        d["guests_running"] = {**current, "now": True}
    d["changes"] = changes(tasks, guests_res, snap_guests(s_, e_), s_, e_) if full else []
    d["snapshots"] = collect_snapshots(S, guests_res, now, settings["general"].get("snapshot_days", 30))

    # ---- health
    d["health"] = collect_health(S, nodes, online, stores_res, storage_cfg, sc, d, s_, e_, now)
    d["logs"], d["log_lines"] = logs_between(s_, e_)
    d["logs_collected"] = db.one("select count(*) as n from logs")["n"] > 0
    lf = db.get_state("logs_from")
    d["logs_from"] = int(lf) if lf and int(lf) > s_ else None  # logs start inside the period
    d["failed_tasks"] = [
        {"t": x.get("starttime"), "node": x.get("node"), "type": x.get("type"), "id": x.get("id"),
         "user": x.get("user"), "status": x.get("status")}
        for x in tasks
        if x.get("status") not in (None, "OK") and x.get("type") != "vzdump" and not str(x.get("status")).startswith("WARNINGS")
    ][:30]

    # ---- Proxmox Backup Server
    d["pbs"] = collect_pbs(S, s_, e_) if S.pbs else None
    return d


def _pve_version(v):
    m = re.search(r"pve-manager/([\d.]+)", v or "")
    return m[1] if m else None


def heat_cells(points, start, end):
    """CPU % per cell for the Pulse heatmap: 2-hour cells for a week, 8 hours up to a month, a day beyond."""
    s_, e_ = ts(start), ts(end)
    days = (e_ - s_) / DAY
    step = 7200 if days <= 8 else 28800 if days <= 31 else DAY
    n = max(1, math.ceil((e_ - s_) / step))
    cells = [[] for _ in range(n)]
    mcells = [[] for _ in range(n)]
    for p in points or []:
        i = int((p["time"] - s_) // step)
        if 0 <= i < n:
            if isinstance(p.get("cpu"), (int, float)):
                cells[i].append(p["cpu"] * 100)
            if p.get("memtotal") and p.get("memused") is not None:
                mcells[i].append(100 * p["memused"] / p["memtotal"])
    return {"step": step, "cpu": [mean(c) for c in cells], "mem": [mean(c) for c in mcells]}


def collect_storage(S, stores_res, storage_cfg, start, end, now, settings):
    """One row per shared storage and per local storage on each node, with growth and when it fills up."""
    s_, e_ = ts(start), ts(end)
    seen, out = set(), []
    hist_start = min(s_, e_ - 84 * DAY)  # 12 weeks for the chart and the trend
    for r in sorted(stores_res, key=lambda r: (not r.get("shared"), r["storage"], r.get("node", ""))):
        key = (r["storage"], "" if r.get("shared") else r.get("node"))
        if key in seen or not r.get("maxdisk"):
            continue
        seen.add(key)
        cfg = storage_cfg.get(r["storage"], {})
        content = set((cfg.get("content") or r.get("content") or "").split(","))
        node = r.get("node")
        pts = rrd(S, f"/nodes/{node}/storage/{r['storage']}", hist_start, e_, "AVERAGE", now, timeframe="year")
        hist = [(p["time"], 100 * p["used"] / p["total"]) for p in pts or [] if p.get("total") and p.get("used") is not None]
        in_period = [h for h in hist if h[0] >= s_]
        cur_pct = 100 * r["disk"] / r["maxdisk"] if r.get("disk") is not None else None
        at_end = in_period[-1][1] if in_period else None
        if e_ >= now - 3600 and cur_pct is not None:
            at_end = cur_pct
        at_start = in_period[0][1] if in_period else None
        recent = [h for h in hist if h[0] >= e_ - 28 * DAY]
        slope, r2 = regression(recent)
        per_day = slope * DAY if slope is not None else None
        pct = at_end
        warn = settings["general"].get("storage_warn", 80)
        steady_trend = per_day is not None and r2 is not None and r2 >= 0.5
        eta_warn = eta_full = None
        if per_day and per_day > 0.01 and steady_trend and pct is not None:
            eta_warn = (warn - pct) / per_day if pct < warn else 0
            eta_full = (100 - pct) / per_day
        out.append({
            "name": r["storage"],
            "node": "" if r.get("shared") else node,
            "shared": bool(r.get("shared")),
            "type": cfg.get("type") or r.get("plugintype") or "",
            "used": r.get("disk"), "total": r.get("maxdisk"),
            "pct": pct,
            "change_pts": (at_end - at_start) if at_end is not None and at_start is not None else None,
            "per_week": per_day * 7 if per_day is not None else None,
            "steady": per_day is None or abs(per_day * 7) < 0.3,
            "even": steady_trend,
            "eta_warn": eta_warn, "eta_full": eta_full,
            "history": [h for h in hist if h[0] >= e_ - 84 * DAY],
            "backup_only": content <= {"backup", "iso", "vztmpl", "snippets", "import", ""} and "backup" in content,
            "backup": "backup" in content,
            "active": r.get("status", "available") == "available",
        })
    return out


def collect_backups(S, tasks, guests_res, stores_res, storage_cfg, bks, s_, e_, tz, full, online_nodes=()):
    names = {g["vmid"]: g.get("name") or str(g["vmid"]) for g in guests_res}
    runs = []
    for t in tasks:
        if t.get("type") != "vzdump" or t.get("starttime") is None:
            continue
        log_lines = S.get(f"/nodes/{t['node']}/tasks/{t['upid']}/log", limit=50000)
        parsed = parse_vzdump([x.get("t", "") for x in log_lines or []], t["starttime"], tz)
        if not parsed and t.get("id", "").isdigit():
            parsed = [{"vmid": int(t["id"]), "ok": t.get("status") == "OK", "t": t["starttime"],
                       "reason": None if t.get("status") == "OK" else t.get("status"), "seconds": None, "size": None}]
        for p in parsed:
            p["node"] = t["node"]
            p["task_seconds"] = (t.get("endtime") or t["starttime"]) - t["starttime"]
            runs.append(p)
    ok = [r for r in runs if r["ok"]]
    failed = [r for r in runs if not r["ok"]]
    out = {"runs_total": len(runs), "runs_ok": len(ok), "runs_failed": len(failed)}
    if not full:
        return out

    # latest backup per guest, from every storage that holds backups
    latest = {}
    seen = set()
    for r in stores_res:
        cfg = storage_cfg.get(r["storage"], {})
        if "backup" not in (cfg.get("content") or r.get("content") or ""):
            continue
        key = r["storage"] if r.get("shared") else (r["storage"], r.get("node"))
        if key in seen or r.get("status", "available") != "available":
            continue
        seen.add(key)
        for c in S.get(f"/nodes/{r['node']}/storage/{r['storage']}/content", content="backup") or []:
            v = c.get("vmid")
            if v is None:
                continue
            if v not in latest or c.get("ctime", 0) > latest[v]["ctime"]:
                latest[v] = {"ctime": c.get("ctime", 0), "size": c.get("size"), "storage": r["storage"],
                             "verify": (c.get("verification") or {}).get("state")}
    jobs = S.get("/cluster/backup") or []
    not_backed = S.get("/cluster/backup-info/not-backed-up")
    not_in_job = {x["vmid"] for x in not_backed or [] if "vmid" in x}

    # Proxmox lists backup files only to callers allowed to make backups, so a read-only token usually sees none.
    # The task logs (and the backup server, when it's set up) say the same thing without write rights.
    def seen_ok(v, t, size=None, verify=None):
        if v not in latest or t > latest[v]["ctime"]:
            latest[v] = {"ctime": t, "size": size if size is not None else (latest.get(v) or {}).get("size"),
                         "storage": None, "verify": verify}

    for r in ok:
        seen_ok(r["vmid"], r["t"], r["size"])
    if S.pbs:
        for grp in pbs_groups(S):
            bid = str(grp.get("backup-id", ""))
            if grp.get("backup-type") in ("vm", "ct") and bid.isdigit() and grp.get("last-backup"):
                seen_ok(int(bid), int(grp["last-backup"]))
    wanted = {g["vmid"] for g in guests_res} - set(latest)
    if wanted and online_nodes:
        # look back a month before the period for guests not backed up inside it, newest runs first
        earlier = []
        for n in online_nodes:
            for t in S.get(f"/nodes/{n}/tasks", since=s_ - 31 * DAY, until=s_, typefilter="vzdump", limit=500) or []:
                t.setdefault("node", n)
                earlier.append(t)
        for t in sorted(earlier, key=lambda t: -(t.get("starttime") or 0)):
            if not wanted:
                break
            if t.get("status") not in ("OK", None) and not str(t.get("status", "")).startswith(("WARNINGS", "job errors")):
                continue
            if str(t.get("id", "")).isdigit():
                found = [{"vmid": int(t["id"]), "ok": t.get("status") == "OK", "t": t["starttime"], "size": None}]
            else:
                lines = S.get(f"/nodes/{t['node']}/tasks/{t['upid']}/log", limit=50000)
                found = parse_vzdump([x.get("t", "") for x in lines or []], t["starttime"], tz)
            for f in found:
                if f["ok"] and f["vmid"] in wanted:
                    seen_ok(f["vmid"], f["t"], f.get("size"))
                    wanted.discard(f["vmid"])
    looked_back = 31 if online_nodes else 0

    per_guest = defaultdict(lambda: [""] * len(bks))
    for r in sorted(runs, key=lambda r: r["t"]):
        i = bucket_of(r["t"], bks)
        if i is None:
            continue
        cell = per_guest[r["vmid"]]
        cell[i] = "ok" if r["ok"] and cell[i] != "fail" else "fail"
    rows = []
    for g in sorted(guests_res, key=lambda g: g["vmid"]):
        v = g["vmid"]
        last = latest.get(v)
        days = per_guest.get(v, [""] * len(bks))
        if v in not_in_job:
            days = ["none" if not x else x for x in days]
        rows.append({"vmid": v, "name": names[v], "days": days, "latest": last["ctime"] if last else None,
                     "looked_back": looked_back,
                     "size": last["size"] if last else None, "in_job": v not in not_in_job,
                     "verify": last["verify"] if last else None, "running": g.get("status") == "running"})
    # guests in a job without a good backup in the period
    ok_ids = {r["vmid"] for r in ok}
    stale = [r for r in rows if r["in_job"] and r["vmid"] not in ok_ids and r["running"] and runs]
    nights = []
    for b in bks:
        rs = [r for r in runs if b["start"] <= r["t"] < b["end"]]
        nights.append({"label": b["label"], "start": b["start"], "jobs": len(rs), "ok": sum(1 for r in rs if r["ok"]),
                       "failed": sum(1 for r in rs if not r["ok"]),
                       "size": sum(r["size"] or 0 for r in rs) or None,
                       "seconds": sum(r["seconds"] or 0 for r in rs) or None})
    failures = [{"vmid": r["vmid"], "name": names.get(r["vmid"], str(r["vmid"])), "t": r["t"], "node": r["node"],
                 "reason": plain_reason(r["reason"]), "raw": (r["reason"] or "")[:200]} for r in failed]
    out.update(
        rows=rows,
        failures=failures,
        nights=nights,
        not_in_job=[r for r in rows if not r["in_job"]],
        stale=stale,
        jobs=len([j for j in jobs if j.get("enabled", 1)]),
        jobs_read=jobs is not None and not_backed is not None,
        oldest_latest=min((r for r in rows if r["latest"] and r["in_job"]), key=lambda r: r["latest"], default=None),
        verify_failed=[r for r in rows if r["verify"] == "failed"],
    )
    return out


def collect_guests(S, guests_res, tasks, s_, e_, now, settings, bks):
    sg = snap_guests(s_, e_)
    out = []
    for g in guests_res:
        v, node, kind = g["vmid"], g.get("node"), g["type"]
        avg = mx = None
        if g.get("status") == "running" or sg.get(v, {}).get("running_share", 0) > 0 or e_ < now - 3600:
            avg = rrd(S, f"/nodes/{node}/{kind}/{v}", s_, e_, "AVERAGE", now)
            mx = rrd(S, f"/nodes/{node}/{kind}/{v}", s_, e_, "MAX", now)
        cpu = vals(avg, "cpu")
        mem = vals(avg, "mem")
        memx = vals(mx, "mem")
        cpux = vals(mx, "cpu")
        written = 0
        pts = avg or []
        for a, b in zip(pts, pts[1:], strict=False):
            if isinstance(a.get("diskwrite"), (int, float)):
                written += a["diskwrite"] * (b["time"] - a["time"])
        info = sg.get(v)
        planned = [t["starttime"] for t in tasks if t.get("id") == str(v) and t.get("type") in PLANNED]
        restarts = []
        for t in (info or {}).get("restarts", []):
            if s_ <= t < e_ and not any(abs(t - p) < 900 for p in planned):
                restarts.append(t)
        stopped_since = None
        if g.get("status") != "running":
            lr = last_running(v, e_)
            stopped_since = lr
        out.append({
            "vmid": v, "name": g.get("name") or str(v), "node": node, "type": kind, "status": g.get("status"),
            "cpus": g.get("maxcpu"), "maxmem": g.get("maxmem"), "maxdisk": g.get("maxdisk"),
            "cpu_avg": mean(cpu) * 100 if cpu else None,
            "cpu_peak_vcpu": (max(cpux) * (g.get("maxcpu") or 0)) if cpux else None,
            "mem_avg": mean(mem), "mem_max": max(memx) if memx else (max(mem) if mem else None),
            "disk_written": written if pts else None,
            "restarts": restarts,
            "ran_all": bool(info) and info["running_share"] >= 0.999 and not restarts,
            "stopped_since": stopped_since,
            "tags": [t for t in (g.get("tags") or "").split(";") if t],
            "pool": g.get("pool"),
        })
    running = [g for g in out if g["status"] == "running"]
    oversized = []
    for g in running:
        if not g["maxmem"] or g["mem_max"] is None:
            continue
        mem_low = g["maxmem"] >= 4 * GIB and g["mem_max"] < 0.25 * g["maxmem"]
        cpu_low = (g["cpus"] or 0) >= 4 and g["cpu_peak_vcpu"] is not None and g["cpu_peak_vcpu"] < 0.25 * g["cpus"]
        if mem_low or cpu_low:
            free_mem = max(0, g["maxmem"] - max(2 * g["mem_max"], 1 * GIB)) if mem_low else 0
            free_cpu = max(0, (g["cpus"] or 0) - max(1, math.ceil(2 * (g["cpu_peak_vcpu"] or 0)))) if cpu_low else 0
            oversized.append({**g, "free_mem": free_mem, "free_cpu": free_cpu})
    oversized.sort(key=lambda g: -(g["free_mem"] / GIB + g["free_cpu"]))
    stopped = sorted((g for g in out if g["status"] != "running"), key=lambda g: g["stopped_since"] or 0)
    has_snaps = any(v.get("rows") for v in sg.values())
    return {
        "list": out,
        "total": len(out),
        "vms": sum(1 for g in out if g["type"] == "qemu"),
        "cts": sum(1 for g in out if g["type"] == "lxc"),
        "running": len(running),
        "ran_all": sum(1 for g in out if g["ran_all"]) if has_snaps else None,
        "top": sorted((g for g in running if g["cpu_avg"] is not None), key=lambda g: -g["cpu_avg"]),
        "oversized": oversized,
        "restarted": [g for g in out if g["restarts"]],
        "stopped": stopped,
        "stopped_disk": sum(g["maxdisk"] or 0 for g in stopped),
        "restarts_known": has_snaps,
    }


def changes(tasks, guests_res, sg, s_, e_):
    names = {g["vmid"]: g.get("name") or str(g["vmid"]) for g in guests_res}
    kinds = {g["vmid"]: g["type"] for g in guests_res}
    out = []
    for t in tasks:
        what = CHANGES.get(t.get("type"))
        if not what or t.get("status") not in ("OK", None) and not str(t.get("status", "")).startswith("WARNINGS"):
            continue
        v = int(t["id"]) if str(t.get("id", "")).isdigit() else None
        name = names.get(v, str(v) if v else t.get("id", ""))
        noun = "container" if t["type"].startswith("vz") or kinds.get(v) == "lxc" else "VM"
        if what == "moved":
            dest = None
            for when, node in (sg.get(v) or {}).get("nodes", []):
                if when > t["starttime"] and node != t["node"]:
                    dest = node
                    break
            dest = dest or next((g.get("node") for g in guests_res if g["vmid"] == v and g.get("node") != t["node"]), None)
            text = f"{name} moved from {t['node']}" + (f" to {dest}" if dest else "")
        elif what == "created":
            text = f"New {noun} {v} {name} on {t['node']}" if v else f"New {noun} on {t['node']}"
        elif what == "removed":
            text = f"{noun.capitalize()} {v} removed"
        else:
            text = f"{name} {what}"
        out.append({"t": t["starttime"], "text": text, "user": t.get("user")})
    # vCPU and memory changes seen in the snapshots
    for v, info in sg.items():
        alloc = info.get("alloc") or []
        for (_t0, c0, m0), (t1, c1, m1) in zip(alloc, alloc[1:], strict=False):
            if not (s_ <= t1 < e_):
                continue
            name = names.get(v, info.get("name") or str(v))
            if c0 and c1 and c1 != c0:
                diff = c1 - c0
                out.append({"t": t1, "text": f"{name} given {abs(int(diff))} {'more' if diff > 0 else 'fewer'} vCPU",
                            "user": None})
            if m0 and m1 and abs(m1 - m0) >= 0.5 * GIB:
                diff = (m1 - m0) / GIB
                out.append({"t": t1, "text": f"{name} given {abs(diff):.0f} GB {'more' if diff > 0 else 'less'} memory",
                            "user": None})
    return sorted(out, key=lambda c: c["t"])


def collect_snapshots(S, guests_res, now, days):
    out = []
    for g in guests_res:
        snaps = S.get(f"/nodes/{g['node']}/{g['type']}/{g['vmid']}/snapshot")
        for sn in snaps or []:
            if sn.get("name") == "current" or not sn.get("snaptime"):
                continue
            age = (now - sn["snaptime"]) / DAY
            if age >= days:
                out.append({"vmid": g["vmid"], "name": g.get("name") or str(g["vmid"]), "snap": sn["name"],
                            "taken": sn["snaptime"], "age": int(age)})
    return sorted(out, key=lambda s: -s["age"])


def collect_health(S, nodes, online, stores_res, storage_cfg, sc, d, s_, e_, now):
    """[{key, what, state, tone}] for the cluster health list; tone ok, warn or bad."""
    h = []
    cov = d["coverage"]
    lost = sc["quorum_lost"]
    single = not d["topology"]["cluster"]
    if single:
        pass  # a standalone node has no quorum to keep
    elif cov >= 0.5:
        if lost:
            mins = sum(b - a for a, b in lost) / 60
            h.append(("quorum", "Quorum", f"lost {len(lost)}× for {fmt_minutes(mins)}", "bad"))
        else:
            h.append(("quorum", "Quorum", "kept all period" if cov >= 0.95 else f"kept while watched ({cov:.0%})", "ok"))
    else:
        status = S.get("/cluster/status") or []
        c = next((x for x in status if x.get("type") == "cluster"), None)
        lost_logged = [e for e in d["logged"] if e["kind"] == "lost"]
        if lost_logged:
            h.append(("quorum", "Quorum", f"loss logged {len(lost_logged)}× (not recorded)", "bad"))
        elif c is not None or len(nodes) == 1:
            q = (c or {}).get("quorate", 1)
            if not q:
                h.append(("quorum", "Quorum", "not quorate now", "bad"))
            elif d["logs_cover"]:
                h.append(("quorum", "Quorum", "no loss logged; quorate now", "ok"))
            else:
                h.append(("quorum", "Quorum", "quorate now; earlier not recorded", "ok"))
    down = [n for n in nodes if n["down"]]
    what = "Server" if single else "Nodes online"
    if down:
        parts = [f"{n['name']} down {fmt_minutes(n['down_s'] / 60)}" for n in down]
        h.append(("nodes", what, ", ".join(parts), "bad"))
    else:
        off = [n["name"] for n in nodes if not n["online"]]
        if off:
            h.append(("nodes", what, f"{', '.join(off)} offline now", "bad"))
        elif single:
            h.append(("nodes", what, "up all period" if cov >= 0.95 else "up now", "ok"))
        else:
            h.append(("nodes", what, f"{len(nodes)} of {len(nodes)}" + (" all period" if cov >= 0.95 else ""),
                      "ok"))
    if d.get("unreachable"):
        mins = sum(b - a for a, b in d["unreachable"]) / 60
        h.append(("reach", "Reachable from the reporter", f"no answer for {fmt_minutes(mins)}", "warn"))

    ha = S.get("/cluster/ha/status/current")
    if ha:
        svcs = [x for x in ha if x.get("type") == "service"]
        bad = [x for x in svcs if x.get("state") in ("error", "fence", "recovery")]
        fenced = [x for x in ha if x.get("type") == "fence" or "fence" in str(x.get("status", ""))]
        fence_logs = db.query("select count(*) as n from logs where t >= ? and t < ? and message like '%fenc%'", s_, e_)
        nf = fence_logs[0]["n"] if fence_logs else 0
        if svcs or fenced:
            tone = "bad" if bad or fenced or nf else "ok"
            state = f"{len(svcs)} guests" + (f", {len(bad)} in error" if bad else "") + (
                f", fencing seen {nf}×" if nf else ", no fencing")
            h.append(("ha", "High availability", state, tone))

    if any(storage_cfg.get(s["storage"], {}).get("type") in ("rbd", "cephfs") and
           not storage_cfg.get(s["storage"], {}).get("monhost") for s in stores_res):
        ceph = S.get("/cluster/ceph/status")
        if ceph:
            health = (ceph.get("health") or {}).get("status", "")
            checks = list(((ceph.get("health") or {}).get("checks") or {}).keys())
            osd = ceph.get("osdmap") or {}
            osd = osd.get("osdmap", osd)
            flags = [c for c in checks if c == "OSDMAP_FLAGS"]
            up, total = osd.get("num_up_osds"), osd.get("num_osds")
            state = "healthy" if health == "HEALTH_OK" else health.replace("HEALTH_", "").lower()
            if total:
                state += f", {up} of {total} OSDs up"
            if flags:
                state += ", flags set"
            elif health == "HEALTH_OK":
                state += ", no flags set"
            tone = "ok" if health == "HEALTH_OK" else "bad" if health == "HEALTH_ERR" else "warn"
            h.append(("ceph", "Ceph", state, tone))
            d["ceph"] = {"health": health, "osds": total, "osds_up": up, "checks": checks}

    pools, bad_pools, scrub_old = 0, [], []
    disks, failing, worn = 0, [], []
    for n in nodes:
        if not n["online"]:
            continue
        for p in S.get(f"/nodes/{n['name']}/disks/zfs") or []:
            pools += 1
            if p.get("health") not in ("ONLINE", None):
                bad_pools.append(f"{p['name']} on {n['name']} {str(p.get('health')).lower()}")
            det = S.get(f"/nodes/{n['name']}/disks/zfs/{p['name']}") or {}
            m = re.search(r"on (\w{3} \w{3} +\d+ [\d:]+ \d{4})", det.get("scan") or "")
            if m:
                try:
                    when = dt.datetime.strptime(re.sub(" +", " ", m[1]), "%a %b %d %H:%M:%S %Y").timestamp()
                    if now - when > 45 * DAY:
                        scrub_old.append(p["name"])
                except ValueError:
                    pass
        for disk in S.get(f"/nodes/{n['name']}/disks/list") or []:
            disks += 1
            hl = str(disk.get("health", "")).upper()
            wear = disk.get("wearout")
            wear = None if wear in (None, "N/A", "") else _num(wear)
            n["disks"].append({"dev": disk.get("devpath"), "type": disk.get("type"), "health": hl, "wear": wear,
                               "model": disk.get("model")})
            if hl and hl not in ("PASSED", "OK", "UNKNOWN"):
                failing.append(f"{disk.get('devpath')} on {n['name']}")
            elif wear is not None and wear >= 80:
                worn.append((n["name"], wear))
    if pools:
        if bad_pools:
            h.append(("zfs", "ZFS pools", ", ".join(bad_pools), "bad"))
        else:
            h.append(("zfs", "ZFS pools", "healthy" + (f", {len(scrub_old)} not scrubbed in 45 days" if scrub_old else ""),
                      "warn" if scrub_old else "ok"))
    if disks:
        if failing:
            h.append(("disks", "Disk health", f"SMART failing: {', '.join(failing)}", "bad"))
        elif worn:
            worst = max(w for _, w in worn)
            h.append(("disks", "Disk health",
                      f"{len(worn)} SSD{'s' * (len(worn) != 1)} at {worst:.0f}% wear" if len(worn) > 1 else
                      f"1 SSD in {worn[0][0]} at {worst:.0f}% wear", "warn"))
        else:
            h.append(("disks", "Disk health", f"{disks} disks pass", "ok"))

    stopped = []
    for n in nodes:
        if not n["online"]:
            continue
        for svc in S.get(f"/nodes/{n['name']}/services") or []:
            name = svc.get("service") or svc.get("name")
            if name in ("corosync", "pve-cluster", "pvestatd", "pvedaemon", "pveproxy", "pve-firewall", "pvescheduler",
                        "pve-ha-lrm", "pve-ha-crm", "chrony", "systemd-timesyncd") and \
                    svc.get("unit-state", "enabled") == "enabled" and svc.get("state") not in ("running", None):
                stopped.append(f"{name} on {n['name']}")
    if any(n["online"] for n in nodes):
        h.append(("services", "Cluster services", "all running" if not stopped else "stopped: " + ", ".join(stopped[:3]),
                  "ok" if not stopped else "bad"))

    jobs, failing_rep = 0, []
    for n in nodes:
        if not n["online"]:
            continue
        for r in S.get(f"/nodes/{n['name']}/replication") or []:
            jobs += 1
            if r.get("fail_count") or r.get("error"):
                failing_rep.append(r.get("id"))
    if jobs:
        h.append(("replication", "Replication", f"{jobs} jobs in sync" if not failing_rep else
                  f"{len(failing_rep)} of {jobs} jobs failing", "ok" if not failing_rep else "bad"))

    reboot = [n["name"] for n in nodes if n["reboot"]]
    if any(n["kernel"] for n in nodes):
        h.append(("kernel", "Kernel", f"reboot due on {_join(reboot)}" if reboot else "running the installed kernel",
                  "warn" if reboot else "ok"))

    skews = []
    for n in nodes:
        if not n["online"]:
            continue
        t0 = time.time()
        tm = S.get(f"/nodes/{n['name']}/time")
        t1 = time.time()
        if tm and tm.get("time"):
            skew = abs(tm["time"] - (t0 + t1) / 2) - (t1 - t0) / 2
            if skew > 5:
                skews.append((n["name"], skew))
    if any(n["online"] for n in nodes) and e_ >= now - DAY:
        h.append(("clock", "Clock", "all nodes in sync" if not skews else
                  ", ".join(f"{a} off by {b:.0f} s" for a, b in skews), "ok" if not skews else "warn"))

    subs = []
    for n in nodes:
        if n["online"]:
            sub = S.get(f"/nodes/{n['name']}/subscription")
            if sub:
                subs.append(sub.get("status", ""))
    if subs:
        expired = [s for s in subs if s in ("expired", "invalid")]
        active = [s for s in subs if s in ("active", "Active")]
        state = "active on all nodes" if len(active) == len(subs) else (
            f"{len(expired)} expired" if expired else "not used" if not active else f"active on {len(active)} of {len(subs)}")
        h.append(("subscription", "Subscription", state, "warn" if expired else "ok"))
    return [{"key": k, "what": w, "state": s, "tone": t} for k, w, s, t in h]


def pbs_groups(S):
    """Every backup group on the backup server, in every datastore and namespace (Proxmox VE often writes into a
    namespace such as Production, which a root-only listing misses)."""
    out = []
    for ds in S.pbs_get("/admin/datastore") or []:
        store = ds.get("store")
        spaces = [n.get("ns", "") for n in S.pbs_get(f"/admin/datastore/{store}/namespace") or []] or [""]
        for ns in spaces:
            out += S.pbs_get(f"/admin/datastore/{store}/groups", ns=ns or None) or []
    return out


def collect_pbs(S, s_, e_):
    usage = S.pbs_get("/status/datastore-usage")
    stores = []
    for u in usage or []:
        if not u.get("total"):
            continue
        full = u.get("estimated-full-date")
        stores.append({"name": u.get("store"), "used": u.get("used"), "total": u.get("total"),
                       "pct": 100 * u["used"] / u["total"] if u.get("used") is not None else None,
                       # PBS sometimes estimates a date already past (e.g. when use fell): that's no forecast
                       "full_date": full if isinstance(full, (int, float)) and full > time.time() else None,
                       "gc": (u.get("gc-status") or {}).get("upid") is not None})
    tasks = S.pbs_get("/nodes/localhost/tasks", since=s_, until=e_, limit=5000)
    kinds = defaultdict(lambda: {"ok": 0, "failed": 0})
    failed = []
    names = {"verificationjob": "verify", "verify": "verify", "prune": "prune", "prunejob": "prune",
             "garbage_collection": "garbage collection", "syncjob": "sync", "backup": "backup"}
    for t in tasks or []:
        k = names.get(t.get("worker_type"))
        if not k or t.get("endtime") is None:
            continue
        if t.get("status") == "OK" or str(t.get("status", "")).startswith("WARNINGS"):
            kinds[k]["ok"] += 1
        else:
            kinds[k]["failed"] += 1
            failed.append({"t": t.get("starttime"), "type": k, "what": t.get("worker_id"), "reason": t.get("status")})
    if usage is None and tasks is None:
        return {"error": True, "stores": [], "kinds": {}, "failed": []}
    if not usage and not tasks:  # answered, but shows nothing: the token lacks the Audit role
        return {"error": True, "denied": True, "stores": [], "kinds": {}, "failed": []}
    return {"error": False, "stores": stores, "kinds": dict(kinds), "failed": failed[:20]}


# ---------------------------------------------------------------- formatting helpers used here


def fmt_minutes(m):
    if m < 1:
        return "under a minute"
    if m < 90:
        return f"{m:.0f} min"
    h = m / 60
    return f"{h:.1f} h" if h < 48 else f"{h / 24:.1f} days"


def _num(x):
    try:
        return float(str(x).rstrip("%"))
    except ValueError:
        return None


def _join(xs):
    xs = list(xs)
    return xs[0] if len(xs) == 1 else ", ".join(xs[:-1]) + " and " + xs[-1] if xs else ""
