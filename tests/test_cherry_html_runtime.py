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
