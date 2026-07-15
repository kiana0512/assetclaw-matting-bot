from pathlib import Path

import pytest

from assetclaw_matting.services import cherry_html_runner


def test_resolve_chrome_rejects_directory(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(cherry_html_runner.os, "environ", {})
    monkeypatch.setattr(cherry_html_runner.shutil, "which", lambda _name: None)

    with pytest.raises(FileNotFoundError, match="Chrome or Edge executable not found"):
        cherry_html_runner._resolve_chrome(tmp_path)


def test_resolve_chrome_accepts_configured_executable(tmp_path: Path) -> None:
    browser = tmp_path / "msedge.exe"
    browser.write_bytes(b"")

    assert cherry_html_runner._resolve_chrome(browser) == browser


def test_browser_candidates_keep_configured_browser_and_system_fallbacks(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    configured = tmp_path / "configured.exe"
    chrome = tmp_path / "Google" / "Chrome" / "Application" / "chrome.exe"
    edge = tmp_path / "Microsoft" / "Edge" / "Application" / "msedge.exe"
    for path in (configured, chrome, edge):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"")
    monkeypatch.setattr(cherry_html_runner.os, "environ", {"PROGRAMFILES": str(tmp_path)})
    monkeypatch.setattr(cherry_html_runner.shutil, "which", lambda _name: None)

    assert cherry_html_runner._resolve_browser_candidates(configured) == [configured, chrome, edge]


def test_wait_for_page_ws_reports_early_browser_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeProcess:
        returncode = 9

        @staticmethod
        def poll() -> int:
            return 9

    class FakeSession:
        trust_env = True

        @staticmethod
        def close() -> None:
            return None

    session = FakeSession()
    monkeypatch.setattr(cherry_html_runner.requests, "Session", lambda: session)

    with pytest.raises(RuntimeError, match="exit_code=9"):
        cherry_html_runner._wait_for_page_ws(13543, FakeProcess())  # type: ignore[arg-type]
    assert session.trust_env is False
