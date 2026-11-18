"""collect() and build() end to end against a fake Proxmox API that answers like a small real cluster, so the
whole path from API JSON to PDF runs in the tests. Every name and number is made up."""

import datetime as dt
import time

import pytest
from app import build as build_mod
from app import config, pve
from app.collect import collect

NOW = time.time()
DAY = 86400
GIB = 1024**3


def rrd(timeframe, cf, kind="node", maxmem=256 * GIB):
    step = {"hour": 60, "day": 1800, "week": 10800, "month": 43200, "year": 21600}.get(timeframe, 3600)
    span = {"hour": 3600, "day": DAY, "week": 7 * DAY, "month": 31 * DAY, "year": 366 * DAY}.get(timeframe, 3660 * DAY)
    out = []
    t = int(NOW - span) // step * step
    while t < NOW:
        x = (t % DAY) / DAY
        p = {"time": t, "cpu": 0.1 + 0.3 * x + (0.2 if cf == "MAX" else 0)}
        if kind == "node":
            p.update(memused=maxmem * (0.5 + 0.3 * x), memtotal=maxmem)
        elif kind == "guest":
            p.update(mem=4 * GIB * (0.5 + 0.3 * x), maxmem=8 * GIB, diskwrite=1e6)
        else:
            p.update(used=1e12 * (0.5 + (t - (NOW - span)) / span * 0.1), total=2e12)
        out.append(p)
        t += step
    return out


class FakeApi:
    def __init__(self):
        self.calls = []

    def get(self, path, **kw):
        self.calls.append(path)
        nodes = ["pve-a", "pve-b"]
        if path == "/cluster/resources":
            return ([{"type": "node", "node": n, "status": "online", "uptime": 40 * DAY, "maxcpu": 16, "maxmem": 64 * GIB}
                     for n in nodes] +
                    [{"type": "qemu", "vmid": 100 + i, "name": f"vm-{i}", "node": nodes[i % 2], "status": "running",
                      "maxcpu": 4, "maxmem": 8 * GIB, "maxdisk": 50e9, "uptime": 9 * DAY} for i in range(4)] +
                    [{"type": "lxc", "vmid": 200, "name": "ct-0", "node": "pve-a", "status": "stopped", "maxcpu": 2,
                      "maxmem": 2 * GIB, "maxdisk": 8e9}] +
                    [{"type": "storage", "storage": "local-zfs", "node": n, "plugintype": "zfspool", "disk": 1.1e12,
                      "maxdisk": 2e12, "shared": 0, "status": "available", "content": "images,rootdir"} for n in nodes] +
                    [{"type": "storage", "storage": "nas", "node": n, "plugintype": "nfs", "disk": 3e12, "maxdisk": 8e12,
                      "shared": 1, "status": "available", "content": "backup"} for n in nodes])
        if path == "/cluster/status":
            return [{"type": "cluster", "name": "lab", "quorate": 1}] + [{"type": "node", "name": n, "online": 1} for n in nodes]
        if path == "/storage":
            return [{"storage": "local-zfs", "type": "zfspool", "content": "images,rootdir"},
                    {"storage": "nas", "type": "nfs", "content": "backup", "shared": 1}]
        if path.endswith("/rrddata"):
            kind = "guest" if ("/qemu/" in path or "/lxc/" in path) else "storage" if "/storage/" in path else "node"
            return rrd(kw.get("timeframe"), kw.get("cf"), kind, 64 * GIB)
        if path.endswith("/tasks") and path.startswith("/nodes/"):
            n = path.split("/")[2]
            since = kw["since"]
            return [{"upid": f"UPID:{n}:{d}", "type": "vzdump", "id": "", "node": n, "starttime": since + d * DAY + 3600,
                     "endtime": since + d * DAY + 4000, "status": "OK" if d != 2 else "job errors", "user": "root@pam"}
                    for d in range(7) if since + d * DAY + 3600 < kw["until"]] + [
                    {"upid": f"UPID:{n}:mig", "type": "qmigrate", "id": "101", "node": n, "starttime": since + 5000,
                     "endtime": since + 5100, "status": "OK", "user": "admin@pve"}]
        if "/tasks/UPID" in path:
            n, d = path.split("/")[2], int(path.rsplit(":", 1)[1].split("/")[0])
            vm = 100 if n == "pve-a" else 101
            if d == 2 and n == "pve-a":
                return [{"n": 1, "t": f"INFO: Starting Backup of VM {vm} (qemu)"},
                        {"n": 2, "t": f"ERROR: Backup of VM {vm} failed - got timeout"}]
            return [{"n": 1, "t": f"INFO: Starting Backup of VM {vm} (qemu)"},
                    {"n": 2, "t": "INFO: archive file size: 12.00GB"},
                    {"n": 3, "t": f"INFO: Finished Backup of VM {vm} (00:02:10)"}]
        if path.endswith("/content"):
            return [{"vmid": 100, "ctime": NOW - DAY, "size": 12e9, "volid": "nas:backup/vzdump-qemu-100.vma.zst"}]
        if path == "/cluster/backup":
            return [{"id": "daily", "enabled": 1, "vmid": "100,101"}]
        if path == "/cluster/backup-info/not-backed-up":
            return [{"vmid": 102, "name": "vm-2", "type": "qemu"}, {"vmid": 103, "name": "vm-3", "type": "qemu"}]
        if path.endswith("/status") and path.startswith("/nodes/"):
            return {"uptime": 40 * DAY, "pveversion": "pve-manager/9.1.2/abc", "cpuinfo": {"cpus": 16},
                    "current-kernel": {"release": "6.14.8-2-pve"}}
        if path.endswith("/apt/versions"):
            return [{"Package": "proxmox-ve", "RunningKernel": "6.14.8-2-pve"},
                    {"Package": "proxmox-kernel-6.14.11-1-pve-signed", "CurrentState": "Installed"}]
        if path.endswith("/snapshot"):
            return [{"name": "current"}, {"name": "before-upgrade", "snaptime": NOW - 90 * DAY}]
        if path == "/cluster/ha/status/current":
            return [{"type": "quorum", "status": "OK"}]
        if path.endswith("/disks/zfs"):
            return [{"name": "rpool", "health": "ONLINE"}]
        if "/disks/zfs/" in path:
            return {"scan": "scrub repaired 0B in 00:10:00 with 0 errors on Sun Nov  8 00:34:01 2026"}
        if path.endswith("/disks/list"):
            return [{"devpath": "/dev/nvme0n1", "type": "nvme", "health": "PASSED", "wearout": 85}]
        if path.endswith("/services"):
            return [{"service": "corosync", "state": "running", "unit-state": "enabled"}]
        if path.endswith("/replication"):
            return []
        if path.endswith("/time"):
            return {"time": int(time.time())}
        if path.endswith("/subscription"):
            return {"status": "notfound"}
        if path.endswith("/certificates/info"):
            return [{"filename": "pve-ssl.pem", "notafter": NOW + 20 * DAY, "subject": "/CN=pve-a.example.lan",
                     "issuer": "/CN=Proxmox Virtual Environment"},
                    {"filename": "pve-root-ca.pem", "notafter": NOW + 3000 * DAY, "subject": "/CN=Proxmox Virtual Environment"}]
        if path == "/cluster/config/nodes":
            return [{"name": "pve-a", "nodeid": "1", "ring0_addr": "192.0.2.1"}, {"name": "pve-b", "nodeid": "2", "ring0_addr": "192.0.2.2"}]
        if path.endswith("/included_volumes"):
            return {"children": [{"id": 100, "name": "vm-0", "children": [
                {"id": "100:scsi0", "included": 1, "reason": "backup=yes"}, {"id": "100:scsi1", "included": 0, "reason": "backup=no"}]}]}
        if path.endswith("/config") and "/qemu/" in path:
            return {"agent": "1"}
        if path.endswith("/agent/get-fsinfo"):
            return {"result": [{"mountpoint": "/", "type": "ext4", "total-bytes": 100 * GIB, "used-bytes": 95 * GIB},
                               {"mountpoint": "/snap/core", "type": "squashfs", "total-bytes": 2 * GIB, "used-bytes": 2 * GIB}]}
        if path.endswith("/firewall/log"):
            return [{"n": 1, "t": "0 5 - 18/Nov/2026:00:39:18 +0000 policy DROP: IN=vmbr0 SRC=203.0.113.9 DST=192.0.2.1 PROTO=TCP DPT=22"}]
        if path.endswith("/disks/lvmthin"):
            return []
        if path == "/cluster/config/qdevice":
            return {}
        raise pve.ApiError("403 Permission check failed", status=403, path=path)


