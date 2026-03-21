from __future__ import annotations

from pathlib import Path
from urllib.parse import quote
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "assets" / "realistic_candidates"
OUT_DIR.mkdir(parents=True, exist_ok=True)

prompt = (
    "ultra realistic black and white vintage group photo of twelve pirate crew members "
    "on a wooden ship deck, hard side lighting, rough weathered faces, cinematic film "
    "grain, Soviet adventure mood inspired by Treasure Island 1988 aesthetic, "
    "historically styled pirate coats and hats, human proportions, no cartoon, no "
    "illustration, high detail"
)

for seed in (101, 202, 303, 404):
    url = (
        "https://image.pollinations.ai/prompt/"
        + quote(prompt)
        + f"?width=2400&height=1400&seed={seed}&model=flux&nologo=true"
    )
    req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
    out = OUT_DIR / f"team_realistic_{seed}.png"
    with urlopen(req, timeout=180) as r:
        data = r.read()
    out.write_bytes(data)
    print(f"saved {out} {len(data)} bytes")
