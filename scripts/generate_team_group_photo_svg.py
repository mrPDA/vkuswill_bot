from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "assets" / "team_hispaniola_group_photo.svg"


def esc(s: str) -> str:
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


agents = [
    {
        "name": "Капитан Смоллетт",
        "role": "тимлид",
        "x": 220,
        "y": 420,
        "coat": "#1f3d7a",
        "trim": "#d7b66f",
        "skin": "#e6b38f",
        "hair": "#2a2a2a",
        "hat": "tricorne",
        "beard": "none",
        "acc": "compass",
        "glasses": False,
    },
    {
        "name": "Доктор Ливси",
        "role": "архитектор",
        "x": 560,
        "y": 420,
        "coat": "#2c6c4f",
        "trim": "#bde4cf",
        "skin": "#e8ba98",
        "hair": "#3b3128",
        "hat": "tricorne",
        "beard": "none",
        "acc": "scroll",
        "glasses": True,
    },
    {
        "name": "Гектор Барбосса",
        "role": "ревью",
        "x": 900,
        "y": 420,
        "coat": "#5c2f1d",
        "trim": "#d9a25f",
        "skin": "#dca47f",
        "hair": "#1f1613",
        "hat": "tricorne",
        "beard": "long",
        "acc": "book",
        "glasses": False,
    },
    {
        "name": "Уилл Тёрнер",
        "role": "рефакторинг",
        "x": 1240,
        "y": 420,
        "coat": "#4a3321",
        "trim": "#b48d62",
        "skin": "#e0ae89",
        "hair": "#2f2119",
        "hat": "bandana",
        "beard": "goatee",
        "acc": "hammer",
        "glasses": False,
    },
    {
        "name": "Дэйви Джонс",
        "role": "security",
        "x": 1580,
        "y": 420,
        "coat": "#234d57",
        "trim": "#76bbc6",
        "skin": "#9eb6a0",
        "hair": "#2b4e52",
        "hat": "captain",
        "beard": "tentacles",
        "acc": "kraken",
        "glasses": False,
    },
    {
        "name": "Тиа Дальма",
        "role": "промпты",
        "x": 1920,
        "y": 420,
        "coat": "#5b2d66",
        "trim": "#d3a7de",
        "skin": "#8d5f44",
        "hair": "#1f1627",
        "hat": "headwrap",
        "beard": "none",
        "acc": "orb",
        "glasses": False,
    },
    {
        "name": "Джек Воробей",
        "role": "диалоги",
        "x": 220,
        "y": 930,
        "coat": "#70462f",
        "trim": "#d6b391",
        "skin": "#d8a17d",
        "hair": "#241812",
        "hat": "bandana",
        "beard": "braids",
        "acc": "coin",
        "glasses": False,
    },
    {
        "name": "Израэль Хэндс",
        "role": "secops",
        "x": 560,
        "y": 930,
        "coat": "#2b5e50",
        "trim": "#a7ddcc",
        "skin": "#c99673",
        "hair": "#2c2c2c",
        "hat": "captain",
        "beard": "stubble",
        "acc": "wheel",
        "glasses": False,
    },
    {
        "name": "Джон Сильвер",
        "role": "devops/git",
        "x": 900,
        "y": 930,
        "coat": "#69511f",
        "trim": "#ebc987",
        "skin": "#d3a176",
        "hair": "#352a18",
        "hat": "tricorne",
        "beard": "long",
        "acc": "parrot",
        "glasses": False,
    },
    {
        "name": "Билли Бонс",
        "role": "тесты",
        "x": 1240,
        "y": 930,
        "coat": "#304c72",
        "trim": "#a8c9ef",
        "skin": "#b98361",
        "hair": "#2b2422",
        "hat": "captain",
        "beard": "thick",
        "acc": "chest",
        "glasses": False,
    },
    {
        "name": "Джошами Гиббс",
        "role": "хабр",
        "x": 1580,
        "y": 930,
        "coat": "#3d5878",
        "trim": "#bfd3eb",
        "skin": "#c99574",
        "hair": "#3b322b",
        "hat": "tricorne",
        "beard": "short",
        "acc": "quill",
        "glasses": False,
    },
    {
        "name": "Бен Ганн",
        "role": "документация",
        "x": 1920,
        "y": 930,
        "coat": "#455a33",
        "trim": "#c3dfad",
        "skin": "#c8916f",
        "hair": "#3c3a2a",
        "hat": "none",
        "beard": "wild",
        "acc": "map",
        "glasses": False,
    },
]


