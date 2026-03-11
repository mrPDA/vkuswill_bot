from __future__ import annotations

from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parent.parent
OUT_SVG = ROOT / "assets" / "team_hispaniola_group_photo_noir.svg"
OUT_PNG = ROOT / "assets" / "team_hispaniola_group_photo_noir.png"


def esc(s: str) -> str:
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


agents = [
    {"name": "Капитан Смоллетт", "role": "тимлид", "x": 230, "y": 465, "hat": "tricorne", "beard": "short", "scar": False, "patch": False},
    {"name": "Доктор Ливси", "role": "архитектор", "x": 560, "y": 465, "hat": "tricorne", "beard": "none", "scar": False, "patch": False},
    {"name": "Гектор Барбосса", "role": "ревью", "x": 890, "y": 465, "hat": "tricorne", "beard": "long", "scar": True, "patch": False},
    {"name": "Уилл Тёрнер", "role": "рефакторинг", "x": 1220, "y": 465, "hat": "bandana", "beard": "goatee", "scar": False, "patch": False},
    {"name": "Дэйви Джонс", "role": "security", "x": 1550, "y": 465, "hat": "captain", "beard": "tentacles", "scar": False, "patch": False},
    {"name": "Тиа Дальма", "role": "промпты", "x": 1880, "y": 465, "hat": "headwrap", "beard": "none", "scar": False, "patch": False},
    {"name": "Джек Воробей", "role": "диалоги", "x": 230, "y": 955, "hat": "bandana", "beard": "braids", "scar": True, "patch": False},
    {"name": "Израэль Хэндс", "role": "secops", "x": 560, "y": 955, "hat": "captain", "beard": "stubble", "scar": False, "patch": False},
    {"name": "Джон Сильвер", "role": "devops/git", "x": 890, "y": 955, "hat": "tricorne", "beard": "long", "scar": False, "patch": False},
    {"name": "Билли Бонс", "role": "тесты", "x": 1220, "y": 955, "hat": "captain", "beard": "thick", "scar": False, "patch": True},
    {"name": "Джошами Гиббс", "role": "хабр", "x": 1550, "y": 955, "hat": "tricorne", "beard": "short", "scar": False, "patch": False},
    {"name": "Бен Ганн", "role": "документация", "x": 1880, "y": 955, "hat": "none", "beard": "wild", "scar": False, "patch": False},
]


def hat_svg(kind: str) -> str:
    if kind == "tricorne":
        return (
            "<path d='M-120 -112 Q0 -200 120 -112 L100 -58 Q0 -98 -100 -58 Z' fill='url(#hatGrad)'/>"
            "<path d='M-82 -104 Q0 -152 82 -104 Q0 -122 -82 -104 Z' fill='rgba(255,255,255,0.08)'/>"
        )
    if kind == "bandana":
        return (
            "<rect x='-112' y='-106' width='224' height='56' rx='18' fill='#2a2a2f'/>"
            "<path d='M102 -58 L154 -22 L98 -16 Z' fill='#24242a'/>"
            "<path d='M-96 -58 L-40 -20 L-98 -12 Z' fill='#24242a'/>"
        )
    if kind == "captain":
        return (
            "<ellipse cx='0' cy='-96' rx='126' ry='40' fill='#16181d'/>"
            "<rect x='-100' y='-140' width='200' height='58' rx='22' fill='#22252c'/>"
            "<path d='M-98 -96 Q0 -126 98 -96' fill='none' stroke='rgba(255,255,255,0.08)' stroke-width='5'/>"
        )
    if kind == "headwrap":
        return (
            "<ellipse cx='0' cy='-98' rx='120' ry='44' fill='#2c2b32'/>"
            "<path d='M-100 -98 Q0 -130 100 -98' fill='none' stroke='rgba(255,255,255,0.12)' stroke-width='4'/>"
        )
    return "<path d='M-94 -122 Q0 -170 94 -122 L84 -72 Q0 -94 -84 -72 Z' fill='#22272e'/>"


