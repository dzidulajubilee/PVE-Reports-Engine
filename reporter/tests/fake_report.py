"""Made-up report data in three sizes, shaped exactly like collect.collect() returns it.

Every name and number here is invented (the same example cluster as the template designs). Used by the render
tests and by `python -m tests.fake_report` previews.
"""

import datetime as dt
import math
import random

from app import config
from app.collect import buckets

DAY = 86400
GIB = 1024**3
TB = 1e12


def _period(kind="weekly"):
    tz = config.TZ
    if kind == "weekly":
        start = dt.datetime(2026, 11, 9, tzinfo=tz)
        end = start + dt.timedelta(days=7)
    elif kind == "monthly":
        start, end = dt.datetime(2026, 10, 1, tzinfo=tz), dt.datetime(2026, 11, 1, tzinfo=tz)
    else:
        start, end = dt.datetime(2026, 7, 1, tzinfo=tz), dt.datetime(2026, 10, 1, tzinfo=tz)
    return start, end


def _heat(rng, s_, e_, base, peak, spike=None):
    days = (e_ - s_) / DAY
    step = 7200 if days <= 8 else 28800 if days <= 31 else DAY
    n = math.ceil((e_ - s_) / step)
    cpu, mem = [], []
    for i in range(n):
        t = dt.datetime.fromtimestamp(s_ + i * step, config.TZ)
        work = 1 if t.weekday() < 5 and 8 <= t.hour < 18 else 0.4 if 6 <= t.hour < 22 else 0.1
        night = 0.35 if 0 <= t.hour < 4 else 0
        v = 100 * min(1, base + peak * work + night * 0.5 + rng.random() * 0.04)
        cpu.append(v)
        mem.append(min(100, 40 + v * 0.4))
    if spike is not None and spike < n:
        cpu[spike] = 95
        mem[spike] = 93
    return {"step": step, "cpu": cpu, "mem": mem}


def _history(e_, pct_now, per_week, noise=0.2, rng=None):
    rng = rng or random.Random(1)
    out = []
    for k in range(84 * 4, -1, -1):
        t = e_ - k * 6 * 3600 - 1
        out.append((t, max(0, pct_now - per_week * k / 28 + rng.uniform(-noise, noise))))
    return out


def storage_row(name, node, shared, typ, pct, total, per_week, change, eta_warn=None, eta_full=None, backup_only=False,
                e_=0, rng=None):
    return {"name": name, "node": node, "shared": shared, "type": typ, "used": total * pct / 100, "total": total,
            "pct": pct, "change_pts": change, "per_week": per_week, "steady": abs(per_week) < 0.3, "even": True,
            "eta_warn": eta_warn, "eta_full": eta_full, "history": _history(e_, pct, per_week, rng=rng),
            "backup_only": backup_only, "backup": backup_only, "active": True}


def guest(vmid, name, node, kind="qemu", status="running", cpus=4, mem_gb=8, cpu=10.0, mem_used=4.0, mem_max=None,
          written=40e9, restarts=(), stopped_since=None, disk_gb=64, ran_all=True):
    return {"vmid": vmid, "name": name, "node": node, "type": kind, "status": status, "cpus": cpus,
            "maxmem": mem_gb * GIB, "maxdisk": disk_gb * 1e9, "cpu_avg": cpu if status == "running" else None,
            "cpu_peak_vcpu": cpus * min(1, cpu / 100 * 2.5) if status == "running" else None,
            "mem_avg": mem_used * GIB, "mem_max": (mem_max or mem_used * 1.1) * GIB, "disk_written": written,
            "restarts": list(restarts), "ran_all": ran_all and status == "running" and not restarts,
            "stopped_since": stopped_since, "tags": [], "pool": None}


