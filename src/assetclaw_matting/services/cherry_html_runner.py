from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import shutil
import socket
import subprocess
import tempfile
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import requests
import websockets
from PIL import Image


@dataclass(frozen=True)
class CherryHtmlResult:
    output_dir: Path
    total: int
    profile: str
    resize: str
    feather_enabled: bool
    steps: list[str]
    downloaded_zip: Path
    source_sha256: str = ""
    executed_steps: list[str] = field(default_factory=list)
    skipped_no_ref: list[str] = field(default_factory=list)
    reference_loaded: bool = False
    reference_sha256: str = ""
    alignment_enabled: bool = False
    alignment_transform: dict[str, Any] = field(default_factory=dict)
    color_match_stats: dict[str, int] = field(default_factory=dict)


def validate_cherry_html_runtime(html_path: Path, chrome_path: Path | None = None) -> dict[str, str]:
    source = Path(html_path)
    if not source.is_file():
        raise FileNotFoundError(f"Cherry algorithm HTML not found: {source}")
    html = source.read_text(encoding="utf-8", errors="ignore")
    required_markers = (
        "file-input",
        "ref-input",
        "btn-process",
        "btn-download",
        "colormatch",
        "align",
        "buildAlignTransform",
        "colorMatchToRef",
        "buildDiagnosticLog",
    )
    missing = [marker for marker in required_markers if marker not in html]
    if missing:
        raise ValueError(f"Cherry algorithm HTML is missing required controls: {', '.join(missing)}")
    browser = _resolve_chrome(chrome_path)
    return {"html_path": str(source), "browser_path": str(browser)}


class CdpClient:
    def __init__(self, ws_url: str) -> None:
        self.ws_url = ws_url
        self._next_id = 1
        self._pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self._events: list[dict[str, Any]] = []
        self._event = asyncio.Event()
        self._ws: Any = None
        self._reader: asyncio.Task[None] | None = None

    async def __aenter__(self) -> "CdpClient":
        self._ws = await websockets.connect(self.ws_url, max_size=None)
        self._reader = asyncio.create_task(self._read_loop())
        return self

    async def __aexit__(self, *_: Any) -> None:
        if self._ws:
            try:
                await self.send("Browser.close", timeout=2.0)
            except Exception:
                pass
        if self._reader:
            self._reader.cancel()
        if self._ws:
            await self._ws.close()

    async def send(self, method: str, params: dict[str, Any] | None = None, timeout: float = 30.0) -> dict[str, Any]:
        msg_id = self._next_id
        self._next_id += 1
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[dict[str, Any]] = loop.create_future()
        self._pending[msg_id] = fut
        await self._ws.send(json.dumps({"id": msg_id, "method": method, "params": params or {}}))
        try:
            result = await asyncio.wait_for(fut, timeout=timeout)
        finally:
            # The reader normally removes completed requests.  A timeout must
            # also release its Future or repeated CDP failures leak memory.
            self._pending.pop(msg_id, None)
        if "error" in result:
            raise RuntimeError(f"CDP {method} failed: {result['error']}")
        return result.get("result") or {}

    async def wait_event(self, method: str, timeout: float = 30.0) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        while True:
            # Clear before scanning.  If the reader publishes between clear
            # and scan, the queued event is seen; if it publishes after scan,
            # wait() observes the set flag.  Scanning before clear loses that
            # notification and can add a false 30-second delay.
            self._event.clear()
            for index, event in enumerate(self._events):
                if event.get("method") == method:
                    return self._events.pop(index)
            remain = deadline - time.monotonic()
            if remain <= 0:
                raise TimeoutError(f"timed out waiting for {method}")
            await asyncio.wait_for(self._event.wait(), timeout=remain)

    async def evaluate(self, expression: str, timeout: float = 30.0) -> Any:
        result = await self.send(
            "Runtime.evaluate",
            {
                "expression": expression,
                "awaitPromise": True,
                "returnByValue": True,
            },
            timeout=timeout,
        )
        payload = result.get("result") or {}
        if payload.get("subtype") == "error":
            raise RuntimeError(payload.get("description") or payload.get("value") or "Runtime.evaluate failed")
        return payload.get("value")

    async def _read_loop(self) -> None:
        async for raw in self._ws:
            message = json.loads(raw)
            msg_id = message.get("id")
            if msg_id is not None:
                fut = self._pending.pop(int(msg_id), None)
                if fut and not fut.done():
                    fut.set_result(message)
            else:
                self._events.append(message)
                if len(self._events) > 256:
                    del self._events[:-256]
                self._event.set()


def run_cherry_html(
    html_path: Path,
    input_root: Path,
    output_root: Path,
    files: list[Path],
    *,
    reference_path: Path | None = None,
    reference_steps_required: bool = True,
    alignment_transform: dict[str, Any] | None = None,
    expected_profile: str | None = None,
    expected_width: int | None = None,
    expected_height: int | None = None,
    chrome_path: Path | None = None,
    timeout_seconds: int = 900,
    storage_dir: Path | None = None,
) -> CherryHtmlResult:
    async def run_with_overall_deadline() -> CherryHtmlResult:
        try:
            return await asyncio.wait_for(
                _run_cherry_html_async(
                    html_path=html_path,
                    input_root=input_root,
                    output_root=output_root,
                    files=files,
                    reference_path=reference_path,
                    reference_steps_required=reference_steps_required,
                    alignment_transform=alignment_transform,
                    expected_profile=expected_profile,
                    expected_width=expected_width,
                    expected_height=expected_height,
                    chrome_path=chrome_path,
                    timeout_seconds=timeout_seconds,
                    storage_dir=storage_dir,
                ),
                timeout=max(0.1, float(timeout_seconds)),
            )
        except asyncio.TimeoutError as exc:
            raise TimeoutError(f"Cherry HTML run exceeded the overall {timeout_seconds}s deadline") from exc

    return asyncio.run(
        run_with_overall_deadline()
    )


