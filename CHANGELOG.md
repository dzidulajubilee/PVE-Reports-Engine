# Changelog

## Unreleased

- Quieter logs: WeasyPrint no longer writes a line for every page it lays out.

- Fonts carry Greek letters, arrows and box drawing, so no symbol falls back to a system font.
- Page flow: longer tables (over 8 rows) flow across pages; a data note stays with the block above it; Rack and
  network headings never end a page; Ledger keeps its data note under the header.

- Settings: the logo picker is a quiet button that shows the chosen file's name and previews it before saving.

- Logbook: dates, node and guest counts and the time made sit under the title; the upper right is left for
  the logo.

- Every page keeps its look: each template styles pages 2 onwards in its own way, and every page after the first
  names the topic it belongs to.
- Preview all pages: Settings opens the whole report in any look and accent, from the latest report's figures
  (or a sample cluster before the first report). Nothing is saved or sent.
- Page flow: each topic starts on a fresh page; a short last page of a topic is accepted instead of flattening
  the layout.
- Security: Flask 3.1.3 and WeasyPrint 70.0 (known vulnerabilities fixed); PDF rendering may load only the
  bundled fonts, the uploaded logo and inline PNG or JPEG images.
- Fixed a slow memory leak in the sign-in rate limit, half-written PDFs left behind after a failed render, and
  stale code.

- Twelve new sections, each switchable in Settings: failed tasks, trends, certificates, disks inside guests,
  sign-ins and firewall, resource pressure, backup jobs and coverage, replication, network traffic, pools and
  tags, cluster network, storage detail. The serious findings also appear in Needs attention.
- Settings no longer shows an old connection problem after the connection works again.

- Single node or cluster: each report works out the setup and follows it. A standalone node reads as a server
  ("Server up", no quorum lines); a cluster with only local disks charts every node's storage, the fullest in
  the accent colour.
- Node uptime counters are used as recorded facts: a node that booted before the period began ran all of it,
  so the timeline and a single server's "Server up" are shown even before the reporter was installed.
- Rounds where no node answered are recorded and reported as "no answer for N min", never as downtime.
- Settings lists cluster nodes missing from the addresses, so reports can fail over.
- The backup server's estimated full date is ignored when it is already past.

- Backup server: backups in namespaces (such as Production) are found; the fingerprint Proxmox VE already
  trusts for the backup server is filled in automatically; a token that sees no datastore is reported as a
  missing permission; the certificate error no longer points to the Proxmox VE CA file.
- Setup guide: the backup server token needs Audit for both the user and the token.

- Availability shows only recorded facts. Without recordings there is no percentage; quorum losses and fencing
  that the nodes logged are shown with their time and node, and "no quorum loss logged" when the logs cover
  the period. The timeline draws node-up bars only for recorded time; unrecorded time is dashed.
- Quorum-regained lines are kept from the journals, so a logged loss shows when it ended.

- Routine AppArmor audit lines are no longer kept from the kernel log (and are removed from earlier
  collections), so real problems stand out in Errors in the logs.

- Fix: reading node journals failed with "utf-8 codec can't decode byte 0x8b" because Proxmox gzips them. API
  answers are now unpacked, and asked for gzipped, which is faster on slow links.
- README: a Proxmox permissions section with commands, web-interface steps and common mistakes.

- Backups: the latest backup of each guest now comes from the backup task logs (and the backup server when set
  up), because Proxmox lists backup files only to tokens that may make backups. No more "never" for guests
  that are backed up.
- A report missing many figures now says it is incomplete instead of calling the period quiet.
- Guests running shows the current count for periods before the reporter was installed.
- The organisation name isn't repeated when it matches the cluster name.

- Settings: the Save button stays in view at the bottom of the window, says when there are unsaved changes,
  and the page asks before you leave with changes unsaved. The test email moved into the Email server section.

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
