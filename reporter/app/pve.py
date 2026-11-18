"""A small read-only client for the Proxmox VE and Proxmox Backup Server APIs.

- Authenticates with an API token (PVEAPIToken= / PBSAPIToken=), never a password.
- Checks certificates against the cluster's CA (pasted in Settings) or a pinned SHA-256 fingerprint. The
  fingerprint is compared before the token is sent. Checking is never switched off quietly.
- Several node addresses: when one doesn't answer, the next is tried, and the one that worked is tried first
  next time.
- Every call made for a report goes through Source.get, which logs a failure, counts it and returns None, so one
  failed call never stops a report.
"""

import hashlib
import http.client
import json
import re
import socket
import ssl
import threading
import urllib.parse

from .config import log

TIMEOUT = 20


class ApiError(Exception):
    def __init__(self, message, status=None, path=None):
        super().__init__(message)
        self.status, self.path = status, path


class CertificateMismatch(ApiError):
    pass


def _norm_fp(fp):
    return (fp or "").replace(":", "").replace(" ", "").strip().lower()


def _split(host, default_port):
    """'pve1', 'pve1:8006', 'https://pve1:8006/' or '[2001:db8::1]:8006' -> (host, port)."""
    h = host.strip()
    if "://" in h:
        u = urllib.parse.urlsplit(h)
        return u.hostname, u.port or default_port
    if h.startswith("["):
        name, _, rest = h[1:].partition("]")
        return name, int(rest[1:]) if rest.startswith(":") else default_port
    if h.count(":") == 1:
        name, port = h.split(":")
        return name, int(port)
    return h, default_port


class Client:
    def __init__(self, hosts, token_id, token_secret, ca_pem="", fingerprint="", verify=True, product="pve"):
        if not hosts:
            raise ApiError("no Proxmox address is set up")
        if not token_id or not token_secret:
            raise ApiError("no API token is set up")
        self.product = product
        port = 8006 if product == "pve" else 8007
        self.hosts = [_split(h, port) for h in hosts]
        scheme = "PVEAPIToken" if product == "pve" else "PBSAPIToken"
        sep = "=" if product == "pve" else ":"
        self.auth = f"{scheme}={token_id}{sep}{token_secret}"
        # one pinned fingerprint per node is fine: each node has its own certificate
        self.fingerprints = {_norm_fp(f) for f in re.split(r"[,\s]+", fingerprint or "") if f.strip()}
        if self.fingerprints:
            # identity comes from the pinned fingerprint, checked by hand below
            ctx = ssl.create_default_context()
            ctx.check_hostname, ctx.verify_mode = False, ssl.CERT_NONE
        elif ca_pem.strip():
            ctx = ssl.create_default_context(cadata=ca_pem)
            # cluster certificates name the node; addresses typed as IPs are in them too on a default install
        else:
            ctx = ssl.create_default_context()
            if not verify:
                raise ApiError("certificate checking can't be off: paste the cluster CA or a fingerprint")
        self.ctx = ctx
        self._first = 0
        self._lock = threading.Lock()

    def _request(self, host, port, path):
        conn = http.client.HTTPSConnection(host, port, context=self.ctx, timeout=TIMEOUT)
        try:
            conn.connect()
            if self.fingerprints:
                der = conn.sock.getpeercert(binary_form=True)
                got = hashlib.sha256(der).hexdigest()
                if got not in self.fingerprints:
                    raise CertificateMismatch(
                        f"{host} showed a certificate with fingerprint {':'.join(got[i:i+2] for i in range(0, 64, 2)).upper()}, "
                        "not the one saved in Settings",
                        path=path,
                    )
            conn.request("GET", path, headers={"Authorization": self.auth, "Accept": "application/json"})
            r = conn.getresponse()
            body = r.read()
            if r.status != 200:
                reason = r.reason
                try:
                    reason = json.loads(body).get("message") or reason
                except ValueError:
                    pass
                raise ApiError(f"{r.status} {reason}".strip(), status=r.status, path=path)
            return json.loads(body).get("data")
        finally:
            conn.close()

    def get(self, path, **params):
        params = {k: v for k, v in params.items() if v is not None}
        prefix = "/api2/json"
        full = prefix + path + ("?" + urllib.parse.urlencode(params) if params else "")
        order = list(range(len(self.hosts)))
        order = order[self._first :] + order[: self._first]
        last = None
        for i in order:
            host, port = self.hosts[i]
            try:
                data = self._request(host, port, full)
                if i != self._first:
                    with self._lock:
                        self._first = i
                return data
            except CertificateMismatch:
                raise
            except ApiError as e:
                if e.status and e.status < 500:  # the node answered: another node would say the same
                    raise
                last = e
            except (OSError, socket.timeout, ssl.SSLError, http.client.HTTPException) as e:
                last = e
        raise ApiError(describe(last), path=path)