def typical(kind="weekly"):
    rng = random.Random(46)
    start, end = _period(kind)
    s_, e_ = int(start.timestamp()), int(end.timestamp())
    bks = buckets(start, end)
    n = len(bks)
    T = lambda d, h, m=0: s_ + d * DAY + h * 3600 + m * 60  # noqa: E731
    gl = [
        guest(101, "web-01", "hv-01", cpu=27, mem_gb=8, mem_used=5.9, written=38e9),
        guest(102, "web-02", "hv-02", cpu=31, mem_gb=8, mem_used=6.2, written=41e9),
        guest(110, "db-primary", "hv-02", cpus=8, mem_gb=32, cpu=64, mem_used=28.4, written=412e9, disk_gb=600),
        guest(111, "db-replica", "hv-01", cpus=8, mem_gb=32, cpu=12, mem_used=22.0, written=180e9, disk_gb=600),
        guest(120, "files", "hv-01", cpu=9, mem_gb=8, mem_used=3.3, written=214e9, disk_gb=2000),
        guest(130, "mail", "hv-01", cpu=18, mem_gb=12, mem_used=7.1, written=96e9, restarts=[T(1, 3, 12), T(4, 3, 9)]),
        guest(140, "ci-runner", "hv-03", cpus=8, mem_gb=16, cpu=52, mem_used=11.0, written=388e9, disk_gb=400),
        guest(150, "monitoring", "hv-02", cpus=8, mem_gb=24, cpu=4, mem_used=5.2, mem_max=5.8),
        guest(160, "vpn", "hv-02", kind="lxc", cpus=4, mem_gb=8, cpu=2, mem_used=0.5, mem_max=0.6),
        guest(170, "wiki", "hv-02", kind="lxc", cpus=8, mem_gb=32, cpu=1.5, mem_used=2.8, mem_max=3.1),
        guest(175, "git", "hv-01", kind="lxc", cpu=6, mem_gb=4, mem_used=2.1),
        guest(180, "dev-sandbox", "hv-03", cpu=3, mem_gb=8, mem_used=2.0, disk_gb=140),
        guest(190, "test-win11", "hv-02", status="stopped", stopped_since=s_ - 38 * DAY, disk_gb=120),
        guest(195, "old-erp", "hv-03", status="stopped", stopped_since=s_ - 148 * DAY, disk_gb=300),
    ]
    for i, nm in enumerate(["proxy", "dns-1", "dns-2", "backup-agent", "ldap", "print", "ntp", "jump", "build-cache",
                            "metrics"]):
        gl.append(guest(200 + i, nm, f"hv-0{1 + i % 3}", kind="lxc" if i % 2 else "qemu", cpu=1 + i, mem_gb=2,
                        mem_used=0.9, written=4e9))
    gl = gl[:24]
    oversized = []
    for gg in gl:
        if gg["status"] == "running" and gg["mem_max"] < 0.25 * gg["maxmem"] and gg["maxmem"] >= 4 * GIB:
            oversized.append({**gg, "free_mem": gg["maxmem"] - max(2 * gg["mem_max"], GIB),
                              "free_cpu": max(0, gg["cpus"] - max(1, math.ceil(2 * gg["cpu_peak_vcpu"])))})
    running = [x for x in gl if x["status"] == "running"]
    stopped = [x for x in gl if x["status"] != "running"]
    guests = {"list": gl, "total": len(gl), "vms": sum(1 for x in gl if x["type"] == "qemu"),
              "cts": sum(1 for x in gl if x["type"] == "lxc"), "running": len(running),
              "ran_all": sum(1 for x in gl if x["ran_all"]),
              "top": sorted(running, key=lambda x: -x["cpu_avg"]), "oversized": oversized,
              "restarted": [x for x in gl if x["restarts"]], "stopped": stopped,
              "stopped_disk": sum(x["maxdisk"] for x in stopped), "restarts_known": True}

    def node(name, cpu, cpu95, cpumax, mem, memmax, at, vcpu, ram, local, pve, kernel, new, up_days, nguests, spike=None):
        return {"name": name, "online": True, "uptime": up_days * DAY, "cpu_avg": cpu, "cpu_p95": cpu95,
                "cpu_max": cpumax, "mem_avg": mem, "mem_p95": memmax - 6, "mem_max": memmax, "mem_max_at": at,
                "cores": 32, "ram_total": 256 * GIB, "vcpu": vcpu, "ram_given": ram * GIB, "guests": nguests,
                "guests_running": nguests, "local_pct": local, "pve": pve, "kernel": kernel, "kernel_new": new,
                "reboot": new is not None, "down": [], "reboots": [], "down_s": 0,
                "heat": _heat(rng, s_, e_, cpu / 300, cpu / 60, spike), "disks": [
                    {"dev": f"/dev/nvme{i}n1", "type": "nvme", "health": "PASSED", "wear": 82 if (name == "hv-02" and i == 2) else 12,
                     "model": "Example NVMe"} for i in range(4)]}

    nodes = [
        node("hv-01", 18, 52, 71, 64, 78, None, 48, 192, 41, "9.1.2", "6.14.8-2-pve", "6.14.11-1-pve", 41, 9),
        node("hv-02", 22, 61, 88, 71, 93, T(1, 14, 20), 56, 240, 38, "9.1.2", "6.14.8-2-pve", "6.14.11-1-pve", 41, 11,
             spike=19 if n == 7 else None),
        node("hv-03", 9, 24, 40, 38, 52, None, 20, 96, 87, "9.1.4", "6.14.11-1-pve", None, 12, 4),
    ]
    storage = [
        storage_row("ceph-vm", "", True, "rbd", 62, 18 * TB, 1.2, 1.1, eta_warn=98, eta_full=230, e_=e_, rng=rng),
        storage_row("backup-nfs", "", True, "nfs", 71, 20 * TB, 2.0, 2.0, eta_warn=32, eta_full=102, backup_only=True,
                    e_=e_, rng=rng),
        storage_row("local-zfs", "hv-01", False, "zfspool", 41, 1.9 * TB, 0.2, 0.2, e_=e_, rng=rng),
        storage_row("local-zfs", "hv-02", False, "zfspool", 38, 1.9 * TB, -0.4, -0.4, e_=e_, rng=rng),
        storage_row("local-zfs", "hv-03", False, "zfspool", 87, 1.9 * TB, 4.6, 4.6, eta_warn=0, eta_full=21, e_=e_, rng=rng),
    ]
    rows, failures = [], []
    fail_days = {110: [2], 140: [3, 4]} if n == 7 else {110: [min(2, n - 1)], 140: [min(3, n - 1)]}
    for gg in gl:
        if gg["status"] != "running":
            continue
        if gg["vmid"] == 180:
            rows.append({"vmid": 180, "name": "dev-sandbox", "days": ["none"] * n, "latest": None, "size": None,
                         "in_job": False, "verify": None, "running": True})
            continue
        fd = fail_days.get(gg["vmid"], [])
        rows.append({"vmid": gg["vmid"], "name": gg["name"], "days": ["fail" if i in fd else "ok" for i in range(n)],
                     "latest": e_ - DAY + 3600 + gg["vmid"] * 60, "size": gg["maxdisk"] * 0.3, "in_job": True,
                     "verify": "ok", "running": True})
        for i in fd:
            reason = ("not enough space for the backup" if gg["vmid"] == 140 else "the backup storage didn't answer in time")
            failures.append({"vmid": gg["vmid"], "name": gg["name"], "t": bks[i]["start"] + 3600 + 4 * 60, "node": gg["node"],
                             "reason": reason, "raw": reason})
    per_night = len([r for r in rows if r["in_job"]])
    nights = []
    for b in bks:
        f = sum(1 for x in failures if b["start"] <= x["t"] < b["end"])
        nights.append({"label": b["label"], "start": b["start"], "jobs": per_night, "ok": per_night - f, "failed": f,
                       "size": 2.3e12 - f * 1e11, "seconds": 10080 + f * 400})
    total = per_night * n
    backups = {"runs_total": total, "runs_ok": total - len(failures), "runs_failed": len(failures), "rows": rows,
               "failures": failures, "nights": nights, "not_in_job": [r for r in rows if not r["in_job"]], "stale": [],
               "jobs": 2, "jobs_read": True, "oldest_latest": None, "verify_failed": []}
    health = [
        {"key": "quorum", "what": "Quorum", "state": "kept all period", "tone": "ok"},
        {"key": "nodes", "what": "Nodes online", "state": "3 of 3 all period", "tone": "ok"},
        {"key": "ha", "what": "High availability", "state": "8 guests, no fencing", "tone": "ok"},
        {"key": "ceph", "what": "Ceph", "state": "healthy, 6 of 6 OSDs up, no flags set", "tone": "ok"},
        {"key": "zfs", "what": "ZFS pools", "state": "healthy", "tone": "ok"},
        {"key": "disks", "what": "Disk health", "state": "1 SSD in hv-02 at 82% wear", "tone": "warn"},
        {"key": "services", "what": "Cluster services", "state": "all running", "tone": "ok"},
        {"key": "replication", "what": "Replication", "state": "4 jobs in sync", "tone": "ok"},
        {"key": "kernel", "what": "Kernel", "state": "reboot due on hv-01 and hv-02", "tone": "warn"},
        {"key": "clock", "what": "Clock", "state": "all nodes in sync", "tone": "ok"},
    ]
    changes = [
        {"t": T(0, 10, 4), "text": "web-02 moved from hv-01 to hv-02", "user": "admin@pve"},
        {"t": T(2, 9, 30), "text": "ci-runner given 4 more vCPU", "user": "ops@pve"},
        {"t": T(3, 15, 2), "text": "New container 185 grafana-test on hv-03", "user": "ops@pve"},
    ]
    snapshots = [
        {"vmid": 110, "name": "db-primary", "snap": "before-upgrade-16", "taken": s_ - 98 * DAY, "age": 104},
        {"vmid": 101, "name": "web-01", "snap": "pre-php83", "taken": s_ - 49 * DAY, "age": 55},
    ]
    logs = [
        {"t": T(2, 1, 4), "source": "journal", "node": "hv-02", "level": "error", "unit": "pvescheduler",
         "message": "ERROR: Backup of VM 110 failed - got timeout", "count": 1, "first": T(2, 1, 4)},
        {"t": T(4, 3, 18), "source": "journal", "node": "hv-03", "level": "error", "unit": "pvescheduler",
         "message": "ERROR: Backup of VM 140 failed - no space left on device", "count": 2, "first": T(3, 3, 18)},
        {"t": T(5, 9, 0), "source": "journal", "node": "hv-01", "level": "warning", "unit": "corosync",
         "message": "[KNET  ] link: host: 2 link: 0 is down", "count": 1, "first": T(5, 9, 0)},
    ]
    d = {"start": s_, "end": e_, "days": round((e_ - s_) / DAY), "buckets": bks, "coverage": 1.0,
         "cluster_name": "example", "api_ok": True, "quorum_lost": [], "available_pct": 100.0,
         "tasks_read": True, "backups": backups, "storage": storage, "nodes": nodes, "guests": guests,
         "guests_running": {"running": len(running), "total": len(gl)}, "changes": changes, "snapshots": snapshots,
         "health": health, "logs": logs, "log_lines": 4, "logs_collected": True, "failed_tasks": [],
         "ceph": {"health": "HEALTH_OK", "osds": 6, "osds_up": 6, "checks": []},
         "pbs": {"error": False, "stores": [{"name": "store1", "used": 7.1e12, "total": 12e12, "pct": 59.2,
                                             "full_date": e_ + 210 * DAY, "gc": True}],
                 "kinds": {"verify": {"ok": 7, "failed": 0}, "prune": {"ok": 7, "failed": 0},
                           "garbage collection": {"ok": 1, "failed": 0}}, "failed": []}}
    p = {"start": s_ - (e_ - s_), "end": s_, "coverage": 1.0, "available_pct": 100.0,
         "backups": {"runs_total": total, "runs_ok": total, "runs_failed": 0},
         "storage": [{**s, "pct": s["pct"] - s["change_pts"]} for s in storage],
         "guests_running": {"running": len(running) - 1, "total": len(gl)}}
    return start, end, d, p


