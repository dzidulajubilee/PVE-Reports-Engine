"""Parsing and maths in collect.py, and figures worked out from the reporter's own snapshots."""

import datetime as dt

from app import config, db
from app.collect import (
    buckets,
    newest_kernel,
    parse_vzdump,
    plain_reason,
    regression,
    snap_guests,
    snapshot_coverage,
)

TZ = config.TZ


def test_vzdump_log_gives_each_guests_result():
    lines = [
        "INFO: starting new backup job: vzdump 101 110 --storage backup-nfs",
        "INFO: Starting Backup of VM 101 (qemu)",
        "INFO: Backup started at 2026-11-11 01:00:05",
        "INFO: archive file size: 36.12GB",
        "INFO: Finished Backup of VM 101 (00:01:23)",
        "INFO: Starting Backup of VM 110 (qemu)",
        "INFO: Backup started at 2026-11-11 01:04:00",
        "ERROR: Backup of VM 110 failed - got timeout",
        "INFO: Backup job finished with errors",
    ]
    r = {x["vmid"]: x for x in parse_vzdump(lines, 0, TZ)}
    assert r[101]["ok"] and r[101]["seconds"] == 83 and round(r[101]["size"] / 1e9, 2) == 36.12
    assert r[110]["ok"] is False and r[110]["reason"] == "got timeout"
    assert r[110]["t"] == int(dt.datetime(2026, 11, 11, 1, 4, tzinfo=TZ).timestamp())
    assert plain_reason("got timeout") == "the backup storage didn't answer in time"


def test_cut_off_backup_counts_as_failed():
    r = parse_vzdump(["INFO: Starting Backup of VM 120 (lxc)"], 5, TZ)
    assert r[0]["ok"] is False


def test_newest_installed_kernel():
    versions = [
        {"Package": "proxmox-kernel-6.14.8-2-pve-signed", "CurrentState": "Installed"},
        {"Package": "proxmox-kernel-6.14.11-1-pve-signed", "CurrentState": "Installed"},
        {"Package": "proxmox-kernel-6.17.1-1-pve-signed", "CurrentState": "ConfigFiles"},
        {"Package": "proxmox-ve", "RunningKernel": "6.14.8-2-pve"},
    ]
    assert newest_kernel(versions) == "6.14.11-1-pve"


def test_regression_slope():
    slope, r2 = regression([(0, 10), (86400, 11), (2 * 86400, 12)])
    assert abs(slope * 86400 - 1) < 1e-9 and r2 > 0.99


def test_buckets_are_days_then_weeks():
    s = dt.datetime(2026, 11, 9, tzinfo=TZ)
    assert [b["label"] for b in buckets(s, s + dt.timedelta(days=7))][:2] == ["Mon 9", "Tue 10"]
    assert len(buckets(s, s + dt.timedelta(days=91))) == 13


def test_restarts_and_coverage_from_snapshots():
    t0 = int(dt.datetime(2026, 1, 5, tzinfo=TZ).timestamp())
    with db.tx() as c:
        for i in range(12):
            t = t0 + i * 300
            c.execute("insert into snap_cluster values (?, 1, 1, 1)", (t,))
            uptime = 10000 + i * 300 if i < 6 else (i - 6) * 300 + 60  # restarted between rounds 5 and 6
            c.execute("insert into snap_guest values (?, 130, 'hv-01', 'qemu', 'mail', 'running', ?, 4, 8e9)", (t, uptime))
    g = snap_guests(t0, t0 + 3600)
    assert len(g[130]["restarts"]) == 1
    assert 0.99 <= snapshot_coverage(t0, t0 + 3600) <= 1.0


def test_backfill_reads_past_journals_once():
    from app import snapshot

    now = int(dt.datetime(2026, 3, 20, 12, tzinfo=TZ).timestamp())

    class Api:
        def get(self, path, **kw):
            if path == "/cluster/status":
                return [{"type": "node", "name": "pve-a", "online": 1}]
            if path == "/cluster/log":
                return [{"time": now - 3 * 86400, "node": "pve-a", "pri": 3, "tag": "pve-ha-crm", "msg": "fencing node"},
                        {"time": now - 90 * 86400, "node": "pve-a", "pri": 3, "tag": "x", "msg": "too old"}]
            if path.endswith("/journal"):
                day = dt.datetime.fromtimestamp(kw["since"], TZ)
                return ["cursor-a", f"{day:%b %d} 03:12:01 pve-a corosync[812]: [KNET] link: host: 2 link: 0 is down",
                        f"{day:%b %d} 03:12:02 pve-a sshd[1]: accepted login", "cursor-b"]
            raise AssertionError(path)

    kept, problems = snapshot.backfill(Api(), 7, now=now)
    assert kept == 8 and problems == []  # 7 corosync lines and one cluster log entry; sshd and the old entry dropped
    again, _ = snapshot.backfill(Api(), 7, now=now)
    assert again == 0
    assert int(db.get_state("logs_from")) <= now - 6 * 86400


def test_apparmor_audit_noise_is_dropped():
    from app import snapshot

    now = int(dt.datetime(2026, 3, 20, 12, tzinfo=TZ).timestamp())
    lines = ['Mar 19 03:00:01 pve-a kernel: audit: type=1400 audit(1773890401.123:42): apparmor="DENIED" '
             'operation="change_onexec" class="file" info="label not found" error=-22 profile="unconfined"',
             "Mar 19 03:00:02 pve-a kernel: ata3: SATA link down (SStatus 0 SControl 300)"]
    assert snapshot._store_journal("pve-a", lines, now) == 1


def test_logged_quorum_events_are_facts_outside_recordings():
    from app.collect import logged_events

    t0 = int(dt.datetime(2026, 2, 2, tzinfo=TZ).timestamp())
    with db.tx() as c:
        for node in ("pve-a", "pve-b"):  # both nodes log the same loss: one event
            c.execute("insert into logs values (?, 'journal', ?, 'warning', 'pmxcfs', '[status] notice: node lost quorum')",
                      (t0 + 100, node))
        c.execute("insert into logs values (?, 'journal', 'pve-a', 'info', 'pmxcfs', '[status] notice: node has quorum')",
                  (t0 + 700,))
        c.execute("insert into logs values (?, 'journal', 'pve-a', 'warning', 'pmxcfs', '[status] notice: node lost quorum')",
                  (t0 + 5000,))
    ev = logged_events(t0, t0 + 86400)
    assert [e["kind"] for e in ev] == ["lost", "back", "lost"] and ev[0]["nodes"] == ["pve-a", "pve-b"]
    assert [e["kind"] for e in logged_events(t0, t0 + 86400, recorded=[(t0 + 4000, t0 + 6000)])] == ["lost", "back"]
