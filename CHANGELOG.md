# Changelog

## Unreleased

- The portal now answers on https://server (port 443) by default, and plain http://server redirects to it. Set
  PORTAL_PORT and HTTP_PORT in .env to use other ports.
- Setup guide: how to fix a 403 Permission check failed.

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
