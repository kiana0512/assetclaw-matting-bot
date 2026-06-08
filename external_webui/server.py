from __future__ import annotations

import argparse
import json
import mimetypes
import os
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parent
STATIC_ROOT = ROOT / "dist" if (ROOT / "dist" / "index.html").exists() else ROOT
DEFAULT_AGENT_URL = "http://127.0.0.1:7865"
ALLOWED_AGENT_HOSTS = {"127.0.0.1", "localhost", "::1"}


def _read_env_value(name: str) -> str:
    env_path = ROOT.parent / ".env"
    if not env_path.exists():
        return ""
    try:
        for line in env_path.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            if key.strip().upper() == name.upper():
                return value.strip().strip('"').strip("'")
    except Exception:
        return ""
    return ""


def _candidate_agent_urls() -> list[str]:
    values = [
        os.environ.get("ASSETCLAW_AGENT_URL", ""),
        os.environ.get("GATEWAY_BASE_URL", ""),
        _read_env_value("GATEWAY_BASE_URL"),
    ]
    port = os.environ.get("GATEWAY_PORT") or _read_env_value("GATEWAY_PORT")
    if port:
        values.append(f"http://127.0.0.1:{port}")
    values.extend([DEFAULT_AGENT_URL, "http://127.0.0.1:8000"])
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        raw = str(value or "").rstrip("/")
        if raw and raw not in seen:
            seen.add(raw)
            result.append(raw)
    return result


def _validate_agent_url(raw: str) -> str:
    parsed = urlparse(raw)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("ASSETCLAW_AGENT_URL must be http(s)")
    if parsed.hostname not in ALLOWED_AGENT_HOSTS:
        raise ValueError("External WebUI proxy only allows localhost Agent backends")
    return raw


def _agent_url() -> str:
    return _validate_agent_url(_candidate_agent_urls()[0])


class Handler(BaseHTTPRequestHandler):
    server_version = "AssetClawWebUI/0.1"

    def do_GET(self) -> None:
        if self.path.startswith("/api/"):
            self._proxy()
            return
        self._serve_static()

    def do_POST(self) -> None:
        if self.path.startswith("/api/"):
            self._proxy()
            return
        self._json({"ok": False, "error": "not found"}, HTTPStatus.NOT_FOUND)

    def log_message(self, fmt: str, *args: object) -> None:
        print("%s - %s" % (self.address_string(), fmt % args))

    def _serve_static(self) -> None:
        route = urlparse(self.path).path
        if route == "/":
            route = "/index.html"
        target = (STATIC_ROOT / route.lstrip("/")).resolve()
        if STATIC_ROOT not in target.parents and target != STATIC_ROOT:
            self._json({"ok": False, "error": "invalid path"}, HTTPStatus.BAD_REQUEST)
            return
        if not target.exists() or not target.is_file():
            fallback = (STATIC_ROOT / "index.html").resolve()
            if fallback.exists() and fallback.is_file() and STATIC_ROOT in fallback.parents:
                target = fallback
            else:
                self._json({"ok": False, "error": "not found"}, HTTPStatus.NOT_FOUND)
                return
        mime = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        data = target.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _proxy(self) -> None:
        parsed = urlparse(self.path)
        target_path = self._map_api_path(parsed.path)
        if not target_path:
            self._json({"ok": False, "error": "unknown api route"}, HTTPStatus.NOT_FOUND)
            return

        qs = parse_qs(parsed.query, keep_blank_values=True)
        query = urlencode(qs, doseq=True)
        body = None
        headers = {"Accept": "application/json"}
        if self.command in {"POST", "PUT", "PATCH"}:
            length = int(self.headers.get("Content-Length") or "0")
            body = self.rfile.read(length) if length else b"{}"
            headers["Content-Type"] = self.headers.get("Content-Type", "application/json")

        configured_skill_token = (
            os.environ.get("ASSETCLAW_SKILL_TOKEN")
            or os.environ.get("SKILL_API_TOKEN")
            or _read_env_value("ASSETCLAW_SKILL_TOKEN")
            or _read_env_value("SKILL_API_TOKEN")
        )
        skill_token = configured_skill_token or self.headers.get("X-Skill-Token") or ""
        if skill_token and target_path.startswith("/skills/"):
            headers["X-Skill-Token"] = skill_token

        last_error = ""
        tried: list[str] = []
        for base_url in _candidate_agent_urls():
            base_url = _validate_agent_url(base_url)
            tried.append(base_url)
            url = f"{base_url}{target_path}"
            if query:
                url += f"?{query}"
            try:
                req = Request(url, data=body, method=self.command, headers=headers)
                with urlopen(req, timeout=30) as resp:
                    data = resp.read()
                    self.send_response(resp.status)
                    self.send_header("Content-Type", resp.headers.get("Content-Type", "application/json"))
                    self.send_header("Content-Length", str(len(data)))
                    self.send_header("X-Agent-Url", base_url)
                    self.end_headers()
                    self.wfile.write(data)
                    return
            except HTTPError as exc:
                data = exc.read()
                self.send_response(exc.code)
                self.send_header("Content-Type", exc.headers.get("Content-Type", "application/json"))
                self.send_header("Content-Length", str(len(data)))
                self.send_header("X-Agent-Url", base_url)
                self.end_headers()
                self.wfile.write(data)
                return
            except (URLError, TimeoutError, ConnectionError) as exc:
                last_error = str(exc)
                continue
        self._json({"ok": False, "offline": True, "error": last_error, "tried_agent_urls": tried, "agent_url": tried[0] if tried else ""}, HTTPStatus.BAD_GATEWAY)

    def _map_api_path(self, path: str) -> str:
        table = {
            "/api/health": "/health",
            "/api/admin/queue": "/admin/queue",
            "/api/admin/brain-messages": "/admin/brain-messages",
            "/api/admin/memory": "/admin/memory",
            "/api/admin/skill-calls": "/admin/skill-calls",
            "/api/brain/test": "/brain/test",
            "/api/skills/manifest": "/skills/v1/manifest",
            "/api/skills/call": "/skills/v1/call",
        }
        return table.get(path, "")

    def _json(self, payload: dict, status: HTTPStatus = HTTPStatus.OK) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the local-only AssetClaw External WebUI.")
    parser.add_argument("--host", default=os.environ.get("ASSETCLAW_WEBUI_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("ASSETCLAW_WEBUI_PORT", "5177")))
    args = parser.parse_args()
    if args.host not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("External WebUI must bind to localhost only")
    _agent_url()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"AssetClaw External WebUI: http://{args.host}:{args.port}")
    print("Agent backend candidates:")
    for item in _candidate_agent_urls():
        print(f"  - {item}")
    server.serve_forever()


if __name__ == "__main__":
    main()