async def _run_cherry_html_async(
    *,
    html_path: Path,
    input_root: Path,
    output_root: Path,
    files: list[Path],
    reference_path: Path | None,
    reference_steps_required: bool,
    alignment_transform: dict[str, Any] | None,
    expected_profile: str | None,
    expected_width: int | None,
    expected_height: int | None,
    chrome_path: Path | None,
    timeout_seconds: int,
    storage_dir: Path | None,
) -> CherryHtmlResult:
    html_path = html_path.resolve()
    input_root = input_root.resolve()
    output_root = output_root.resolve()
    files = [path.resolve() for path in files]
    reference_path = Path(reference_path).resolve() if reference_path else None
    if not html_path.exists():
        raise FileNotFoundError(str(html_path))
    if not files:
        raise ValueError("no input images")
    for path in files:
        if not path.exists():
            raise FileNotFoundError(str(path))
    if reference_steps_required and reference_path is None:
        raise ValueError("reference_path is required when Cherry reference post-processing is enabled")
    expected_output = _normalize_expected_output_profile(
        expected_profile,
        expected_width,
        expected_height,
        required=reference_steps_required,
    )
    if reference_path is not None:
        _validate_reference_image(reference_path)

    # Keep browser sessions in a fresh inheritable directory.  The legacy
    # ``cherry_html_runner`` directory may carry an old protected Windows ACL;
    # merely checking that it exists does not mean the current service account
    # can create children in it.
    work_root = Path(storage_dir or tempfile.gettempdir()) / "cherry_browser_sessions"
    work_root.mkdir(parents=True, exist_ok=True)
    # Do not sweep abandoned Chromium profiles on the latency-critical path.
    # A crashed browser can leave a very large cache tree, and even a bounded
    # rmtree can monopolize Python for minutes before the new task starts.  The
    # current session is removed in ``finally`` below; stale-session cleanup is
    # an explicit maintenance operation.

    proc: subprocess.Popen[Any] | None = None
    session_dir: Path | None = None
    download_dir: Path | None = None
    ws_url = ""
    launch_errors: list[str] = []
    browsers = _resolve_browser_candidates(chrome_path)
    if not browsers:
        raise FileNotFoundError("Chrome or Edge executable not found")
    for browser in browsers:
        for attempt in range(1, 3):
            candidate_session = _create_session_dir(work_root)
            profile_dir = candidate_session / "chrome_profile"
            candidate_download = candidate_session / "downloads"
            stdout_path = candidate_session / "browser.stdout.log"
            stderr_path = candidate_session / "browser.stderr.log"
            profile_dir.mkdir(parents=True, exist_ok=True)
            candidate_download.mkdir(parents=True, exist_ok=True)
            port = _free_port()
            candidate_proc: subprocess.Popen[Any] | None = None
            try:
                candidate_proc = _start_chrome(
                    browser,
                    port,
                    profile_dir,
                    stdout_path=stdout_path,
                    stderr_path=stderr_path,
                )
                candidate_ws = _wait_for_page_ws(port, candidate_proc)
            except Exception as exc:
                if candidate_proc is not None:
                    _stop_chrome(candidate_proc)
                detail = _browser_start_failure(browser, attempt, port, exc, stdout_path, stderr_path)
                launch_errors.append(detail)
                _write_browser_start_failure(work_root, detail)
                _remove_session_dir(candidate_session)
                continue
            proc = candidate_proc
            session_dir = candidate_session
            download_dir = candidate_download
            ws_url = candidate_ws
            break
        if proc is not None:
            break

    if proc is None or session_dir is None or download_dir is None or not ws_url:
        raise RuntimeError("Cherry browser could not start after retries:\n" + "\n".join(launch_errors))

    try:
        # Navigate to an immutable per-session copy. A pipeline git update can
        # replace the configured HTML while jobs are running; pinning the bytes
        # here prevents one task from executing two algorithm revisions.
        runtime_html = session_dir / "cherry-postprocess.html"
        shutil.copy2(html_path, runtime_html)
        source_sha256 = _sha256_file(runtime_html)
        runtime_reference: Path | None = None
        reference_sha256 = ""
        if reference_path is not None:
            runtime_reference = session_dir / f"color-reference{reference_path.suffix.lower()}"
            shutil.copy2(reference_path, runtime_reference)
            reference_sha256 = _sha256_file(runtime_reference)
        async with CdpClient(ws_url) as cdp:
            await cdp.send("Page.enable")
            await cdp.send("Runtime.enable")
            await cdp.send("DOM.enable")
            await cdp.send(
                "Browser.setDownloadBehavior",
                {"behavior": "allow", "downloadPath": str(download_dir)},
            )
            await cdp.send("Page.navigate", {"url": runtime_html.as_uri()})
            try:
                await cdp.wait_event("Page.loadEventFired", timeout=30.0)
            except TimeoutError:
                pass
            await _wait_ready(cdp)
            doc = await cdp.send("DOM.getDocument", {"depth": 1, "pierce": True})
            await _install_processing_probe(cdp)
            if reference_steps_required:
                assert runtime_reference is not None
                reference = await _load_reference(cdp, doc["root"]["nodeId"], runtime_reference)
                if not reference.get("referenceLoaded"):
                    raise RuntimeError("Cherry color reference did not load")
            node = await cdp.send(
                "DOM.querySelector",
                {"nodeId": doc["root"]["nodeId"], "selector": "#file-input"},
            )
            node_id = node.get("nodeId")
            if not node_id:
                raise RuntimeError("cherry html file input not found")
            # Recent standalone HTML revisions intentionally expose a
            # single-file picker while drag/drop still accepts many images.
            # CDP uses the picker contract, so enable multiple only inside the
            # disposable automation page before assigning a micro-batch.
            await cdp.evaluate(
                "document.getElementById('file-input').multiple=true;",
                timeout=10.0,
            )
            await cdp.send("DOM.setFileInputFiles", {"nodeId": node_id, "files": [str(path) for path in files]})
            preset = await cdp.evaluate(
                _file_input_preset_script(len(files)),
                timeout=30.0,
            )
            preset = dict(preset or {})
            if int((preset or {}).get("count") or 0) != len(files):
                raise RuntimeError(f"cherry html loaded {(preset or {}).get('count')} files, expected {len(files)}")
            if expected_output is not None:
                profile, width, height = expected_output
                forced_output = await _force_output_profile(cdp, profile, width, height)
                preset.update(
                    {
                        "resize": str(forced_output.get("resize") or ""),
                        "feather": bool(forced_output.get("feather")),
                    }
                )
            if reference_steps_required:
                forced = await _force_reference_finishing_steps(cdp)
                if not forced.get("colormatchEnabled") or not forced.get("alignEnabled"):
                    raise RuntimeError("Cherry module policy could not be enforced (colormatch=on, align=on)")
                expected_alignment = _normalize_alignment_transform(alignment_transform) if alignment_transform else None
                if expected_alignment:
                    await _install_fixed_alignment_transform(cdp, expected_alignment)
            else:
                forced = await _force_reference_steps_disabled(cdp)
                expected_alignment = None
            preset["steps"] = [str(step) for step in (forced.get("configuredSteps") or [])]
            await cdp.evaluate("document.getElementById('btn-process').click();", timeout=10.0)
            await _wait_processing_done(cdp, timeout_seconds)
            report = await _read_processing_report(cdp)
            _validate_processing_report(
                report,
                expected_alignment_transform=expected_alignment,
                reference_steps_required=reference_steps_required,
                expected_frames=len(files),
            )
            await cdp.evaluate("document.getElementById('btn-download').click();", timeout=10.0)
            downloaded = await _wait_download(download_dir, timeout_seconds)

        _extract_outputs(downloaded, input_root, output_root, files)
        resize = str((preset or {}).get("resize") or "")
        # Preserve the legacy non-reference classification for older callers;
        # reference-backed runs are checked against the exact resize below.
        profile = "half" if resize == "256x256" else "full"
        if expected_output is not None:
            expected_name, expected_width_value, expected_height_value = expected_output
            expected_resize = f"{expected_width_value}x{expected_height_value}"
            if profile != expected_name or resize != expected_resize:
                raise RuntimeError(
                    f"Cherry output profile mismatch: expected {expected_name} {expected_resize}, got {profile} {resize}"
                )
            _validate_extracted_output_dimensions(
                input_root,
                output_root,
                files,
                expected_width_value,
                expected_height_value,
            )
        return CherryHtmlResult(
            output_dir=output_root,
            total=len(files),
            profile=profile,
            resize=resize,
            feather_enabled=bool((preset or {}).get("feather")),
            steps=[str(step) for step in ((preset or {}).get("steps") or [])],
            downloaded_zip=downloaded,
            source_sha256=source_sha256,
            executed_steps=[str(step) for step in (report.get("executedSteps") or [])],
            skipped_no_ref=[str(step) for step in (report.get("skippedNoRef") or [])],
            reference_loaded=bool(report.get("referenceLoaded")),
            reference_sha256=reference_sha256,
            alignment_enabled=bool(report.get("alignEnabled")),
            alignment_transform=dict(report.get("alignmentTransform") or {}),
            color_match_stats={
                key: int((report.get("colorMatchStats") or {}).get(key) or 0)
                for key in ("calls", "applied", "insufficient")
            },
        )
    finally:
        _stop_chrome(proc)
        _remove_session_dir(session_dir)


