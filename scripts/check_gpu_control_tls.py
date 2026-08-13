from __future__ import annotations

import argparse
import json

import httpx


READ_ONLY_ENDPOINTS = (
    "/api/v1/version",
    "/health/live",
    "/health/ready",
    "/api/v1/scheduler/capacity",
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only GPU Control TLS preflight")
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--ca-bundle", required=True)
    args = parser.parse_args()

    base_url = args.base_url.rstrip("/")
    failed = False
    with httpx.Client(verify=args.ca_bundle, timeout=15.0) as client:
        for path in READ_ONLY_ENDPOINTS:
            try:
                response = client.get(base_url + path)
                print(f"{path} status={response.status_code}")
                try:
                    print(json.dumps(response.json(), ensure_ascii=False, sort_keys=True))
                except Exception:
                    print(response.text[:1000])
                failed = failed or response.status_code != 200
            except Exception as exc:
                failed = True
                print(f"{path} error={type(exc).__name__}: {exc}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
