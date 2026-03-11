from __future__ import annotations

from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen
import json
import hashlib
import random

from PIL import Image, ImageChops, ImageDraw, ImageEnhance, ImageFilter, ImageOps


ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "assets" / "team_hispaniola_group_photo_realistic_bw.png"
CACHE_DIR = ROOT / "assets" / "realistic_sources"

W, H = 2400, 1400

FILE_TITLES = [
    "File:Anime Expo 2011 - Captain Jack Sparrow (5892743693).jpg",
    "File:C2E2 2013 - Jack Sparrow (8687941881).jpg",
    "File:C2E2 2013 - Jack Sparrow (8699837677).jpg",
    "File:Cap'n Jack Sparrow (203943108).jpg",
    "File:Captain Jack (5755676624).jpg",
    "File:Captain Jack Sparrow (5763467649).jpg",
    "File:Captain jack sparrow cosplay (14049832800).jpg",
    "File:Captain Jack Sparrow cosplayer (8422419188).jpg",
    "File:Cosplay of Jack Sparrow at Brussels Comic Con 2022 (51973183344).jpg",
    "File:MCM London 2013 - Captain Jack & Tia Dalma (8964196168).jpg",
    "File:New York Comic Con 2014 - Captain Jack Sparrow (15335754179).jpg",
    "File:Otakuthon 2014- Captain Jack Sparrow (15029629895).jpg",
    "File:Hastings Pirate Day - Female European Pirate.jpg",
    "File:Louisiana Renaissance Festival Pirate.jpg",
    "File:French Quarter Pirates 2018-03-31 New Orleans.jpg",
    "File:Blackbeard (2348664448).jpg",
]


def _api_get(params: dict[str, str]) -> dict:
    url = "https://commons.wikimedia.org/w/api.php?" + urlencode(params)
    req = Request(url, headers={"User-Agent": "vkuswill-bot-codex/1.0"})
    with urlopen(req, timeout=60) as resp:
        return json.load(resp)


def _file_url(title: str) -> str | None:
    data = _api_get(
        {
            "action": "query",
            "prop": "imageinfo",
            "iiprop": "url",
            "iiurlwidth": "1400",
            "titles": title,
            "format": "json",
        }
    )
    pages = data.get("query", {}).get("pages", {})
    for page in pages.values():
        info = page.get("imageinfo")
        if info and isinstance(info, list):
            return info[0].get("thumburl") or info[0].get("url")
    return None


def _download_image(title: str, out_path: Path) -> bool:
    url = _file_url(title)
    if not url:
        return False
    req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urlopen(req, timeout=120) as resp:
        data = resp.read()
    out_path.write_bytes(data)
    return True