def hat_svg(kind: str, hair: str) -> str:
    if kind == "tricorne":
        return (
            "<path d='M-118 -102 Q0 -180 118 -102 L100 -58 Q0 -96 -100 -58 Z' fill='#1d1f2b'/>"
            "<path d='M-80 -100 Q0 -146 80 -100 Q0 -120 -80 -100 Z' fill='#30364a'/>"
        )
    if kind == "bandana":
        return (
            "<rect x='-110' y='-102' width='220' height='54' rx='16' fill='#7a2f2f'/>"
            "<path d='M95 -60 L146 -24 L92 -18 Z' fill='#7a2f2f'/>"
            "<path d='M-84 -60 L-42 -22 L-90 -14 Z' fill='#7a2f2f'/>"
        )
    if kind == "captain":
        return (
            "<ellipse cx='0' cy='-98' rx='122' ry='38' fill='#1b2a36'/>"
            "<rect x='-96' y='-140' width='192' height='58' rx='24' fill='#243b49'/>"
        )
    if kind == "headwrap":
        return (
            "<ellipse cx='0' cy='-98' rx='116' ry='44' fill='#5d2f6e'/>"
            "<circle cx='96' cy='-78' r='11' fill='#d6b263'/>"
            "<circle cx='-98' cy='-80' r='10' fill='#86c1d1'/>"
        )
    return f"<path d='M-92 -120 Q0 -168 92 -120 L86 -74 Q0 -96 -86 -74 Z' fill='{hair}'/>"


def beard_svg(kind: str, hair: str) -> str:
    if kind == "none":
        return ""
    if kind == "goatee":
        return f"<path d='M-18 40 Q0 86 18 40 Z' fill='{hair}'/>"
    if kind == "stubble":
        return "<ellipse cx='0' cy='28' rx='48' ry='18' fill='rgba(30,30,30,0.35)'/>"
    if kind == "short":
        return f"<ellipse cx='0' cy='36' rx='58' ry='34' fill='{hair}'/>"
    if kind == "thick":
        return f"<ellipse cx='0' cy='46' rx='72' ry='52' fill='{hair}'/>"
    if kind == "long":
        return f"<path d='M-68 10 Q0 168 68 10 Q0 84 -68 10 Z' fill='{hair}'/>"
    if kind == "wild":
        return (
            f"<path d='M-90 12 Q-12 154 0 66 Q18 150 90 14 Q0 124 -90 12 Z' fill='{hair}'/>"
        )
    if kind == "braids":
        return (
            f"<path d='M-46 26 Q-22 130 -10 58 Z' fill='{hair}'/>"
            f"<path d='M46 26 Q22 130 10 58 Z' fill='{hair}'/>"
            "<circle cx='-24' cy='86' r='7' fill='#d8b165'/><circle cx='24' cy='86' r='7' fill='#d8b165'/>"
        )
    if kind == "tentacles":
        return (
            "<path d='M-70 8 Q-92 110 -62 146 Q-30 108 -38 38 Z' fill='#6fa2a7'/>"
            "<path d='M-28 8 Q-48 118 -8 156 Q20 116 8 40 Z' fill='#6fa2a7'/>"
            "<path d='M20 8 Q6 120 42 150 Q70 110 56 34 Z' fill='#6fa2a7'/>"
            "<path d='M62 8 Q60 110 100 140 Q128 90 92 24 Z' fill='#6fa2a7'/>"
        )
    return ""