def beard_svg(kind: str) -> str:
    if kind == "none":
        return ""
    if kind == "goatee":
        return "<path d='M-18 44 Q0 96 18 44 Z' fill='#1e1f23'/>"
    if kind == "stubble":
        return "<ellipse cx='0' cy='34' rx='56' ry='20' fill='rgba(18,18,18,0.45)'/>"
    if kind == "short":
        return "<ellipse cx='0' cy='38' rx='58' ry='36' fill='#1f2024'/>"
    if kind == "thick":
        return "<ellipse cx='0' cy='48' rx='74' ry='56' fill='#17181c'/>"
    if kind == "long":
        return "<path d='M-72 10 Q0 176 72 10 Q0 92 -72 10 Z' fill='#16171a'/>"
    if kind == "wild":
        return "<path d='M-92 12 Q-14 166 0 70 Q14 166 92 12 Q0 132 -92 12 Z' fill='#18191d'/>"
    if kind == "braids":
        return (
            "<path d='M-48 28 Q-24 134 -10 58 Z' fill='#1a1b20'/>"
            "<path d='M48 28 Q24 134 10 58 Z' fill='#1a1b20'/>"
            "<circle cx='-24' cy='92' r='8' fill='#8b8b8b'/><circle cx='24' cy='92' r='8' fill='#8b8b8b'/>"
        )
    if kind == "tentacles":
        return (
            "<path d='M-72 8 Q-96 116 -64 154 Q-30 112 -40 40 Z' fill='#666f73'/>"
            "<path d='M-28 8 Q-50 120 -10 160 Q22 120 8 40 Z' fill='#6f797d'/>"
            "<path d='M20 8 Q8 122 44 156 Q72 114 56 36 Z' fill='#677176'/>"
            "<path d='M62 8 Q60 114 102 146 Q130 96 92 24 Z' fill='#626d71'/>"
        )
    return ""


def accessories(role: str) -> str:
    if role == "security":
        return "<path d='M96 122 q22 -26 44 0 q-20 30 -44 0 z' fill='#5a6469'/>"
    if role == "архитектор":
        return "<rect x='80' y='108' width='62' height='20' rx='5' fill='#9a9a96'/>"
    if role == "ревью":
        return "<rect x='78' y='100' width='66' height='34' rx='4' fill='#2a2a2d' stroke='#9d9d9d' stroke-width='3'/>"
    if role == "рефакторинг":
        return "<rect x='84' y='100' width='10' height='48' fill='#4f4f52'/><rect x='72' y='90' width='34' height='14' rx='3' fill='#8c8d90'/>"
    if role == "промпты":
        return "<circle cx='112' cy='118' r='20' fill='url(#orbGrad)' stroke='#b8b8b8' stroke-width='3'/>"
    if role == "диалоги":
        return "<circle cx='112' cy='120' r='18' fill='#9a9a8f' stroke='#666' stroke-width='3'/>"
    if role == "secops":
        return "<circle cx='110' cy='118' r='22' fill='none' stroke='#747474' stroke-width='4'/><line x1='110' y1='96' x2='110' y2='140' stroke='#747474' stroke-width='3'/><line x1='88' y1='118' x2='132' y2='118' stroke='#747474' stroke-width='3'/>"
    if role == "devops/git":
        return "<ellipse cx='112' cy='112' rx='17' ry='13' fill='#5d6461'/><circle cx='126' cy='104' r='7' fill='#d6d6d1'/>"
    if role == "тесты":
        return "<rect x='84' y='106' width='52' height='28' rx='4' fill='#424242' stroke='#999' stroke-width='3'/>"
    if role == "хабр":
        return "<path d='M84 136 Q120 78 134 96 Q120 124 92 142 Z' fill='#d9d9d4' stroke='#8e8e8a' stroke-width='2'/>"
    if role == "документация":
        return "<rect x='80' y='100' width='62' height='36' rx='5' fill='#b6b6ad' stroke='#75756c' stroke-width='3'/><path d='M86 120 Q102 110 118 120 Q128 126 136 114' fill='none' stroke='#707068' stroke-width='2'/>"
    return "<circle cx='112' cy='118' r='20' fill='#999'/>"