def empty(kind="weekly"):
    """A brand-new cluster the reporter has just started watching: one node, nothing backed up, no history."""
    start, end = _period(kind)
    s_, e_ = int(start.timestamp()), int(end.timestamp())
    bks = buckets(start, end)
    d = {"start": s_, "end": e_, "days": round((e_ - s_) / DAY), "buckets": bks, "coverage": 0.0,
         "cluster_name": None, "api_ok": True, "quorum_lost": [], "available_pct": None, "tasks_read": True,
         "backups": {"runs_total": 0, "runs_ok": 0, "runs_failed": 0, "rows": [], "failures": [],
                     "nights": [{"label": b["label"], "start": b["start"], "jobs": 0, "ok": 0, "failed": 0, "size": None,
                                 "seconds": None} for b in bks],
                     "not_in_job": [], "stale": [], "jobs": 0, "jobs_read": True, "oldest_latest": None,
                     "verify_failed": []},
         "storage": [], "nodes": [{"name": "pve", "online": True, "uptime": 3 * 3600, "cpu_avg": None, "cpu_p95": None,
                                   "cpu_max": None, "mem_avg": None, "mem_p95": None, "mem_max": None,
                                   "mem_max_at": None, "cores": 8, "ram_total": 32 * GIB, "vcpu": 0, "ram_given": 0,
                                   "guests": 0, "guests_running": 0, "local_pct": None, "pve": "9.1.4",
                                   "kernel": None, "kernel_new": None, "reboot": False, "down": [], "reboots": [],
                                   "down_s": 0, "heat": {"step": 7200, "cpu": [None] * 84, "mem": [None] * 84},
                                   "disks": []}],
         "guests": {"list": [], "total": 0, "vms": 0, "cts": 0, "running": 0, "ran_all": None, "top": [], "oversized": [],
                    "restarted": [], "stopped": [], "stopped_disk": 0, "restarts_known": False},
         "guests_running": {"running": 0, "total": 0}, "changes": [], "snapshots": [], "health": [], "logs": [],
         "log_lines": 0, "logs_collected": False, "failed_tasks": [], "pbs": None}
    p = {"start": s_ - (e_ - s_), "end": s_, "coverage": 0.0, "available_pct": None,
         "backups": {"runs_total": 0, "runs_ok": 0, "runs_failed": 0}, "storage": [], "guests_running": None}
    return start, end, d, p