def _file_input_preset_script(expected_count: int) -> str:
    return (
        """
                (async()=>{
                  const expected=__EXPECTED__;
                  const deadline=Date.now()+5000;
                  // DOM.setFileInputFiles already emits the native change event.
                  // A second synthetic event runs after onPicked() clears the
                  // input and overwrites the successfully loaded list with [].
                  while(collectedFiles.length!==expected && Date.now()<deadline){
                    await new Promise(resolve=>setTimeout(resolve,50));
                  }
                  if (typeof applyInputDefaultsForInput === 'function') {
                    await applyInputDefaultsForInput();
                  }
                  return {
                    count: collectedFiles.length,
                    resize: `${document.getElementById('p-rw2').value}x${document.getElementById('p-rh2').value}`,
                    feather: !!moduleState.feather,
                    steps: currentOrder().filter(step=>moduleState[step])
                  };
                })()
        """.replace("__EXPECTED__", str(int(expected_count)))
        )


def _normalize_expected_output_profile(
    profile: str | None,
    width: int | None,
    height: int | None,
    *,
    required: bool,
) -> tuple[str, int, int] | None:
    normalized = str(profile or "").strip().lower()
    if not normalized:
        if required:
            raise ValueError(
                "reference-enabled Cherry run requires explicit expected_profile='full' or expected_profile='half'"
            )
        if width is not None or height is not None:
            raise ValueError("expected_width/expected_height require an explicit expected_profile")
        return None
    if normalized not in {"full", "half"}:
        raise ValueError("expected_profile must be 'full' or 'half'; auto is forbidden")
    canonical_width, canonical_height = (384, 512) if normalized == "full" else (256, 256)
    if width is not None and int(width) != canonical_width:
        raise ValueError(
            f"expected_width conflicts with Cherry {normalized} profile ({canonical_width}x{canonical_height})"
        )
    if height is not None and int(height) != canonical_height:
        raise ValueError(
            f"expected_height conflicts with Cherry {normalized} profile ({canonical_width}x{canonical_height})"
        )
    return normalized, canonical_width, canonical_height


