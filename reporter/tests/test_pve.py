"""The API client: addresses, plain-language errors, and certificate pinning that is checked before the token
is sent."""

import socket
import ssl

import pytest
from app import pve


def test_addresses():
    assert pve._split("pve1", 8006) == ("pve1", 8006)
    assert pve._split("pve1:8443", 8006) == ("pve1", 8443)
    assert pve._split("https://pbs.example:8007/", 8007) == ("pbs.example", 8007)
    assert pve._split("[2001:db8::1]:8006", 8006) == ("2001:db8::1", 8006)


def test_token_header():
    c = pve.Client(["pve1"], "reporter@pve!reports", "s3cret", fingerprint="AA:" * 31 + "AA")
    assert c.auth == "PVEAPIToken=reporter@pve!reports=s3cret"
    b = pve.Client(["pbs"], "reporter@pbs!reports", "s3cret", fingerprint="AA:" * 31 + "AA", product="pbs")
    assert b.auth == "PBSAPIToken=reporter@pbs!reports:s3cret"


def test_checking_cannot_be_switched_off():
    with pytest.raises(pve.ApiError):
        pve.Client(["pve1"], "a@pve!b", "c", verify=False)


def test_several_fingerprints():
    fp1, fp2 = "AA:" * 31 + "AA", "BB:" * 31 + "BB"
    c = pve.Client(["pve1", "pve2"], "a@pve!b", "c", fingerprint=f"{fp1}, {fp2}")
    assert c.fingerprints == {"aa" * 32, "bb" * 32}


def test_failover_and_plain_errors(monkeypatch):
    c = pve.Client(["pve1", "pve2"], "a@pve!b", "c", fingerprint="AA:" * 31 + "AA")
    calls = []

    def fake(host, port, path):
        calls.append(host)
        if host == "pve1":
            raise ConnectionRefusedError()
        return {"ok": True}

    monkeypatch.setattr(c, "_request", fake)
    assert c.get("/version") == {"ok": True} and calls == ["pve1", "pve2"]
    assert c.get("/version") and calls[-1] == "pve2"  # the node that worked is tried first next time
    assert "token" in pve.describe(pve.ApiError("401", status=401))
    assert "certificate" in pve.describe(ssl.SSLCertVerificationError())
    assert "address" in pve.describe(socket.gaierror())


def test_source_counts_failures_and_permission_refusals():
    class Boom:
        def get(self, path, **kw):
            raise pve.ApiError("403 Permission check failed", status=403, path=path)

    s = pve.Source(Boom())
    assert s.get("/cluster/log") is None
    assert s.errors and "/cluster/log" in s.denied