@pytest.fixture
def period():
    tz = config.TZ
    end = dt.datetime.now(tz).replace(hour=0, minute=0, second=0, microsecond=0)
    return end - dt.timedelta(days=7), end


def test_collect_from_api(period):
    api = FakeApi()
    S = pve.Source(api)
    st = config.load_settings()
    d = collect(S, *period, st)
    assert [n["name"] for n in d["nodes"]] == ["pve-a", "pve-b"]
    assert d["nodes"][0]["reboot"] and d["nodes"][0]["kernel_new"] == "6.14.11-1-pve"
    assert d["nodes"][0]["cpu_avg"] is not None and d["nodes"][0]["mem_max"] is not None
    b = d["backups"]
    assert b["runs_failed"] == 1 and b["failures"][0]["reason"] == "the backup storage didn't answer in time"
    assert {r["vmid"] for r in b["not_in_job"]} == {102, 103}
    assert any(s["name"] == "nas" and s["shared"] and s["backup_only"] for s in d["storage"])
    assert d["snapshots"] and d["snapshots"][0]["snap"] == "before-upgrade"
    assert any("moved" in c["text"] for c in d["changes"])
    assert any(h["key"] == "disks" and h["tone"] == "warn" for h in d["health"])
    assert S.errors == []
    assert d["x_certs"][0]["days"] in (19, 20) and d["x_backup_jobs"]["excluded"][0]["disk"] == "scsi1"
    assert d["x_guest_disks"]["rows"][0]["pct"] == 95 and len(d["x_guest_disks"]["rows"]) == 4  # squashfs left out
    assert d["x_security"]["drops"] == 2 and d["x_security"]["top_ports"][0] == ("22", 2)
    assert d["x_cluster_net"]["links"][0]["rings"] == ["192.0.2.1"]


def test_build_end_to_end(monkeypatch, period):
    api = FakeApi()
    monkeypatch.setattr(build_mod, "pve_client", lambda st: api)
    pdfs, subject, lines, errors, pages = build_mod.build("custom", period[1], period[0])
    assert pdfs["full"].exists() and pdfs["summary"].exists()
    assert errors == 0 and pages == {"full": [], "summary": []}
    assert "backup" in " ".join(lines)
