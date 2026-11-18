"""Builds a report: collects the data once and renders the full and summary PDFs through the chosen template."""

import datetime as dt
import json

from jinja2 import Environment, FileSystemLoader
from markupsafe import Markup

from . import config, layout
from .charts import heatmap, storage_chart
from .collect import collect
from .config import OUT, STYLES, TEMPLATES, load_settings, log, logo_path, look
from .periods import period_end, period_label, period_start, report_path
from .pve import ApiError, Source, pbs_client, pve_client
from .text import fbytes, fmem, fnum, fpct, view

# Fonts and accent-free styling of each template's charts
CHART_LOOK = {
    "logbook": {"mono": "IBM Plex Mono", "ink": "#171717", "muted": "#6b6b6b"},
    "original": {"mono": "IBM Plex Mono", "ink": "#1b1b1a", "muted": "#6f6e69", "fs": 11},
    "briefing": {"mono": "Inter", "ink": "#111827", "muted": "#6b7487"},
    "ledger": {"mono": "JetBrains Mono", "ink": "#171717", "muted": "#737373", "fs": 11},
    "rack": {"mono": "IBM Plex Mono", "ink": "#171717", "muted": "#6b6b6b"},
    "pulse": {"mono": "Geist Mono", "ink": "#111827", "muted": "#6b7487"},
}
CONTENT_WIDTH = {"logbook": 650, "original": 666, "briefing": 634, "ledger": 682, "rack": 666, "pulse": 666}


def render_pdf(html_out, pdf):
    """Lay the report out, check the page flow, and if the check finds problems lay it out once more with looser
    page breaks, keeping whichever has fewer. Writes the PDF and returns the problems left (usually none)."""
    from weasyprint import HTML  # imported late: slow to load, not needed until a report is due

    doc = HTML(string=html_out, base_url=str(config.ROOT)).render()
    issues = layout.problems(doc)
    if issues:
        loose = html_out.replace("</head>", f"<style>{layout.LOOSE_CSS}</style></head>", 1)
        doc2 = HTML(string=loose, base_url=str(config.ROOT)).render()
        issues2 = layout.problems(doc2)
        log.info("layout: %d problem(s), %d with looser page breaks", len(issues), len(issues2))
        if len(issues2) < len(issues):
            doc, issues = doc2, issues2
    for issue in issues:
        log.warning("%s layout: %s", pdf.name, issue)
    tmp = pdf.with_suffix(".part")
    doc.write_pdf(tmp)
    tmp.replace(pdf)  # appears in the portal only once complete
    return issues


def css_string(text):
    """A CSS string literal that can't end the style element it sits in."""
    return Markup(json.dumps(str(text), ensure_ascii=False).replace("<", "\\3c ").replace(">", "\\3e "))


def environment():
    env = Environment(loader=FileSystemLoader(config.ROOT / "templates"), autoescape=True, trim_blocks=True,
                      lstrip_blocks=True)
    env.filters.update(bytes=fbytes, mem=fmem, num=fnum, pct=fpct, css=css_string)
    return env


def report_name(kind, title):
    return f"{kind.capitalize()} cluster report, {title}" if kind != "custom" else f"Cluster report for {title}"


