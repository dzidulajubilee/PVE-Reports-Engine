# PVE Reports Engine

Scheduled PDF reports for a Proxmox VE cluster, written for the people who read them: what happened, what needs
attention and what to do about it. Weekly, monthly and quarterly reports arrive by email on their own; anyone with
an account can make one for any dates in the web portal.

- **Four areas:** capacity, VMs and containers, backups, and cluster health. Optional Proxmox Backup Server section.
- **Six looks**, picked in Settings: Logbook (the default), Original, Briefing (for managers), Ledger (for
  engineers), Rack (for wall printouts) and Pulse (a heatmap for capacity planning). Every page keeps its look,
  and **Preview all pages** opens the whole report in any look and accent, from your latest figures.
- **Single node or cluster**, worked out on every report: wording, availability and checks follow the setup.
- **Optional sections**, each switchable: failed tasks, trends, certificates, disks inside guests, sign-ins and
  firewall, resource pressure, backup jobs and coverage, replication, network traffic, pools and tags, cluster
  network and storage detail.
- **Two versions of every report** from one data collection: the full report and a one-page summary.
- **Read-only.** An API token with a read-only role; the reporter can't change anything in the cluster.
- **Every figure from data.** When something can't be read it shows as –, and comparisons with the previous period
  are only made when both periods have enough data.
- **Offline at runtime.** Fonts are bundled and charts are drawn in Python; the only traffic goes to Proxmox, the
  backup server and your mail server.

## Install

You need a Linux host with Docker and the Compose plugin that can reach the Proxmox nodes on port 8006.

