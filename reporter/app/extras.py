"""The optional report sections beyond the four main areas: failed tasks, trends, certificates, disks inside VMs,
sign-ins and firewall, resource pressure, backup jobs and coverage, replication, network traffic, pools and tags,
the cluster network and storage detail.

Each collector runs only when its section is ticked for either version, reads only what a read-only token can,
and returns plain dicts. The view_* functions turn them into ready-made rows for the templates.
"""

import re
from collections import Counter, defaultdict

from . import db
from .collect import DAY, mean, pbs_snapshots, rrd, timeframe_for

GIB = 1024**3
SKIP_FS = {"squashfs", "iso9660", "tmpfs", "devtmpfs", "overlay", "efivarfs", "udf", "cdfs", "ramfs", "nsfs"}
TASK_NAMES = {
    "qmstart": "VM start", "qmstop": "VM stop", "qmshutdown": "VM shutdown", "qmreboot": "VM reboot",
    "qmigrate": "VM migration", "qmclone": "VM clone", "qmcreate": "VM create", "qmdestroy": "VM removal",
    "qmrestore": "VM restore", "qmsnapshot": "snapshot", "qmdelsnapshot": "snapshot removal", "qmrollback": "rollback",
    "vzstart": "container start", "vzstop": "container stop", "vzshutdown": "container shutdown",
    "vzmigrate": "container migration", "vzcreate": "container create", "vzdestroy": "container removal",
    "vzrestore": "container restore", "vzsnapshot": "snapshot", "vzdelsnapshot": "snapshot removal",
    "imgcopy": "disk copy", "imgdel": "disk removal", "move_volume": "disk move", "qmmove": "disk move",
    "aptupdate": "package update check", "srvreload": "service reload", "srvrestart": "service restart",
    "startall": "start all guests", "stopall": "stop all guests", "zfscreate": "ZFS create",
    "hamigrate": "HA migration", "harelocate": "HA relocation", "hastart": "HA start", "hastop": "HA stop",
    "download": "download", "acmerenew": "certificate renewal", "acmenewcert": "certificate order",
}
LOGIN = re.compile(r"authentication (?:failure|failed);?.*?rhost=(?:::ffff:)?(?P<rhost>[^\s]+).*?user=(?P<user>[^\s]+)",
                   re.I)
TICKET = re.compile(r"invalid PVE ticket", re.I)
KNET = re.compile(r"host: (?P<host>\d+) link: (?P<link>\d+) is (?P<state>down|up)", re.I)
FW = re.compile(r"\b(?P<act>DROP|REJECT)\b.*?SRC=(?P<src>\S+)", re.I)
DPT = re.compile(r"DPT=(\d+)")


def wanted(on, *keys):
    return any(k in on for k in keys)


# ---------------------------------------------------------------- collection


def collect_extras(S, d, ctx, on):
    """Adds the ticked optional sections to d. ctx: online, guests_res, tasks, s_, e_, now, storage_cfg, kind."""
    s_, e_ = ctx["s_"], ctx["e_"]
    online, guests_res = ctx["online"], ctx["guests_res"]
    names = {g["vmid"]: g.get("name") or str(g["vmid"]) for g in guests_res}
    if "tasks" in on:
        d["x_tasks"] = failed_tasks(ctx["tasks"], names)
    if "certificates" in on:
        d["x_certs"] = certificates(S, online, ctx["now"])
    if "guest_disks" in on:
        d["x_guest_disks"] = guest_disks(S, guests_res)
    if "security" in on:
        d["x_security"] = security(S, online, s_, e_)
    if "backup_coverage" in on:
        d["x_backup_jobs"] = backup_jobs(S, names)
    if "replication" in on:
        d["x_replication"] = replication(S, online, names)
    if "pools" in on:
        d["x_pools"] = pools(d, guests_res)
    if "cluster_net" in on and d.get("topology", {}).get("cluster"):
        d["x_cluster_net"] = cluster_net(S, s_, e_)
    if "storage_detail" in on:
        d["x_storage_detail"] = storage_detail(S, online, d)
    if "trends" in on and ctx.get("kind") in ("weekly", "monthly", "quarterly"):
        d["x_trends"] = trends(S, online, ctx)
    return d


