from __future__ import annotations

import asyncio
import json
import os
import shutil
import socket
import subprocess
import tempfile
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests
import websockets


@dataclass(frozen=True)
class CherryHtmlResult:
    output_dir: Path
    total: int
    profile: str
    resize: str
    feather_enabled: bool
    steps: list[str]
    downloaded_zip: Path


def validate_cherry_html_runtime(html_path: Path, chrome_path: Path | None = None) -> dict[str, str]:
    source = Path(html_path)
    if not source.is_file():
        raise FileNotFoundError(f"Cherry algorithm HTML not found: {source}")
    html = source.read_text(encoding="utf-8", errors="ignore")
    missing = [marker for marker in ("file-input", "btn-process", "btn-download") if marker not in html]
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
        result = await asyncio.wait_for(fut, timeout=timeout)
        if "error" in result:
            raise RuntimeError(f"CDP {method} failed: {result['error']}")
        return result.get("result") or {}

    async def wait_event(self, method: str, timeout: float = 30.0) -> dict[str, Any]:
        deadline = time.time() + timeout
        while True:
            for index, event in enumerate(self._events):
                if event.get("method") == method:
                    return self._events.pop(index)
            remain = deadline - time.time()
            if remain <= 0:
                raise TimeoutError(f"timed out waiting for {method}")
            self._event.clear()
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
                self._event.set()


def run_cherry_html(
    html_path: Path,
    input_root: Path,
    output_root: Path,
    files: list[Path],
    *,
    chrome_path: Path | None = None,
    timeout_seconds: int = 900,
    storage_dir: Path | None = None,
) -> CherryHtmlResult:
    return asyncio.run(
        _run_cherry_html_async(
            html_path=html_path,
            input_root=input_root,
            output_root=output_root,
            files=files,
            chrome_path=chrome_path,
            timeout_seconds=timeout_seconds,
            storage_dir=storage_dir,
        )
    )


async def _run_cherry_html_async(
    *,
    html_path: Path,
    input_root: Path,
    output_root: Path,
    files: list[Path],
    chrome_path: Path | None,
    timeout_seconds: int,
    storage_dir: Path | None,
) -> CherryHtmlResult:
    html_path = html_path.resolve()
    input_root = input_root.resolve()
    output_root = output_root.resolve()
    files = [path.resolve() for path in files]
    if not html_path.exists():
        raise FileNotFoundError(str(html_path))
    if not files:
        raise ValueError("no input images")
    for path in files:
        if not path.exists():
            raise FileNotFoundError(str(path))

    chrome = _resolve_chrome(chrome_path)
    work_root = Path(storage_dir or tempfile.gettempdir()) / "cherry_html_runner"
    work_root.mkdir(parents=True, exist_ok=True)
    _cleanup_old_sessions(work_root)
    session_dir = Path(tempfile.mkdtemp(prefix="run_", dir=str(work_root)))
    profile_dir = session_dir / "chrome_profile"
    download_dir = session_dir / "downloads"
    profile_dir.mkdir(parents=True, exist_ok=True)
    download_dir.mkdir(parents=True, exist_ok=True)
    port = _free_port()
    proc = _start_chrome(chrome, port, profile_dir)
    try:
        ws_url = _wait_for_page_ws(port)
        async with CdpClient(ws_url) as cdp:
            await cdp.send("Page.enable")
            await cdp.send("Runtime.enable")
            await cdp.send("DOM.enable")
            await cdp.send(
                "Browser.setDownloadBehavior",
                {"behavior": "allow", "downloadPath": str(download_dir)},
            )
            await cdp.send("Page.navigate", {"url": html_path.as_uri()})
            try:
                await cdp.wait_event("Page.loadEventFired", timeout=30.0)
            except TimeoutError:
                pass
            await _wait_ready(cdp)
            doc = await cdp.send("DOM.getDocument", {"depth": 1, "pierce": True})
            node = await cdp.send(
                "DOM.querySelector",
                {"nodeId": doc["root"]["nodeId"], "selector": "#file-input"},
            )
            node_id = node.get("nodeId")
            if not node_id:
                raise RuntimeError("cherry html file input not found")
            await cdp.send("DOM.setFileInputFiles", {"nodeId": node_id, "files": [str(path) for path in files]})
            preset = await cdp.evaluate(
                """
                (async()=>{
                  const input=document.getElementById('file-input');
                  input.dispatchEvent(new Event('change',{bubbles:true}));
                  await new Promise(resolve=>setTimeout(resolve,100));
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
                """,
                timeout=30.0,
            )
            if int((preset or {}).get("count") or 0) != len(files):
                raise RuntimeError(f"cherry html loaded {(preset or {}).get('count')} files, expected {len(files)}")
            await cdp.evaluate("document.getElementById('btn-process').click();", timeout=10.0)
            await _wait_processing_done(cdp, timeout_seconds)
            await cdp.evaluate("document.getElementById('btn-download').click();", timeout=10.0)
            downloaded = _wait_download(download_dir, timeout_seconds)

        _extract_outputs(downloaded, input_root, output_root, files)
        resize = str((preset or {}).get("resize") or "")
        return CherryHtmlResult(
            output_dir=output_root,
            total=len(files),
            profile="half" if resize == "256x256" else "full",
            resize=resize,
            feather_enabled=bool((preset or {}).get("feather")),
            steps=[str(step) for step in ((preset or {}).get("steps") or [])],
            downloaded_zip=downloaded,
        )
    finally:
        _stop_chrome(proc)
        shutil.rmtree(session_dir, ignore_errors=True)