def render(kind, start, end, d, p, st, style, shown, template=None, accent=None, generated=None):
    """The HTML of one version of a report. Used by build() and by the render tests and previews."""
    lk = look(st)
    template = template or lk["template"]
    accent = accent or (lk["accent"] if template == lk["template"] else TEMPLATES[template]["accent"])
    accent_ink = config.ink(accent)
    v = view(d, p, kind, st, shown)
    title, dates = period_label(kind, start, end)
    name = st["general"]["name"].strip()
    week_word = {"weekly": f"week {start.isocalendar().week}", "monthly": f"{start:%B %Y}",
                 "quarterly": f"Q{(start.month - 1) // 3 + 1} {start.year}"}.get(kind, title)
    cl = CHART_LOOK[template]
    w = CONTENT_WIDTH[template]
    chart = storage_chart(v["main"], v["warn_pct"], accent, d["end"], w=w, h=280 if style == "full" else 200,
                          mono=cl["mono"], ink=cl["ink"], muted=cl["muted"], fs=cl.get("fs", 12))
    heat_w = w - 96
    heat = heatmap(v["heat"]["rows"], accent, w=heat_w) if template == "pulse" else []
    pve_url = st["general"].get("pve_url", "").rstrip("/") if st["general"].get("pve_links", True) else ""
    logo = logo_path()
    generated = generated or dt.datetime.now(config.TZ)
    period_word = {"weekly": "week", "monthly": "month", "quarterly": "quarter"}.get(kind, "period")
    footer_left = " · ".join(x for x in (name, "Cluster report" if template != "briefing" else "Cluster briefing",
                                         dates if kind != "custom" else title) if x)
    if template == "ledger":
        footer_left = f"{(name or 'cluster').lower().replace(' ', '-')} · cluster-report · " + (
            f"{start:%G}-W{start.isocalendar().week:02d}" if kind == "weekly" else f"{start:%Y-%m-%d}")
    cl = d.get("cluster_name") or "Proxmox cluster"
    kicker = name if name and name.lower() == cl.lower() else f"{name or 'Proxmox'} · {cl}"  # never "Acme · Acme"
    env = environment()
    return env.get_template(f"reports/{template}/report.html").render(
        T=template, name=name, kind=kind, title=title, dates=dates, week_word=week_word, period_word=period_word,
        report_title=report_name(kind, title), start=start, end=end, last=end - dt.timedelta(days=1),
        style=style, on=shown, d=d, p=p, v=v, chart=chart, heat=heat, heat_w=heat_w,
        accent=accent, accent_ink=accent_ink, ok=config.OK, warn=config.WARN, bad=config.BAD,
        fonts=(config.ROOT / "fonts").as_uri(), logo=logo.as_uri() if logo else None, generated=generated,
        pve_url=pve_url, footer_left=footer_left, tz=str(config.TZ), errors=d.get("_errors", 0),
        denied=d.get("_denied", []), cluster=d.get("cluster_name") or "Proxmox cluster",
        kicker=kicker,
    )


def build(kind, end=None, start=None):
    st = load_settings()  # applies the time zone before any dates are worked out
    end = end or period_end(kind, dt.datetime.now(config.TZ))
    start = start or period_start(kind, end)
    prev_start = period_start(kind, start, end - start)
    title, dates = period_label(kind, start, end)
    show = {"full": {k for k, v in st["sections"].items() if v}, "summary": {k for k, v in st["sections_summary"].items() if v}}
    lk = look(st)
    log.info("building %s report %s (%s) with the %s template", kind, title, dates, lk["template"])
    try:
        client = pve_client(st)
    except ApiError as e:
        raise RuntimeError(f"the Proxmox connection isn't set up yet ({e}). An admin can add it in Settings") from e
    S = Source(client, pbs_client(st) if ("pbs" in show["full"] | show["summary"]) else None)
    d = collect(S, start, end, st, full=True)
    if not d["api_ok"]:
        reason = S.errors[0][1] if S.errors else "no data came back"
        raise RuntimeError(f"Proxmox couldn't be read: {reason}")
    p = collect(S, prev_start, start, st, full=False)
    d["_errors"] = len(S.errors)
    d["_denied"] = sorted(S.denied)
    OUT.mkdir(parents=True, exist_ok=True)
    pdfs, layout_issues, lines = {}, {}, []
    for style in STYLES:
        html_out = render(kind, start, end, d, p, st, style, show[style])
        pdf = report_path(kind, start, end, style)
        layout_issues[style] = render_pdf(html_out, pdf)
        pdfs[style] = pdf
    v = view(d, p, kind, st, show["full"])
    lines = [v["headline"], v["subline"]] + [f"– {a['text']}" for a in v["attention"][:6]]
    name = st["general"]["name"].strip()
    what = report_name(kind, title)
    subject = f"{name}: {what} ({dates})" if name else f"{what} ({dates})"
    log.info("wrote %s and its summary (%d API calls failed)", pdfs["full"], len(S.errors))
    return pdfs, subject, lines, len(S.errors), layout_issues
