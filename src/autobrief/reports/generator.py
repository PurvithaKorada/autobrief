"""HTML and PDF report generation."""

from __future__ import annotations

import base64
import io
import logging
import os
from datetime import datetime
from pathlib import Path
from xml.sax.saxutils import escape

from jinja2 import Environment, PackageLoader, select_autoescape
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    HRFlowable,
    Image,
    KeepTogether,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from autobrief import __version__
from autobrief.exceptions import ReportError
from autobrief.processing.analyzer import AnalysisResult
from autobrief.reports.charts import precipitation_chart, temperature_chart

logger = logging.getLogger(__name__)

FORMATS = ("html", "pdf", "both")
NAVY = colors.HexColor("#0B1F3A")
TEAL = colors.HexColor("#0E9AA7")
MUTED = colors.HexColor("#64748B")
LINE = colors.HexColor("#E2E8F0")
ZEBRA = colors.HexColor("#F8FAFC")


def _fmt_timestamp(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%d %H:%M %Z").strip()


def _table_rows(analysis: AnalysisResult) -> list[dict[str, str]]:
    return [
        {
            "date": row.date.strftime("%a %d %b %Y"),
            "high": f"{row.temp_max:.1f}",
            "low": f"{row.temp_min:.1f}",
            "mean": f"{row.temp_mean:.1f}",
            "rain": f"{row.precipitation:.1f}",
            "wind": f"{row.wind_max:.1f}",
        }
        for row in analysis.frame.itertuples()
    ]


def _current_line(analysis: AnalysisResult) -> str:
    c, u = analysis.current, analysis.units
    parts = []
    if "temperature_2m" in c:
        parts.append(f"{c['temperature_2m']:.1f}{u['temp']}")
    if "relative_humidity_2m" in c:
        parts.append(f"humidity {c['relative_humidity_2m']:.0f}%")
    if "wind_speed_10m" in c:
        parts.append(f"wind {c['wind_speed_10m']:.1f} {u['wind']}")
    return ", ".join(parts)


class ReportGenerator:
    """Writes timestamped HTML/PDF reports into ``reports_dir``."""

    def __init__(self, reports_dir: Path) -> None:
        self.reports_dir = Path(reports_dir)
        self._jinja = Environment(
            loader=PackageLoader("autobrief.reports", "templates"),
            autoescape=select_autoescape(["html", "j2"]),
        )

    def generate(self, analysis: AnalysisResult, fmt: str = "both", generated_at: datetime | None = None) -> list[Path]:
        """Create the requested report(s) and return their paths.

        Raises:
            ReportError: unknown format, or the files could not be written.
        """
        if fmt not in FORMATS:
            raise ReportError(f"Unsupported report format {fmt!r}.", hint=f"Choose one of: {', '.join(FORMATS)}.")
        moment = generated_at or datetime.now().astimezone()
        extensions = ["html", "pdf"] if fmt == "both" else [fmt]
        stem = self._unique_stem(moment, extensions)

        try:
            temp_png, rain_png = temperature_chart(analysis), precipitation_chart(analysis)
        except Exception as exc:  # chart libs can raise many types; wrap with context
            logger.exception("Chart rendering failed")
            raise ReportError(f"Could not render charts ({type(exc).__name__}).") from exc

        paths: list[Path] = []
        for ext in extensions:
            content = (
                self._render_html(analysis, moment, temp_png, rain_png).encode("utf-8")
                if ext == "html"
                else self._render_pdf(analysis, moment, temp_png, rain_png)
            )
            path = self.reports_dir / f"{stem}.{ext}"
            self._write(path, content)
            logger.info("Report generated: %s (%d bytes)", path, len(content))
            paths.append(path)
        return paths

    # ------------------------------------------------------------------ files
    def _unique_stem(self, moment: datetime, extensions: list[str]) -> str:
        base = f"autobrief_{moment:%Y-%m-%d_%H-%M}"
        candidates = [base, f"{base}-{moment:%S}"] + [f"{base}-{moment:%S}_{n}" for n in range(2, 100)]
        for stem in candidates:
            if not any((self.reports_dir / f"{stem}.{ext}").exists() for ext in extensions):
                return stem
        raise ReportError("Could not find a free report filename.")

    def _write(self, path: Path, content: bytes) -> None:
        tmp = path.with_suffix(path.suffix + ".tmp")
        try:
            self.reports_dir.mkdir(parents=True, exist_ok=True)
            tmp.write_bytes(content)
            os.replace(tmp, path)
        except OSError as exc:
            logger.error("Could not write report %s: %s", path, exc)
            try:
                tmp.unlink(missing_ok=True)  # best-effort cleanup; must never mask the real error
            except OSError as cleanup_exc:
                logger.debug("Temp file cleanup failed: %s", cleanup_exc)
            raise ReportError(f"Could not write report file ({exc.strerror or type(exc).__name__}).") from exc

    # ------------------------------------------------------------------- html
    def _render_html(self, a: AnalysisResult, moment: datetime, temp_png: bytes, rain_png: bytes) -> str:
        template = self._jinja.get_template("report.html.j2")
        return template.render(
            location=a.location,
            generated_at=_fmt_timestamp(moment),
            source_name=a.source_name,
            source_url=a.source_url,
            summary=a.summary,
            metrics=a.metrics,
            insights=a.insights,
            rows=_table_rows(a),
            units=a.units,
            current_line=_current_line(a),
            timezone=a.timezone,
            coordinates=f"{a.latitude:.4f}, {a.longitude:.4f}",
            dropped_rows=a.dropped_rows,
            temperature_chart=base64.b64encode(temp_png).decode("ascii"),
            precipitation_chart=base64.b64encode(rain_png).decode("ascii"),
            version=__version__,
        )

    # -------------------------------------------------------------------- pdf
    def _render_pdf(self, a: AnalysisResult, moment: datetime, temp_png: bytes, rain_png: bytes) -> bytes:
        base = getSampleStyleSheet()
        body = ParagraphStyle(
            "body",
            parent=base["Normal"],
            fontName="Helvetica",
            fontSize=10,
            leading=14.5,
            textColor=colors.HexColor("#1E293B"),
        )
        h2 = ParagraphStyle(
            "h2", parent=body, fontName="Helvetica-Bold", fontSize=9.5, textColor=TEAL, spaceBefore=14, spaceAfter=6
        )
        small = ParagraphStyle("small", parent=body, fontSize=8, leading=11, textColor=MUTED)
        e = escape

        def section(title: str) -> list:
            return [Paragraph(title.upper(), h2), HRFlowable(width="100%", thickness=0.6, color=LINE, spaceAfter=6)]

        header = Table(
            [
                [Paragraph('<font color="#7DD3DB" size="9"><b>A U T O B R I E F</b></font>', body)],
                [
                    Paragraph(
                        '<font color="white" size="21"><b>Automated Intelligence Report</b></font>',
                        ParagraphStyle("t", parent=body, leading=26),
                    )
                ],
                [
                    Paragraph(
                        f'<font color="#CBD5E1" size="9">Generated: '
                        f'<font color="white">{e(_fmt_timestamp(moment))}</font>'
                        f'&nbsp;&nbsp;|&nbsp;&nbsp;Source: <font color="white">{e(a.source_name)}</font>'
                        f'&nbsp;&nbsp;|&nbsp;&nbsp;Location: <font color="white">{e(a.location)}</font></font>',
                        body,
                    )
                ],
            ],
            colWidths=[174 * mm],
        )
        header.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, -1), NAVY),
                    ("LEFTPADDING", (0, 0), (-1, -1), 14),
                    ("TOPPADDING", (0, 0), (0, 0), 14),
                    ("BOTTOMPADDING", (0, -1), (-1, -1), 14),
                ]
            )
        )

        cards = Table(
            [
                [
                    Paragraph(
                        f'<font size="7.5" color="#64748B">{e(m.label.upper())}</font><br/>'
                        f'<font size="15" color="#0B1F3A"><b>{e(m.value)}</b></font><br/>'
                        f'<font size="7" color="#64748B">{e(m.detail)}</font>',
                        ParagraphStyle("c", parent=body, leading=15),
                    )
                    for m in a.metrics
                ]
            ],
            colWidths=[43.5 * mm] * len(a.metrics),
        )
        cards.setStyle(
            TableStyle(
                [
                    ("BOX", (0, 0), (-1, -1), 0.6, LINE),
                    ("INNERGRID", (0, 0), (-1, -1), 0.6, LINE),
                    ("BACKGROUND", (0, 0), (-1, -1), ZEBRA),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("TOPPADDING", (0, 0), (-1, -1), 8),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
                ]
            )
        )

        u = a.units
        data = [
            [
                "Date",
                f"High ({u['temp']})",
                f"Low ({u['temp']})",
                f"Mean ({u['temp']})",
                f"Rain ({u['precip']})",
                f"Wind ({u['wind']})",
            ]
        ]
        data += [[r["date"], r["high"], r["low"], r["mean"], r["rain"], r["wind"]] for r in _table_rows(a)]
        grid = Table(data, colWidths=[40 * mm] + [26.8 * mm] * 5, repeatRows=1)
        grid.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), NAVY),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                    ("FONTSIZE", (0, 0), (-1, -1), 8.5),
                    ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
                    ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, ZEBRA]),
                    ("LINEBELOW", (0, 1), (-1, -1), 0.4, LINE),
                    ("TOPPADDING", (0, 0), (-1, -1), 5),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                ]
            )
        )

        def chart(png: bytes) -> Image:
            img = Image(io.BytesIO(png))
            ratio = img.imageHeight / img.imageWidth
            img.drawWidth, img.drawHeight = 170 * mm, 170 * mm * ratio
            return img

        story: list = [header, Spacer(1, 4)]
        story += section("Executive summary") + [Paragraph(e(a.summary), body)]
        story += section("Key metrics") + [cards]
        if _current_line(a):
            story.append(Paragraph(f"Current conditions: {e(_current_line(a))}", small))
        story += section("Data analysis") + [chart(temp_png), Spacer(1, 4), chart(rain_png), Spacer(1, 8), grid]
        if a.dropped_rows:
            story.append(Paragraph(f"{a.dropped_rows} incomplete record(s) were excluded during validation.", small))
        story += [
            KeepTogether(
                section("Insights")
                + [
                    Paragraph(
                        f"&bull;&nbsp;&nbsp;{e(i)}",
                        ParagraphStyle("li", parent=body, leftIndent=12, firstLineIndent=-12, spaceAfter=4),
                    )
                    for i in a.insights
                ]
            )
        ]
        story += [
            KeepTogether(
                section("Data source")
                + [
                    Paragraph(
                        f"<b>Provider:</b> {e(a.source_name)}<br/><b>Endpoint:</b> {e(a.source_url)}<br/>"
                        f"<b>Coordinates:</b> {a.latitude:.4f}, {a.longitude:.4f} &nbsp; "
                        f"<b>Timezone:</b> {e(a.timezone)}<br/>"
                        f"<b>Records used:</b> {len(a.frame)} daily forecast records",
                        body,
                    )
                ]
            )
        ]

        def on_page(canvas, doc) -> None:
            canvas.saveState()
            canvas.setStrokeColor(LINE)
            canvas.line(18 * mm, 16 * mm, 192 * mm, 16 * mm)
            canvas.setFont("Helvetica", 8)
            canvas.setFillColor(MUTED)
            canvas.drawString(18 * mm, 11 * mm, f"Generated automatically by AutoBrief v{__version__}")
            canvas.drawRightString(192 * mm, 11 * mm, f"Page {doc.page}")
            canvas.restoreState()

        buffer = io.BytesIO()
        doc = SimpleDocTemplate(
            buffer,
            pagesize=A4,
            leftMargin=18 * mm,
            rightMargin=18 * mm,
            topMargin=14 * mm,
            bottomMargin=22 * mm,
            title=f"AutoBrief report - {a.location}",
            author="AutoBrief",
        )
        doc.build(story, onFirstPage=on_page, onLaterPages=on_page)
        return buffer.getvalue()
