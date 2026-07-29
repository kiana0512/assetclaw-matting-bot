from __future__ import annotations

import asyncio
import os
from pathlib import Path
import time
import zipfile

import pytest
from PIL import Image

from assetclaw_matting.services import cherry_html_runner
from assetclaw_matting.skills import cherry_skills


def _valid_report(**overrides):
    report = {
        "referenceLoaded": True,
        "executedSteps": ["fringe", "resize2", "colormatch", "align"],
        "skippedNoRef": [],
        "colormatchEnabled": True,
        "alignEnabled": True,
        "alignmentTransform": {"s": 1.25, "tx": 3.0, "ty": -2.0, "anchor": "0001.png"},
        "colorMatchStats": {"calls": 1, "applied": 1, "insufficient": 0},
    }
    report.update(overrides)
    return report


def test_processing_report_accepts_color_match_then_alignment_last() -> None:
    cherry_html_runner._validate_processing_report(_valid_report())


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"referenceLoaded": False}, "without a loaded color reference"),
        ({"executedSteps": ["fringe", "colormatch"], "alignEnabled": False}, "alignment was not executed"),
        ({"skippedNoRef": ["校色 · 参考对齐"]}, "skipped reference-dependent"),
        ({"executedSteps": ["fringe", "resize2"]}, "was not executed"),
        ({"executedSteps": ["fringe", "align", "colormatch"]}, "were not the final executed steps in order"),
    ],
)
def test_processing_report_fails_closed(overrides: dict, message: str) -> None:
    with pytest.raises(RuntimeError, match=message):
        cherry_html_runner._validate_processing_report(_valid_report(**overrides))


def test_force_policy_enables_reference_steps_and_moves_them_last() -> None:
    class FakeCdp:
        expression = ""

        async def evaluate(self, expression: str, **_kwargs):
            self.expression = expression
            return {
                "ok": True,
                "colormatchEnabled": True,
                "alignEnabled": True,
                "configuredSteps": ["fringe", "resize2", "colormatch", "align"],
            }

    cdp = FakeCdp()
    state = asyncio.run(cherry_html_runner._force_reference_finishing_steps(cdp))  # type: ignore[arg-type]

    assert state["configuredSteps"][-2:] == ["colormatch", "align"]
    assert "setModuleState('align',true)" in cdp.expression
    assert "setModuleState('colormatch',true)" in cdp.expression
    assert "appendChild(colorModule)" in cdp.expression
    assert "appendChild(alignModule)" in cdp.expression


def test_fixed_alignment_transform_is_injected_and_verified() -> None:
    class FakeCdp:
        expression = ""

        async def evaluate(self, expression: str, **_kwargs):
            self.expression = expression
            return {"ok": True, "fixed": {"s": 1.25, "tx": 3.0, "ty": -2.0}}

    cdp = FakeCdp()
    fixed = {"s": 1.25, "tx": 3.0, "ty": -2.0}
    asyncio.run(cherry_html_runner._install_fixed_alignment_transform(cdp, fixed))  # type: ignore[arg-type]

    assert "buildAlignTransform=function" in cdp.expression
    cherry_html_runner._validate_processing_report(_valid_report(), expected_alignment_transform=fixed)


def test_processing_report_rejects_changed_frozen_alignment_transform() -> None:
    with pytest.raises(RuntimeError, match="did not reuse the frozen"):
        cherry_html_runner._validate_processing_report(
            _valid_report(),
            expected_alignment_transform={"s": 1.5, "tx": 3.0, "ty": -2.0},
        )


def test_legacy_report_allows_reference_steps_only_when_both_are_disabled() -> None:
    report = {
        "referenceLoaded": False,
        "executedSteps": ["fringe", "resize2"],
        "skippedNoRef": [],
        "colormatchEnabled": False,
        "alignEnabled": False,
        "alignmentTransform": None,
    }
    cherry_html_runner._validate_processing_report(report, reference_steps_required=False)

    with pytest.raises(RuntimeError, match="unexpectedly remained enabled"):
        cherry_html_runner._validate_processing_report(
            {**report, "colormatchEnabled": True},
            reference_steps_required=False,
        )


def test_reference_is_injected_through_cdp_and_waited_until_decoded(tmp_path: Path) -> None:
    reference = tmp_path / "reference.png"
    Image.new("RGBA", (8, 8), (20, 40, 60, 255)).save(reference)

    class FakeCdp:
        calls: list[tuple[str, dict]]

        def __init__(self) -> None:
            self.calls = []

        async def send(self, method: str, params: dict):
            self.calls.append((method, params))
            if method == "DOM.querySelector":
                return {"nodeId": 42}
            return {}

        async def evaluate(self, *_args, **_kwargs):
            return {"referenceLoaded": True, "width": 8, "height": 8, "info": "已载入参考图"}

    cdp = FakeCdp()
    state = asyncio.run(cherry_html_runner._load_reference(cdp, 1, reference))  # type: ignore[arg-type]

    assert state["referenceLoaded"] is True
    assert ("DOM.querySelector", {"nodeId": 1, "selector": "#ref-input"}) in cdp.calls
    assert ("DOM.setFileInputFiles", {"nodeId": 42, "files": [str(reference)]}) in cdp.calls


