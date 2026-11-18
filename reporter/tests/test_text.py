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
    assert v["headline"].startswith("A steady week with 2 things to fix: storage on hv-03 is nearly full")
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
    assert not any("backup-nfs" in a["text"] for a in v["attention"])


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
    assert "wasn't recording yet" in v["coverage_note"]
    assert v["fig"]["available"]["value"] == "–"