1. On a Proxmox node, make a read-only role, user and token: see [Proxmox permissions](#proxmox-permissions) below.
2. On the Docker host:
   ```sh
   git clone https://github.com/dzidulajubilee/PVE-Reports-Engine.git
   cd PVE-Reports-Engine
   ./install.sh
   ```
   Run it as any user: if that user can't use Docker, it runs itself again with `sudo`. The reporter always runs
   as that user, never as root. The installer asks for a name, time zone, node addresses and the token, reads each node's certificate
   fingerprint for you to confirm, makes an HTTPS certificate for the portal, builds and starts the containers, and
   creates the first admin account.
   At the end it offers to read back past logs (30 days by default) so your first reports already show errors
   from before the install.
3. Open `https://<docker-host>`, sign in, and check **Settings**: email server, recipients, schedule and look.
4. Press **Make report** for last week to see it straight away.

The portal uses ports 443 (HTTPS) and 80 (which only redirects to HTTPS). If another web server already has them,
set `PORTAL_PORT=8443` and/or `HTTP_PORT=8080` in `.env` (next to UID/GID) and open `https://<docker-host>:8443`.

Updating: `git pull && ./install.sh` (your settings stay in `reporter/out` and `reporter/.env`).

### Without the installer

Copy `reporter/.env.example` to `reporter/.env` and fill it in, put a certificate in `nginx/certs/server.crt` and
`server.key`, then `docker compose up -d --build`. With no accounts yet, the portal shows a setup page that asks for
a one-time code; the container logs it: `docker compose logs reporter | grep "setup code"`.

## Proxmox permissions

The reporter only reads. It needs one API token with a read-only role. Most setup problems come from the steps
below, so follow them exactly.

**The rule that catches people out:** a token made with *privilege separation* (the default, and the safe choice)
only gets the rights that **both** the user **and** the token have. Give the role twice, once to the user and once
to the token, both on path `/`.

### With commands (on any node, as root)

```sh
# 1. A read-only role: PVEAuditor plus Sys.Syslog (to read logs). On Proxmox VE 8, leave out VM.GuestAgent.Audit
pveum role add ReportReader --privs "Sys.Audit Sys.Syslog VM.Audit Datastore.Audit Pool.Audit SDN.Audit Mapping.Audit VM.GuestAgent.Audit"

# 2. A user for the reporter, given the role on /
pveum user add reporter@pve --comment "Cluster reports (read-only)"
pveum acl modify / --users reporter@pve --roles ReportReader

# 3. A token for that user (prints the secret once: copy it), given the same role on /
pveum user token add reporter@pve reports --privsep 1
pveum acl modify / --tokens 'reporter@pve!reports' --roles ReportReader

# 4. Check: the list must include Sys.Audit, Sys.Syslog, VM.Audit and Datastore.Audit
pveum user token permissions reporter@pve reports --path /
```

### Or in the web interface

| Step | Where | What to fill in |
|---|---|---|
| 1. Role | Datacenter → Permissions → Roles → **Create** | Name `ReportReader`; tick Sys.Audit, Sys.Syslog, VM.Audit, Datastore.Audit, Pool.Audit, SDN.Audit, Mapping.Audit, VM.GuestAgent.Audit (PVE 9) |
| 2. User | Datacenter → Permissions → Users → **Add** | User name `reporter`, Realm `Proxmox VE authentication server` (pve). No password needed |
| 3. User's permission | Datacenter → Permissions → **Add → User Permission** | Path `/`, User `reporter@pve`, Role `ReportReader`, Propagate ticked |
| 4. Token | Datacenter → Permissions → API Tokens → **Add** | User `reporter@pve`, Token ID `reports` (just the name, no `!`), Privilege Separation ticked. Copy the secret now: it's shown once |
| 5. Token's permission | Datacenter → Permissions → **Add → API Token Permission** | Path `/`, API Token `reporter@pve!reports`, Role `ReportReader`, Propagate ticked |

In the installer and in Settings, the token ID is the full form `reporter@pve!reports`, and the secret is the UUID
from step 4.

### Common mistakes

| What you see | Cause | Fix |
|---|---|---|
| `403 Permission check failed (/, Sys.Audit)` | The role is on the token but not the user, or the other way round | Do both step 3 and step 5 |
| `403 … (/nodes/<node>, Sys.Syslog)`; Errors in the logs is empty; Read past logs fails | The role is PVEAuditor, which can't read logs | Use ReportReader (step 1) on both user and token |
| Some sections empty, no error | The permission is on a path other than `/`, or Propagate is off | Path `/`, Propagate ticked |
| `401 … token was rejected` | Wrong token ID or secret | Token ID `user@realm!name`; make a new token if the secret is lost |
| PVESysAdmin or Administrator given "to be safe" | These can change the cluster, and PVESysAdmin still lacks VM.Audit and Datastore.Audit | Use ReportReader only |

The reporter never needs write rights. It doesn't list pending updates (that needs `Sys.Modify`); it reports nodes
that need a reboot for an installed kernel instead. For certificates and the optional Proxmox Backup Server token
(the same rule applies there: Audit on `/` for both the user and the token), see
[docs/proxmox-setup.md](docs/proxmox-setup.md#3-proxmox-backup-server-optional).

## Accounts

The portal has its own accounts, separate from Proxmox.

| | Viewer | Operator | Admin |
|---|:-:|:-:|:-:|
| See and download reports | ✓ | ✓ | ✓ |
| Make reports | | ✓ | ✓ |
| Email reports, recipients, schedule, delete reports | | | ✓ |
| Settings and users | | | ✓ |

Admins add people under **Users**, either with a temporary password or a one-time password the portal shows once.
Everyone chooses their own password at first sign-in. Passwords need 12 characters; after 5 wrong tries an account
locks for 15 minutes. Lost the only admin password?

```sh
docker compose exec reporter python report.py user reset-password <name>
docker compose exec reporter python report.py user add <name> --role admin
docker compose exec reporter python report.py user list
```

## Command line

```sh
docker compose exec reporter python report.py run weekly              # last week, emailed to its recipients
docker compose exec reporter python report.py run monthly --no-email
docker compose exec reporter python report.py run custom --start 2026-11-01 --end 2026-11-15
docker compose exec reporter python report.py logs backfill --days 30   # read past logs (also in Settings)
```

## How it works

One small container runs the portal, a report worker, the scheduler, and a snapshot thread that records the
cluster every 5 minutes into SQLite (`reporter/out/reporter.db`). Proxmox keeps only averages, so the snapshots are
what give accurate outages, restarts and quorum history. The first reports after install say how much of the period
they cover.

On first install, or any time from Settings → Past logs, the reporter can read back node journals and the cluster
log, as far back as each node still keeps them. Backups, migrations and load/storage history never need this:
Proxmox keeps those itself. Outages, guest restarts and quorum history can't be read back and start at install.

Reports are HTML templates (`reporter/templates/reports/<look>/report.html`) laid out by WeasyPrint. After layout
each PDF is checked for near-empty pages, stranded headings and content past the page edge, and laid out again with
looser page breaks if needed. `reporter/tests/test_render.py` runs every look with made-up data in three sizes.

Development:

```sh
python -m venv .venv && .venv/bin/pip install -r reporter/requirements.txt pytest==8.3.3 ruff==0.6.9
.venv/bin/pytest -q && .venv/bin/ruff check reporter
cd reporter && OUT_DIR=/tmp/out ../.venv/bin/python -m tests.fake_report /tmp/preview   # every look as PDF
```

## Security

- The container runs as your user, not root, and publishes no port; nginx serves the portal over HTTPS.
- Proxmox and PBS certificates are always checked, by fingerprint or CA, before the token is sent.
- Secrets live only in `reporter/out/settings.json` and `reporter/.env` (both mode 600, both ignored by git) and
  are never shown back in forms or written to logs.
- Passwords are hashed with scrypt; sign-in is rate-limited per address and per account; every form carries a CSRF
  token; changing a password or role ends that person's other sessions.
- Only admins can send report data anywhere (email) or change settings.

## Licence

GPL-3.0. Bundled fonts are under the SIL Open Font License; see `reporter/fonts/README.md`.