async def _wait_ready(cdp: CdpClient) -> None:
    deadline = time.time() + 30
    while time.time() < deadline:
        value = await cdp.evaluate(
            "document.readyState === 'complete' && !!document.getElementById('file-input') && typeof setFiles === 'function'",
            timeout=5.0,
        )
        if value is True:
            return
        await asyncio.sleep(0.2)
    raise TimeoutError("cherry html did not become ready")


async def _wait_processing_done(cdp: CdpClient, timeout_seconds: int) -> None:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        state = await cdp.evaluate(
            """
            (()=>{
              const err=document.getElementById('error-msg');
              const progress=document.getElementById('progress-text');
              return {
                done: !!resultBlob,
                error: err && err.style.display !== 'none' ? err.textContent : '',
                progress: progress ? progress.textContent : ''
              };
            })()
            """,
            timeout=10.0,
        )
        if state and state.get("error"):
            raise RuntimeError(str(state.get("error")))
        if state and state.get("done"):
            return
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


def _resolve_chrome(chrome_path: Path | None) -> Path:
    candidates = []
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
    for candidate in candidates:
        if candidate.exists():
            return candidate
    resolved = shutil.which("chrome") or shutil.which("msedge")
    if resolved:
        return Path(resolved)
    raise FileNotFoundError("Chrome or Edge executable not found")


def _start_chrome(chrome: Path, port: int, profile_dir: Path) -> subprocess.Popen[Any]:
    flags = 0
    if hasattr(subprocess, "CREATE_NO_WINDOW"):
        flags = subprocess.CREATE_NO_WINDOW
    return subprocess.Popen(
        [
            str(chrome),
            "--headless=new",
            "--disable-gpu",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-background-networking",
            "--disable-component-update",
            "--disable-sync",
            "--disable-extensions",
            "--disable-default-apps",
            "--disable-logging",
            "--disable-features=Translate,OptimizationHints",
            "--allow-file-access-from-files",
            f"--remote-debugging-port={port}",
            f"--user-data-dir={profile_dir}",
            "about:blank",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=flags,
    )


def _stop_chrome(proc: subprocess.Popen[Any]) -> None:
    if proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()


def _wait_for_page_ws(port: int) -> str:
    deadline = time.time() + 20
    last_error: Exception | None = None
    while time.time() < deadline:
        try:
            pages = requests.get(f"http://127.0.0.1:{port}/json/list", timeout=2).json()
            for page in pages:
                if page.get("type") == "page" and page.get("webSocketDebuggerUrl"):
                    return str(page["webSocketDebuggerUrl"])
        except Exception as exc:
            last_error = exc
        time.sleep(0.2)
    raise TimeoutError(f"Chrome remote debugging endpoint did not start: {last_error}")


def _wait_download(download_dir: Path, timeout_seconds: int) -> Path:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        partials = list(download_dir.glob("*.crdownload"))
        zips = sorted(download_dir.glob("*.zip"), key=lambda path: path.stat().st_mtime, reverse=True)
        if zips and not partials:
            return zips[0]
        time.sleep(0.2)
    raise TimeoutError("cherry html zip download timed out")


def _cleanup_old_sessions(work_root: Path, max_age_seconds: int = 3600) -> None:
    now = time.time()
    for path in work_root.glob("run_*"):
        try:
            if not path.is_dir():
                continue
            newest = max((item.stat().st_mtime for item in path.rglob("*")), default=path.stat().st_mtime)
            if now - newest > max_age_seconds:
                shutil.rmtree(path, ignore_errors=True)
        except OSError:
            continue


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])
