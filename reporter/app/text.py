"""Formatting, plain-language sentences, Needs attention, comparisons, and the view every report template draws.

One collection (collect.py) feeds every template: this module turns it into ready-made strings and small lists so
the six templates differ only in markup and CSS. Wording is for a manager, not an engineer; the Briefing template
says "servers" and "systems" instead of nodes and guests.
"""

import datetime as dt
import math

from . import config

DAY = 86400
GIB = 1024**3
TYPES = {"zfspool": "ZFS", "rbd": "Ceph", "cephfs": "CephFS", "nfs": "NFS", "cifs": "SMB", "lvmthin": "LVM-thin",
         "lvm": "LVM", "dir": "Directory", "pbs": "PBS", "iscsi": "iSCSI", "zfs": "ZFS over iSCSI", "btrfs": "Btrfs",
         "esxi": "ESXi"}

# ---------------------------------------------------------------- formatting


def fbytes(b, digits=None):
    """Disk sizes in decimal units as Proxmox storage shows them: 412 GB, 11.2 TB."""
    if b is None:
        return "–"
    for unit, f in (("TB", 1e12), ("GB", 1e9), ("MB", 1e6), ("kB", 1e3)):
        if abs(b) >= f:
            v = b / f
            d = digits if digits is not None else (2 if v < 10 and unit == "TB" else 1 if v < 100 else 0)
            return f"{v:.{d}f} {unit}"
    return f"{b:.0f} B"


def fmem(b, digits=None):
    """Memory in binary units, written GB as everyone says it: 32 GB."""
    if b is None:
        return "–"
    v = b / GIB
    if v >= 1024:
        return f"{v / 1024:.1f} TB"
    d = digits if digits is not None else (1 if v < 10 else 0)
    return f"{v:.{d}f} GB"


def fpct(x, digits=0):
    return "–" if x is None else f"{x:.{digits}f}%"


def fnum(x):
    return "–" if x is None else f"{x:,.0f}"