def test_skill_start_requires_a_reference_before_creating_run(monkeypatch, tmp_path: Path) -> None:
    source = tmp_path / "input"
    output = tmp_path / "output"
    source.mkdir()
    Image.new("RGBA", (8, 8), (255, 0, 0, 255)).save(source / "0001.png")
    monkeypatch.setattr(cherry_skills, "_require_html_runtime", lambda: {"browser_path": "test"})

    with pytest.raises(ValueError, match="reference_path is required"):
        cherry_skills.run_start(str(source), str(output), color_match_required=True)


def test_merge_options_cannot_disable_or_reorder_reference_finishing_steps() -> None:
    options = cherry_skills._merge_options(
        {
            "html_align_enabled": False,
            "html_colormatch_enabled": False,
            "html_modules": ["colormatch", "fringe", "align", "resize2"],
        }
    )

    assert options["html_align_enabled"] is True
    assert options["html_colormatch_enabled"] is True
    assert options["html_modules"] == ["fringe", "resize2", "colormatch", "align"]


def test_sequence_alignment_anchor_uses_smallest_final_number(tmp_path: Path) -> None:
    files = [tmp_path / name for name in ("frame_10.png", "frame_2.png", "frame_001.png")]
    assert cherry_skills._sequence_alignment_anchor(files).name == "frame_001.png"


def test_cherry_html_is_pinned_for_the_entire_run(tmp_path: Path) -> None:
    source = tmp_path / "live" / "cherry-postprocess.html"
    source.parent.mkdir()
    source.write_text("version-one", encoding="utf-8")

    snapshot, digest = cherry_skills._pin_html_for_run("CHERRY_PIN", source, tmp_path / "storage")
    source.write_text("version-two", encoding="utf-8")

    assert snapshot.read_text(encoding="utf-8") == "version-one"
    assert cherry_skills._sha256_path(snapshot) == digest


def test_session_cleanup_never_recursively_walks_chrome_profiles(monkeypatch, tmp_path: Path) -> None:
    stale = tmp_path / "run_stale" / "chrome_profile" / "deep" / "cache"
    stale.mkdir(parents=True)
    (stale / "entry.bin").write_bytes(b"cache")
    fresh = tmp_path / "run_fresh"
    fresh.mkdir()
    old = time.time() - 100
    os.utime(tmp_path / "run_stale", (old, old))

    def fail_if_recursive(*_args, **_kwargs):
        raise AssertionError("session cleanup must not recursively scan Chrome profiles")

    monkeypatch.setattr(Path, "rglob", fail_if_recursive)
    cherry_html_runner._cleanup_old_sessions(tmp_path, max_age_seconds=50)

    assert not (tmp_path / "run_stale").exists()
    assert fresh.exists()


def test_session_creation_fails_immediately_on_real_permission_error(monkeypatch, tmp_path: Path) -> None:
    calls = 0

    def denied(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        raise PermissionError("denied")

    monkeypatch.setattr(Path, "mkdir", denied)
    with pytest.raises(PermissionError, match="session directory is not writable"):
        cherry_html_runner._create_session_dir(tmp_path)

    assert calls == 1


def test_download_wait_keeps_event_loop_responsive(tmp_path: Path) -> None:
    async def scenario() -> Path:
        async def produce_download() -> None:
            await asyncio.sleep(0.01)
            with zipfile.ZipFile(tmp_path / "result.zip", "w") as archive:
                archive.writestr("frame.png", b"png")

        producer = asyncio.create_task(produce_download())
        result = await cherry_html_runner._wait_download(tmp_path, timeout_seconds=1)
        await producer
        return result

    assert asyncio.run(scenario()).name == "result.zip"


def test_runner_timeout_is_one_overall_deadline(monkeypatch, tmp_path: Path) -> None:
    canceled = False

    async def slow_runner(**_kwargs):
        nonlocal canceled
        try:
            await asyncio.sleep(1)
        finally:
            canceled = True

    monkeypatch.setattr(cherry_html_runner, "_run_cherry_html_async", slow_runner)
    started = time.monotonic()
    with pytest.raises(TimeoutError, match="overall"):
        cherry_html_runner.run_cherry_html(
            tmp_path / "tool.html",
            tmp_path,
            tmp_path / "output",
            [],
            timeout_seconds=0.1,
        )

    assert time.monotonic() - started < 0.5
    assert canceled is True


def test_session_cleanup_retries_transient_windows_profile_lock(monkeypatch, tmp_path: Path) -> None:
    session = tmp_path / "run_locked"
    session.mkdir()
    (session / "lockfile").write_text("test", encoding="utf-8")
    original_rmtree = cherry_html_runner.shutil.rmtree
    calls = 0

    def transient_lock(path):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise PermissionError("Chrome is still closing")
        return original_rmtree(path)

    monkeypatch.setattr(cherry_html_runner.shutil, "rmtree", transient_lock)

    assert cherry_html_runner._remove_session_dir(session, timeout_seconds=1) is True
    assert calls == 2
    assert not session.exists()