async def _force_output_profile(cdp: CdpClient, profile: str, width: int, height: int) -> dict[str, Any]:
    """Override HTML input-shape defaults with the task's frozen output contract."""

    payload = json.dumps(
        {
            "profile": str(profile),
            "width": int(width),
            "height": int(height),
            "feather": str(profile) == "full",
        },
        ensure_ascii=True,
    )
    state = await cdp.evaluate(
        f"""
        (()=>{{
          if(typeof setModuleState !== 'function' || typeof moduleState !== 'object'){{
            return {{ok:false,error:'Cherry module controls are unavailable'}};
          }}
          const expected={payload};
          const rw=document.getElementById('p-rw2');
          const rh=document.getElementById('p-rh2');
          if(!rw || !rh){{
            return {{ok:false,error:'Cherry final resize controls are unavailable'}};
          }}
          rw.value=rw.defaultValue=String(expected.width);
          rh.value=rh.defaultValue=String(expected.height);
          setModuleState('resize2',true);
          setModuleState('feather',!!expected.feather);
          return {{
            ok:true,
            profile:expected.profile,
            resize:`${{rw.value}}x${{rh.value}}`,
            feather:!!moduleState.feather,
            resize2Enabled:!!moduleState.resize2
          }};
        }})()
        """,
        timeout=10.0,
    )
    if not isinstance(state, dict) or not state.get("ok"):
        detail = state.get("error") if isinstance(state, dict) else "unknown error"
        raise RuntimeError(f"Cherry output profile could not be enforced: {detail}")
    expected_resize = f"{int(width)}x{int(height)}"
    if str(state.get("profile") or "") != str(profile) or str(state.get("resize") or "") != expected_resize:
        raise RuntimeError(
            f"Cherry output controls mismatch: expected {profile} {expected_resize}, "
            f"got {state.get('profile')} {state.get('resize')}"
        )
    if not state.get("resize2Enabled"):
        raise RuntimeError("Cherry final resize module could not be enabled")
    if bool(state.get("feather")) != (profile == "full"):
        raise RuntimeError(f"Cherry feather policy does not match the {profile} profile")
    return state


async def _install_processing_probe(cdp: CdpClient) -> None:
    """Capture the algorithm's actual post-run step list without changing its code."""

    state = await cdp.evaluate(
        """
        (()=>{
          if(typeof buildDiagnosticLog !== 'function' || typeof colorMatchToRef !== 'function'){
            return {installed:false,error:'Cherry diagnostic functions are unavailable'};
          }
          if(!window.__assetclawOriginalColorMatchToRef){
            window.__assetclawOriginalColorMatchToRef=colorMatchToRef;
            colorMatchToRef=function(image,ref,opts){
              const threshold=Math.round(Number(opts?.threshold??0.01)*255);
              let subjectPixels=0;
              for(let i=3;i<image.data.length;i+=4){if(image.data[i]>threshold)subjectPixels++;}
              window.__assetclawColorMatchStats.calls++;
              if(subjectPixels<64)window.__assetclawColorMatchStats.insufficient++;
              else window.__assetclawColorMatchStats.applied++;
              return window.__assetclawOriginalColorMatchToRef.call(this,image,ref,opts);
            };
          }
          if(!window.__assetclawOriginalBuildDiagnosticLog){
            window.__assetclawOriginalBuildDiagnosticLog=buildDiagnosticLog;
            buildDiagnosticLog=function(stats,order,...rest){
              window.__assetclawCherryReport={
                referenceLoaded:typeof refImageData !== 'undefined' && !!refImageData,
                executedSteps:Array.isArray(order)?order.slice():[],
                skippedNoRef:Array.isArray(stats?.skippedNoRef)?stats.skippedNoRef.slice():[],
                colormatchEnabled:!!moduleState?.colormatch,
                alignEnabled:!!moduleState?.align,
                alignmentTransform:stats?.alignInfo ? {
                  s:Number(stats.alignInfo.s),
                  tx:Number(stats.alignInfo.tx),
                  ty:Number(stats.alignInfo.ty),
                  anchor:String(stats.alignInfo.anchor||'')
                } : null,
                colorMatchStats:{...window.__assetclawColorMatchStats}
              };
              return window.__assetclawOriginalBuildDiagnosticLog.call(this,stats,order,...rest);
            };
          }
          window.__assetclawColorMatchStats={calls:0,applied:0,insufficient:0};
          window.__assetclawCherryReport=null;
          return {installed:true};
        })()
        """,
        timeout=10.0,
    )
    if not isinstance(state, dict) or not state.get("installed"):
        detail = state.get("error") if isinstance(state, dict) else "unknown error"
        raise RuntimeError(f"Cherry processing verification probe could not be installed: {detail}")


async def _load_reference(
    cdp: CdpClient,
    root_node_id: int,
    reference_path: Path,
    *,
    timeout_seconds: float = 30.0,
) -> dict[str, Any]:
    node = await cdp.send(
        "DOM.querySelector",
        {"nodeId": root_node_id, "selector": "#ref-input"},
    )
    node_id = node.get("nodeId")
    if not node_id:
        raise RuntimeError("cherry html color reference input not found")
    await cdp.send("DOM.setFileInputFiles", {"nodeId": node_id, "files": [str(reference_path)]})

    deadline = time.monotonic() + timeout_seconds
    last_state: dict[str, Any] = {}
    while time.monotonic() < deadline:
        state = await cdp.evaluate(
            """
            (()=>({
              referenceLoaded:typeof refImageData !== 'undefined' && !!refImageData,
              width:typeof refImageData !== 'undefined' && refImageData ? refImageData.width : 0,
              height:typeof refImageData !== 'undefined' && refImageData ? refImageData.height : 0,
              info:document.getElementById('ref-info')?.textContent || ''
            }))()
            """,
            timeout=10.0,
        )
        if isinstance(state, dict):
            last_state = state
            if state.get("referenceLoaded") and int(state.get("width") or 0) > 0 and int(state.get("height") or 0) > 0:
                return state
            if "失败" in str(state.get("info") or ""):
                raise RuntimeError(str(state.get("info")))
        await asyncio.sleep(0.1)
    detail = str(last_state.get("info") or "reference decode did not finish")
    raise TimeoutError(f"Cherry color reference did not load: {detail}")


