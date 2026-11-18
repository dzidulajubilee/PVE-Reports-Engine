"""The scheduler makes a weekly report on the chosen day, after the send hour, once per period."""

import datetime as dt

from app import config, history, schedule


def test_weekly_report_is_due_on_the_chosen_day():
    s = config.load_settings()
    s["schedule"].update(enabled=True, hour=7, kinds=["weekly"], week_day=4)  # Friday
    config.save_settings(s)
    tz = config.TZ
    try:
        (config.OUT / ".scheduler-since").write_text(dt.datetime(2026, 11, 1, tzinfo=tz).isoformat())
        history.SENT.unlink(missing_ok=True)
        history.start_record({})
        assert schedule.due_reports(dt.datetime(2026, 11, 20, 6, 0, tzinfo=tz)) == []  # Friday, before the send hour
        due = schedule.due_reports(dt.datetime(2026, 11, 20, 8, 0, tzinfo=tz))
        assert due == [("weekly", dt.datetime(2026, 11, 20, tzinfo=tz))]
        history.mark_sent("weekly", due[0][1])
        assert schedule.due_reports(dt.datetime(2026, 11, 21, 8, 0, tzinfo=tz)) == []  # sent once
    finally:
        s["schedule"].update(week_day=0, kinds=["weekly", "monthly", "quarterly"])
        config.save_settings(s)
        config.load_settings()
