"""Render tests: made-up data through every real template and WeasyPrint, in three sizes. The layout check must
find nothing, so a template change that breaks page flow fails here before anyone receives a report."""

import pytest
from app import config
from app.build import render, render_pdf

from tests import fake_report


@pytest.mark.parametrize("template", list(config.TEMPLATES))
@pytest.mark.parametrize("size", ["empty", "typical", "huge"])
def test_every_template_lays_out_cleanly(template, size, tmp_path):
    st = config.load_settings()
    st["general"]["name"] = "Example Co"
    start, end, d, p = fake_report.SIZES[size]("weekly")
    for style, shown in (("full", set(config.SECTION_KEYS)), ("summary", set())):
        html = render("weekly", start, end, d, p, st, style, shown, template=template)
        pdf = tmp_path / f"{template}-{size}-{style}.pdf"
        assert render_pdf(html, pdf) == []
        assert pdf.stat().st_size > 5000


@pytest.mark.parametrize("kind", ["monthly", "quarterly"])
@pytest.mark.parametrize("template", ["logbook", "ledger", "pulse"])
def test_longer_periods(template, kind, tmp_path):
    st = config.load_settings()
    start, end, d, p = fake_report.typical(kind)
    html = render(kind, start, end, d, p, st, "full", set(config.SECTION_KEYS), template=template)
    assert render_pdf(html, tmp_path / "r.pdf") == []


def test_summary_is_one_page(tmp_path):
    from weasyprint import HTML

    st = config.load_settings()
    start, end, d, p = fake_report.typical()
    for t in config.TEMPLATES:
        html = render("weekly", start, end, d, p, st, "summary", set(), template=t)
        assert len(HTML(string=html, base_url=str(config.ROOT)).render().pages) == 1, t