def fduration(secs):
    if secs is None:
        return "–"
    secs = int(secs)
    h, m = divmod(secs // 60, 60)
    return f"{h}h{m:02d}m" if h else f"{m}m{secs % 60:02d}s" if m else f"{secs}s"


def fdays(days):
    """~21 d / ~3 weeks / ~5 months, rounded the way people say it."""
    if days is None:
        return "–"
    if days < 14:
        return f"{max(1, round(days))} day{'s' * (round(days) != 1)}"
    if days < 120:
        return f"{round(days / 7)} weeks"
    return f"{round(days / 30)} months"


def when(t, fmt="%a %-d %b %H:%M"):
    return dt.datetime.fromtimestamp(t, config.TZ).strftime(fmt) if t else "–"


def day(t):
    return when(t, "%a")


def date(t):
    return when(t, "%-d %b %Y")


def join(xs):
    xs = [x for x in xs if x]
    if not xs:
        return ""
    return xs[0] if len(xs) == 1 else ", ".join(xs[:-1]) + " and " + xs[-1]


def plural(n, word, many=None):
    return f"{n} {word if n == 1 else (many or word + 's')}"


def times(n):
    return {1: "once", 2: "twice"}.get(n, f"{n} times")


def storage_label(s):
    return f"{s['name']} {s['node']}" if s["node"] else s["name"]


def storage_on(s):
    return f"{s['name']} on {s['node']}" if s["node"] else s["name"]


# ---------------------------------------------------------------- comparisons


def change_count(cur, prev, word, fair, noun_more="more", noun_fewer="fewer"):
    """'1 more vs last week', 'same as last week', or '' when the earlier period can't be compared fairly."""
    if cur is None or prev is None or not fair:
        return ""
    diff = cur - prev
    if diff == 0:
        return f"same as last {word}"
    return f"{abs(diff):,.0f} {noun_more if diff > 0 else noun_fewer} vs last {word}"


def change_pts(cur, prev, word, fair=True):
    if cur is None or prev is None or not fair:
        return ""
    diff = cur - prev
    if abs(diff) < 0.05:
        return f"same as last {word}"
    return f"{'+' if diff > 0 else '−'}{abs(diff):.1f} pts vs last {word}"


def signed_pts(x):
    if x is None:
        return "–"
    if abs(x) < 0.05:
        return "0.0 pts"
    return f"{'+' if x > 0 else '−'}{abs(x):.1f} pts"


# ---------------------------------------------------------------- the view


def tone_of_storage(s, warn):
    if s["pct"] is None:
        return ""
    if s["pct"] >= 90 or (s["eta_full"] is not None and s["eta_full"] <= 30):
        return "bad"
    if s["pct"] >= warn or (s["eta_warn"] is not None and 0 < s["eta_warn"] <= 30):
        return "warn"
    return ""


def rate_text(s, warn):
    if s["pct"] is None:
        return "–"
    if s["eta_full"] is not None and (s["pct"] >= warn or s["eta_full"] <= 60):
        return f"full in ~{fdays(s['eta_full'])}"
    if s["eta_warn"] is not None and s["eta_warn"] > 0:
        return f"{warn}% in ~{fdays(s['eta_warn'])}"
    if s["per_week"] is not None and not s["even"] and not s["steady"]:
        return "growth uneven"
    if s["steady"] or (s["per_week"] is not None and s["per_week"] <= 0):
        return "steady"
    return "–"


def main_storage(storage):
    """The storage the capacity chart follows: the largest shared one for guests, else the fullest local one."""
    shared = [s for s in storage if s["shared"] and not s["backup_only"] and s["pct"] is not None]
    if shared:
        return max(shared, key=lambda s: s["total"] or 0)
    local = [s for s in storage if not s["backup_only"] and s["pct"] is not None]
    return max(local, key=lambda s: s["pct"]) if local else None


def booted(d, n):
    """When a node last started, from its uptime counter, or None."""
    return d.get("now", d["end"]) - n["uptime"] if n.get("uptime") else None


def ran_through(d, n):
    """True when the node's own uptime counter proves it ran the whole period (it started before the period did
    and hasn't restarted since): a recorded fact, not an estimate."""
    b = booted(d, n)
    return b is not None and b <= d["start"]


def chart_rows(rows, main):
    """What the capacity chart draws: shared storage when there is some, else each node's main guest storage, so
    a cluster with only local disks shows every node and a single node shows its own."""
    if not main:
        return [], ""
    if main["shared"]:
        return [main], f"{main['label']} used, last 12 weeks"
    per_node = {}
    for r in rows:
        if r["backup_only"] or r["shared"] or r["pct"] is None or not r["history"]:
            continue
        if r["node"] not in per_node or (r["total"] or 0) > (per_node[r["node"]]["total"] or 0):
            per_node[r["node"]] = r  # a node's largest guest storage is the one that matters
    lines = sorted(per_node.values(), key=lambda r: -r["pct"])[:8]
    if len(lines) <= 1:
        return [main], f"{main['label']} used, last 12 weeks"
    return lines, "Guest storage per node, last 12 weeks"


def view(d, p, kind, settings, on=None):
    """Everything a template shows, worked out once. d: this period, p: the period before (light collection)."""
    word = {"weekly": "week", "monthly": "month", "quarterly": "quarter"}.get(kind, "period")
    this = f"this {word}" if kind != "custom" else "in this period"
    warn_pct = int(settings["general"].get("storage_warn", 80))
    g = d["guests"]
    b = d["backups"]
    nodes = d["nodes"]
    n_nodes = len(nodes)
    cov, pcov = d["coverage"], p.get("coverage", 0)
    fair_rates = pcov >= 0.7  # peaks and shares; counts need the snapshot at the period end
    v = {"word": word, "this": this, "warn_pct": warn_pct, "snap_days": settings["general"].get("snapshot_days", 30),
         "_on": set(config.SECTION_KEYS) if on is None else on}

    # ---- storage rows
    rows = []
    for s in d["storage"]:
        tone = tone_of_storage(s, warn_pct)
        rows.append({**s, "label": storage_label(s), "on": storage_on(s), "type_label": TYPES.get(s["type"], s["type"]),
                     "pct_text": fpct(s["pct"]), "pct1": fpct(s["pct"], 1),
                     "size_text": f"{fbytes(s['used'])} / {fbytes(s['total'])}" if s["used"] is not None else "–",
                     "size_short": fbytes(s["total"]),
                     "change_text": signed_pts(s["change_pts"]), "rate": rate_text(s, warn_pct), "tone": tone,
                     "eta_warn_text": ("passed" if s["eta_warn"] == 0 else f"~{s['eta_warn']:.0f}d") if s["eta_warn"] is not None else "–",
                     "eta_full_text": f"~{s['eta_full']:.0f}d" if s["eta_full"] is not None else "–",
                     "per_week_text": signed_pts(s["per_week"]).replace(" pts", "") if s["per_week"] is not None else "–"})
    v["storage"] = rows
    main = main_storage(d["storage"])
    v["main"] = next((r for r in rows if main and r["name"] == main["name"] and r["node"] == main["node"]), None)
    v["chart_rows"], v["chart_caption"] = chart_rows(rows, v["main"])

    # ---- figures
    pm = None
    if main:
        pm = next((s for s in p.get("storage", []) if s["name"] == main["name"] and s["node"] == main["node"]), None)
    gr, pgr = d.get("guests_running"), p.get("guests_running")
    figs = []
    single = not d.get("topology", {}).get("cluster", True)
    figs.append({"key": "available", "label": "Server up" if single else "Cluster available", "value": fpct(d["available_pct"], 0 if (d["available_pct"] or 0) >= 99.95 or d["available_pct"] is None else 2),
                 "of": "", "change": change_pts(d["available_pct"], p.get("available_pct"), word, fair_rates and cov >= 0.7)
                 .replace(" pts", " pts") if d["available_pct"] is not None else "not recorded yet", "tone": ""})
    if d["available_pct"] is not None and p.get("available_pct") is not None and fair_rates and abs(d["available_pct"] - p["available_pct"]) < 0.05:
        figs[-1]["change"] = f"same as last {word}"
    if d["available_pct"] is not None and (d["available_pct"] < 99.9):
        figs[-1]["tone"] = "bad"
    lost_logged = [e for e in d.get("logged", []) if e["kind"] == "lost"]
    if d["available_pct"] is None and single and d["nodes"] and ran_through(d, d["nodes"][0]):
        # one server whose uptime counter covers the whole period: it was up all of it
        figs[-1].update(value="100%", change=f"running since {when(booted(d, d['nodes'][0]), '%-d %b')} (uptime)")
    elif d["available_pct"] is None:  # nothing recorded: no percentage, only what the nodes logged
        if lost_logged:
            figs[-1].update(change=f"quorum loss logged {times(len(lost_logged))}", tone="bad")
        elif d.get("logs_cover"):
            figs[-1]["change"] = "not recorded; no quorum loss logged"
        else:
            figs[-1]["change"] = "not recorded before install"
    elif cov < 0.95 and not figs[-1]["change"]:
        figs[-1]["change"] = f"over the {cov:.0%} recorded"
    figs.append({"key": "guests", "label": "Guests running", "value": fnum(gr["running"]) if gr else "–",
                 "of": f"of {gr['total']}" if gr else "",
                 "change": "running now; history starts at install" if gr and gr.get("now") else
                 change_count(gr["running"] if gr else None, pgr["running"] if pgr else None, word, bool(pgr) and not pgr.get("now")),
                 "tone": ""})
    bc = b["runs_failed"]
    pbc = p.get("backups", {}).get("runs_failed")
    bchange = ""
    if bc and pbc is not None:
        bchange = f"{bc} failed vs {'none' if not pbc else pbc} last {word}"
    elif bc:
        bchange = f"{bc} failed"
    elif pbc is not None:
        bchange = change_count(b["runs_ok"], p["backups"]["runs_ok"], word, True)
    figs.append({"key": "backups", "label": "Backups that ran", "value": fnum(b["runs_ok"]),
                 "of": f"of {b['runs_total']}", "change": bchange, "tone": "bad" if bc else ""})
    figs.append({"key": "storage", "label": "Shared storage used" if main and main["shared"] else "Fullest guest storage",
                 "value": fpct(main["pct"]) if main else "–", "of": "",
                 "change": change_pts(main["pct"] if main else None, pm["pct"] if pm else None, word), "tone": ""})
    v["figs"] = figs
    v["fig"] = {f["key"]: f for f in figs}

    # ---- attention
    att = attention(d, v, word, this, warn_pct, n_nodes)
    v["attention"] = att
    v["att_top"], v["att_more"] = att[:4], att[4:]
    v["bad"] = [a for a in att if a["level"] == "bad"]
    v["warn"] = [a for a in att if a["level"] == "warn"]

    # ---- sentences
    v["headline"], v["subline"] = sentences(d, v, word, this, n_nodes)
    v["plain_line"] = plain_line(d, v)
    v["guest_line"] = guest_line(g, word)
    v["backup_line"] = backup_line(b, word, this)
    v["capacity_line"] = capacity_line(v["main"], warn_pct, word)

    # ---- nodes
    nrows = []
    for n in nodes:
        nrows.append({**n, "cpu_text": f"{fpct(n['cpu_avg'])} · {fpct(n['cpu_max'])}",
                      "mem_text": f"{fpct(n['mem_avg'])} · {fpct(n['mem_max'])}", "mem_hot": (n["mem_max"] or 0) >= 90,
                      "local_text": fpct(n["local_pct"]), "local_hot": (n["local_pct"] or 0) >= warn_pct,
                      "up_text": fdays_short(n["uptime"]), "up_long": up_long(n["uptime"]),
                      "ratio": f"{n['vcpu'] / n['cores']:.1f}×" if n["cores"] else "–",
                      "ver_text": (n["pve"] or "–") + (" · reboot due" if n["reboot"] else ""),
                      "ram_given_text": fmem(n["ram_given"]), "ram_total_text": fmem(n["ram_total"]),
                      "guests_text": f"{plural(n['guests'], 'guest')} · {n['vcpu']} vCPU on {n['cores'] or '–'} cores"})
    v["nodes"] = nrows
    v["memory_note"] = memory_note(d, word)
    v["writes_note"] = writes_note(d, v)

    # ---- guests
    v["top"] = [{**x, "cpu_text": fpct(x["cpu_avg"]), "mem_text": f"{fmem(x['mem_avg'])} of {fmem(x['maxmem'], 0)}",
                 "disk_text": fbytes(x["disk_written"])} for x in g["top"][:6]]
    v["top_more"] = max(0, len(g["top"]) - 6)
    v["oversized"] = [{**x, "mem_text": f"{fmem(x['mem_max'])} of {fmem(x['maxmem'], 0)}",
                       "cpu_text": f"{x['cpu_peak_vcpu']:.1f} of {x['cpus']}" if x["cpu_peak_vcpu"] is not None else "–"}
                      for x in g["oversized"][:6]]
    free_mem = sum(x["free_mem"] for x in g["oversized"])
    free_cpu = sum(x["free_cpu"] for x in g["oversized"])
    v["oversized_note"] = (f"Right-sizing these frees {fmem(free_mem, 0)} and {free_cpu} vCPU"
                           if g["oversized"] and (free_mem or free_cpu) else "")
    events = []
    for x in g["restarted"]:
        stamps = [when(t, "%a %H:%M") for t in x["restarts"][:3]]
        events.append({**x, "what": "Restarted " + join(stamps), "tone": "warn"})
    for x in g["stopped"]:
        since = f"Stopped since {when(x['stopped_since'], '%-d %b')}" if x["stopped_since"] else f"Stopped all {word}"
        events.append({**x, "what": since, "tone": ""})
    v["events"] = events[:8]
    v["stopped_note"] = (f"The stopped guests still hold {fbytes(g['stopped_disk'])} of disk"
                         if len(g["stopped"]) > 1 and g["stopped_disk"] else
                         f"The stopped guest still holds {fbytes(g['stopped_disk'])} of disk" if g["stopped"] and g["stopped_disk"] else "")
    v["snapshots"] = [{**s, "taken_text": date(s["taken"]), "age_text": f"{s['age']} days"} for s in d["snapshots"][:10]]
    v["changes"] = [{**c, "day": when(c["t"], "%a %-d %b"), "user": c["user"] or ""} for c in d["changes"][:12]]

    # ---- backups
    v["backup_rows"] = backup_rows(b)
    v["failures"] = failure_groups(b["failures"])
    v["nights"] = [{**n, "size_text": fbytes(n["size"]) if n["size"] else "–", "dur_text": fduration(n["seconds"])}
                   for n in b["nights"]]
    v["buckets"] = d["buckets"]
    v["night_labels"] = [when(b["start"], "%a %m-%d").lower() if len(d["buckets"]) <= 31 else b["label"].lower()
                         for b in d["buckets"]]
    v["health"] = d["health"]
    v["health_ok"] = sum(1 for h in d["health"] if h["tone"] == "ok")
    v["logs_from_text"] = when(d.get("logs_from"), "%a %-d %b %H:%M") if d.get("logs_from") else ""
    v["logs"] = [{**lg, "when": when(lg["t"], "%a %-d %b %H:%M")} for lg in d["logs"][:12]]
    v["pbs"] = pbs_view(d.get("pbs"))
    v["timeline"] = timeline(d, v, warn_pct)
    v["rack"] = rack_units(d, v)
    v["heat"] = heat_rows(d)
    v["pulse_line"] = pulse_line(d, word)
    v["findings"] = findings(d, v)
    v["areas"] = areas(d, v, word, n_nodes)
    v["recommend"] = recommend(d, v, word, n_nodes)
    v["verdict"] = verdict(v)
    if d.get("_errors", 0) >= 3:
        v["verdict"] = "This report is incomplete. Some figures couldn't be read from Proxmox."
    v["coverage_note"] = coverage_note(d, word)
    v["counts"] = {"ok": v["health_ok"] + sum(1 for r in rows if not r["tone"]) +
                   sum(1 for n in nodes if n["online"] and not n["down"] and (n["mem_max"] or 0) < 90),
                   "warn": sum(1 for f in v["findings"] if f["level"] == "warn"),
                   "fail": sum(1 for f in v["findings"] if f["level"] == "fail")}
    return v


def fdays_short(secs):
    if not secs:
        return "–"
    d = secs / DAY
    return f"{d:.0f}d" if d >= 1 else f"{secs / 3600:.0f}h"


def up_long(secs):
    if not secs:
        return "–"
    d = secs / DAY
    return f"{d:.0f} days" if d >= 2 else f"{secs / 3600:.0f} hours"


def attention(d, v, word, this, warn_pct, n_nodes):
    """Only what is actually wrong or unusual, most serious first: what happened, where and how much."""
    out = []

    def add(level, area, text, short, aside="", t=None, ledger=None, plain=None, head=None):
        out.append({"level": level, "area": area, "text": text, "short": short, "aside": aside, "t": t,
                    "ledger": ledger or text, "plain": plain or text, "head": head or short})

    if d["quorum_lost"]:
        mins = sum(b - a for a, b in d["quorum_lost"]) / 60
        add("bad", "availability", f"The cluster lost quorum {times(len(d['quorum_lost']))} for {fmin(mins)} in total",
            f"Quorum lost for {fmin(mins)}", when(d["quorum_lost"][0][0], "%a %H:%M"), d["quorum_lost"][0][0],
            head=f"the cluster lost quorum for {fmin(mins)}",
            plain=f"The servers lost contact with each other for {fmin(mins)}")
    for e in d.get("logged", []):
        who = f" (logged by {join(e['nodes'])})" if e["nodes"] else ""
        if e["kind"] == "lost":
            back = next((x for x in d["logged"] if x["kind"] == "back" and x["t"] > e["t"]), None)
            until = f", back {when(back['t'], '%a %H:%M')}" if back else ""
            add("bad", "availability", f"Quorum lost {when(e['t'], '%a %-d %b %H:%M')}{until}{who}",
                f"Quorum lost {when(e['t'], '%a %H:%M')}", "logged", e["t"], head="the cluster lost quorum",
                plain=f"The servers lost contact with each other on {when(e['t'], '%a %-d %b at %H:%M')}")
        elif e["kind"] == "fence":
            add("bad", "availability", f"Fencing logged {when(e['t'], '%a %-d %b %H:%M')}{who}",
                f"Fencing {when(e['t'], '%a %H:%M')}", "logged", e["t"], head="a node was fenced",
                plain=f"A server was forcibly restarted by high availability on {when(e['t'], '%a %-d %b')}")
    for n in v_nodes_down(d):
        add("bad", "availability", f"{n['name']} was down for {fmin(n['down_s'] / 60)}", f"{n['name']} down {fmin(n['down_s'] / 60)}",
            when(n["down"][0][0], "%a %H:%M"), n["down"][0][0], plain=f"Server {n['name']} was down for {fmin(n['down_s'] / 60)}")
    for n in d["nodes"]:
        if not n["online"] and not n["down"]:
            add("bad", "availability", f"{n['name']} is offline", f"{n['name']} offline", "now",
                plain=f"Server {n['name']} is offline")
    for s in v["storage"]:
        if s["tone"] not in ("bad", "warn") or s["backup_only"] and s["tone"] != "bad":
            continue
        grow = f", up {s['change_pts']:.1f} points {this}" if s["change_pts"] and s["change_pts"] >= 0.5 else ""
        aside = s["rate"] if s["rate"] not in ("–", "steady") else ""
        add(s["tone"], "capacity", f"{storage_on(s)} is {s['pct']:.0f}% full{grow}",
            f"{s['label']} {s['pct']:.0f}%" + (f", {aside}" if aside else ""), aside, d["end"] - 1,
            ledger=f"{s['label'].replace(' ', '/')} {s['pct']:.1f}%" + (f" ({signed_pts(s['per_week'])}/wk)" if s["per_week"] else "") +
            (f" → 100% in {s['eta_full_text']}" if s["eta_full"] else ""),
            plain=f"{'Shared storage' if s['shared'] else 'Disk on ' + s['node']} is {s['pct']:.0f}% full" +
            (f" and will be full in about {fdays(s['eta_full'])}" if s["eta_full"] else ""),
            head=("storage on " + s["node"] if s["node"] else s["name"]) +
            (" is nearly full" if s["pct"] >= 85 or (s["eta_full"] or 999) <= 30 else " is filling up"))
    b = d["backups"]
    if b["runs_failed"]:
        f = b["failures"]
        by = {}
        for x in f:
            by.setdefault(x["name"], []).append(day(x["t"]))
        parts = [f"{name} on {join(sorted(set(days), key=days.index))}" for name, days in list(by.items())[:3]]
        more = len(by) - 3
        add("bad", "backups", f"{plural(len(f), 'backup')} failed: {join(parts)}" + (f" and {more} more" if more > 0 else ""),
            f"{plural(len(f), 'backup')} failed: " + join(f"{n}" + (f" ×{len(dd)}" if len(dd) > 1 else "") for n, dd in list(by.items())[:3]),
            "page 4" if "backups" in v.get("_on", {"backups"}) else "", f[0]["t"],
            plain=f"{plural(len(f), 'backup')} did not complete",
            head=f"{len(f)} {'nightly ' if word == 'week' else ''}backup{'s' * (len(f) != 1)} failed")
    for r in b.get("stale", [])[:3]:
        add("bad", "backups", f"{r['name']} ({r['vmid']}) had no successful backup {this}", f"{r['name']}: no good backup",
            f"last {when(r['latest'], '%-d %b')}" if r["latest"] else "none recently",
            plain=f"System {r['name']} had no successful backup {this}", head=f"{r['name']} has no recent backup")
    nij = [r for r in b.get("not_in_job", []) if r["running"]] or b.get("not_in_job", [])
    if nij:
        if len(nij) == 1:
            r = nij[0]
            add("warn", "backups", f"{r['name']} ({r['vmid']}) is in no backup job", f"{r['name']} in no backup job",
                "no recent backup" if not r["latest"] else f"last {when(r['latest'], '%-d %b')}",
                plain=f"One system ({r['name']}) has no recent backup" if not r["latest"] else
                f"System {r['name']} is not in any backup schedule")
        else:
            add("warn", "backups", f"{len(nij)} guests are in no backup job: {join([r['name'] for r in nij[:4]])}" +
                (" and more" if len(nij) > 4 else ""), f"{len(nij)} guests in no backup job", "",
                plain=f"{len(nij)} systems are not in any backup schedule")
    for r in b.get("verify_failed", [])[:2]:
        add("bad", "backups", f"The latest backup of {r['name']} failed verification", f"{r['name']} backup unverified")
    for h in d["health"]:
        if h["key"] in ("zfs", "ceph", "disks", "ha", "replication", "services") and h["tone"] in ("bad", "warn"):
            label = {"zfs": "ZFS", "ceph": "Ceph", "disks": "Disks", "ha": "HA", "replication": "Replication",
                     "services": "Services"}[h["key"]]
            add(h["tone"], "health", f"{h['what']}: {h['state']}", f"{label}: {h['state']}"[:44],
                plain=f"{h['what']}: {h['state']}")
    g = d["guests"]
    for x in g["restarted"][:3]:
        add("warn", "availability", f"{x['name']} restarted {times(len(x['restarts']))} on its own",
            f"{x['name']} restarted {times(len(x['restarts']))}", join([when(t, "%a %H:%M") for t in x["restarts"][:2]]),
            x["restarts"][0], plain=f"System {x['name']} restarted unexpectedly {times(len(x['restarts']))}")
    reboot = [n["name"] for n in d["nodes"] if n["reboot"]]
    if reboot:
        kn = next(n["kernel_new"] for n in d["nodes"] if n["reboot"])
        add("warn", "maintenance", f"{join(reboot)} need{'s' if len(reboot) == 1 else ''} a reboot to run the installed kernel",
            f"{join(reboot)} need{'s' if len(reboot) == 1 else ''} a reboot", kn.replace("-pve", "").rsplit("-", 1)[0],
            d["end"] - 1, ledger=f"{', '.join(reboot)} running {d['nodes'][0]['kernel'] or '?'}, installed {kn} → reboot",
            plain=f"{plural(len(reboot), 'server')} need{'s' if len(reboot) == 1 else ''} a restart to finish an update")
    for n in d["nodes"]:
        if (n["mem_max"] or 0) >= 90:
            add("warn", "capacity", f"{n['name']} memory peaked at {n['mem_max']:.0f}%", f"{n['name']} memory peak {n['mem_max']:.0f}%",
                when(n["mem_max_at"], "%a %H:%M") if n["mem_max_at"] else "", n["mem_max_at"],
                ledger=f"{n['name']} memory peak {n['mem_max']:.1f}%",
                plain=f"Server {n['name']} briefly ran short of memory ({n['mem_max']:.0f}%)")
    pbs = d.get("pbs")
    if pbs and not pbs.get("error"):
        for s in pbs["stores"]:
            if s["full_date"] and s["full_date"] - d["end"] < 30 * DAY:
                add("bad", "backups", f"Backup server datastore {s['name']} is full by {date(s['full_date'])}",
                    f"PBS {s['name']} full {when(s['full_date'], '%-d %b')}")
            elif (s["pct"] or 0) >= warn_pct:
                add("warn", "backups", f"Backup server datastore {s['name']} is {s['pct']:.0f}% full", f"PBS {s['name']} {s['pct']:.0f}%")
        nf = sum(k["failed"] for k in pbs["kinds"].values())
        if nf:
            kinds = join([k for k, x in pbs["kinds"].items() if x["failed"]])
            add("bad", "backups", f"{plural(nf, 'backup-server job')} failed ({kinds})", f"{nf} PBS jobs failed")
    elif pbs and pbs.get("error"):
        add("warn", "backups", "The backup server couldn't be reached", "PBS not reachable")
    for h in d["health"]:
        if h["key"] in ("clock",) and h["tone"] == "warn":
            add("warn", "health", f"Clock: {h['state']}", "Clock out of sync")
    order = {"bad": 0, "warn": 1}
    out.sort(key=lambda a: order[a["level"]])
    return out


def v_nodes_down(d):
    return [n for n in d["nodes"] if n["down"]]


def fmin(m):
    if m < 1:
        return "under a minute"
    if m < 90:
        return f"{m:.0f} minutes"
    h = m / 60
    return f"{h:.1f} hours" if h < 48 else f"{h / 24:.1f} days"


def sentences(d, v, word, this, n_nodes):
    bad, warn = v["bad"], v["warn"]
    rough = any(a["area"] == "availability" and a["level"] == "bad" for a in bad)
    mood = "A rough" if rough else "A steady"
    missing = d.get("_errors", 0)
    if missing >= 3:  # never call a week quiet when most of it couldn't be read
        head = (f"This report is incomplete: {missing} requests to Proxmox failed, so some figures show as –."
                + (f" {plural(len(bad), 'problem')} found in what could be read." if bad else ""))
    elif not bad and not warn:
        head = f"A quiet {word}: nothing needs attention."
    elif not bad:
        head = f"{mood} {word} with nothing urgent and {plural(len(warn), 'thing')} to plan."
    else:
        bits = [a["head"] for a in bad[:2]]
        head = f"{mood} {word} with {plural(len(bad), 'thing')} to fix: {join(bits)}."
    facts = []
    nodes = d["nodes"]
    single = not d.get("topology", {}).get("cluster", True)
    q = "lost quorum" if d["quorum_lost"] else "kept quorum" if d["coverage"] >= 0.5 else None
    down = [n for n in nodes if n["down"]]
    nodes_part = (f"{join([n['name'] for n in down])} went down" if down else
                  ("every node stayed up" if d["coverage"] >= 0.5 else None))
    through = [n for n in nodes if ran_through(d, n)]
    if single and d["coverage"] >= 0.5:
        facts.append(f"{nodes[0]['name']} went down." if down else "The server stayed up.")
    elif single and nodes and through:
        facts.append(f"The server has been running since {when(booted(d, nodes[0]), '%-d %b')}, by its uptime counter.")
    elif q and nodes_part:
        facts.append(f"The cluster {q} and {nodes_part}.")
    elif single:
        facts.append(f"The reporter didn't record all of this {word}, and the server restarted on "
                     f"{when(booted(d, nodes[0]), '%a %-d %b')}." if nodes and booted(d, nodes[0]) else
                     f"The reporter didn't record this {word}.")
    elif d["coverage"] < 0.5 and through and len(through) == len(nodes):
        lost_logged = [e for e in d.get("logged", []) if e["kind"] == "lost"]
        tail = (f"; Proxmox logged {plural(len(lost_logged), 'quorum loss', 'quorum losses')}" if lost_logged else
                "; Proxmox logged no quorum loss or fencing" if d.get("logs_cover") else "")
        facts.append(f"Every node has been running since before this {word} began, by their uptime counters{tail}.")
    elif d["coverage"] < 0.5:
        rec = "didn't record" if d["coverage"] == 0 else f"recorded only {d['coverage']:.0%} of"
        lost_logged = [e for e in d.get("logged", []) if e["kind"] == "lost"]
        if lost_logged:
            facts.append(f"The reporter {rec} this {word}; Proxmox logged {plural(len(lost_logged), 'quorum loss', 'quorum losses')}.")
        elif d.get("logs_cover"):
            facts.append(f"The reporter {rec} this {word}; Proxmox logged no quorum loss or fencing.")
        else:
            facts.append(f"The reporter {rec} this {word}, so there are no uptime figures for it.")
    g = d["guests"]
    if g["ran_all"] is not None:
        part = f"{g['ran_all']} of {g['total']} guests ran all {word}"
        restarted = g["restarted"]
        if restarted:
            r = restarted[0]
            part += f"; {r['name']} restarted {times(len(r['restarts']))} on its own"
            if len(restarted) > 1:
                part += f" and {plural(len(restarted) - 1, 'other')} too"
        facts.append(part + ".")
    else:
        facts.append(f"{g['running']} of {g['total']} guests are running.")
    return head, " ".join(facts)


def plain_line(d, v):
    """Briefing's intro, without technical words: what stayed up, then the most serious problems."""
    av = [a for a in v["bad"] if a["area"] == "availability"]
    first = av[0]["plain"] + "." if av else ("Everything stayed up." if d["coverage"] >= 0.5 else
                                            "Everything is up now; uptime history starts with this report.")
    rest = [a["plain"] for a in v["bad"] if a["area"] != "availability"][:2]
    if not rest:
        rest = [a["plain"] for a in v["warn"]][:1]
    if rest:
        rest = [rest[0]] + [r[0].lower() + r[1:] for r in rest[1:]]
        return first + " " + ", and ".join(rest) + "."
    return first + " Nothing needs a decision."


def _lower(s):
    return s[0].lower() + s[1:] if s and not s[:2].isupper() and not s.split(" ")[0].count("-") else s


def guest_line(g, word):
    s = f"{plural(g['total'], 'guest')}: {plural(g['vms'], 'VM')} and {plural(g['cts'], 'container')}."
    bits = []
    if g["ran_all"] is not None:
        bits.append(f"{g['ran_all']} ran all {word}")
    for r in g["restarted"][:2]:
        bits.append(f"{r['name']} restarted {times(len(r['restarts']))} on its own")
    if g["stopped"]:
        bits.append(f"{len(g['stopped'])} {'is' if len(g['stopped']) == 1 else 'are'} stopped")
    if bits:
        s += " " + join(bits)[0].upper() + join(bits)[1:] + "."
    return s


def backup_line(b, word, this):
    if not b["runs_total"]:
        return f"No backups ran {this}." + (" No backup jobs are set up." if not b.get("jobs") else "")
    s = f"{b['runs_ok']} of {b['runs_total']} backups ran."
    in_job = [r for r in b.get("rows", []) if r["in_job"] and r["running"]]
    if in_job and b.get("rows"):
        newest_ok = min((r["latest"] or 0) for r in in_job)
        if newest_ok:
            age = (b["rows"] and max(r["latest"] or 0 for r in b["rows"])) - newest_ok
            days = max(1, math.ceil(age / DAY + 0.01))
            if not b.get("stale"):
                s += f" Every guest in a job has a good backup from the last {'day' if days == 1 else plural(days, 'day')}."
    nij = b.get("not_in_job", [])
    if len(nij) == 1:
        s += f" {nij[0]['name']} is in no backup job" + ("" if nij[0]["latest"] else " and has no recent backup") + "."
    elif nij:
        s += f" {len(nij)} guests are in no backup job."
    return s


def capacity_line(main, warn_pct, word):
    if not main:
        return "No storage figures could be read."
    who = "Shared storage" if main["shared"] else f"The fullest guest storage, {main['on']},"
    if main["per_week"] is None:
        return f"{who} is {main['pct_text']} full."
    if main["steady"] or main["per_week"] <= 0:
        return f"{who} use was steady at {main['pct_text']}."
    s = f"{who} grows about {main['per_week']:.1f} points a week."
    if main["eta_warn"] and main["even"]:
        target = dt.datetime.fromtimestamp(main["history"][-1][0] if main["history"] else 0, config.TZ) + dt.timedelta(days=main["eta_warn"])
        s += f" At that rate {main['name']} reaches {warn_pct}% around {target:%-d %b %Y}."
    elif not main["even"]:
        s += " Growth is uneven, so there's no estimate of when it fills up."
    return s


def memory_note(d, word):
    nodes = [n for n in d["nodes"] if n["mem_max"] is not None]
    if not nodes:
        return ""
    hot = max(nodes, key=lambda n: n["mem_max"])
    if hot["mem_max"] < 85:
        return ""
    s = f"{hot['name']} peaked at {hot['mem_max']:.0f}% memory" + (f" on {when(hot['mem_max_at'], '%a %-d %b at %H:%M')}" if hot["mem_max_at"] else "") + "."
    # a guest whose move would bring the peak under 80% and fits on the node with the most room
    others = [n for n in nodes if n is not hot and n["online"] and n["ram_total"]]
    if others and hot["ram_total"]:
        target = min(others, key=lambda n: n["mem_max"])
        need = (hot["mem_max"] - 80) / 100 * hot["ram_total"]
        cands = [x for x in d["guests"]["list"] if x["node"] == hot["name"] and x["status"] == "running" and x["mem_max"]]
        for x in sorted(cands, key=lambda x: x["mem_max"]):
            if x["mem_max"] >= need and target["mem_max"] + 100 * (x["maxmem"] or 0) / target["ram_total"] < 80:
                s += f" Moving {x['name']} to {target['name']} would keep it under 80%."
                break
    return s


def writes_note(d, v):
    hot = [s for s in v["storage"] if s["tone"] and s["node"]]
    if not hot:
        return ""
    s = hot[0]
    cands = [x for x in d["guests"]["list"] if x["node"] == s["node"] and x["disk_written"]]
    if not cands:
        return ""
    x = max(cands, key=lambda x: x["disk_written"])
    return f"Most writes on {s['node']} came from {x['name']} ({x['vmid']}): {fbytes(x['disk_written'])} written {v['this']}"


def backup_rows(b):
    rows = []
    for r in b.get("rows", []):
        if not r["running"] and not any(x in ("ok", "fail") for x in r["days"]) and r["in_job"]:
            continue  # a stopped guest nobody backs up any more is clutter; it's listed under stopped guests
        rows.append({**r, "latest_text": when(r["latest"], "%-d %b %H:%M") if r["latest"] else (
                         "over a month ago" if r["in_job"] and r.get("looked_back") else "none recently"),
                     "size_text": fbytes(r["size"]) if r["size"] else "–", "late": not r["in_job"] or not r["latest"]})
    return rows


def failure_groups(failures):
    """One line per guest and reason: 'ci-runner · Thu, Fri' -> 'not enough space ...'."""
    out = {}
    for f in failures:
        k = (f["name"], f["reason"])
        out.setdefault(k, []).append(f)
    rows = []
    for (name, reason), fs in out.items():
        when_ = when(fs[0]["t"], "%a %H:%M") if len(fs) == 1 else ", ".join(day(x["t"]) for x in fs[:4])
        rows.append({"name": name, "when": when_, "reason": reason[0].upper() + reason[1:], "count": len(fs),
                     "full_when": when(fs[0]["t"], "%a %-d %b %H:%M") if len(fs) == 1 else
                     ", ".join(when(x["t"], "%a %-d") for x in fs[:4]) + when(fs[0]["t"], " %b"), "t": fs[0]["t"],
                     "vmid": fs[0]["vmid"]})
    return rows


def pbs_view(pbs):
    if not pbs:
        return None
    return {**pbs, "stores": [{**s, "pct_text": fpct(s["pct"]), "size_text": f"{fbytes(s['used'])} / {fbytes(s['total'])}",
                               "full_text": date(s["full_date"]) if s["full_date"] else "not in sight"} for s in pbs["stores"]],
            "failed": [{**f, "when": when(f["t"], "%a %-d %b %H:%M")} for f in pbs["failed"]]}


def coverage_note(d, word):
    c = d["coverage"]
    if c >= 0.95:
        return ""
    if c == 0:
        return (f"The reporter wasn't recording yet during this {word}, so uptime, restarts and quorum aren't shown. "
                "Figures from Proxmox's own history (load, storage, backups) are complete.")
    return (f"The reporter recorded {c:.0%} of this {word}. Uptime, restarts and quorum cover only that part; "
            "comparisons with the previous period are left out where they wouldn't be fair.")


# ---------------------------------------------------------------- per-template pieces


def timeline(d, v, warn_pct):
    """Logbook's 'The week': a lane per node with up-bars and marks, then backups and restarts."""
    bks = d["buckets"]
    n = len(bks)
    s_, e_ = d["start"], d["end"]
    span = max(1, e_ - s_)
    wide = n <= 8

    def pos(t):
        i = next((k for k, b in enumerate(bks) if b["start"] <= t < b["end"]), n - 1)
        return 100 * i / n

    lanes = []
    for node in d["nodes"]:
        # up = recorded and not down; time nobody recorded is drawn as such, never as up
        segs = []
        for ra, rb in d.get("recorded", [(s_, e_)] if d["coverage"] >= 0.95 else []):
            last = ra
            for a, b in node["down"]:
                if b <= ra or a >= rb:
                    continue
                if a > last:
                    segs.append((last, a))
                last = max(last, b)
            if last < rb:
                segs.append((last, rb))
        bars = [{"left": 100 * (a - s_) / span, "width": max(0.5, 100 * (b - a) / span)} for a, b in segs]
        unrec, last = [], s_
        for ra, rb in d.get("recorded", [(s_, e_)] if d["coverage"] >= 0.95 else []):
            if ra > last:
                unrec.append((last, ra))
            last = max(last, rb)
        if last < e_:
            unrec.append((last, e_))
        boot = booted(d, node)
        if boot is not None and boot < e_:  # its uptime counter proves it ran from boot onwards
            segs.append((max(s_, boot), e_))
            bars = [{"left": 100 * (a - s_) / span, "width": max(0.5, 100 * (b - a) / span)} for a, b in segs]
            unrec = [(a, min(b, boot)) for a, b in unrec if a < boot]
        norec = [{"left": 100 * (a - s_) / span, "width": 100 * (b - a) / span} for a, b in unrec if b - a > 900]
        marks = {}

        def mark(t, text, level="", marks=marks):
            i = int(pos(t) * n / 100 + 0.001)
            rank = {"bad": 2, "warn": 1, "": 0}
            if i not in marks or rank[level] > rank[marks[i]["level"]]:
                marks[i] = {"left": 100 * i / n, "text": text[:16] if wide else "", "level": level, "end": i == n - 1}

        for a, b in node["down"]:
            mark(a, f"down {fmin((b - a) / 60).replace(' minutes', ' min')}", "bad")
        for t in node["reboots"]:
            mark(t, "restarted", "")
        for c in d["changes"]:
            if "moved from" in c["text"]:
                name = c["text"].split(" moved")[0]
                if f"from {node['name']}" in c["text"]:
                    mark(c["t"], f"{name} moved out")
                elif c["text"].endswith(f"to {node['name']}"):
                    mark(c["t"], f"{name} in")
        if (node["mem_max"] or 0) >= 90 and node["mem_max_at"]:
            mark(node["mem_max_at"], f"memory {node['mem_max']:.0f}%", "warn")
        for s in v["storage"]:
            if s["node"] == node["name"] and s["tone"] and s["history"]:
                hist = [h for h in s["history"] if h[0] >= s_]
                crossed = next((h for h in hist if h[1] >= warn_pct), None)
                if crossed:
                    mark(crossed[0], f"{s['name']} {crossed[1]:.0f}%", "warn")
                if hist and s["tone"] == "bad":
                    mark(hist[-1][0], f"{hist[-1][1]:.0f}%", "bad")
        for e in d.get("logged", []):
            if node["name"] in e["nodes"] and e["kind"] in ("lost", "fence"):
                mark(e["t"], "quorum lost" if e["kind"] == "lost" else "fenced", "bad")
        lanes.append({"name": node["name"], "bar": True, "bars": bars, "norec": norec,
                      "marks": sorted(marks.values(), key=lambda m: m["left"])})
    nights = d["backups"]["nights"]
    bmarks = []
    for i, nt in enumerate(nights):
        if nt["jobs"]:
            bmarks.append({"left": 100 * i / n, "text": f"{nt['ok']}/{nt['jobs']}" if wide else "",
                           "level": "bad" if nt["failed"] else "ok", "end": False})
    if bmarks or d["backups"]["runs_total"]:
        lanes.append({"name": "Backups", "bar": False, "bars": [], "marks": bmarks, "row": True})
    rmarks = {}
    for g in d["guests"]["restarted"]:
        for t in g["restarts"]:
            i = int(pos(t) * n / 100 + 0.001)
            rmarks.setdefault(i, {"left": 100 * i / n, "text": f"{g['name'][:12]} {when(t, '%H:%M')}" if wide else "",
                                  "level": "warn", "end": i == n - 1})
    if rmarks:
        lanes.append({"name": "Restarts", "bar": False, "bars": [], "marks": sorted(rmarks.values(), key=lambda m: m["left"]), "row": True})
    return {"lanes": lanes, "days": [b["label"] for b in bks], "n": n, "wide": wide,
            "any_norec": any(lane.get("norec") for lane in lanes)}


def rack_units(d, v):
    units = []
    for n in v["nodes"]:
        bays = []
        for disk in n["disks"][:8]:
            tone = "bad" if disk["health"] not in ("PASSED", "OK", "UNKNOWN", "") else "warn" if (disk["wear"] or 0) >= 80 else "ok"
            bays.append(tone)
        meters = [("CPU", n["cpu_avg"], "")]
        if n["local_hot"]:
            meters.append(("Disk", n["local_pct"], "bad" if (n["local_pct"] or 0) >= 90 else "warn"))
        else:
            meters.append(("Memory", n["mem_avg"], "warn" if (n["mem_max"] or 0) >= 90 else ""))
        sub = f"{plural(n['guests'], 'guest')} · " + ("offline" if not n["online"] else f"up {n['up_text'].replace('d', ' d')}")
        units.append({"name": n["name"], "sub": sub, "bays": (bays + ["off"] * 8)[:8],
                      "meters": [{"label": a, "v": b, "text": fpct(b), "tone": t} for a, b, t in meters],
                      "tone": "bad" if not n["online"] else ""})
    ceph = d.get("ceph")
    shared = [s for s in v["storage"] if s["shared"] and not s["backup_only"]]
    for s in shared[:2]:
        if s["type"] in ("rbd", "cephfs") and ceph:
            up, total = ceph.get("osds_up") or 0, ceph.get("osds") or 0
            bays = ["ok"] * min(up, 8) + ["bad"] * min(max(0, total - up), 8 - min(up, 8))
            sub = f"{total} OSDs · {'healthy' if ceph['health'] == 'HEALTH_OK' else ceph['health'].replace('HEALTH_', '').lower()}"
        else:
            bays, sub = [], f"{s['type_label']} · shared"
        units.append({"name": s["name"], "sub": sub, "bays": (bays + ["off"] * 8)[:8] if bays else [],
                      "meters": [{"label": "Used", "v": s["pct"], "text": s["pct_text"], "tone": s["tone"]}], "tone": ""})
    backup = [s for s in v["storage"] if s["backup_only"]]
    if backup:
        s = max(backup, key=lambda s: s["total"] or 0)
        fails = [f for f in d["backups"]["failures"]]
        units.append({"name": s["name"], "sub": f"{s['type_label']} · " + (plural(len(fails), "failed run") if fails else "backups"),
                      "bays": [], "meters": [{"label": "Used", "v": s["pct"], "text": s["pct_text"], "tone": s["tone"]}],
                      "tone": ""})
    return units[:9]


def heat_rows(d):
    rows = []
    for n in d["nodes"]:
        h = n["heat"]
        rows.append({"name": n["name"], "what": "CPU", "cells": h["cpu"], "peak": max([c for c in h["cpu"] if c is not None], default=None)})
        if (n["mem_max"] or 0) >= 80:
            rows.append({"name": n["name"], "what": "Memory", "cells": h["mem"],
                         "peak": max([c for c in h["mem"] if c is not None], default=None)})
    if len(rows) > 12:
        rows = sorted(rows, key=lambda r: -(r["peak"] or 0))[:12]
    step = d["nodes"][0]["heat"]["step"] if d["nodes"] else 7200
    return {"rows": rows, "step": step, "cell_text": {7200: "2 hours", 28800: "8 hours"}.get(step, "a day")}


def pulse_line(d, word):
    """When the cluster was busy, from the CPU cells, and its heaviest moment."""
    day_vals, night_vals = [], []
    peak = None
    for n in d["nodes"]:
        h = n["heat"]
        for i, c in enumerate(h["cpu"]):
            if c is None:
                continue
            t = dt.datetime.fromtimestamp(d["start"] + i * h["step"], config.TZ)
            if h["step"] < DAY:
                if t.weekday() < 5 and 8 <= t.hour < 18:
                    day_vals.append(c)
                elif t.hour < 6:
                    night_vals.append(c)
            if peak is None or c > peak[0]:
                peak = (c, n["name"], d["start"] + i * h["step"])
    if not peak:
        return "Load history isn't available for this period yet."
    s = ""
    if day_vals and night_vals:
        a, b = sum(day_vals) / len(day_vals), sum(night_vals) / len(night_vals)
        if a > 1.5 * b:
            s = "Busy on weekday working hours, quiet at night. "
        elif b > 1.5 * a:
            s = "Busiest at night, when backups run. "
        else:
            s = f"Load was even across the {word}. "
    hot = max((n for n in d["nodes"] if n["mem_max"] is not None), key=lambda n: n["mem_max"], default=None)
    if hot and hot["mem_max"] >= 85 and hot["mem_max_at"]:
        s += (f"The heaviest moment was {when(hot['mem_max_at'], '%a %-d %b around %H:00')}, when {hot['name']} "
              f"reached {hot['mem_max']:.0f}% memory.")
    else:
        s += f"The busiest moment was {when(peak[2], '%a %-d %b around %H:00')}, when {peak[1]} averaged {peak[0]:.0f}% CPU."
    return s


def findings(d, v):
    """Ledger's findings log: one line per event, timestamped, fail before warn, newest first within each."""
    out = []
    for f in d["backups"]["failures"]:
        out.append({"t": f["t"], "level": "fail", "text": f"backup {f['name']} ({f['vmid']}): {f['reason']}"})
    for a in v["attention"]:
        if a["area"] == "backups" and "failed:" in a["text"]:
            continue
        out.append({"t": a["t"] or d["end"] - 1, "level": "fail" if a["level"] == "bad" else "warn",
                    "text": a["ledger"][0].lower() + a["ledger"][1:] if not a["ledger"][:2].isupper() else a["ledger"]})
    out.sort(key=lambda f: (f["level"] != "fail", -f["t"]))
    for f in out:
        f["when"] = when(f["t"], "%m-%d %H:%M")
    return out


def areas(d, v, word, n_nodes):
    """Briefing's five areas: Good / Plan / Act, one sentence and one number each, in plain words."""
    def verdict_of(items):
        return "Act" if any(a["level"] == "bad" for a in items) else "Plan" if items else "Good"

    att = v["attention"]
    by = lambda area: [a for a in att if a["area"] == area]  # noqa: E731
    g = d["guests"]
    servers = plural(n_nodes, "server")
    out = []
    av = by("availability")
    if av:
        text = av[0]["plain"]
    elif n_nodes == 1 and g["ran_all"] is not None:
        text = f"The server and {g['ran_all']} of {g['total']} systems ran the whole {word}"
    elif n_nodes == 1:
        text = f"The server is up and {g['running']} of {g['total']} systems are running"
    elif g["ran_all"] is not None:
        text = f"All {servers} and {g['ran_all']} of {g['total']} systems ran the whole {word}"
    else:
        text = f"All {servers} are up and {g['running']} of {g['total']} systems are running"
    out.append({"name": "Availability", "verdict": verdict_of(av), "text": text, "number": v["fig"]["available"]["value"]})
    cap = [a for a in by("capacity") if a["level"] == "bad"] + [a for a in by("capacity") if a["level"] == "warn"]
    hot = max((s for s in v["storage"] if not s["backup_only"] and s["pct"] is not None), key=lambda s: s["pct"], default=None)
    out.append({"name": "Capacity", "verdict": verdict_of(cap),
                "text": cap[0]["plain"] if cap else "Every disk has room to spare",
                "number": fpct(hot["pct"]) if hot else "–"})
    bk = by("backups")
    b = d["backups"]
    pct = 100 * b["runs_ok"] / b["runs_total"] if b["runs_total"] else None
    text = f"{b['runs_ok']} of {b['runs_total']} ran" if b["runs_total"] else f"No backups ran {v['this']}"
    nij = [a for a in bk if "backup job" in a["text"] or "backup schedule" in a["plain"]]
    if nij:
        text += ". " + nij[0]["plain"]
    out.append({"name": "Backups", "verdict": verdict_of(bk), "text": text, "number": fpct(pct) if pct is not None else "–"})
    mt = by("maintenance") + by("health")
    out.append({"name": "Maintenance", "verdict": verdict_of(mt), "text": mt[0]["plain"] if mt else "Nothing is waiting",
                "number": str(len(mt))})
    m = v["main"]
    if m and m["per_week"] is not None and not m["steady"] and m["per_week"] > 0:
        grow = f"{'Shared storage' if m['shared'] else 'Storage'} grows {m['per_week']:.1f} points a {'week'}"
        if m["eta_warn"] and m["even"]:
            target = dt.datetime.fromtimestamp(d["end"], config.TZ) + dt.timedelta(days=m["eta_warn"])
            grow += f", {v['warn_pct']}% around {target:%-d %b}"
        gv = "Plan" if m["eta_warn"] is not None and m["eta_warn"] < 60 else "Good"
    else:
        grow, gv = "Storage use is steady", "Good"
    out.append({"name": "Growth", "verdict": gv, "text": grow, "number": m["pct_text"] if m else "–"})
    tones = {"Good": "ok", "Plan": "warn", "Act": "bad"}
    for a in out:
        a["tone"] = tones[a["verdict"]]
    return out


def verdict(v):
    acts = sum(1 for a in v["areas"] if a["verdict"] == "Act")
    plans = sum(1 for a in v["areas"] if a["verdict"] == "Plan")
    word = v["word"]
    if acts:
        return f"A {'difficult' if v['areas'][0]['verdict'] == 'Act' else 'steady'} {word}. " \
               f"{'One thing needs' if acts == 1 else ['Two', 'Three', 'Four', 'Five'][min(acts, 5) - 2] + ' things need'} a decision soon."
    if plans:
        return f"A steady {word}. Nothing is urgent; {plural(plans, 'thing')} to plan."
    return f"A quiet {word}. Nothing needs a decision."


def recommend(d, v, word, n_nodes):
    """Briefing's 'What we recommend': up to three actions with a reason, from the most serious findings."""
    out = []
    att = v["attention"]
    for s in v["storage"]:
        if s["tone"] == "bad" and len(out) < 3:
            why = v["writes_note"] if s["node"] and v["writes_note"] else (
                f"It grew {s['change_pts']:.1f} points {v['this']}" if s["change_pts"] else f"It is {s['pct_text']} full")
            out.append({"title": f"Free space on {s['node'] or s['name']} {'this week' if word == 'week' else 'soon'}",
                        "why": why})
            break
    if d["backups"]["failures"] and len(out) < 3:
        r = v["failures"][0]
        out.append({"title": "Fix the failing backups", "why": f"{r['name']}: {r['reason'][0].lower() + r['reason'][1:]}"})
    nij = d["backups"].get("not_in_job", [])
    if nij and len(out) < 3:
        x = next((g for g in d["guests"]["list"] if g["vmid"] == nij[0]["vmid"]), None)
        size = f"It holds {fbytes(x['maxdisk'])} and has no recent backup" if x and x["maxdisk"] and not nij[0]["latest"] else \
            "It isn't in any backup schedule"
        out.append({"title": f"Add {nij[0]['name']} to a backup job" if len(nij) == 1 else f"Add {len(nij)} systems to a backup job",
                    "why": size})
    down = [a for a in att if a["area"] == "availability" and a["level"] == "bad"]
    if down and len(out) < 3:
        out.append({"title": "Find out why " + down[0]["short"].split(" ")[0] + " went down", "why": down[0]["text"]})
    reboot = [n["name"] for n in d["nodes"] if n["reboot"]]
    if reboot and len(out) < 3:
        why = ("One at a time; their systems move to the other servers while each restarts" if n_nodes > 1 else
               "The systems on it stop for a few minutes while it restarts")
        out.append({"title": f"Book a restart window for {join(reboot)}", "why": why})
    for a in att:
        if len(out) >= 3:
            break
        if a["area"] == "health":
            out.append({"title": "Look at " + a["short"].split(":")[0], "why": a["plain"]})
        elif a["area"] == "capacity" and "memory" in a["text"] and v["memory_note"]:
            out.append({"title": f"Spread the load on {a['short'].split(' ')[0]}", "why": v["memory_note"]})
    return out[:3]