def describe(e):
    """Plain-language reason a call failed."""
    if e is None:
        return "no address answered"
    if isinstance(e, ssl.SSLCertVerificationError):
        return ("the certificate couldn't be checked: paste the cluster CA (/etc/pve/pve-root-ca.pem) or the "
                "certificate fingerprint in Settings")
    if isinstance(e, ApiError):
        if e.status == 401:
            return "the API token was rejected: check the token ID and secret"
        if e.status == 403:
            m = re.search(r"\(([^,()]+), ([\w.]+)\)", str(e))  # Proxmox says e.g. "(/, Sys.Audit)"
            missing = f" ({m[2]} on {m[1]})" if m else ""
            return (f"the API token is missing a permission{missing}. With privilege separation, give the "
                    "ReportReader role on / to both the user and the token; see docs/proxmox-setup.md")
        return str(e)
    if isinstance(e, (socket.timeout, TimeoutError)):
        return "no answer: check the address and that port 8006 is reachable"
    if isinstance(e, ConnectionRefusedError):
        return "the connection was refused: check the address and port"
    if isinstance(e, socket.gaierror):
        return "the address couldn't be found"
    return str(e) or e.__class__.__name__


def pve_client(settings):
    p = settings["pve"]
    return Client(p["hosts"], p["token_id"], p["token_secret"], p.get("ca_pem", ""), p.get("fingerprint", ""),
                  p.get("verify", True), "pve")


def pbs_client(settings):
    p = settings["pbs"]
    if not p.get("enabled") or not p.get("url"):
        return None
    return Client([p["url"]], p["token_id"], p["token_secret"], p.get("ca_pem", ""), p.get("fingerprint", ""),
                  True, "pbs")


def test(settings, product="pve"):
    """None when the connection works, else a plain-language reason."""
    try:
        c = pve_client(settings) if product == "pve" else pbs_client(settings)
        if c is None:
            return "Proxmox Backup Server isn't switched on"
        if product == "pve":
            c.get("/version")
            c.get("/cluster/resources", type="node")
        else:
            c.get("/version")
            c.get("/admin/datastore")
        return None
    except Exception as e:  # noqa: BLE001
        return describe(e)


class Source:
    """API access for one report: failures become None and are counted, never raised."""

    def __init__(self, client, pbs=None):
        self.client, self.pbs = client, pbs
        self.errors = []  # [(path, reason)]
        self.denied = set()  # paths refused for lack of permission

    def get(self, path, **params):
        return self._safe(self.client, path, params)

    def pbs_get(self, path, **params):
        return self._safe(self.pbs, path, params) if self.pbs else None

    def _safe(self, client, path, params):
        if client is None:
            return None
        try:
            return client.get(path, **params)
        except Exception as e:  # noqa: BLE001 - shown as – in the report and counted
            if isinstance(e, ApiError) and e.status == 403:
                self.denied.add(path)
            log.warning("API call %s failed: %s", path, describe(e))
            self.errors.append((path, describe(e)))
            return None
