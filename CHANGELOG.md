# Changelog

## Unreleased

- A refused API call now names the missing permission and how to fix it, in the command line, Settings and
  notifications, instead of a traceback.

- Fix: install.sh stopped silently on a re-run when .env had no PORTAL_PORT. It now says where it stops if
  anything fails.
- install.sh can be run by any user. Without Docker access or write access to the folder it runs itself again
  with sudo. The reporter runs as the person who ran it (never root); an existing root install is moved to an
  ordinary user and its data handed over.

- The portal now answers on https://server (port 443) by default, and plain http://server redirects to it. Set
  PORTAL_PORT and HTTP_PORT in .env to use other ports.
- Setup guide: how to fix a 403 Permission check failed.
- Read past logs: the installer offers to read back node journals and the cluster log (30 days by default), and
  Settings → Past logs or `report.py logs backfill --days N` does it any time. Reports say where the logs start.
- Corosync link-down and quorum-loss lines are now kept from the journals.

## 0.1.0

- Scheduled and on-demand PDF reports for a Proxmox VE cluster: weekly, monthly, quarterly and custom dates, each as
  a full report and a one-page summary, from one data collection.
- Six report looks to pick from in Settings (Logbook, Original, Briefing, Ledger, Rack, Pulse), each with its own
  accent colour.
- Read-only API token, certificate fingerprints or the cluster CA, failover between nodes.
- 5-minute snapshots of nodes, guests, storage and quorum, and the cluster log and node journals as they happen.
- Optional Proxmox Backup Server section.
- The portal's own accounts with Viewer, Operator and Admin roles, a setup code for the first admin, lockout,
  session ending on password or role change, and an audit log.
- Page-flow check of every PDF with a second, looser layout when needed; render tests for every template.