def huge(kind="weekly"):
    """Many nodes and guests, long names: everything that can overflow a page."""
    start, end, d, p = typical(kind)
    rng = random.Random(7)
    e_ = d["end"]
    base_nodes = d["nodes"]
    nodes = []
    for i in range(12):
        nd = dict(base_nodes[i % 3])
        nd["name"] = f"pve-node-{i + 1:02d}-rack-b-long-name"
        nodes.append(nd)
    d["nodes"] = nodes
    gl = []
    for i in range(240):
        gl.append(guest(1000 + i, f"customer-{i:03d}-application-server-with-a-long-name", nodes[i % 12]["name"],
                        kind="lxc" if i % 3 else "qemu", cpu=rng.uniform(1, 90), mem_gb=16, mem_used=rng.uniform(1, 15),
                        restarts=[e_ - 3 * DAY] if i % 37 == 0 else (), status="stopped" if i % 41 == 0 else "running",
                        stopped_since=e_ - 60 * DAY if i % 41 == 0 else None))
    g = d["guests"]
    running = [x for x in gl if x["status"] == "running"]
    g.update(list=gl, total=len(gl), vms=sum(1 for x in gl if x["type"] == "qemu"), cts=sum(1 for x in gl if x["type"] == "lxc"),
             running=len(running), ran_all=len(running) - 7, top=sorted(running, key=lambda x: -x["cpu_avg"]),
             restarted=[x for x in gl if x["restarts"]], stopped=[x for x in gl if x["status"] != "running"],
             oversized=[{**x, "free_mem": 8 * GIB, "free_cpu": 2} for x in running[:30]])
    d["guests_running"] = {"running": len(running), "total": len(gl)}
    d["storage"] = d["storage"] + [
        storage_row("local-lvm-thin-with-a-long-name", nd["name"], False, "lvmthin", rng.uniform(20, 95), 3.8 * TB,
                    rng.uniform(-1, 3), 1.0, eta_full=rng.uniform(10, 300), e_=e_, rng=rng) for nd in nodes]
    n = len(d["buckets"])
    rows = []
    for x in gl:
        rows.append({"vmid": x["vmid"], "name": x["name"], "days": ["fail" if (x["vmid"] + i) % 29 == 0 else "ok" for i in range(n)],
                     "latest": e_ - DAY, "size": 80e9, "in_job": x["vmid"] % 50 != 0, "verify": "ok", "running": x["status"] == "running"})
    d["backups"]["rows"] = rows
    d["backups"]["failures"] = [{"vmid": r["vmid"], "name": r["name"], "t": d["buckets"][i]["start"] + 3600, "node": "pve-node-01",
                                 "reason": "the backup storage didn't answer in time", "raw": ""}
                                for r in rows for i, c in enumerate(r["days"]) if c == "fail"][:60]
    d["backups"]["not_in_job"] = [r for r in rows if not r["in_job"]]
    d["backups"]["runs_failed"] = len(d["backups"]["failures"])
    d["changes"] = [{"t": d["start"] + i * 3000, "text": f"customer-{i:03d}-application-server-with-a-long-name moved from "
                     f"pve-node-01-rack-b-long-name to pve-node-02-rack-b-long-name", "user": "automation@pve"} for i in range(40)]
    d["snapshots"] = [{"vmid": 1000 + i, "name": gl[i]["name"], "snap": f"before-upgrade-{i}-with-a-long-snapshot-name",
                       "taken": d["start"] - 100 * DAY, "age": 100 + i} for i in range(25)]
    d["logs"] = d["logs"] * 6
    return start, end, d, p


SIZES = {"empty": empty, "typical": typical, "huge": huge}


if __name__ == "__main__":  # python -m tests.fake_report OUTDIR: every template, typical week, as PDFs
    import sys
    from pathlib import Path

    from app.build import render, render_pdf

    out = Path(sys.argv[1] if len(sys.argv) > 1 else "preview")
    out.mkdir(parents=True, exist_ok=True)
    st = config.load_settings()
    st["general"]["name"] = "Example Co"
    size = sys.argv[2] if len(sys.argv) > 2 else "typical"
    kind = sys.argv[3] if len(sys.argv) > 3 else "weekly"
    which = sys.argv[4].split(",") if len(sys.argv) > 4 else list(config.TEMPLATES)
    for t in which:
        start, end, d, p = SIZES[size](kind)
        html = render(kind, start, end, d, p, st, "full", set(config.SECTION_KEYS), template=t)
        (out / f"{t}.html").write_text(html)
        issues = render_pdf(html, out / f"{t}.pdf")
        print(t, issues or "ok")
