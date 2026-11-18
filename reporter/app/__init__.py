"""Proxmox VE cluster reports.

Modules, roughly bottom to top:
  config    settings, paths, the report time zone, report sections and the six report looks
  db        SQLite: users, audit log, 5-minute snapshots, collected logs
  pve       read-only Proxmox VE / Backup Server API client with certificate pinning and node failover
  snapshot  records the cluster every 5 minutes and reads new log lines
  periods   weeks / months / quarterly / custom ranges, report file names and titles
  collect   figures for a period, from the API and the snapshots
  text      formatting, plain-language sentences, needs attention, the view every template draws
  charts    SVG charts for the PDF
  layout    page-flow check of the finished PDF
  build     builds a report: full and summary PDFs from one data collection
  mail      email delivery
  history   what the schedule sent and who made each report
  notify    portal notifications
  schedule  which scheduled reports are due
  users     accounts, roles, passwords, lockout, setup code
  cli       command line (report.py)
  web       the reports portal (portal.py)
"""
