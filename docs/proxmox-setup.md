# Setting up Proxmox for the reporter

The reporter only reads. It uses an API token on a user with a read-only role; it never needs a password, root,
or anything that can change the cluster.

## 1. A read-only role, user and token (run once, on any node)

```sh
# PVEAuditor plus Sys.Syslog (read the cluster log and node journals). Still read-only.
pveum role add ReportReader --privs "Sys.Audit Sys.Syslog VM.Audit Datastore.Audit Pool.Audit SDN.Audit Mapping.Audit VM.GuestAgent.Audit"

pveum user add reporter@pve --comment "Cluster reports (read-only)"
pveum acl modify / --users reporter@pve --roles ReportReader

# a token with privilege separation, given the same role
pveum user token add reporter@pve reports --privsep 1
pveum acl modify / --tokens 'reporter@pve!reports' --roles ReportReader
```

The `token add` command prints the secret once. Keep it for the installer or Settings.

- On Proxmox VE 8, `VM.GuestAgent.Audit` doesn't exist yet: leave it out of the first command.
- Prefer plain PVEAuditor? Use `--roles PVEAuditor` instead and skip the first command. Everything works except
  **Errors in the logs** and the HA fencing count, which need `Sys.Syslog`.
- The reporter never asks for `Sys.Modify`, so it can't list pending updates. It reports a node that needs a reboot
  for an installed kernel instead (from the installed package versions).

## 2. Certificates

The reporter checks it's talking to your cluster before it sends the token. Either:

- **Fingerprints** (what `install.sh` sets up): each node's certificate fingerprint, confirmed by you. After a node
  renews its certificate, run `./install.sh --cert` or paste the new one in Settings.
- **The cluster CA**: paste `/etc/pve/pve-root-ca.pem` into Settings → Proxmox cluster. This covers every node
  that uses the cluster's own certificates, including ones added later.

Certificate checking can't be switched off.

## 3. Proxmox Backup Server (optional)

```sh
proxmox-backup-manager user create reporter@pbs
proxmox-backup-manager user generate-token reporter@pbs reports
proxmox-backup-manager acl update / Audit --auth-id 'reporter@pbs!reports'
```

Then in Settings → Proxmox Backup Server: tick Include, give the address (https://pbs.example.lan:8007), the token
and its secret, and the fingerprint shown on the PBS dashboard (Show Fingerprint).

## What the reporter reads

| Section | API calls |
|---|---|
| Cluster health | `/cluster/status`, `/cluster/ha/status/current`, `/cluster/log`, `/nodes/{n}/status`, `/nodes/{n}/apt/versions`, `/nodes/{n}/disks/list`, `/nodes/{n}/disks/zfs`, `/nodes/{n}/services`, `/nodes/{n}/replication`, `/nodes/{n}/time`, `/nodes/{n}/subscription`, `/cluster/ceph/status` (when Ceph is used) |
| Capacity | `/cluster/resources`, `/storage`, `/nodes/{n}/rrddata`, `/nodes/{n}/storage/{s}/rrddata` |
| VMs and containers | `/cluster/resources`, `/nodes/{n}/qemu|lxc/{id}/rrddata`, `/nodes/{n}/qemu|lxc/{id}/snapshot`, `/nodes/{n}/tasks` |
| Backups | `/nodes/{n}/tasks` (vzdump runs and their logs), `/cluster/backup`, `/cluster/backup-info/not-backed-up`, `/nodes/{n}/storage/{s}/content?content=backup` |
| Logs (every 5 minutes) | `/nodes/{n}/journal` from a saved cursor, `/cluster/log` |
| Backup server | `/status/datastore-usage`, `/nodes/localhost/tasks` |

Every 5 minutes the reporter also records node, guest and storage state and quorum in `reporter/out/reporter.db`.
That gives accurate outages, restarts and fair comparisons; Proxmox itself only keeps averages. Rows older than
about 15 months are removed.