def accessory_svg(kind: str) -> str:
    if kind == "compass":
        return (
            "<circle cx='104' cy='120' r='24' fill='#d9c17a' stroke='#8a6b2d' stroke-width='4'/>"
            "<path d='M104 98 L112 120 L104 142 L96 120 Z' fill='#314f74'/>"
        )
    if kind == "scroll":
        return "<rect x='74' y='104' width='58' height='22' rx='6' fill='#efe1b8' stroke='#9b8a61' stroke-width='3'/>"
    if kind == "book":
        return "<rect x='72' y='98' width='64' height='34' rx='5' fill='#3f2c21' stroke='#d9ae77' stroke-width='3'/>"
    if kind == "hammer":
        return "<rect x='82' y='96' width='10' height='48' fill='#5e4630'/><rect x='70' y='86' width='34' height='14' rx='3' fill='#9ba5af'/>"
    if kind == "kraken":
        return "<path d='M88 112 q20 -24 42 0 q-20 28 -42 0 z' fill='#5fa3a9'/>"
    if kind == "orb":
        return "<circle cx='108' cy='118' r='21' fill='url(#orbGrad)' stroke='#d3b56f' stroke-width='3'/>"
    if kind == "coin":
        return "<circle cx='108' cy='120' r='19' fill='#d8b665' stroke='#8f6d2c' stroke-width='3'/>"
    if kind == "wheel":
        return (
            "<circle cx='108' cy='118' r='22' fill='none' stroke='#805f35' stroke-width='4'/>"
            "<line x1='108' y1='96' x2='108' y2='140' stroke='#805f35' stroke-width='3'/>"
            "<line x1='86' y1='118' x2='130' y2='118' stroke='#805f35' stroke-width='3'/>"
        )
    if kind == "parrot":
        return (
            "<ellipse cx='110' cy='108' rx='18' ry='14' fill='#2ea35d'/><circle cx='123' cy='100' r='7' fill='#e8ddc3'/>"
            "<path d='M128 100 l9 3 l-8 4 z' fill='#d58a3a'/>"
        )
    if kind == "chest":
        return "<rect x='84' y='104' width='48' height='28' rx='4' fill='#7a4b24' stroke='#d1a15e' stroke-width='3'/>"
    if kind == "quill":
        return "<path d='M82 134 Q118 80 132 96 Q120 122 92 140 Z' fill='#f2f2e6' stroke='#8f8f7c' stroke-width='2'/>"
    if kind == "map":
        return "<rect x='78' y='98' width='62' height='36' rx='5' fill='#ebddb6' stroke='#a48e57' stroke-width='3'/><path d='M84 118 Q100 108 116 118 Q126 124 134 112' fill='none' stroke='#8a7a49' stroke-width='2'/>"
    return ""


def draw_agent(a: dict[str, object]) -> str:
    x = int(a["x"])
    y = int(a["y"])
    skin = str(a["skin"])
    coat = str(a["coat"])
    trim = str(a["trim"])
    hair = str(a["hair"])
    hat = str(a["hat"])
    beard = str(a["beard"])
    acc = str(a["acc"])
    name = esc(str(a["name"]))
    role = esc(str(a["role"]))
    glasses = bool(a["glasses"])

    glasses_svg = ""
    if glasses:
        glasses_svg = (
            "<rect x='-44' y='-22' width='34' height='24' rx='6' fill='none' stroke='#1e1e1e' stroke-width='3'/>"
            "<rect x='10' y='-22' width='34' height='24' rx='6' fill='none' stroke='#1e1e1e' stroke-width='3'/>"
            "<line x1='-10' y1='-10' x2='10' y2='-10' stroke='#1e1e1e' stroke-width='3'/>"
        )

    return f"""
<g transform='translate({x},{y})'>
  <ellipse cx='0' cy='202' rx='146' ry='30' fill='rgba(0,0,0,0.25)'/>
  <path d='M-120 170 Q0 64 120 170 L120 210 L-120 210 Z' fill='{coat}'/>
  <path d='M-88 162 Q0 94 88 162 L88 210 L-88 210 Z' fill='{trim}' opacity='0.34'/>
  <rect x='-22' y='96' width='44' height='30' rx='11' fill='#d8a87e'/>
  <circle cx='0' cy='0' r='98' fill='{skin}'/>
  <path d='M-98 4 Q-114 -12 -98 -42 Q-88 -6 -98 4 Z' fill='{skin}' opacity='0.95'/>
  <path d='M98 4 Q114 -12 98 -42 Q88 -6 98 4 Z' fill='{skin}' opacity='0.95'/>
  <path d='M-92 -28 Q0 -118 92 -28 Q62 -110 0 -114 Q-64 -110 -92 -28 Z' fill='{hair}' opacity='0.96'/>
  {hat_svg(hat, hair)}
  <circle cx='-30' cy='-8' r='7' fill='#1e1e1e'/>
  <circle cx='30' cy='-8' r='7' fill='#1e1e1e'/>
  {glasses_svg}
  <path d='M-16 24 Q0 34 16 24' fill='none' stroke='#73492e' stroke-width='4' stroke-linecap='round'/>
  {beard_svg(beard, hair)}
  {accessory_svg(acc)}
  <rect x='-136' y='226' width='272' height='66' rx='12' fill='rgba(10,17,28,0.60)'/>
  <text x='0' y='253' text-anchor='middle' class='name'>{name}</text>
  <text x='0' y='278' text-anchor='middle' class='role'>{role}</text>
</g>
"""