async def _force_reference_finishing_steps(cdp: CdpClient) -> dict[str, Any]:
    """Make color matching then reference alignment the final two active steps."""

    state = await cdp.evaluate(
        """
        (()=>{
          if(typeof setModuleState !== 'function' || typeof currentOrder !== 'function'){
            return {ok:false,error:'Cherry module controls are unavailable'};
          }
          const modules=document.getElementById('modules');
          const colorModule=document.getElementById('mod-colormatch');
          const alignModule=document.getElementById('mod-align');
          if(!modules || !colorModule || !alignModule){
            return {ok:false,error:'Cherry reference module DOM is unavailable'};
          }
          setModuleState('colormatch',true);
          setModuleState('align',true);
          modules.appendChild(colorModule);
          modules.appendChild(alignModule);
          const configuredSteps=currentOrder().filter(step=>!!moduleState[step]);
          return {
            ok:true,
            colormatchEnabled:!!moduleState.colormatch,
            alignEnabled:!!moduleState.align,
            configuredSteps
          };
        })()
        """,
        timeout=10.0,
    )
    if not isinstance(state, dict) or not state.get("ok"):
        detail = state.get("error") if isinstance(state, dict) else "unknown error"
        raise RuntimeError(f"Cherry module policy could not be enforced: {detail}")
    configured = [str(step) for step in (state.get("configuredSteps") or [])]
    if configured[-2:] != ["colormatch", "align"]:
        raise RuntimeError("Cherry color matching and alignment could not be placed last in order")
    return state


async def _force_reference_steps_disabled(cdp: CdpClient) -> dict[str, Any]:
    """Preserve legacy non-reference Cherry callers without silently color-matching."""

    state = await cdp.evaluate(
        """
        (()=>{
          if(typeof setModuleState !== 'function' || typeof currentOrder !== 'function'){
            return {ok:false,error:'Cherry module controls are unavailable'};
          }
          setModuleState('colormatch',false);
          setModuleState('align',false);
          return {
            ok:true,
            colormatchEnabled:!!moduleState.colormatch,
            alignEnabled:!!moduleState.align,
            configuredSteps:currentOrder().filter(step=>!!moduleState[step])
          };
        })()
        """,
        timeout=10.0,
    )
    if not isinstance(state, dict) or not state.get("ok"):
        detail = state.get("error") if isinstance(state, dict) else "unknown error"
        raise RuntimeError(f"Cherry legacy module policy could not be enforced: {detail}")
    if state.get("colormatchEnabled") or state.get("alignEnabled"):
        raise RuntimeError("Cherry reference steps could not be disabled for a legacy run")
    return state


async def _install_fixed_alignment_transform(cdp: CdpClient, transform: dict[str, Any]) -> None:
    """Reuse the first micro-batch's transform across the rest of one sequence."""

    payload = json.dumps(_normalize_alignment_transform(transform), ensure_ascii=True)
    state = await cdp.evaluate(
        f"""
        (()=>{{
          if(typeof buildAlignTransform !== 'function'){{
            return {{ok:false,error:'buildAlignTransform is unavailable'}};
          }}
          const fixed={payload};
          window.__assetclawOriginalBuildAlignTransform ||= buildAlignTransform;
          buildAlignTransform=function(){{
            return {{s:fixed.s,tx:fixed.tx,ty:fixed.ty,assetclawFixed:true}};
          }};
          return {{ok:true,fixed}};
        }})()
        """,
        timeout=10.0,
    )
    if not isinstance(state, dict) or not state.get("ok"):
        detail = state.get("error") if isinstance(state, dict) else "unknown error"
        raise RuntimeError(f"Cherry fixed alignment transform could not be installed: {detail}")


async def _read_processing_report(cdp: CdpClient) -> dict[str, Any]:
    report = await cdp.evaluate(
        """
        (()=>{
          const report=window.__assetclawCherryReport;
          return report ? {
            referenceLoaded:!!report.referenceLoaded,
            executedSteps:Array.isArray(report.executedSteps)?report.executedSteps.slice():[],
            skippedNoRef:Array.isArray(report.skippedNoRef)?report.skippedNoRef.slice():[],
            colormatchEnabled:!!report.colormatchEnabled,
            alignEnabled:!!report.alignEnabled,
            alignmentTransform:report.alignmentTransform ? {
              s:Number(report.alignmentTransform.s),
              tx:Number(report.alignmentTransform.tx),
              ty:Number(report.alignmentTransform.ty),
              anchor:String(report.alignmentTransform.anchor||'')
            } : null,
            colorMatchStats:report.colorMatchStats ? {
              calls:Number(report.colorMatchStats.calls||0),
              applied:Number(report.colorMatchStats.applied||0),
              insufficient:Number(report.colorMatchStats.insufficient||0)
            } : null
          } : null;
        })()
        """,
        timeout=10.0,
    )
    if not isinstance(report, dict):
        raise RuntimeError("Cherry completed without an execution verification report")
    return report


