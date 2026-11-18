"""Wording, attention rules and comparisons, from made-up data."""

from app import config
from app.text import change_count, change_pts, fbytes, fdays, view

from tests import fake_report


def v_typical(**gen):
    st = config.load_settings()
    st["general"].update(gen)
    start, end, d, p = fake_report.typical()
    return view(d, p, "weekly", st), d


def test_headline_names_the_most_serious_problems():
    v, _ = v_typical()
    assert v["headline"].startswith("A steady week with 3 things to fix: storage on hv-03 is nearly full")
    assert "3 nightly backups failed" in v["headline"]


def test_attention_is_most_serious_first_and_specific():
    v, _ = v_typical()
    levels = [a["level"] for a in v["attention"]]
    assert levels == sorted(levels, key=lambda x: x != "bad")
    texts = " | ".join(a["text"] for a in v["attention"])
    assert "local-zfs on hv-03 is 87% full" in texts
    assert "dev-sandbox (180) is in no backup job" in texts
    assert "hv-01 and hv-02 need a reboot" in texts
    assert "hv-02 memory peaked at 93%" in texts


def test_storage_threshold_is_a_setting():
    v, _ = v_typical(storage_warn=95)
    assert not any(a["area"] == "capacity" and "backup-nfs" in a["text"] for a in v["attention"])


def test_comparisons_only_when_fair():
    assert change_count(22, 21, "week", True) == "1 more vs last week"
    assert change_count(22, 21, "week", False) == ""
    assert change_pts(62.0, 60.9, "week") == "+1.1 pts vs last week"
    assert change_pts(62.0, 62.01, "week") == "same as last week"


def test_no_comparison_without_snapshots_of_the_period_before():
    st = config.load_settings()
    start, end, d, p = fake_report.typical()
    p["coverage"] = 0.5
    v = view(d, p, "weekly", st)
    assert v["fig"]["available"]["change"] == ""


def test_formats():
    assert fbytes(412e9) == "412 GB" and fbytes(11.2e12) == "11.2 TB" and fbytes(1.9e12) == "1.90 TB" and fbytes(None) == "–"
    assert fdays(21) == "3 weeks" and fdays(3) == "3 days"


def test_empty_cluster_reads_plainly():
    st = config.load_settings()
    start, end, d, p = fake_report.empty()
    v = view(d, p, "weekly", st)
    assert v["headline"] == "A quiet week: nothing needs attention."
    assert "wasn't recording yet" in v["coverage_note"] and "own counters" in v["coverage_note"]
    assert v["fig"]["available"]["value"] == "–"


def test_incomplete_data_is_never_called_quiet():
    st = config.load_settings()
    start, end, d, p = fake_report.empty()
    d["_errors"] = 30
    v = view(d, p, "weekly", st)
    assert v["headline"].startswith("This report is incomplete: 30 requests to Proxmox failed")
    assert "incomplete" in v["verdict"]


def test_unrecorded_time_is_never_drawn_or_counted_as_up():
    st = config.load_settings()
    start, end, d, p = fake_report.typical()
    d.update(coverage=0.0, available_pct=None, recorded=[], logs_cover=True, logged=[])
    for n in d["nodes"]:
        n["uptime"] = None  # no uptime counter either: nothing proves they ran
    v = view(d, p, "weekly", st)
    assert v["fig"]["available"]["value"] == "–"
    assert v["fig"]["available"]["change"] == "not recorded; no quorum loss logged"
    node_lanes = [lane for lane in v["timeline"]["lanes"] if lane["bar"]]
    assert all(not lane["bars"] and lane["norec"] for lane in node_lanes)


def test_logged_quorum_loss_is_reported_as_a_fact():
    st = config.load_settings()
    start, end, d, p = fake_report.typical()
    t = d["start"] + 86400 + 3 * 3600
    d.update(coverage=0.0, available_pct=None, recorded=[],
             logged=[{"t": t, "kind": "lost", "nodes": ["hv-02"], "message": "node lost quorum"},
                     {"t": t + 600, "kind": "back", "nodes": ["hv-02"], "message": "node has quorum"}])
    v = view(d, p, "weekly", st)
    assert v["fig"]["available"]["change"] == "quorum loss logged once"
    a = v["attention"][0]
    assert a["level"] == "bad" and a["text"].startswith("Quorum lost Tue 10 Nov 03:00, back Tue 03:10 (logged by hv-02)")


def test_single_node_reads_as_a_server():
    st = config.load_settings()
    start, end, d, p = fake_report.typical()
    d["topology"] = {"cluster": False, "nodes": 1, "shared": False, "node_ips": {}}
    d["nodes"] = d["nodes"][:1]
    d.update(coverage=0.0, available_pct=None, recorded=[], logged=[])
    d["nodes"][0]["uptime"] = 41 * 86400  # booted long before the week: its counter proves it ran throughout
    v = view(d, p, "weekly", st)
    assert v["fig"]["available"]["label"] == "Server up" and v["fig"]["available"]["value"] == "100%"
    assert "(uptime)" in v["fig"]["available"]["change"]
    assert "The server has been running since" in v["subline"] and "quorum" not in v["subline"]
    lane = v["timeline"]["lanes"][0]
    assert lane["bars"] and not lane["norec"]


def test_local_only_cluster_charts_every_node():
    st = config.load_settings()
    start, end, d, p = fake_report.typical()
    d["storage"] = [s for s in d["storage"] if not s["shared"]]
    v = view(d, p, "weekly", st)
    assert [r["node"] for r in v["chart_rows"]] == ["hv-03", "hv-01", "hv-02"]
    assert v["chart_caption"] == "Guest storage per node, last 12 weeks"
