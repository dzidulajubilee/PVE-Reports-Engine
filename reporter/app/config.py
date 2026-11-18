"""Settings, paths, the report time zone, report sections and report templates."""

import json
import logging
import os
import re
from pathlib import Path
from zoneinfo import ZoneInfo

# Report time zone. Starts from $TZ; the portal's setting overrides it (see load_settings).
TZ = ZoneInfo(os.environ.get("TZ") or "UTC")

# The day weekly reports are made, 0 = Monday: each covers the 7 days before it. Set from Settings.
WEEK_DAY = 0
DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

OUT = Path(os.environ.get("OUT_DIR", "/out"))
ROOT = Path(__file__).resolve().parent.parent  # reporter/: templates/ and fonts/ live here

log = logging.getLogger("reporter")

# What a report can include; admins switch these on and off per version in the portal. Page 1 (the summary,
# needs attention, headline numbers and the week at a glance) is always there.
SECTIONS = [
    ("capacity", "Capacity", "Storage use and growth, when each storage fills up, vCPU and memory given out vs installed"),
    ("guests", "VMs and containers", "Busiest guests, oversized guests, restarts and stopped guests"),
    ("snapshots", "Old snapshots", "Snapshots older than 30 days, which keep growing as the disk changes"),
    ("changes", "Changes", "Migrations, new and removed guests, and who made them, from the task log"),
    ("backups", "Backups", "Each guest's backups night by night, why runs failed, guests in no backup job"),
    ("health", "Cluster health", "Quorum, HA, Ceph, ZFS, disk wear and SMART, services, replication, kernel, clock"),
    ("pbs", "Proxmox Backup Server", "Datastore use and when it fills up, verify, prune and garbage-collection runs"),
    ("logs", "Errors in the logs", "Warnings and errors from node journals and the cluster log (needs Sys.Syslog)"),
    ("tasks", "Failed tasks", "Migrations, starts, stops, disk moves and other tasks that failed, with who ran them"),
    ("trends", "Trends", "This period next to the ones before: 8 weeks, 6 months or 4 quarters (not for custom dates)"),
    ("certificates", "Certificates", "When each node's web certificate and the cluster CA expire"),
    ("guest_disks", "Disks inside guests", "Filesystems filling up inside VMs (needs the guest agent) and containers"),
    ("security", "Sign-ins and firewall", "Failed sign-ins by user and address, and connections the host firewall blocked"),
    ("pressure", "Resource pressure", "How long work waited for CPU, memory or disk on each node (Proxmox VE 9)"),
    ("backup_coverage", "Backup jobs and coverage", "Each job's schedule, storage and retention, disks left out, restore points and verification"),
    ("replication", "Replication", "Each replication job's last sync, how long it took and failures"),
    ("network", "Network traffic", "Data in and out per node, peak rate, and the guests that moved the most"),
    ("pools", "Pools and tags", "Guests, allocation, load and backups per pool, and the tags in use"),
    ("cluster_net", "Cluster network", "Corosync links per node and every link that went down (clusters only)"),
    ("storage_detail", "Storage detail", "LVM-thin metadata, ZFS fragmentation, Ceph pools"),
]
SECTION_KEYS = [k for k, *_ in SECTIONS]

STYLES = ("full", "summary")

# what the summary version shows until an admin changes it: page 1 only
SUMMARY_SECTIONS: set[str] = set()

SETTINGS = OUT / "settings.json"

# The six report looks. Each has its own accent that admins can change; status colours never change.
TEMPLATES = {
    "logbook": {"label": "Logbook", "for": "Everyone, the default", "accent": "#0e7c7b"},
    "original": {"label": "Original", "for": "Everyone: node cards with load bars", "accent": "#0e7c7b"},
    "briefing": {"label": "Briefing", "for": "Managers and owners: a verdict and what to do", "accent": "#1d3f7a"},
    "ledger": {"label": "Ledger", "for": "Engineers: dense, tabular, timestamped", "accent": "#171717"},
    "rack": {"label": "Rack", "for": "Mixed audiences and wall printouts", "accent": "#5b8fe0"},
    "pulse": {"label": "Pulse", "for": "Capacity planning: a heatmap of the period", "accent": "#1f4fbf"},
}
DEFAULT_TEMPLATE = "logbook"

# Accent presets offered in Settings next to a free colour field
ACCENTS = ["#0e7c7b", "#2a78d6", "#1d3f7a", "#1f4fbf", "#5a52d5", "#2f8a4e", "#c27400", "#3f3f3c"]

# Fixed across templates
OK, WARN, BAD = "#16794a", "#a15c00", "#b42318"

# An optional logo for the first page, uploaded in Settings: OUT/logo.png, logo.jpg or logo.svg
LOGO_TYPES = {"png": "image/png", "jpg": "image/jpeg", "svg": "image/svg+xml"}
LOGO_MAX_BYTES = 1024 * 1024

HEX = re.compile(r"^#[0-9a-fA-F]{6}$")


def _lum(hexcolor):
    def ch(c):
        c = c / 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (int(hexcolor[i : i + 2], 16) for i in (1, 3, 5))
    return 0.2126 * ch(r) + 0.7152 * ch(g) + 0.0722 * ch(b)


