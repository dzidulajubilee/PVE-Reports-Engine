"""The made-up report data lives in app/sample.py (Settings previews use it too); this keeps the tests' name."""

from app import config
from app.sample import SIZES, empty, huge, typical  # noqa: F401

if __name__ == "__main__":  # python -m tests.fake_report OUTDIR: every template, typical week, as PDFs
    import sys
    from pathlib import Path

    from app.build import render, render_pdf

    out = Path(sys.argv[1] if len(sys.argv) > 1 else "preview")
    out.mkdir(parents=True, exist_ok=True)
    st = config.load_settings()
    st["general"]["name"] = "Example Co"
    size = sys.argv[2] if len(sys.argv) > 2 else "typical"
    kind = sys.argv[3] if len(sys.argv) > 3 else "weekly"
    which = sys.argv[4].split(",") if len(sys.argv) > 4 else list(config.TEMPLATES)
    for t in which:
        start, end, d, p = SIZES[size](kind)
        html = render(kind, start, end, d, p, st, "full", set(config.SECTION_KEYS), template=t)
        (out / f"{t}.html").write_text(html)
        issues = render_pdf(html, out / f"{t}.pdf")
        print(t, issues or "ok")
