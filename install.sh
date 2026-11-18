#!/usr/bin/env bash
# PVE Reports Engine installer. Run from the repo folder on the Docker host:
#   ./install.sh            first install, or update after git pull (keeps your settings)
#   ./install.sh --cert     read the nodes' certificate fingerprints again (after a node's certificate changes)
set -euo pipefail
cd "$(dirname "$0")"

note() { printf '  %s\n' "$*"; }
warn() { printf '\n  ! %s\n' "$*" >&2; }
ask() { local q=$1 def=${2:-} a; read -r -p "  $q${def:+ [$def]}: " a; printf '%s' "${a:-$def}"; }
yes_no() { local a; read -r -p "  $1 [${2:-y}/$([ "${2:-y}" = y ] && echo n || echo y)]: " a; a=${a:-${2:-y}}; [[ $a =~ ^[Yy] ]]; }
set_env() { # set_env KEY VALUE: replace or add KEY=VALUE in reporter/.env without echoing the value
  local k=$1 v=$2 tmp
  tmp=$(mktemp)
  grep -v "^$k=" reporter/.env > "$tmp" || true
  printf '%s=%s\n' "$k" "$v" >> "$tmp"
  cat "$tmp" > reporter/.env && rm -f "$tmp"
}
get_env() { grep -E "^$1=" reporter/.env 2>/dev/null | tail -1 | cut -d= -f2- || true; }

CERT_ONLY=0
[ "${1:-}" = "--cert" ] && CERT_ONLY=1

command -v docker > /dev/null || { warn "Docker isn't installed: https://docs.docker.com/engine/install/"; exit 1; }
docker compose version > /dev/null 2>&1 || { warn "The Docker Compose plugin isn't installed"; exit 1; }
command -v openssl > /dev/null || { warn "openssl isn't installed (apt install openssl)"; exit 1; }

# ---------------------------------------------------------------- settings
[ -f .env ] || printf 'UID=%s\nGID=%s\n' "$(id -u)" "$(id -g)" > .env
if [ ! -f reporter/.env ]; then
  cp reporter/.env.example reporter/.env
  FRESH=1
else
  FRESH=0
fi
chmod 600 reporter/.env
mkdir -p reporter/out

# fingerprint HOST[:PORT] -> SHA-256 fingerprint of the certificate it shows, or nothing
fingerprint() {
  local hp=$1
  [[ $hp == *:* ]] || hp="$hp:8006"
  openssl s_client -connect "$hp" -servername "${hp%:*}" < /dev/null 2> /dev/null \
    | openssl x509 -noout -fingerprint -sha256 2> /dev/null | cut -d= -f2
}

read_fingerprints() {
  local hosts fps="" h fp list
  hosts=$(get_env PVE_HOSTS)
  [ -n "$hosts" ] || return 0
  printf '\n  Certificates\n'
  note "The reporter checks it's talking to your cluster by each node's certificate fingerprint."
  note "Compare each one with the node: Proxmox web UI → node → System → Certificates → pveproxy-ssl.pem,"
  note "or on the node: openssl x509 -noout -fingerprint -sha256 -in /etc/pve/local/pveproxy-ssl.pem (or pve-ssl.pem)"
  IFS=', ' read -r -a list <<< "$hosts"
  for h in "${list[@]}"; do
    fp=$(fingerprint "$h")
    if [ -z "$fp" ]; then warn "Couldn't reach $h to read its certificate. Run ./install.sh --cert when it's up."; continue; fi
    printf '\n  %s\n  %s\n' "$h" "$fp"
    if yes_no "Does it match? Trust this certificate?" y; then fps="${fps:+$fps, }$fp"; fi
  done
  set_env PVE_FINGERPRINT "$fps"
  note "Saved. You can also paste the cluster CA in Settings instead."
}

if [ "$CERT_ONLY" = 1 ]; then
  read_fingerprints
  note "Restart to use them: docker compose restart reporter (Settings in the portal override these once saved)"
  exit 0
fi

if [ "$FRESH" = 1 ]; then
  printf '\n  PVE Reports Engine setup. Press Enter to accept a [default]; everything can be changed later in Settings.\n\n'
  set_env REPORT_NAME "$(ask 'Name on the reports, e.g. your company' '')"
  tz=$(cat /etc/timezone 2> /dev/null || timedatectl show -p Timezone --value 2> /dev/null || echo UTC)
  set_env TZ "$(ask 'Time zone' "$tz")"
  printf '\n  Proxmox connection: make a read-only token first (docs/proxmox-setup.md, three commands on a node)\n'
  set_env PVE_HOSTS "$(ask 'Node addresses, comma-separated (name or IP, :8006 is assumed)' '')"
  set_env PVE_TOKEN_ID "$(ask 'Token ID' 'reporter@pve!reports')"
  read -r -s -p "  Token secret (not shown): " secret; echo
  set_env PVE_TOKEN_SECRET "$secret"; unset secret
  first=$(get_env PVE_HOSTS | cut -d, -f1 | tr -d ' ')
  [ -n "$first" ] && set_env PVE_PUBLIC_URL "https://${first%%:*}:8006"
  read_fingerprints
fi

# ---------------------------------------------------------------- HTTPS certificate for the portal
if [ ! -s nginx/certs/server.crt ]; then
  host=$(hostname -f 2> /dev/null || hostname)
  ip=$(hostname -I 2> /dev/null | awk '{print $1}')
  openssl req -x509 -newkey rsa:2048 -sha256 -days 3650 -nodes -keyout nginx/certs/server.key -out nginx/certs/server.crt \
    -subj "/CN=$host" -addext "subjectAltName=DNS:$host${ip:+,IP:$ip}" 2> /dev/null
  chmod 600 nginx/certs/server.key
  note "Made a self-signed HTTPS certificate in nginx/certs (replace it with your own any time)."
fi

# ---------------------------------------------------------------- start
# the portal's ports must be free on this host (another web server often holds 80 or 443)
port=$(grep -E '^PORTAL_PORT=' .env | cut -d= -f2); port=${port:-443}
http_port=$(grep -E '^HTTP_PORT=' .env | cut -d= -f2); http_port=${http_port:-80}
if ! docker compose ps --status running nginx 2> /dev/null | grep -q nginx; then
  for p in "$port" "$http_port"; do
    if command -v ss > /dev/null && ss -ltnH "sport = :$p" | grep -q .; then
      warn "Port $p is already in use on this host. Set PORTAL_PORT (HTTPS) or HTTP_PORT (redirect) in .env to a free port, e.g. PORTAL_PORT=8443, then run ./install.sh again."
      exit 1
    fi
  done
fi

printf '\n  Building and starting…\n'
docker compose build -q reporter
docker compose up -d

# first admin: asked for here, never put on a command line
sleep 3
if docker compose exec -T reporter python report.py user list 2> /dev/null | grep -q "No accounts yet"; then
  printf '\n  Create the first admin account for the portal\n'
  admin=$(ask 'Username' 'admin')
  docker compose exec reporter python report.py user add "$admin" --role admin
fi

suffix=""; [ "$port" = 443 ] || suffix=":$port"
printf '\n  Done. Open https://%s%s and sign in.\n' "$(hostname -I 2> /dev/null | awk '{print $1}')" "$suffix"
note "The first report covers only what Proxmox keeps; uptime and restart history fill in from now on."