def contrast(a, b="#ffffff"):
    la, lb = sorted((_lum(a), _lum(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def ink(hexcolor, target=4.5):
    """A darker shade of the accent that reaches `target` contrast on white, for text and links."""
    r, g, b = (int(hexcolor[i : i + 2], 16) for i in (1, 3, 5))
    for _ in range(40):
        c = f"#{r:02x}{g:02x}{b:02x}"
        if contrast(c) >= target:
            return c
        r, g, b = int(r * 0.92), int(g * 0.92), int(b * 0.92)
    return "#171717"


def look(s):
    """{'template', 'accent', 'accent_ink'} from settings, falling back to the template's own accent."""
    t = s["general"].get("template")
    t = t if t in TEMPLATES else DEFAULT_TEMPLATE
    accent = (s["general"].get("accents") or {}).get(t) or TEMPLATES[t]["accent"]
    if not HEX.match(accent):
        accent = TEMPLATES[t]["accent"]
    return {"template": t, "accent": accent.lower(), "accent_ink": ink(accent)}


def logo_path():
    for ext in LOGO_TYPES:
        p = OUT / f"logo.{ext}"
        if p.is_file():
            return p
    return None


def logo_type(data):
    """'png', 'jpg' or 'svg' from a file's first bytes, or None for anything else."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpg"
    head = data[:1024].lstrip(b"\xef\xbb\xbf").lstrip().lower()
    if head.startswith(b"<svg") or (head.startswith((b"<?xml", b"<!--")) and b"<svg" in head):
        return "svg"
    return None


def default_settings():
    """First-run values come from the environment; after that settings.json wins."""
    env = os.environ.get
    to = env("REPORT_TO", "")
    hosts = [h.strip() for h in env("PVE_HOSTS", "").split(",") if h.strip()]
    return {
        "general": {
            "name": env("REPORT_NAME", ""),
            "timezone": env("TZ") or "UTC",
            "pve_url": env("PVE_PUBLIC_URL", ""),  # Proxmox web UI as readers reach it, for links in the PDF
            "pve_links": True,
            "template": env("REPORT_TEMPLATE", DEFAULT_TEMPLATE),
            "accents": {},  # template -> accent chosen by an admin
            "storage_warn": 80,  # % used that counts as needing attention
            "snapshot_days": 30,  # snapshots older than this are listed
        },
        "pve": {
            "hosts": hosts,  # node addresses, tried in order: host or host:port
            "token_id": env("PVE_TOKEN_ID", ""),  # user@realm!name
            "token_secret": env("PVE_TOKEN_SECRET", ""),
            "ca_pem": "",  # the cluster CA (/etc/pve/pve-root-ca.pem), or
            "fingerprint": env("PVE_FINGERPRINT", ""),  # a pinned SHA-256 certificate fingerprint
            "verify": True,  # only for a CA the system already trusts; never switched off quietly
        },
        "pbs": {
            "enabled": False,
            "url": "",  # https://pbs.example:8007
            "token_id": "",  # user@realm!name
            "token_secret": "",
            "ca_pem": "",
            "fingerprint": "",
        },
        "sections": {k: True for k in SECTION_KEYS},  # full report
        "sections_summary": {k: k in SUMMARY_SECTIONS for k in SECTION_KEYS},  # summary
        "smtp": {
            "host": env("SMTP_HOST", ""),
            "port": int(env("SMTP_PORT", 587)),
            "security": env("SMTP_SECURITY", "starttls"),
            "verify": env("SMTP_VERIFY", "true").lower() != "false",
            "user": env("SMTP_USER", ""),
            "password": env("SMTP_PASSWORD", ""),
            "from": env("SMTP_FROM", ""),
        },
        "recipients": {k: to for k in ("weekly", "monthly", "quarterly")},
        "recipients_summary": {k: "" for k in ("weekly", "monthly", "quarterly")},
        "schedule": {
            "enabled": True,
            "hour": int(env("SEND_HOUR", 7)),
            "week_day": int(env("WEEK_DAY", 0)),  # weekly reports: the day they're made, 0 = Monday
            "kinds": env("REPORTS", "weekly,monthly,quarterly").split(","),
        },
    }


def load_settings():
    s = default_settings()
    try:
        saved = json.loads(SETTINGS.read_text())
        for k in s:
            s[k].update(saved.get(k, {}))
    except FileNotFoundError:
        pass
    set_timezone(s["general"]["timezone"])
    global WEEK_DAY
    try:
        WEEK_DAY = min(6, max(0, int(s["schedule"].get("week_day", 0))))
    except (TypeError, ValueError):
        WEEK_DAY = 0
    return s


def set_timezone(name):
    """Every date in reports and the portal goes through TZ, so switching it here is enough."""
    global TZ
    try:
        TZ = ZoneInfo(name)
    except (ValueError, KeyError, OSError):
        log.warning("unknown time zone %r, keeping %s", name, TZ)


def timezones():
    from zoneinfo import available_timezones

    return sorted(
        z for z in available_timezones() if "/" in z and not z.startswith(("Etc/", "SystemV/", "posix/", "right/"))
    ) + ["UTC"]


def display_name(s=None):
    return (s or load_settings())["general"]["name"].strip() or "Reports"


def save_settings(s):
    OUT.mkdir(parents=True, exist_ok=True)
    tmp = SETTINGS.with_suffix(".tmp")
    tmp.write_text(json.dumps(s, indent=2))
    os.chmod(tmp, 0o600)  # holds the API token secrets and the SMTP password
    tmp.replace(SETTINGS)


def split_addresses(text):
    return [a.strip() for a in re.split(r"[,;\s]+", text or "") if a.strip()]
