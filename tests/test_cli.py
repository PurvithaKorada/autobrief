import logging

import pytest
import requests
import responses

from autobrief import cli
from tests.conftest import API_URL, RAW_PAYLOAD


def run(argv, capsys):
    code = cli.main(argv)
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_top_level_help_lists_all_commands(capsys):
    with pytest.raises(SystemExit) as info:
        cli.main(["--help"])
    out = capsys.readouterr().out
    assert info.value.code == 0
    for command in ("generate", "schedule", "status", "logs"):
        assert command in out


def test_command_help_shows_command_options(capsys):
    with pytest.raises(SystemExit) as info:
        cli.main(["generate", "--help"])
    assert info.value.code == 0
    out = capsys.readouterr().out
    assert "--format" in out and "html,pdf,both" in out


def test_no_command_prints_help_and_succeeds(capsys, cli_env):
    code, out, _ = run([], capsys)
    assert code == 0 and "usage: autobrief" in out


def test_generate_html_end_to_end(cli_env, http, capsys):
    http.add(responses.GET, API_URL, json=RAW_PAYLOAD)

    code, out, err = run(["generate", "--format", "html"], capsys)

    assert code == 0, err
    assert "Report generated successfully!" in out and "HTML report created" in out
    [report] = list((cli_env / "reports").glob("autobrief_*.html"))
    assert f"reports/{report.name}" in out
    assert "\x1b[" not in out  # no ANSI codes when not a terminal
    assert "Report generated" in (cli_env / "logs" / "autobrief.log").read_text()


def test_generate_pdf_end_to_end(cli_env, http, capsys):
    http.add(responses.GET, API_URL, json=RAW_PAYLOAD)
    code, out, _ = run(["generate", "--format", "pdf"], capsys)
    assert code == 0 and "PDF report created" in out
    assert len(list((cli_env / "reports").glob("autobrief_*.pdf"))) == 1


def test_default_format_creates_both_reports(cli_env, http, capsys):
    http.add(responses.GET, API_URL, json=RAW_PAYLOAD)
    code, _, _ = run(["generate"], capsys)
    assert code == 0
    assert {p.suffix for p in (cli_env / "reports").iterdir()} == {".html", ".pdf"}


def test_location_override_appears_in_report(cli_env, http, capsys):
    http.add(responses.GET, API_URL, json=RAW_PAYLOAD)
    run(["generate", "--format", "html", "--location", "Paris", "--latitude", "48.85", "--longitude", "2.35"], capsys)

    html = next((cli_env / "reports").glob("*.html")).read_text(encoding="utf-8")
    assert "Paris" in html
    assert "latitude=48.85" in http.calls[0].request.url


@pytest.mark.parametrize(
    "argv",
    [
        ["generate", "--format", "docx"],
        ["generate", "--latitude", "123"],
        ["generate", "--longitude", "abc"],
        ["schedule"],
        ["schedule", "--daily", "09:00", "--every", "5"],
        ["schedule", "--every", "0"],
        ["logs", "--lines", "-3"],
        ["frobnicate"],
    ],
)
def test_invalid_arguments_exit_with_usage_error(argv, capsys):
    with pytest.raises(SystemExit) as info:
        cli.main(argv)
    assert info.value.code == 2
    assert "usage:" in capsys.readouterr().err


def test_api_timeout_shows_friendly_error_not_traceback(cli_env, http, capsys):
    http.add(responses.GET, API_URL, body=requests.exceptions.Timeout())

    code, out, err = run(["generate", "--format", "html"], capsys)

    assert code == 1
    assert "Report generation failed" in out and "API request timed out" in out
    assert "autobrief.log" in out
    assert "Traceback" not in out + err
    assert not (cli_env / "reports").exists()  # nothing partial left behind


def test_invalid_api_response_shows_reason(cli_env, http, capsys):
    http.add(responses.GET, API_URL, json={"hello": "world"})
    code, out, _ = run(["generate"], capsys)
    assert code == 1 and "missing the 'daily' forecast data" in out


def test_unexpected_bug_is_hidden_from_user_but_logged(cli_env, http, capsys, monkeypatch):
    http.add(responses.GET, API_URL, json=RAW_PAYLOAD)

    def boom(*args, **kwargs):
        raise ZeroDivisionError("kaboom")

    monkeypatch.setattr("autobrief.workflow.analyze", boom)
    code, out, err = run(["generate"], capsys)

    assert code == 1
    assert "Unexpected error (ZeroDivisionError)" in out and "Traceback" not in out + err
    assert "Traceback" in (cli_env / "logs" / "autobrief.log").read_text()


def test_invalid_configuration_is_reported(cli_env, monkeypatch, capsys):
    monkeypatch.setenv("AUTOBRIEF_FORECAST_DAYS", "abc")
    code, out, _ = run(["generate"], capsys)
    assert code == 1 and "Invalid configuration" in out and "AUTOBRIEF_FORECAST_DAYS" in out


def test_status_before_and_after_runs(cli_env, http, capsys):
    code, out, _ = run(["status"], capsys)
    assert code == 0 and "Scheduler   : not running" in out and "none yet" in out

    http.add(responses.GET, API_URL, json=RAW_PAYLOAD)
    run(["generate", "--format", "html"], capsys)
    _, out, _ = run(["status"], capsys)
    assert "1 (1 succeeded, 0 failed)" in out and "manual" in out and "Last report" in out


def test_status_shows_last_error_after_failure(cli_env, http, capsys):
    http.add(responses.GET, API_URL, status=500, json={"reason": "down"})
    run(["generate"], capsys)
    _, out, _ = run(["status"], capsys)
    assert "0 succeeded, 1 failed" in out and "Last error" in out and "HTTP 500" in out


def test_logs_command(cli_env, http, capsys):
    http.add(responses.GET, API_URL, json=RAW_PAYLOAD)
    run(["generate", "--format", "html"], capsys)

    _, out, _ = run(["logs", "-n", "3"], capsys)
    assert 1 <= len(out.strip().splitlines()) <= 3
    _, everything, _ = run(["logs", "-n", "200"], capsys)
    assert "Report workflow started" in everything and "Data retrieved successfully" in everything


def test_logs_without_log_file_is_friendly(cli_env, capsys, monkeypatch):
    monkeypatch.setattr(cli, "setup_logging", lambda settings: logging.getLogger("autobrief"))
    code, out, _ = run(["logs"], capsys)
    assert code == 0 and "No log file yet" in out


def test_schedule_with_invalid_time_fails_cleanly(cli_env, capsys):
    code, out, _ = run(["schedule", "--daily", "25:99"], capsys)
    assert code == 1 and "Scheduler could not start" in out and "HH:MM" in out
