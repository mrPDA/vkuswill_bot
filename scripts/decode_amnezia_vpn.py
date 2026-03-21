#!/usr/bin/env python3
"""Декодирование Amnezia `vpn://...` → JSON и/или awg-quick .conf (AmneziaWG).

Формат контейнера: `containers[].awg.last_config` — JSON-строка с полем
`config` (WireGuard ini + AmneziaWG поля).
"""

from __future__ import annotations

import argparse
import base64
import json
import sys
import zlib


def decode_root(path: str) -> dict:
    with open(path, encoding="utf-8") as fh:
        raw = fh.read().strip()
    if not raw.startswith("vpn://"):
        raise ValueError("Ожидается строка, начинающаяся с vpn://")
    encoded_data = raw.replace("vpn://", "").strip()
    pad = (4 - (len(encoded_data) % 4)) % 4
    encoded_data += "=" * pad
    blob = base64.urlsafe_b64decode(encoded_data)
    if len(blob) < 5:
        raise ValueError("Слишком короткие данные после base64")
    original_len = int.from_bytes(blob[:4], byteorder="big")
    decompressed = zlib.decompress(blob[4:])
    if len(decompressed) != original_len:
        raise ValueError(
            f"Несовпадение длины после zlib: ожидали {original_len}, получили {len(decompressed)}"
        )
    return json.loads(decompressed.decode("utf-8"))


def extract_awg_quick_conf(data: dict) -> str:
    dns1 = str(data.get("dns1", "1.1.1.1"))
    dns2 = str(data.get("dns2", "8.8.8.8"))
    for c in data.get("containers", []):
        if not isinstance(c, dict) or c.get("container") != "amnezia-awg":
            continue
        awg = c.get("awg")
        if not isinstance(awg, dict):
            continue
        lc = awg.get("last_config")
        if isinstance(lc, str) and lc.strip().startswith("{"):
            inner = json.loads(lc)
        elif isinstance(lc, dict):
            inner = lc
        else:
            continue
        cfg = inner.get("config")
        if isinstance(cfg, str) and "[Interface]" in cfg:
            return cfg.replace("$PRIMARY_DNS", dns1).replace("$SECONDARY_DNS", dns2)
    raise ValueError("Не найден amnezia-awg / last_config.config в конфиге")


def main() -> int:
    ap = argparse.ArgumentParser(description="Amnezia vpn:// → JSON / awg-quick.conf")
    ap.add_argument("vpn_file")
    ap.add_argument("--json-out", help="Полный JSON")
    ap.add_argument(
        "--awg-out",
        help="Файл для awg-quick (AmneziaWG), например deploy/amnezia-wg0.conf",
    )
    args = ap.parse_args()
    try:
        data = decode_root(args.vpn_file)
    except Exception as e:
        print("Ошибка декодирования:", e, file=sys.stderr)
        return 1
    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(data, indent=2, ensure_ascii=False))
        print("JSON:", args.json_out)
    if args.awg_out:
        try:
            wg = extract_awg_quick_conf(data)
        except ValueError as e:
            print(e, file=sys.stderr)
            return 2
        with open(args.awg_out, "w", encoding="utf-8") as fh:
            fh.write(wg)
        print("AmneziaWG:", args.awg_out)
    if not args.json_out and not args.awg_out:
        print(json.dumps(data, indent=2, ensure_ascii=False)[:12000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