def _validate_processing_report(
    report: dict[str, Any],
    *,
    expected_alignment_transform: dict[str, Any] | None = None,
    reference_steps_required: bool = True,
    expected_frames: int | None = None,
) -> None:
    executed = [str(step) for step in (report.get("executedSteps") or [])]
    skipped = [str(step) for step in (report.get("skippedNoRef") or [])]
    if not reference_steps_required:
        if report.get("colormatchEnabled") or report.get("alignEnabled"):
            raise RuntimeError("Cherry reference steps unexpectedly remained enabled")
        if "colormatch" in executed or "align" in executed:
            raise RuntimeError("Cherry unexpectedly executed reference-dependent steps")
        return
    if not report.get("referenceLoaded"):
        raise RuntimeError("Cherry completed without a loaded color reference")
    if skipped:
        raise RuntimeError(f"Cherry skipped reference-dependent steps: {', '.join(skipped)}")
    if not report.get("colormatchEnabled") or "colormatch" not in executed:
        raise RuntimeError("Cherry color matching was not executed")
    if not report.get("alignEnabled") or "align" not in executed:
        raise RuntimeError("Cherry reference alignment was not executed")
    if executed[-2:] != ["colormatch", "align"]:
        raise RuntimeError("Cherry color matching and alignment were not the final executed steps in order")
    color_stats = report.get("colorMatchStats") or {}
    calls = int(color_stats.get("calls") or 0)
    applied = int(color_stats.get("applied") or 0)
    insufficient = int(color_stats.get("insufficient") or 0)
    if insufficient or calls <= 0 or applied != calls:
        raise RuntimeError("Cherry color matching was a no-op for one or more frames")
    if expected_frames is not None and calls != int(expected_frames):
        raise RuntimeError(f"Cherry color matching covered {calls}/{int(expected_frames)} frames")
    actual_transform = _normalize_alignment_transform(report.get("alignmentTransform"))
    if expected_alignment_transform:
        expected = _normalize_alignment_transform(expected_alignment_transform)
        if any(abs(float(actual_transform[key]) - float(expected[key])) > 1e-6 for key in ("s", "tx", "ty")):
            raise RuntimeError("Cherry did not reuse the frozen sequence alignment transform")