def draw_agent(a: dict[str, object]) -> str:
    x = int(a["x"])
    y = int(a["y"])
    name = esc(str(a["name"]))
    role = esc(str(a["role"]))
    hat = str(a["hat"])
    beard = str(a["beard"])
    scar = bool(a["scar"])
    patch = bool(a["patch"])

    scar_svg = ""
    if scar:
        scar_svg = "<line x1='-18' y1='-20' x2='6' y2='12' stroke='#67696f' stroke-width='3' opacity='0.85'/>"

    patch_svg = ""
    if patch:
        patch_svg = (
            "<ellipse cx='-30' cy='-10' rx='16' ry='12' fill='#111217'/>"
            "<line x1='-54' y1='-16' x2='-8' y2='-2' stroke='#111217' stroke-width='4'/>"
        )

    return f"""
<g transform='translate({x},{y})'>
  <ellipse cx='0' cy='208' rx='152' ry='34' fill='rgba(0,0,0,0.32)'/>
  <path d='M-124 172 Q0 56 124 172 L124 214 L-124 214 Z' fill='url(#coatGrad)'/>
  <path d='M-90 164 Q0 96 90 164 L90 214 L-90 214 Z' fill='rgba(255,255,255,0.10)'/>
  <rect x='-22' y='96' width='44' height='30' rx='11' fill='#8e8c87'/>

  <circle cx='0' cy='0' r='100' fill='url(#skinGrad)'/>
  <ellipse cx='0' cy='-10' rx='98' ry='46' fill='rgba(0,0,0,0.12)'/>
  <path d='M-100 2 Q-118 -12 -100 -44 Q-90 -6 -100 2 Z' fill='#8f8c84' opacity='0.95'/>
  <path d='M100 2 Q118 -12 100 -44 Q90 -6 100 2 Z' fill='#8f8c84' opacity='0.95'/>

  <path d='M-92 -30 Q0 -122 92 -30 Q64 -114 0 -118 Q-66 -114 -92 -30 Z' fill='#1b1c21' opacity='0.96'/>
  {hat_svg(hat)}

  <circle cx='-30' cy='-8' r='7' fill='#111218'/>
  <circle cx='30' cy='-8' r='7' fill='#111218'/>
  {patch_svg}
  <path d='M-16 22 Q0 34 16 22' fill='none' stroke='#53555b' stroke-width='4' stroke-linecap='round'/>
  {scar_svg}
  {beard_svg(beard)}

  {accessories(str(a['role']))}

  <rect x='-142' y='230' width='284' height='68' rx='12' fill='rgba(8,10,14,0.66)'/>
  <text x='0' y='257' text-anchor='middle' class='name'>{name}</text>
  <text x='0' y='282' text-anchor='middle' class='role'>{role}</text>
</g>
"""


