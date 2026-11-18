import datetime as dt

from app import config
from app.periods import period_end, period_label, period_start, report_path, report_title, trend_periods

TZ = config.TZ


def d(*a):
    return dt.datetime(*a, tzinfo=TZ)


def test_weekly_period_is_last_closed_monday_to_sunday():
    end = period_end("weekly", d(2026, 11, 7, 15))  # a Saturday
    assert end == d(2026, 11, 2)                     # Monday 00:00 of the running week
    assert period_start("weekly", end) == d(2026, 10, 26)


def test_monthly_and_quarterly_boundaries():
    assert period_end("monthly", d(2026, 11, 7)) == d(2026, 11, 1)
    assert period_start("monthly", d(2026, 1, 1)) == d(2025, 12, 1)
    assert period_end("quarterly", d(2026, 11, 7)) == d(2026, 10, 1)
    assert period_start("quarterly", d(2026, 1, 1)) == d(2025, 10, 1)


def test_labels():
    assert period_label("weekly", d(2026, 10, 26), d(2026, 11, 2)) == ("Week 44", "26 Oct – 1 Nov 2026")
    assert period_label("monthly", d(2026, 10, 1), d(2026, 11, 1))[0] == "October 2026"
    assert period_label("quarterly", d(2026, 7, 1), d(2026, 10, 1)) == ("Q3 2026", "Jul – Sep 2026")
    assert period_label("custom", d(2026, 11, 6), d(2026, 11, 7)) == ("6 Nov 2026", "1 day")


def test_report_files_and_titles():
    p = report_path("custom", d(2026, 11, 1), d(2026, 11, 8), "summary")
    assert p.name == "custom-2026-11-01-to-2026-11-07-summary.pdf"
    assert report_title("weekly", report_path("weekly", d(2026, 10, 26))) == "Weekly Proxmox Report, Week 44 (26 Oct – 1 Nov 2026)"
    assert report_title("custom", p) == "Proxmox Report for 1 Nov – 7 Nov 2026"


def test_trend_periods_end_with_the_report_period():
    weeks = trend_periods("weekly", d(2026, 10, 26))
    assert len(weeks) == 8 and weeks[-1][0] == d(2026, 10, 26) and weeks[-1][2] == "W44"
    assert [lbl for *_, lbl in trend_periods("quarterly", d(2026, 7, 1))] == ["Q4 25", "Q1 26", "Q2 26", "Q3 26"]
    assert trend_periods("custom", d(2026, 11, 1)) == []


def test_weekly_reports_on_a_chosen_day_cover_the_seven_days_before_it():
    config.WEEK_DAY = 4  # Friday
    try:
        end = period_end("weekly", d(2026, 11, 25, 9))  # a Wednesday
        assert end == d(2026, 11, 20)  # last Friday 00:00
        start = period_start("weekly", end)
        assert start == d(2026, 11, 13)  # Friday to Thursday
        assert period_label("weekly", start, end) == ("Week to Thu 19 Nov", "13 Nov – 19 Nov 2026")
        assert period_end("weekly", d(2026, 11, 20, 0, 1)) == d(2026, 11, 20)  # made on the Friday itself
        assert [lbl for *_, lbl in trend_periods("weekly", start)][-2:] == ["6 Nov", "13 Nov"]
    finally:
        config.WEEK_DAY = 0


def test_monday_keeps_calendar_weeks():
    assert config.WEEK_DAY == 0
    assert period_label("weekly", d(2026, 11, 9), d(2026, 11, 16))[0] == "Week 46"


def test_week_day_setting_is_read_and_kept_in_range():
    s = config.load_settings()
    s["schedule"]["week_day"] = 9
    config.save_settings(s)
    try:
        config.load_settings()
        assert config.WEEK_DAY == 6
    finally:
        s["schedule"]["week_day"] = 0
        config.save_settings(s)
        config.load_settings()