def _crop_to_ratio(img: Image.Image, target_w: int, target_h: int) -> Image.Image:
    src_w, src_h = img.size
    target_ratio = target_w / target_h
    src_ratio = src_w / src_h if src_h else 1

    if src_ratio > target_ratio:
        new_w = int(src_h * target_ratio)
        left = (src_w - new_w) // 2
        right = left + new_w
        top, bottom = 0, src_h
    else:
        new_h = int(src_w / target_ratio)
        center_y = int(src_h * 0.34)
        top = max(0, center_y - new_h // 2)
        if top + new_h > src_h:
            top = src_h - new_h
        left, right = 0, src_w
        bottom = top + new_h
    return img.crop((left, top, right, bottom))


def _make_tile(img: Image.Image, tile_w: int, tile_h: int, seed: int) -> Image.Image:
    rnd = random.Random(seed)

    base = img.convert("RGB")
    base = _crop_to_ratio(base, tile_w, tile_h)
    base = base.resize((tile_w, tile_h), Image.Resampling.LANCZOS)

    gray = ImageOps.grayscale(base)
    gray = ImageOps.autocontrast(gray, cutoff=2)
    gray = ImageEnhance.Contrast(gray).enhance(1.25)
    gray = ImageEnhance.Sharpness(gray).enhance(1.18)
    gray = gray.filter(ImageFilter.GaussianBlur(radius=0.4))

    # Local vignette on each portrait.
    vig = Image.new("L", (tile_w, tile_h), color=255)
    d = ImageDraw.Draw(vig)
    d.ellipse((-tile_w * 0.18, -tile_h * 0.08, tile_w * 1.18, tile_h * 1.12), fill=0)
    vig = vig.filter(ImageFilter.GaussianBlur(radius=70))
    gray = ImageChops.subtract(gray, ImageOps.invert(vig).point(lambda x: x * 0.45))

    # Slight random rotation for "group photo" feel.
    ang = rnd.uniform(-1.8, 1.8)
    rotated = gray.rotate(ang, resample=Image.Resampling.BICUBIC, expand=False, fillcolor=18)

    # Soft edge so tiles merge into a single group scene.
    mask = Image.new("L", (tile_w, tile_h), 255)
    md = ImageDraw.Draw(mask)
    md.rounded_rectangle((3, 3, tile_w - 4, tile_h - 4), radius=18, fill=255)
    mask = mask.filter(ImageFilter.GaussianBlur(radius=3))

    out = Image.new("L", (tile_w, tile_h), 0)
    out.paste(rotated, (0, 0), mask)
    return out


def main() -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    loaded: list[Image.Image] = []
    loaded_hashes: set[str] = set()
    for idx, title in enumerate(FILE_TITLES, start=1):
        digest = hashlib.sha1(title.encode("utf-8")).hexdigest()[:16]
        cache = CACHE_DIR / f"source_{idx:02d}_{digest}.img"
        try:
            if not cache.exists():
                ok = _download_image(title, cache)
                if not ok:
                    continue
            file_hash = hashlib.sha1(cache.read_bytes()).hexdigest()
            if file_hash in loaded_hashes:
                continue
            img = Image.open(cache)
            img.load()
            loaded.append(img)
            loaded_hashes.add(file_hash)
        except Exception:
            continue

    # Add manually downloaded sources (useful when Commons rate-limits API requests).
    for cache in sorted(CACHE_DIR.glob("manual_*.img")):
        try:
            file_hash = hashlib.sha1(cache.read_bytes()).hexdigest()
            if file_hash in loaded_hashes:
                continue
            img = Image.open(cache)
            img.load()
            loaded.append(img)
            loaded_hashes.add(file_hash)
        except Exception:
            continue

    if not loaded:
        raise RuntimeError("No source photos could be loaded from Wikimedia Commons")

    random.Random(42).shuffle(loaded)

    # Ensure we have 12 faces; repeat shuffled images when fewer are available.
    seed_pool = loaded.copy()
    random.Random(42).shuffle(seed_pool)
    k = 0
    while len(loaded) < 12:
        loaded.append(seed_pool[k % len(seed_pool)].copy())
        k += 1

    loaded = loaded[:12]

    canvas = Image.new("L", (W, H), color=36)
    draw = ImageDraw.Draw(canvas)

    # Background: moody cloudy gradient + deck.
    for y in range(H):
        if y < 860:
            v = int(112 - (y / 860) * 58)
        else:
            v = int(48 - ((y - 860) / 540) * 12)
        draw.line((0, y, W, y), fill=max(8, min(255, v)))

    draw.rectangle((0, 760, W, H), fill=44)
    for y in range(780, H, 84):
        draw.line((0, y, W, y), fill=58, width=2)

    cols = 6
    rows = 2
    outer_x = 95
    outer_y_top = 280
    gap_x = 14
    gap_y = 40
    tile_w = (W - outer_x * 2 - gap_x * (cols - 1)) // cols
    tile_h = 430

    for i, img in enumerate(loaded):
        row = i // cols
        col = i % cols
        x = outer_x + col * (tile_w + gap_x)
        y = outer_y_top + row * (tile_h + gap_y)
        tile = _make_tile(img, tile_w, tile_h, seed=1000 + i)
        # Slight overlap and depth to mimic one group photo instead of a grid.
        x += (col % 2) * 6 - 3
        y += row * 8 + (i % 3) * 2
        canvas.paste(tile, (x, y), tile)

    # Global cinematic contrast.
    canvas = ImageEnhance.Contrast(canvas).enhance(1.22)

    # Film grain.
    noise = Image.effect_noise((W, H), 20).convert("L")
    noise = ImageEnhance.Contrast(noise).enhance(1.4)
    canvas = ImageChops.add(canvas, noise.point(lambda p: (p - 128) * 0.22 + 128))

    # Global vignette.
    mask = Image.new("L", (W, H), color=0)
    md = ImageDraw.Draw(mask)
    md.ellipse((-260, -140, W + 260, H + 220), fill=255)
    mask = mask.filter(ImageFilter.GaussianBlur(radius=150))
    dark = Image.new("L", (W, H), color=8)
    canvas = Image.composite(canvas, dark, mask)

    # Subtle lower fade.
    strip_h = 80
    fade = Image.new("L", (W, strip_h), color=20)
    canvas.paste(fade, (0, H - strip_h))

    canvas = ImageOps.autocontrast(canvas, cutoff=1)
    canvas = ImageEnhance.Contrast(canvas).enhance(1.12)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    canvas.convert("RGB").save(OUT, format="PNG", optimize=True)
    print(f"Generated: {OUT}")
    print("Sources used (Wikimedia Commons):")
    for t in FILE_TITLES:
        print(f" - {t}")


if __name__ == "__main__":
    main()