def _normalize_alignment_transform(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise RuntimeError("Cherry completed without a verifiable alignment transform")
    normalized: dict[str, Any] = {}
    for key in ("s", "tx", "ty"):
        try:
            number = float(value.get(key))
        except (TypeError, ValueError) as exc:
            raise RuntimeError("Cherry returned an invalid alignment transform") from exc
        if not math.isfinite(number) or (key == "s" and number <= 0):
            raise RuntimeError("Cherry returned an invalid alignment transform")
        normalized[key] = number
    normalized["anchor"] = str(value.get("anchor") or "")
    return normalized


async def _wait_ready(cdp: CdpClient) -> None:
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        value = await cdp.evaluate(
            "document.readyState === 'complete' && !!document.getElementById('file-input') && "
            "!!document.getElementById('ref-input') && typeof setFiles === 'function' && "
            "typeof setRefFromFile === 'function' && typeof setModuleState === 'function' && "
            "typeof buildDiagnosticLog === 'function' && typeof buildAlignTransform === 'function'"
            " && typeof colorMatchToRef === 'function'",
            timeout=5.0,
        )
        if value is True:
            return
        await asyncio.sleep(0.2)
    raise TimeoutError("cherry html did not become ready")


def _validate_reference_image(reference_path: Path) -> None:
    if not reference_path.is_file():
        raise FileNotFoundError(f"Cherry color reference not found: {reference_path}")
    if reference_path.suffix.lower() not in {".png", ".webp", ".jpg", ".jpeg"}:
        raise ValueError(f"unsupported Cherry color reference format: {reference_path.suffix}")
    try:
        with Image.open(reference_path) as image:
            image.verify()
    except Exception as exc:
        raise ValueError(f"invalid Cherry color reference image: {reference_path}") from exc


async def _wait_processing_done(cdp: CdpClient, timeout_seconds: int) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        state = await cdp.evaluate(
            """
            (()=>{
              const err=document.getElementById('error-msg');
              const progress=document.getElementById('progress-text');
              const button=document.getElementById('btn-process');
              return {
                done: !!resultBlob,
                error: err && err.style.display !== 'none' ? err.textContent : '',
                progress: progress ? progress.textContent : '',
                processing: !!(button && button.disabled)
              };
            })()
            """,
            timeout=10.0,
        )
        if state and state.get("done"):
            return
        # Some Cherry HTML revisions reuse error-msg for non-fatal notices
        # (for example, optional reference-image steps being skipped). The
        # process button stays disabled while work is still progressing, so do
        # not turn an in-progress notice into a failed bot task. A real catch
        # path re-enables the button without producing resultBlob.
        if state and state.get("error") and not state.get("processing"):
            raise RuntimeError(str(state.get("error")))
        await asyncio.sleep(0.5)
    raise TimeoutError("cherry html processing timed out")


def _extract_outputs(zip_path: Path, input_root: Path, output_root: Path, files: list[Path]) -> None:
    output_root.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as archive:
        members = [name for name in archive.namelist() if not name.endswith("/")]
        by_name: dict[str, list[str]] = {}
        for member in members:
            by_name.setdefault(Path(member).name.lower(), []).append(member)
        for index, source in enumerate(files):
            target = output_root / source.relative_to(input_root).with_suffix(".png")
            expected = source.with_suffix(".png").name.lower()
            candidates = by_name.get(expected) or []
            member = candidates.pop(0) if candidates else (members[index] if index < len(members) else "")
            if not member:
                raise RuntimeError(f"missing processed output for {source.name}")
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(member) as src, target.open("wb") as dst:
                shutil.copyfileobj(src, dst)


def _validate_extracted_output_dimensions(
    input_root: Path,
    output_root: Path,
    files: list[Path],
    expected_width: int,
    expected_height: int,
) -> None:
    expected = (int(expected_width), int(expected_height))
    for source in files:
        target = output_root / source.relative_to(input_root).with_suffix(".png")
        try:
            with Image.open(target) as image:
                actual = image.size
        except Exception as exc:
            raise RuntimeError(f"Cherry output image could not be verified: {target}") from exc
        if actual != expected:
            raise RuntimeError(
                f"Cherry output dimensions mismatch for {target.name}: "
                f"expected {expected[0]}x{expected[1]}, got {actual[0]}x{actual[1]}"
            )


def _resolve_browser_candidates(chrome_path: Path | None) -> list[Path]:
    candidates: list[Path] = []
    if chrome_path:
        candidates.append(Path(chrome_path))
    for env_name in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
        base = os.environ.get(env_name)
        if not base:
            continue
        candidates.extend(
            [
                Path(base) / "Google" / "Chrome" / "Application" / "chrome.exe",
                Path(base) / "Microsoft" / "Edge" / "Application" / "msedge.exe",
            ]
        )
    for name in ("chrome", "msedge"):
        resolved = shutil.which(name)
        if resolved:
            candidates.append(Path(resolved))

    found: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        # An empty Path setting is represented as Path("."), which exists but
        # is not executable.  Accept only files so preflight cannot falsely
        # report the current directory as the browser runtime.
        key = str(candidate).lower()
        if candidate.is_file() and key not in seen:
            found.append(candidate)
            seen.add(key)
    return found


def _resolve_chrome(chrome_path: Path | None) -> Path:
    candidates = _resolve_browser_candidates(chrome_path)
    if candidates:
        return candidates[0]
    raise FileNotFoundError("Chrome or Edge executable not found")


def _start_chrome(
    chrome: Path,
    port: int,
    profile_dir: Path,
    *,
    stdout_path: Path | None = None,
    stderr_path: Path | None = None,
) -> subprocess.Popen[Any]:
    flags = 0
    if hasattr(subprocess, "CREATE_NO_WINDOW"):
        flags = subprocess.CREATE_NO_WINDOW
    stdout_handle = stdout_path.open("wb") if stdout_path else None
    stderr_handle = stderr_path.open("wb") if stderr_path else None
    try:
        return subprocess.Popen(
            [
                str(chrome),
                "--headless=new",
                # The bot is often launched from an elevated PowerShell window
                # on Windows. --enable-automation prevents Chromium's automatic
                # de-elevation restart from discarding the DevTools launch;
                # no-sandbox keeps that elevated headless instance runnable.
                "--enable-automation",
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-gpu",
                "--no-first-run",
                "--no-default-browser-check",
                "--disable-background-networking",
                "--disable-component-update",
                "--disable-sync",
                "--disable-extensions",
                "--disable-default-apps",
                "--enable-logging=stderr",
                "--v=0",
                "--disable-features=Translate,OptimizationHints",
                "--allow-file-access-from-files",
                f"--remote-debugging-port={port}",
                f"--user-data-dir={profile_dir}",
                "about:blank",
            ],
            stdout=stdout_handle or subprocess.DEVNULL,
            stderr=stderr_handle or subprocess.DEVNULL,
            creationflags=flags,
        )
    finally:
        if stdout_handle:
            stdout_handle.close()
        if stderr_handle:
            stderr_handle.close()


def _stop_chrome(proc: subprocess.Popen[Any]) -> None:
    if proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()


def _wait_for_page_ws(port: int, proc: subprocess.Popen[Any] | None = None) -> str:
    deadline = time.time() + 30
    last_error: Exception | None = None
    clean_launcher_exit_at: float | None = None
    session = requests.Session()
    session.trust_env = False
    try:
        while time.time() < deadline:
            if proc is not None:
                return_code = proc.poll()
                if return_code is not None:
                    if return_code != 0:
                        raise RuntimeError(f"browser exited before debugger became ready (exit_code={return_code})")
                    # chrome.exe/msedge.exe may be a Windows launcher stub. It
                    # can hand the real headless browser to another process and
                    # exit with code 0 before the debugger starts listening.
                    clean_launcher_exit_at = clean_launcher_exit_at or time.time()
            try:
                pages = session.get(f"http://127.0.0.1:{port}/json/list", timeout=(0.5, 1.5)).json()
                for page in pages:
                    if page.get("type") == "page" and page.get("webSocketDebuggerUrl"):
                        return str(page["webSocketDebuggerUrl"])
            except Exception as exc:
                last_error = exc
            if clean_launcher_exit_at is not None and time.time() - clean_launcher_exit_at >= 5:
                raise RuntimeError(
                    "browser launcher exited cleanly, but debugger did not become ready "
                    f"within 5s (exit_code=0, last_error={last_error})"
                )
            time.sleep(0.2)
    finally:
        session.close()
    raise TimeoutError(f"Chrome remote debugging endpoint did not start: {last_error}")


def _browser_start_failure(
    browser: Path,
    attempt: int,
    port: int,
    exc: Exception,
    stdout_path: Path,
    stderr_path: Path,
) -> str:
    diagnostics = _read_log_tail(stderr_path) or _read_log_tail(stdout_path)
    suffix = f"\nbrowser output:\n{diagnostics}" if diagnostics else ""
    return f"browser={browser} attempt={attempt} port={port}: {exc}{suffix}"


def _read_log_tail(path: Path, max_chars: int = 6000) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return ""
    return text[-max_chars:]


def _write_browser_start_failure(work_root: Path, detail: str) -> None:
    stamp = time.strftime("%Y%m%d_%H%M%S")
    path = work_root / f"browser_start_failure_{stamp}_{time.time_ns() % 1_000_000:06d}.log"
    try:
        path.write_text(detail, encoding="utf-8")
    except OSError:
        pass


async def _wait_download(download_dir: Path, timeout_seconds: int) -> Path:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        partials = list(download_dir.glob("*.crdownload"))
        zips = sorted(download_dir.glob("*.zip"), key=lambda path: path.stat().st_mtime, reverse=True)
        if zips and not partials:
            return zips[0]
        await asyncio.sleep(0.2)
    raise TimeoutError("cherry html zip download timed out")


def _create_session_dir(work_root: Path) -> Path:
    """Create one browser session without tempfile's Windows permission loop.

    ``tempfile.mkdtemp`` deliberately retries ``PermissionError`` on Windows
    because older Windows versions can report a name collision that way.  On
    a genuinely unwritable inherited ACL it therefore spins through TMP_MAX
    candidates at 100% CPU.  Our names include pid + nanoseconds, so a real
    permission error must fail immediately and visibly.
    """
    for attempt in range(8):
        candidate = work_root / f"run_{os.getpid()}_{time.time_ns()}_{attempt}"
        try:
            candidate.mkdir(mode=0o700)
        except FileExistsError:
            continue
        except PermissionError as exc:
            raise PermissionError(f"Cherry browser session directory is not writable: {work_root}") from exc
        return candidate
    raise FileExistsError(f"could not allocate a unique Cherry browser session under {work_root}")


def _remove_session_dir(session_dir: Path, timeout_seconds: float = 5.0) -> bool:
    """Wait briefly for Chromium to release its Windows profile locks."""
    deadline = time.monotonic() + max(0.0, timeout_seconds)
    while True:
        try:
            shutil.rmtree(session_dir)
            return True
        except FileNotFoundError:
            return True
        except OSError:
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.1)