def failed_tasks(tasks, names):
    out = []
    for t in tasks:
        status = str(t.get("status") or "")
        if t.get("type") == "vzdump" or status in ("", "OK") or status.startswith("WARNINGS") or t.get("endtime") is None:
            continue
        gid = int(t["id"]) if str(t.get("id", "")).isdigit() else None
        out.append({"t": t.get("starttime"), "node": t.get("node"), "what": TASK_NAMES.get(t.get("type"), t.get("type")),
                    "guest": f"{gid} {names.get(gid, '')}".strip() if gid else (t.get("id") or ""),
                    "user": t.get("user") or "", "status": status[:160]})
    return sorted(out, key=lambda x: -(x["t"] or 0))


def certificates(S, online, now):
    """Each node's web certificate (the custom pveproxy one when present) and the cluster CA, with days left."""
    out = []
    for n in online:
        certs = S.get(f"/nodes/{n}/certificates/info") or []
        by_file = {c.get("filename"): c for c in certs}
        web = by_file.get("pveproxy-ssl.pem") or by_file.get("pve-ssl.pem")
        for c, what in ((web, "web"), (by_file.get("pve-root-ca.pem"), "ca")):
            if not c or not c.get("notafter"):
                continue
            if what == "ca" and any(x["what"] == "ca" for x in out):
                continue  # the cluster CA is the same on every node
            cn = re.search(r"CN=([^/,]+)", c.get("subject") or "")
            out.append({"node": n if what == "web" else "cluster", "what": what, "file": c.get("filename"),
                        "name": cn[1] if cn else c.get("filename"), "until": c["notafter"],
                        "days": int((c["notafter"] - now) // DAY), "acme": "Let's Encrypt" in (c.get("issuer") or "")})
    return sorted(out, key=lambda c: c["days"])


def guest_disks(S, guests_res):
    """Filesystems inside guests: from the guest agent for VMs, from the container's own figures for LXC."""
    rows, checked, no_agent = [], 0, 0
    for g in guests_res:
        if g.get("status") != "running":
            continue
        v, node, name = g["vmid"], g.get("node"), g.get("name") or str(g["vmid"])
        if g["type"] == "lxc":
            st = S.quiet(f"/nodes/{node}/lxc/{v}/status/current") or {}
            if st.get("maxdisk"):
                checked += 1
                rows.append({"vmid": v, "name": name, "mount": "/", "type": "rootfs", "used": st.get("disk") or 0,
                             "total": st["maxdisk"]})
            continue
        cfg = S.quiet(f"/nodes/{node}/qemu/{v}/config") or {}
        if not str(cfg.get("agent") or "").startswith(("1", "enabled=1")):
            no_agent += 1
            continue
        fs = (S.quiet(f"/nodes/{node}/qemu/{v}/agent/get-fsinfo") or {}).get("result")
        if fs is None:
            no_agent += 1
            continue
        checked += 1
        for f in fs:
            total, used = f.get("total-bytes"), f.get("used-bytes")
            if not total or used is None or total < GIB or (f.get("type") or "").lower() in SKIP_FS:
                continue
            rows.append({"vmid": v, "name": name, "mount": f.get("mountpoint") or f.get("name"),
                         "type": f.get("type"), "used": used, "total": total})
    for r in rows:
        r["pct"] = 100 * r["used"] / r["total"]
    rows.sort(key=lambda r: -r["pct"])
    return {"rows": rows, "checked": checked, "no_agent": no_agent}


def security(S, online, s_, e_):
    """Failed sign-ins from the collected node logs, and the host firewall's drops."""
    fails, tickets = Counter(), 0
    last = {}
    for r in db.query("select t, message from logs where t >= ? and t < ? and source = 'journal' "
                      "and (message like '%authentication fail%' or message like '%invalid PVE ticket%')", s_, e_):
        m = LOGIN.search(r["message"])
        if m:
            k = (m["user"], m["rhost"])
            fails[k] += 1
            last[k] = max(last.get(k, 0), r["t"])
        elif TICKET.search(r["message"]):
            tickets += 1
    drops, srcs, ports, logging = 0, Counter(), Counter(), False
    for n in online:
        lines = S.quiet(f"/nodes/{n}/firewall/log", since=s_, until=e_, limit=5000) or []
        logging = logging or bool(lines)
        for x in lines:
            line = x.get("t", "")
            m = FW.search(line)
            if m:
                drops += 1
                srcs[m["src"]] += 1
                port = DPT.search(line)
                if port:
                    ports[port[1]] += 1
    return {"logins": [{"user": u, "rhost": h, "count": c, "last": last[(u, h)]} for (u, h), c in fails.most_common(10)],
            "login_total": sum(fails.values()), "tickets": tickets, "logs_collected": bool(db.get_state("logs_from")),
            "drops": drops, "top_src": srcs.most_common(5), "top_ports": ports.most_common(5), "fw_logging": logging}


def backup_jobs(S, names):
    """Each backup job: on or off, what it covers, where it writes, how much it keeps, and disks it leaves out."""
    jobs = S.get("/cluster/backup") or []
    out, excluded = [], []
    for j in jobs:
        keep = j.get("prune-backups") or {}
        if isinstance(keep, str):
            keep = dict(x.split("=", 1) for x in keep.split(",") if "=" in x)
        keep_text = "keeps all" if str(keep.get("keep-all")) == "1" else ", ".join(
            f"{v} {k.replace('keep-', '')}" for k, v in keep.items() if k.startswith("keep-")) or "storage default"
        covers = "all guests" if j.get("all") else (f"pool {j['pool']}" if j.get("pool") else
                                                     f"{len(str(j.get('vmid') or '').split(','))} guests")
        if j.get("exclude"):
            covers += f", {len(str(j['exclude']).split(','))} left out"
        out.append({"id": j.get("id"), "enabled": bool(int(j.get("enabled", 1) or 0)), "schedule": j.get("schedule") or "–",
                    "storage": j.get("storage") or "–", "keep": keep_text, "covers": covers, "mode": j.get("mode") or ""})
        if not int(j.get("enabled", 1) or 0):
            continue
        tree = S.quiet(f"/cluster/backup/{j['id']}/included_volumes") or {}
        for guest in tree.get("children") or []:
            for disk in guest.get("children") or []:
                if not disk.get("included"):
                    excluded.append({"vmid": guest.get("id"), "name": guest.get("name") or names.get(guest.get("id"), ""),
                                     "disk": str(disk.get("id", "")).split(":")[-1], "volume": disk.get("name"),
                                     "reason": disk.get("reason") or ""})
    seen, unique = set(), []
    for x in excluded:
        if (x["vmid"], x["disk"]) not in seen:
            seen.add((x["vmid"], x["disk"]))
            unique.append(x)
    points = {}
    if S.pbs:  # restore points and the newest snapshot's verification, per guest, from the backup server
        for snap in pbs_snapshots(S):
            bid = str(snap.get("backup-id", ""))
            if snap.get("backup-type") not in ("vm", "ct") or not bid.isdigit():
                continue
            p = points.setdefault(int(bid), {"count": 0, "last": 0, "verify": None})
            p["count"] += 1
            if (snap.get("backup-time") or 0) >= p["last"]:
                p["last"] = snap.get("backup-time") or 0
                ver = snap.get("verification")
                p["verify"] = ver.get("state") if isinstance(ver, dict) else None
    return {"jobs": out, "excluded": unique, "points": points}


def replication(S, online, names):
    rows = []
    for n in online:
        for r in S.get(f"/nodes/{n}/replication") or []:
            rows.append({"id": r.get("id"), "guest": r.get("guest"), "name": names.get(r.get("guest"), ""),
                         "source": r.get("source") or n, "target": r.get("target"), "schedule": r.get("schedule"),
                         "last_sync": r.get("last_sync"), "duration": r.get("duration"),
                         "fail_count": r.get("fail_count") or 0, "error": (r.get("error") or "")[:160]})
    return sorted(rows, key=lambda r: (-(r["fail_count"]), r["last_sync"] or 0))


def pools(d, guests_res):
    """Allocation and use per pool, and how many guests carry each tag, for reports split by team or customer."""
    by = defaultdict(list)
    for g in d["guests"]["list"]:
        by[g.get("pool") or ""].append(g)
    backup_rows = {r["vmid"]: r for r in d["backups"].get("rows", [])}
    out = []
    for pool, gs in by.items():
        running = [g for g in gs if g["status"] == "running"]
        days = [c for g in gs for c in (backup_rows.get(g["vmid"]) or {}).get("days", [])]
        out.append({"pool": pool or "(no pool)", "guests": len(gs), "running": len(running),
                    "vcpu": sum(g["cpus"] or 0 for g in gs), "ram": sum(g["maxmem"] or 0 for g in gs),
                    "disk": sum(g["maxdisk"] or 0 for g in gs),
                    "cpu": mean([g["cpu_avg"] for g in running if g["cpu_avg"] is not None]),
                    "backups_ok": days.count("ok"), "backups_failed": days.count("fail")})
    tags = Counter(t for g in d["guests"]["list"] for t in g.get("tags") or [])
    return {"pools": sorted(out, key=lambda p: (p["pool"] == "(no pool)", -p["guests"])), "tags": tags.most_common(12),
            "used": any(p["pool"] != "(no pool)" for p in out) or bool(tags)}


def cluster_net(S, s_, e_):
    """Corosync links per node and every link going down or up that the nodes logged."""
    nodes = S.get("/cluster/config/nodes") or []
    ids = {str(n.get("nodeid")): n.get("name") for n in nodes}
    links = [{"node": n.get("name"), "id": n.get("nodeid"),
              "rings": [n[k] for k in sorted(n) if re.match(r"ring\d+_addr|link\d+", k) and n.get(k)]} for n in nodes]
    events = Counter()
    for r in db.query("select t, node, message from logs where t >= ? and t < ? and message like '%link:%is down%'", s_, e_):
        m = KNET.search(r["message"])
        if m:
            events[(r["node"], ids.get(m["host"], f"node {m['host']}"), m["link"])] += 1
    qd = S.quiet("/cluster/config/qdevice")
    return {"links": sorted(links, key=lambda x: str(x["node"])),
            "down": [{"seen_by": a, "peer": b, "link": c, "count": n} for (a, b, c), n in events.most_common(10)],
            "qdevice": bool(qd)}


def storage_detail(S, online, d):
    lvm, zfs = [], []
    for n in online:
        for x in S.quiet(f"/nodes/{n}/disks/lvmthin") or []:
            if x.get("lv_size"):
                lvm.append({"node": n, "name": f"{x.get('vg')}/{x.get('lv')}", "pct": 100 * (x.get("used") or 0) / x["lv_size"],
                            "meta_pct": 100 * (x.get("metadata_used") or 0) / x["metadata_size"] if x.get("metadata_size") else None})
        for p in S.quiet(f"/nodes/{n}/disks/zfs") or []:
            size = p.get("size") or 0
            zfs.append({"node": n, "name": p.get("name"), "health": p.get("health"), "frag": p.get("frag"),
                        "pct": 100 * (p.get("alloc") or 0) / size if size else None, "dedup": p.get("dedup")})
    ceph = None
    if d.get("ceph"):
        pools_ = []
        for n in online[:1]:
            for p in S.quiet(f"/nodes/{n}/ceph/pool") or []:
                pools_.append({"name": p.get("pool_name"), "pct": 100 * (p.get("percent_used") or 0),
                               "size": p.get("size"), "pgs": p.get("pg_num")})
        ceph = {"pools": pools_, **d["ceph"]}
    return {"lvm": lvm, "zfs": zfs, "ceph": ceph}


def trends(S, online, ctx):
    """This period next to the ones before it: 8 weeks, 6 months or 4 quarters. Load from Proxmox's own history,
    backup runs and failed tasks from its task archive. A period without data is a gap, never a zero."""
    from .periods import trend_periods

    per = trend_periods(ctx["kind"], ctx["start"])
    if not per:
        return None
    t0, t1 = int(per[0][0].timestamp()), int(per[-1][1].timestamp())
    tf = timeframe_for(t0, ctx["now"])
    cpu, mem = [[] for _ in per], [[] for _ in per]
    for n in online:
        for p in rrd(S, f"/nodes/{n}", t0, t1, "AVERAGE", ctx["now"], timeframe=tf) or []:
            i = next((k for k, (a, b, _) in enumerate(per) if a.timestamp() <= p["time"] < b.timestamp()), None)
            if i is None:
                continue
            if isinstance(p.get("cpu"), (int, float)):
                cpu[i].append(100 * p["cpu"])
            if p.get("memtotal") and p.get("memused") is not None:
                mem[i].append(100 * p["memused"] / p["memtotal"])
    runs, bad, failed = [0] * len(per), [0] * len(per), [0] * len(per)
    for n in online:
        for t in S.get(f"/nodes/{n}/tasks", since=t0, until=t1, limit=10000, source="all") or []:
            i = next((k for k, (a, b, _) in enumerate(per) if a.timestamp() <= (t.get("starttime") or 0) < b.timestamp()), None)
            if i is None or t.get("endtime") is None:
                continue
            ok = t.get("status") == "OK" or str(t.get("status", "")).startswith("WARNINGS")
            if t.get("type") == "vzdump":
                runs[i] += 1
                bad[i] += 0 if ok else 1
            elif not ok:
                failed[i] += 1
    return {"labels": [lbl for *_, lbl in per],
            "rows": [("CPU, average %", [mean(x) for x in cpu], "pct"), ("Memory, average %", [mean(x) for x in mem], "pct"),
                     ("Backup jobs run", runs, "num"), ("Backup jobs with errors", bad, "num"),
                     ("Other tasks failed", failed, "num")]}


def node_rrd_extras(avg, mx):
    """Network totals and resource pressure for one node, from the RRD points already read."""
    net_in = net_out = 0.0
    pts = avg or []
    for a, b in zip(pts, pts[1:], strict=False):
        step = b["time"] - a["time"]
        net_in += (a.get("netin") or 0) * step
        net_out += (a.get("netout") or 0) * step
    peak = max([max(p.get("netin") or 0, p.get("netout") or 0) for p in mx or []], default=None)
    pressure = {}
    for key, label in (("pressurecpusome", "cpu"), ("pressurememorysome", "mem"), ("pressurememoryfull", "mem_full"),
                       ("pressureiosome", "io"), ("pressureiofull", "io_full")):
        a = [p[key] for p in pts if isinstance(p.get(key), (int, float))]
        m = [p[key] for p in mx or [] if isinstance(p.get(key), (int, float))]
        if a:
            pressure[label] = (mean(a), max(m) if m else max(a))
    return {"net_in": net_in if pts else None, "net_out": net_out if pts else None, "net_peak": peak,
            "pressure": pressure}


# ---------------------------------------------------------------- the view


def view_extras(d, v, warn_pct, when, fbytes, fmem, fpct, fduration, join, plural):
    """Ready-made rows for the optional sections, and their Needs attention lines."""
    x, att = {}, []
    if d.get("x_tasks") is not None:
        x["tasks"] = [{**t, "when": when(t["t"], "%a %-d %b %H:%M")} for t in d["x_tasks"][:15]]
        x["tasks_more"] = max(0, len(d["x_tasks"]) - 15)
        if d["x_tasks"]:
            kinds = Counter(t["what"] for t in d["x_tasks"])
            att.append(("warn", "health", f"{plural(len(d['x_tasks']), 'task')} failed: "
                        + join([f"{n} {k}" if n > 1 else k for k, n in kinds.most_common(3)]),
                        f"{len(d['x_tasks'])} tasks failed"))
    if d.get("x_certs") is not None:
        x["certs"] = [{**c, "until_text": when(c["until"], "%-d %b %Y"),
                       "tone": "bad" if c["days"] < 7 else "warn" if c["days"] < 30 else ""} for c in d["x_certs"]]
        for c in d["x_certs"]:
            if c["days"] < 30:
                who = "The cluster CA" if c["what"] == "ca" else f"The web certificate of {c['node']}"
                left = "has expired" if c["days"] < 0 else f"expires in {plural(c['days'], 'day')}"
                att.append(("bad" if c["days"] < 7 else "warn", "maintenance", f"{who} {left}",
                            f"{c['node']} certificate {'expired' if c['days'] < 0 else str(c['days']) + ' d'}"))
    if d.get("x_guest_disks") is not None:
        gd = d["x_guest_disks"]
        full = [r for r in gd["rows"] if r["pct"] >= warn_pct]
        x["guest_disks"] = [{**r, "pct_text": fpct(r["pct"]), "size_text": f"{fbytes(r['used'])} / {fbytes(r['total'])}",
                             "tone": "bad" if r["pct"] >= 90 else "warn" if r["pct"] >= warn_pct else ""}
                            for r in (full or gd["rows"][:5])[:12]]
        x["guest_disks_note"] = (f"{plural(gd['checked'], 'guest')} checked"
                                 + (f"; {gd['no_agent']} VMs without a running guest agent can't be seen inside" if gd["no_agent"] else ""))
        x["guest_disks_full"] = bool(full)
        for r in full[:3]:
            att.append(("bad" if r["pct"] >= 90 else "warn", "capacity",
                        f"{r['mount']} inside {r['name']} ({r['vmid']}) is {r['pct']:.0f}% full",
                        f"{r['name']} {r['mount']} {r['pct']:.0f}%"))
    if d.get("x_security") is not None:
        sec = d["x_security"]
        x["security"] = {**sec, "logins": [{**r, "last_text": when(r["last"], "%a %-d %b %H:%M")} for r in sec["logins"]]}
        for r in sec["logins"][:2]:
            if r["count"] >= 10:
                att.append(("warn", "health", f"{r['count']} failed sign-ins as {r['user']} from {r['rhost']}",
                            f"{r['count']} failed sign-ins ({r['user']})"))
    if d.get("x_backup_jobs") is not None:
        bj = d["x_backup_jobs"]
        rows = d["backups"].get("rows", [])
        x["backup_jobs"] = bj["jobs"]
        x["excluded"] = bj["excluded"][:12]
        pts = bj["points"]
        x["points"] = [{"vmid": r["vmid"], "name": r["name"], "count": pts[r["vmid"]]["count"],
                        "verify": pts[r["vmid"]]["verify"] or "not verified"} for r in rows if r["vmid"] in pts]
        off = [j for j in bj["jobs"] if not j["enabled"]]
        if off:
            att.append(("warn", "backups", f"{plural(len(off), 'backup job')} {'is' if len(off) == 1 else 'are'} switched off "
                        f"({join([j['schedule'] + ' to ' + j['storage'] for j in off[:3]])})",
                        f"{len(off)} backup jobs off"))
        if bj["excluded"]:
            att.append(("warn", "backups", f"{plural(len(bj['excluded']), 'disk')} left out of backups: "
                        + join([f"{e['name']} {e['disk']}" for e in bj["excluded"][:3]]), f"{len(bj['excluded'])} disks not backed up"))
    if d.get("x_replication") is not None:
        x["replication"] = [{**r, "last_text": when(r["last_sync"], "%a %-d %b %H:%M") if r["last_sync"] else "never",
                             "dur_text": fduration(r["duration"]), "tone": "bad" if r["fail_count"] else ""}
                            for r in d["x_replication"][:20]]
        x["replication_more"] = max(0, len(d["x_replication"]) - 20)
    nodes = d.get("nodes") or []
    if any(n.get("net_in") is not None for n in nodes) and "network" in v["_on"]:
        x["network"] = [{"name": n["name"], "in": fbytes(n.get("net_in")), "out": fbytes(n.get("net_out")),
                         "peak": (fbytes(n["net_peak"] * 8).replace("B", "bit") + "/s") if n.get("net_peak") else "–"}
                        for n in nodes]
        top = sorted((g for g in d["guests"]["list"] if g.get("net_in") is not None),
                     key=lambda g: -((g.get("net_in") or 0) + (g.get("net_out") or 0)))[:6]
        x["network_guests"] = [{"vmid": g["vmid"], "name": g["name"], "node": g["node"], "in": fbytes(g["net_in"]),
                                "out": fbytes(g["net_out"])} for g in top]
    if any(n.get("pressure") for n in nodes) and "pressure" in v["_on"]:
        def pr(n, k):
            p = n["pressure"].get(k)
            return f"{p[0]:.1f}% · {p[1]:.0f}%" if p else "–"
        x["pressure"] = [{"name": n["name"], "cpu": pr(n, "cpu"), "mem": pr(n, "mem"), "io": pr(n, "io"),
                          "hot": any((n["pressure"].get(k) or (0, 0))[0] >= 10 for k in ("cpu", "mem", "io"))} for n in nodes]
    if d.get("x_pools") is not None and d["x_pools"]["used"]:
        x["pools"] = [{**p, "ram_text": fmem(p["ram"], 0), "disk_text": fbytes(p["disk"]), "cpu_text": fpct(p["cpu"])}
                      for p in d["x_pools"]["pools"]]
        x["tags"] = d["x_pools"]["tags"]
    if d.get("x_cluster_net") is not None:
        x["cluster_net"] = d["x_cluster_net"]
        for e in d["x_cluster_net"]["down"][:2]:
            att.append(("warn", "health", f"Cluster link {e['link']} to {e['peer']} went down {plural(e['count'], 'time')} "
                        f"(logged by {e['seen_by']})", f"link {e['link']} to {e['peer']} down {e['count']}×"))
    if d.get("x_storage_detail") is not None:
        sd = d["x_storage_detail"]
        x["storage_detail"] = sd if (sd["lvm"] or sd["zfs"] or sd["ceph"]) else None
        for t in sd["lvm"]:
            if (t["meta_pct"] or 0) >= 80:
                att.append(("bad" if t["meta_pct"] >= 90 else "warn", "capacity",
                            f"LVM-thin metadata of {t['name']} on {t['node']} is {t['meta_pct']:.0f}% full",
                            f"{t['name']} metadata {t['meta_pct']:.0f}%"))
    if d.get("x_trends"):
        tr = d["x_trends"]

        def fmt(val, kind):
            return "–" if val is None else (f"{val:.0f}%" if kind == "pct" else f"{val:,.0f}")
        x["trends"] = {"labels": tr["labels"],
                       "rows": [{"label": lbl, "cells": [fmt(val, kind) for val in vals]} for lbl, vals, kind in tr["rows"]]}
    return x, att