svg = [
    """<svg xmlns='http://www.w3.org/2000/svg' width='2400' height='1400' viewBox='0 0 2400 1400'>
<defs>
  <linearGradient id='fogSky' x1='0' y1='0' x2='0' y2='1'>
    <stop offset='0%' stop-color='#8d9199'/>
    <stop offset='55%' stop-color='#6a6e76'/>
    <stop offset='100%' stop-color='#484c54'/>
  </linearGradient>
  <linearGradient id='seaGrad' x1='0' y1='0' x2='0' y2='1'>
    <stop offset='0%' stop-color='#424851'/>
    <stop offset='100%' stop-color='#2b3037'/>
  </linearGradient>
  <linearGradient id='coatGrad' x1='0' y1='0' x2='0' y2='1'>
    <stop offset='0%' stop-color='#353a43'/>
    <stop offset='100%' stop-color='#20242b'/>
  </linearGradient>
  <linearGradient id='hatGrad' x1='0' y1='0' x2='0' y2='1'>
    <stop offset='0%' stop-color='#2c313b'/>
    <stop offset='100%' stop-color='#161a21'/>
  </linearGradient>
  <radialGradient id='skinGrad' cx='50%' cy='38%' r='62%'>
    <stop offset='0%' stop-color='#a8a39a'/>
    <stop offset='100%' stop-color='#7d7971'/>
  </radialGradient>
  <radialGradient id='orbGrad' cx='50%' cy='40%' r='60%'>
    <stop offset='0%' stop-color='#f2f2ef'/>
    <stop offset='100%' stop-color='#8f9498'/>
  </radialGradient>

  <filter id='grain' x='-10%' y='-10%' width='120%' height='120%'>
    <feTurbulence type='fractalNoise' baseFrequency='0.95' numOctaves='2' stitchTiles='stitch' result='noise'/>
    <feColorMatrix type='saturate' values='0' in='noise' result='gray'/>
    <feComponentTransfer in='gray' result='grainAlpha'>
      <feFuncA type='table' tableValues='0 0.035'/>
    </feComponentTransfer>
  </filter>

  <radialGradient id='vignette' cx='50%' cy='45%' r='68%'>
    <stop offset='60%' stop-color='rgba(0,0,0,0)'/>
    <stop offset='100%' stop-color='rgba(0,0,0,0.42)'/>
  </radialGradient>

  <style>
    .title { font: 700 66px 'DejaVu Serif', Georgia, serif; fill: #e7e8ea; letter-spacing: 1px; }
    .subtitle { font: 400 25px 'DejaVu Serif', Georgia, serif; fill: #c9ccd1; }
    .name { font: 700 22px 'DejaVu Serif', Georgia, serif; fill: #e4e6ea; }
    .role { font: 400 18px 'DejaVu Serif', Georgia, serif; fill: #b4b9c1; }
  </style>
</defs>

<rect x='0' y='0' width='2400' height='860' fill='url(#fogSky)'/>
<rect x='0' y='860' width='2400' height='540' fill='url(#seaGrad)'/>

<path d='M0 788 Q260 744 520 788 T1040 788 T1560 788 T2080 788 T2400 782 L2400 860 L0 860 Z' fill='rgba(255,255,255,0.08)'/>
<path d='M0 826 Q310 774 620 826 T1240 826 T1860 826 T2400 818 L2400 860 L0 860 Z' fill='rgba(255,255,255,0.05)'/>

<rect x='0' y='660' width='2400' height='740' fill='#3a2a1f'/>
<g opacity='0.26'>
  <line x1='0' y1='820' x2='2400' y2='820' stroke='#675247' stroke-width='4'/>
  <line x1='0' y1='980' x2='2400' y2='980' stroke='#675247' stroke-width='4'/>
  <line x1='0' y1='1140' x2='2400' y2='1140' stroke='#675247' stroke-width='4'/>
  <line x1='0' y1='1300' x2='2400' y2='1300' stroke='#675247' stroke-width='4'/>
</g>

<rect x='1170' y='238' width='60' height='1088' fill='#2d2018'/>
<path d='M1200 252 L1570 380 L1200 494 Z' fill='rgba(220,220,220,0.22)'/>
<path d='M1200 332 L860 438 L1200 518 Z' fill='rgba(200,200,200,0.20)'/>

<rect x='0' y='0' width='2400' height='1400' fill='url(#vignette)'/>

<text x='1200' y='94' text-anchor='middle' class='title'>Команда «Испаньолы»</text>
<text x='1200' y='136' text-anchor='middle' class='subtitle'>Брутальный портрет экипажа в стиле архивной черно-белой фотографии</text>
"""
]

for agent in agents:
    svg.append(draw_agent(agent))

svg.append("<rect x='0' y='0' width='2400' height='1400' fill='#ffffff' filter='url(#grain)' opacity='0.16'/>")
svg.append("</svg>\n")

OUT_SVG.parent.mkdir(parents=True, exist_ok=True)
OUT_SVG.write_text("".join(svg), encoding="utf-8")

res = subprocess.run(
    [
        "sips",
        "-s",
        "format",
        "png",
        str(OUT_SVG),
        "--out",
        str(OUT_PNG),
    ],
    capture_output=True,
    text=True,
)
if res.returncode != 0:
    print(res.stdout)
    print(res.stderr)
    raise SystemExit(res.returncode)

print(f"Generated: {OUT_SVG}")
print(f"Generated: {OUT_PNG}")
