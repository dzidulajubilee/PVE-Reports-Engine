# Changelog

## Unreleased

## 1.0.0 (18 Nov 2026)

The first release: scheduled PDF reports for Proxmox VE, from a single node to a cluster.

### Reports
- Weekly, monthly, quarterly and custom reports, each made as a full report and a one-page summary from one data
  collection. Weekly reports are made on the day you choose and cover the 7 days before it.
- Four main areas: capacity, VMs and containers, backups, and cluster health. Twelve optional sections, each
  switchable in Settings: failed tasks, trends, certificates, disks inside guests, sign-ins and firewall,
  resource pressure, backup jobs and coverage, replication, network traffic, pools and tags, cluster network,
  storage detail. Optional Proxmox Backup Server section, including backups in namespaces.
- Plain-language summaries and Needs attention, written by rules from the data: most serious first, thresholds
  and trends, plain reasons for failed backups, comparisons only when both periods have enough data.
- Recorded facts only: availability comes from the reporter's own 5-minute recordings, node uptime counters and
  events the nodes logged, never from an estimate. Gaps are labelled as such.
- Works out the setup on every report: a standalone node reads as a server, a cluster gets quorum and per-node
  views; Ceph, ZFS, HA, replication and the backup server appear only where present.
- Past logs: node journals and the cluster log can be read back on install, from Settings or the command line.

### Looks
- Six report looks (Logbook, Original, Briefing, Ledger, Rack, Pulse), each with its own style on every page and
  its own accent colour. Status colours stay the same in all of them.
- Preview all pages: Settings opens the whole report in any look and accent, from the latest figures.
- Every PDF is checked for page-flow problems and laid out again when needed.

### Portal and security
- The portal's own accounts with Viewer, Operator and Admin roles, a one-time setup code for the first admin,
  scrypt password hashing, lockout, sessions that end on a password or role change, and an audit log.
- Read-only Proxmox and PBS tokens; certificates checked by fingerprint or CA before the token is sent; failover
  between nodes, with a hint in Settings for nodes not listed.
- HTTPS through nginx 1.31.9 (fixes the 2026 "nginx2root" CVEs) with HTTP/2 and HTTP/3 off; PDF rendering may
  load only the bundled fonts and the uploaded logo.
- Installer for any user (uses sudo when needed); the reporter never runs as root.