parts: list[str] = []
parts.append(
    """<svg xmlns='http://www.w3.org/2000/svg' width='2400' height='1400' viewBox='0 0 2400 1400'>
<defs>
  <linearGradient id='skyGrad' x1='0' y1='0' x2='0' y2='1'>
    <stop offset='0%' stop-color='#f7b06a'/>
    <stop offset='55%' stop-color='#ee7f5f'/>
    <stop offset='100%' stop-color='#3b4f7a'/>
  </linearGradient>
  <linearGradient id='seaGrad' x1='0' y1='0' x2='0' y2='1'>
    <stop offset='0%' stop-color='#335173'/>
    <stop offset='100%' stop-color='#1b2d46'/>
  </linearGradient>
  <radialGradient id='orbGrad' cx='50%' cy='40%' r='60%'>
    <stop offset='0%' stop-color='#fff9d4'/>
    <stop offset='100%' stop-color='#86b6d8'/>
  </radialGradient>
  <style>
    .title {{ font: 700 62px 'DejaVu Sans', Arial, sans-serif; fill: #fff4df; }}
    .subtitle {{ font: 400 26px 'DejaVu Sans', Arial, sans-serif; fill: #ffe2c5; }}
    .name {{ font: 700 22px 'DejaVu Sans', Arial, sans-serif; fill: #f2f6ff; }}
    .role {{ font: 400 18px 'DejaVu Sans', Arial, sans-serif; fill: #bdd5ff; }}
  </style>
</defs>

<rect x='0' y='0' width='2400' height='860' fill='url(#skyGrad)'/>
<circle cx='2160' cy='180' r='108' fill='rgba(255,242,198,0.44)'/>
<rect x='0' y='860' width='2400' height='540' fill='url(#seaGrad)'/>

<path d='M0 760 Q240 710 480 760 T960 760 T1440 760 T1920 760 T2400 760 L2400 860 L0 860 Z' fill='rgba(255,255,255,0.10)'/>
<path d='M0 816 Q280 770 560 816 T1120 816 T1680 816 T2240 816 T2400 812 L2400 860 L0 860 Z' fill='rgba(255,255,255,0.08)'/>

<rect x='0' y='660' width='2400' height='740' fill='#5e3b25'/>
<g opacity='0.25'>
  <line x1='0' y1='820' x2='2400' y2='820' stroke='#8b5d3c' stroke-width='4'/>
  <line x1='0' y1='980' x2='2400' y2='980' stroke='#8b5d3c' stroke-width='4'/>
  <line x1='0' y1='1140' x2='2400' y2='1140' stroke='#8b5d3c' stroke-width='4'/>
  <line x1='0' y1='1300' x2='2400' y2='1300' stroke='#8b5d3c' stroke-width='4'/>
</g>

<rect x='1172' y='240' width='56' height='1080' fill='#4a2d1a'/>
<path d='M1200 254 L1570 378 L1200 490 Z' fill='rgba(244,235,206,0.50)'/>
<path d='M1200 330 L860 438 L1200 514 Z' fill='rgba(231,222,194,0.46)'/>

<text x='1200' y='96' text-anchor='middle' class='title'>Команда «Испаньолы»</text>
<text x='1200' y='138' text-anchor='middle' class='subtitle'>Групповое фото пиратского экипажа проекта</text>
"""
)

for a in agents:
    parts.append(draw_agent(a))

parts.append("</svg>\n")

OUT.parent.mkdir(parents=True, exist_ok=True)
OUT.write_text("".join(parts), encoding="utf-8")
print(f"Generated: {OUT}")
