import re
from datetime import UTC, datetime

import pytest
from pypdf import PdfReader

from autobrief.exceptions import ReportError
from autobrief.processing.analyzer import analyze
from autobrief.reports.generator import ReportGenerator

FIXED = datetime(2026, 10, 4, 11, 30, 15, tzinfo=UTC)


def test_html_report_contains_all_sections_and_data(tmp_path, analysis):
    [path] = ReportGenerator(tmp_path).generate(analysis, "html", FIXED)
    html = path.read_text(encoding="utf-8")

    for expected in (
        "AUTOBRIEF",
        "Automated Intelligence Report",
        "2026-10-04 11:30 UTC",
        "Open-Meteo Forecast API",
        "Executive summary",
        "Key metrics",
        "Data analysis",
        "Insights",
        "Data source",
        analysis.summary,
        "13.4°C",
        "22.0°C",
        "16.9 mm",
        "45.0 km/h",
        "Generated automatically by AutoBrief",
    ):
        assert expected in html, f"missing {expected!r}"
    assert html.count("data:image/png;base64,") == 2  # both charts embedded
    assert html.count("<tr>") == 8  # header + 7 data rows
    for insight in analysis.insights:
        assert insight in html


def test_html_escapes_untrusted_text(tmp_path, raw_payload):
    result = analyze(raw_payload, location="<script>alert(1)</script>", source_url="https://example.test")
    [path] = ReportGenerator(tmp_path).generate(result, "html", FIXED)

    html = path.read_text(encoding="utf-8")
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


def test_pdf_report_is_valid_and_contains_content(tmp_path, analysis):
    [path] = ReportGenerator(tmp_path).generate(analysis, "pdf", FIXED)

    assert path.read_bytes().startswith(b"%PDF")
    reader = PdfReader(str(path))
    text = "\n".join(page.extract_text() for page in reader.pages)
    assert len(reader.pages) >= 1
    for expected in (
        "Automated Intelligence Report",
        "EXECUTIVE SUMMARY",
        "KEY METRICS",
        "INSIGHTS",
        "DATA SOURCE",
        "Testville",
        "13.4",
        "Wed 07 Oct 2026",
        "Generated automatically by AutoBrief",
    ):
        assert expected in text, f"missing {expected!r}"


def test_filename_uses_timestamp_pattern(tmp_path, analysis):
    [path] = ReportGenerator(tmp_path).generate(analysis, "pdf", FIXED)
    assert path.name == "autobrief_2026-10-04_11-30.pdf"
    assert re.fullmatch(r"autobrief_\d{4}-\d{2}-\d{2}_\d{2}-\d{2}\.pdf", path.name)


def test_both_format_creates_html_and_pdf_with_same_stem(tmp_path, analysis):
    paths = ReportGenerator(tmp_path).generate(analysis, "both", FIXED)
    assert sorted(p.suffix for p in paths) == [".html", ".pdf"]
    assert len({p.stem for p in paths}) == 1


def test_same_minute_runs_never_overwrite(tmp_path, analysis):
    gen = ReportGenerator(tmp_path)
    first = gen.generate(analysis, "html", FIXED)[0]
    second = gen.generate(analysis, "html", FIXED)[0]
    assert first != second and first.exists() and second.exists()


def test_reports_directory_is_created_automatically(tmp_path, analysis):
    target = tmp_path / "nested" / "reports"
    [path] = ReportGenerator(target).generate(analysis, "html", FIXED)
    assert path.parent == target and path.exists()


def test_unsupported_format_raises_report_error(tmp_path, analysis):
    with pytest.raises(ReportError, match="Unsupported report format"):
        ReportGenerator(tmp_path).generate(analysis, "docx", FIXED)


def test_unwritable_location_raises_report_error_without_leftovers(tmp_path, analysis):
    blocker = tmp_path / "reports"
    blocker.write_text("i am a file, not a directory")

    with pytest.raises(ReportError, match="Could not write report file"):
        ReportGenerator(blocker).generate(analysis, "html", FIXED)
    assert blocker.read_text() == "i am a file, not a directory"
