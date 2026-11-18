"""Hand-written SVG for the PDFs: no charting library, one accent for what matters, muted grid, mono labels."""

import datetime as dt
from html import escape

from . import config

DAY = 86400


def storage_chart(row, warn_pct, accent, end, w=650, h=280, mono="IBM Plex Mono", warn_color=config.WARN,
                  ink="#171717", muted="#6b6b6b", grid="rgba(23,23,23,.08)", axis="rgba(23,23,23,.28)", fs=12):
    """A storage's % used over the last 12 weeks, one point a week, and a dashed line to where it is heading."""
    if not row or not row.get("history"):
        return ""
    weeks = []
    for k in range(12, -1, -1):
        t = end - k * 7 * DAY
        pts = [p for p in row["history"] if p[0] <= t]
        if pts and t - pts[-1][0] < 7 * DAY:
            weeks.append((t, pts[-1][1]))
    if len(weeks) < 2:
        return ""
    proj = None
    if row.get("eta_warn") and row.get("even") and 0 < row["eta_warn"] <= 26 * 7 and row["pct"] is not None:
        proj = (weeks[-1][0] + row["eta_warn"] * DAY, float(warn_pct))
    t0 = weeks[0][0]
    t1 = proj[0] if proj else weeks[-1][0] + 2 * 7 * DAY
    t1 = max(t1, weeks[-1][0] + 7 * DAY)
    lo_data = min(v for _, v in weeks)
    hi_data = max([v for _, v in weeks] + ([warn_pct] if proj or row["pct"] >= warn_pct - 15 else []))
    lo = max(0, int((lo_data - 8) // 10 * 10))
    hi = min(100, int((hi_data + 12) // 10 * 10 + (10 if (hi_data + 12) % 10 else 0)))
    hi = max(hi, lo + 20)
    left, right, top, bottom = 40, w, 24, h - 40

    def x(t):
        return left + (t - t0) / (t1 - t0) * (right - left - 52)

    def y(v):
        return bottom - (v - lo) / (hi - lo) * (bottom - top)

    out = [f'<svg width="{w}" height="{h}" viewBox="0 0 {w} {h}" role="img" aria-label="{escape(row["name"])} '
           f'used over the last 12 weeks" style="display:block">',
           f'<g font-family="{mono}, monospace" font-size="{fs}" fill="{muted}">']
    for v in range(lo, hi + 1, 10):
        is_warn = v == warn_pct
        stroke = warn_color if is_warn else axis if v == lo else grid
        dash = ' stroke-dasharray="4 4"' if is_warn else ""
        out.append(f'<line x1="{left}" x2="{right}" y1="{y(v):.1f}" y2="{y(v):.1f}" stroke="{stroke}"{dash}/>')
        out.append(f'<text x="0" y="{y(v) + 4:.1f}"{" fill=" + chr(34) + warn_color + chr(34) if is_warn else ""}>{v}</text>')
    if warn_pct % 10 and lo < warn_pct < hi:
        out.append(f'<line x1="{left}" x2="{right}" y1="{y(warn_pct):.1f}" y2="{y(warn_pct):.1f}" stroke="{warn_color}" stroke-dasharray="4 4"/>')
    line = " ".join(f"{'M' if i == 0 else 'L'}{x(t):.1f} {y(v):.1f}" for i, (t, v) in enumerate(weeks))
    last_x, last_y = x(weeks[-1][0]), y(weeks[-1][1])
    out.append(f'<path d="{line} L{last_x:.1f} {bottom} L{x(t0):.1f} {bottom} Z" fill="{accent}" fill-opacity="0.08"/>')
    out.append(f'<path d="{line}" fill="none" stroke="{accent}" stroke-width="2" stroke-linejoin="round"/>')
    if proj:
        px, py = x(proj[0]), y(proj[1])
        out.append(f'<path d="M{last_x:.1f} {last_y:.1f} L{px:.1f} {py:.1f}" fill="none" stroke="{accent}" '
                   f'stroke-width="1.5" stroke-dasharray="6 4"/>')
        out.append(f'<rect x="{px - 5:.1f}" y="{py - 4:.1f}" width="10" height="8" rx="2" fill="{warn_color}"/>')
        when = dt.datetime.fromtimestamp(proj[0], config.TZ)
        label = f"{warn_pct}% around {when:%-d %b}"
        out.append(f'<text x="{min(px - 8, right - len(label) * fs * 0.62):.1f}" y="{py - 12:.1f}" fill="{warn_color}">{label}</text>')
    out.append(f'<circle cx="{last_x:.1f}" cy="{last_y:.1f}" r="4" fill="{accent}"/>')
    now_label = f"{weeks[-1][1]:.0f}% now"
    ly = last_y - 8 if last_y - 8 > top + 4 else last_y + 18
    out.append(f'<text x="{last_x + 10:.1f}" y="{ly:.1f}" fill="{ink}">{now_label}</text>')
    # date labels: the start, a few weeks in, the report's end and the projection
    marks = [weeks[0][0], weeks[len(weeks) // 3][0], weeks[-1][0]]
    if proj:
        marks += [weeks[-1][0] + (proj[0] - weeks[-1][0]) / 2, proj[0]] if proj[0] - weeks[-1][0] > 6 * 7 * DAY else []
    last_end = -999
    for t in marks:
        is_end = t == weeks[-1][0]
        label = "this week" if is_end else dt.datetime.fromtimestamp(t, config.TZ).strftime("%-d %b")
        tx = min(x(t) - (12 if is_end else 0), right - len(label) * fs * 0.62)
        if tx < last_end + 12:
            continue
        out.append(f'<text x="{tx:.1f}" y="{h - 12}"{" fill=" + chr(34) + ink + chr(34) if is_end else ""}>{label}</text>')
        last_end = tx + len(label) * fs * 0.62
    out.append("</g></svg>")
    return "".join(out)


def heatmap(rows, accent, w=534, cell_h=20, gap=1, empty="#eeeeee"):
    """One SVG row per heat row: cells shaded by value 0..100, missing cells light grey."""
    out = []
    for r in rows:
        cells = r["cells"]
        n = max(1, len(cells))
        cw = (w - gap * (n - 1)) / n
        parts = [f'<svg width="{w}" height="{cell_h}" viewBox="0 0 {w} {cell_h}" style="display:block">']
        for i, v in enumerate(cells):
            xx = i * (cw + gap)
            if v is None:
                parts.append(f'<rect x="{xx:.2f}" y="0" width="{cw:.2f}" height="{cell_h}" fill="{empty}"/>')
            else:
                op = 0.06 + 0.94 * max(0.0, min(1.0, v / 100))
                parts.append(f'<rect x="{xx:.2f}" y="0" width="{cw:.2f}" height="{cell_h}" fill="{accent}" fill-opacity="{op:.2f}"/>')
        parts.append("</svg>")
        out.append("".join(parts))
    return out