def _cleanup_old_sessions(work_root: Path, max_age_seconds: int = 24 * 3600) -> None:
    """Remove abandoned browser sessions without walking Chromium profiles.

    Chrome profiles contain large, deeply nested cache trees (and can contain
    Windows reparse points).  Recursively scanning every previous profile on
    the hot path made a new one-frame Cherry run spend minutes at 100% CPU
    before it even created its own session directory.  The run directory's
    mtime is sufficient here: every session is created immediately before the
    browser starts, and the 24-hour grace period is far longer than the
    processing timeout.
    """
    now = time.time()
    for path in work_root.glob("run_*"):
        try:
            if not path.is_dir():
                continue
            if now - path.stat().st_mtime > max_age_seconds:
                shutil.rmtree(path, ignore_errors=True)
        except OSError:
            continue


def verified_cherry_html_path(source_path: Path, storage_dir: Path) -> Path:
    metadata_path = Path(storage_dir) / "cherry_html_releases" / "active.json"
    try:
        payload = json.loads(metadata_path.read_text(encoding="utf-8"))
        configured_source = Path(source_path).resolve()
        release_source = Path(str(payload.get("source_path") or "")).resolve()
        if release_source != configured_source:
            return Path(source_path)
        active = Path(str(payload.get("path") or ""))
        if active.is_file() and str(payload.get("sha256") or "") == _sha256_file(active):
            return active
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        pass
    return Path(source_path)


def verify_and_promote_cherry_html(
    source_path: Path,
    storage_dir: Path,
    *,
    chrome_path: Path | None = None,
    timeout_seconds: int = 180,
) -> dict[str, Any]:
    source = Path(source_path).resolve()
    storage = Path(storage_dir).resolve()
    release_root = storage / "cherry_html_releases"
    metadata_path = release_root / "active.json"
    active_before = verified_cherry_html_path(source, storage)
    digest = ""
    try:
        validate_cherry_html_runtime(source, chrome_path)
        digest = _sha256_file(source)
    except Exception as exc:
        if active_before.is_file() and active_before.resolve() != source:
            return {
                "ok": True,
                "promoted": False,
                "sha256": digest,
                "path": str(active_before),
                "fallback": True,
                "candidate_error": str(exc),
            }
        return {"ok": False, "promoted": False, "sha256": digest, "path": "", "candidate_error": str(exc)}
    try:
        current = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        current = {}
    if str(current.get("sha256") or "") == digest and active_before.is_file():
        return {"ok": True, "promoted": False, "sha256": digest, "path": str(active_before), "cached": True}

    canary_root = storage / "cherry_html_canary" / digest[:16]
    input_dir = canary_root / "input"
    output_dir = canary_root / "output"
    input_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)
    probe = input_dir / "canary.png"
    Image.new("RGBA", (32, 32), (96, 160, 224, 192)).save(probe)
    produced = output_dir / "canary.png"
    produced.unlink(missing_ok=True)
    try:
        result = run_cherry_html(
            source,
            input_dir,
            output_dir,
            [probe],
            reference_path=probe,
            expected_profile="half",
            expected_width=256,
            expected_height=256,
            chrome_path=chrome_path,
            timeout_seconds=max(30, int(timeout_seconds)),
            storage_dir=storage,
        )
        if not produced.is_file() or produced.stat().st_size <= 0:
            raise RuntimeError("Cherry canary did not produce canary.png")
        with Image.open(produced) as image:
            image.verify()
        if result.total != 1:
            raise RuntimeError(f"Cherry canary returned total={result.total}, expected 1")
    except Exception as exc:
        if active_before.is_file() and active_before.resolve() != source:
            return {
                "ok": True,
                "promoted": False,
                "sha256": digest,
                "path": str(active_before),
                "fallback": True,
                "candidate_error": str(exc),
            }
        return {"ok": False, "promoted": False, "sha256": digest, "path": "", "candidate_error": str(exc)}

    release_dir = release_root / digest
    release_dir.mkdir(parents=True, exist_ok=True)
    release_path = release_dir / "cherry-postprocess.html"
    if not release_path.is_file() or _sha256_file(release_path) != digest:
        shutil.copy2(source, release_path)
    payload = {
        "sha256": digest,
        "path": str(release_path.resolve()),
        "source_path": str(source),
        "verified_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "canary_resize": result.resize,
        "canary_steps": result.steps,
        "canary_executed_steps": result.executed_steps,
        "canary_reference_loaded": result.reference_loaded,
        "canary_reference_sha256": result.reference_sha256,
    }
    release_root.mkdir(parents=True, exist_ok=True)
    metadata_tmp = release_root / "active.json.tmp"
    metadata_tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    metadata_tmp.replace(metadata_path)
    return {"ok": True, "promoted": True, "sha256": digest, "path": str(release_path), "cached": False}


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])
