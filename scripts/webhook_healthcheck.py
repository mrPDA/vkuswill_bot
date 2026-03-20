#!/usr/bin/env python3
"""Проверка /health для Docker HEALTHCHECK (HTTP или HTTPS с самоподписью)."""

from __future__ import annotations

import os
import ssl
import sys
import urllib.error
import urllib.request


def main() -> None:
    use_webhook = os.environ.get("USE_WEBHOOK", "").strip().lower() in ("1", "true", "yes")
    if not use_webhook:
        sys.exit(0)

    port = int(os.environ.get("WEBHOOK_PORT", "8080"))
    key_path = os.environ.get("WEBHOOK_KEY_PATH", "").strip()
    url = f"https://127.0.0.1:{port}/health" if key_path else f"http://127.0.0.1:{port}/health"

    try:
        if key_path:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            urllib.request.urlopen(url, context=ctx, timeout=8)
        else:
            urllib.request.urlopen(url, timeout=8)
    except (urllib.error.URLError, TimeoutError, OSError):
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
