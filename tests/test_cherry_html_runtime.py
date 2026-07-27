from pathlib import Path
import asyncio
import hashlib
import json

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


def test_wait_for_page_ws_allows_clean_windows_launcher_handoff(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeProcess:
        returncode = 0

        @staticmethod
        def poll() -> int:
            return 0

    class FakeResponse:
        @staticmethod
        def json():
            return [{"type": "page", "webSocketDebuggerUrl": "ws://127.0.0.1/devtools/page/1"}]

    class FakeSession:
        trust_env = True

        @staticmethod
        def get(*_args, **_kwargs):
            return FakeResponse()

        @staticmethod
        def close() -> None:
            return None

    session = FakeSession()
    monkeypatch.setattr(cherry_html_runner.requests, "Session", lambda: session)

    assert cherry_html_runner._wait_for_page_ws(13543, FakeProcess()) == "ws://127.0.0.1/devtools/page/1"  # type: ignore[arg-type]
    assert session.trust_env is False


def test_start_chrome_uses_elevated_headless_safe_flags(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    def fake_popen(args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return object()

    monkeypatch.setattr(cherry_html_runner.subprocess, "Popen", fake_popen)

    cherry_html_runner._start_chrome(tmp_path / "chrome.exe", 9222, tmp_path / "profile")

    assert "--enable-automation" in captured["args"]
    assert "--no-sandbox" in captured["args"]
    assert "--disable-setuid-sandbox" in captured["args"]


def test_file_input_script_does_not_dispatch_duplicate_change_event() -> None:
    script = cherry_html_runner._file_input_preset_script(54)

    assert "const expected=54" in script
    assert "dispatchEvent" not in script
    assert "collectedFiles.length!==expected" in script


def test_runner_enables_multi_file_picker_for_automation() -> None:
    source = Path(cherry_html_runner.__file__).read_text(encoding="utf-8")

    assert "document.getElementById('file-input').multiple=true" in source


def test_wait_processing_ignores_nonfatal_notice_while_button_is_busy(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeCdp:
        def __init__(self) -> None:
            self.states = iter(
                [
                    {"done": False, "error": "optional reference step skipped", "processing": True},
                    {"done": True, "error": "optional reference step skipped", "processing": False},
                ]
            )

        async def evaluate(self, *_args, **_kwargs):
            return next(self.states)

    async def no_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(cherry_html_runner.asyncio, "sleep", no_sleep)
    asyncio.run(cherry_html_runner._wait_processing_done(FakeCdp(), 5))  # type: ignore[arg-type]


def test_wait_processing_raises_error_after_processing_stops() -> None:
    class FakeCdp:
        @staticmethod
        async def evaluate(*_args, **_kwargs):
            return {"done": False, "error": "decode failed", "processing": False}

    with pytest.raises(RuntimeError, match="decode failed"):
        asyncio.run(cherry_html_runner._wait_processing_done(FakeCdp(), 5))  # type: ignore[arg-type]


def test_verified_release_is_scoped_to_configured_source(tmp_path: Path) -> None:
    source_a = tmp_path / "a.html"
    source_b = tmp_path / "b.html"
    source_a.write_text("a", encoding="utf-8")
    source_b.write_text("b", encoding="utf-8")
    release = tmp_path / "storage" / "cherry_html_releases" / "release.html"
    release.parent.mkdir(parents=True)
    release.write_text("verified", encoding="utf-8")
    (release.parent / "active.json").write_text(
        json.dumps(
            {
                "source_path": str(source_a.resolve()),
                "path": str(release.resolve()),
                "sha256": hashlib.sha256(release.read_bytes()).hexdigest(),
            }
        ),
        encoding="utf-8",
    )

    assert cherry_html_runner.verified_cherry_html_path(source_a, tmp_path / "storage") == release.resolve()
    assert cherry_html_runner.verified_cherry_html_path(source_b, tmp_path / "storage") == source_b


def test_invalid_candidate_falls_back_to_last_verified_release(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    source = tmp_path / "cherry-postprocess.html"
    source.write_text("candidate", encoding="utf-8")
    release = tmp_path / "storage" / "cherry_html_releases" / "old" / "cherry-postprocess.html"
    release.parent.mkdir(parents=True)
    release.write_text("verified", encoding="utf-8")
    metadata = release.parents[1] / "active.json"
    metadata.write_text(
        json.dumps(
            {
                "source_path": str(source.resolve()),
                "path": str(release.resolve()),
                "sha256": hashlib.sha256(release.read_bytes()).hexdigest(),
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        cherry_html_runner,
        "validate_cherry_html_runtime",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("missing process button")),
    )

    result = cherry_html_runner.verify_and_promote_cherry_html(source, tmp_path / "storage")

    assert result["ok"] is True
    assert result["fallback"] is True
    assert Path(result["path"]) == release.resolve()
    assert "missing process button" in result["candidate_error"]
