#!/usr/bin/env python3
"""Generate separate portrait SVGs and profile Markdown files for Hispaniola agents."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PORTRAITS_DIR = ROOT / "assets" / "agents_portraits"
PROFILES_DIR = ROOT / "docs" / "agents_profiles"

AGENTS = [
    {
        "slug": "captain_smollett",
        "name": "Капитан Смоллетт",
        "title": "тимлид",
        "role": "Координация команды, декомпозиция задач, делегирование и контроль качества.",
        "description": "Обеспечивает, чтобы правильные задачи выполнялись нужными людьми в нужном порядке и с понятным результатом.",
        "invoke": "тимлид, капитан, Смоллетт",
        "accent": "#4FB3FF",
        "accent2": "#1C3F78",
        "hair": "#1a1a1a",
        "hat": "#12263F",
        "badge": "TL",
        "beard": False,
        "glasses": False,
    },
    {
        "slug": "doctor_livesey",
        "name": "Доктор Ливси",
        "title": "архитектор-аналитик",
        "role": "Архитектура, ADR, оценка trade-offs и декомпозиция модулей.",
        "description": "Разбирает сложные зависимости, предлагает варианты решений и объясняет технические последствия каждого.",
        "invoke": "архитектура, ADR, Доктор Ливси",
        "accent": "#55D0A8",
        "accent2": "#1F5D4B",
        "hair": "#2b2b2b",
        "hat": "#145E4B",
        "badge": "AR",
        "beard": False,
        "glasses": True,
    },
    {
        "slug": "hector_barbossa",
        "name": "Гектор Барбосса",
        "title": "ревьювер кода",
        "role": "Code review, стандарты качества, проверка ошибок, безопасности и тестов.",
        "description": "Проверяет архитектуру и реализацию, фиксирует риски и даёт вердикт по качеству изменений.",
        "invoke": "ревью, code review, Барбосса",
        "accent": "#F5A742",
        "accent2": "#5D3417",
        "hair": "#2b1a12",
        "hat": "#3F2416",
        "badge": "CR",
        "beard": True,
        "glasses": False,
    },
    {
        "slug": "will_turner",
        "name": "Уилл Тёрнер",
        "title": "рефакторщик",
        "role": "Рефакторинг, устранение code smells, улучшение структуры без изменения поведения.",
        "description": "Превращает сложный код в более чистую и поддерживаемую структуру с сохранением функциональности.",
        "invoke": "рефакторинг, упрости, Уилл Тёрнер",
        "accent": "#7FA7FF",
        "accent2": "#2B3F78",
        "hair": "#3b2a1d",
        "hat": "#1E2E58",
        "badge": "RF",
        "beard": False,
        "glasses": False,
    },
    {
        "slug": "davy_jones",
        "name": "Дэйви Джонс",
        "title": "страж безопасности",
        "role": "AppSec: SAST, DAST, OSS-аудит, OWASP Top 10, защита ИИ от атак.",
        "description": "Находит уязвимости в коде, зависимостях и промптах и формирует отчёт с приоритизацией рисков.",
        "invoke": "безопасность, SAST, аудит, Дэйви Джонс",
        "accent": "#41C8C6",
        "accent2": "#1A5B6F",
        "hair": "#12343d",
        "hat": "#134F66",
        "badge": "SEC",
        "beard": True,
        "glasses": False,
    },
    {
        "slug": "tia_dalma",
        "name": "Тиа Дальма",
        "title": "промпт-инженер",
        "role": "Оптимизация промптов, A/B-тестирование, защита от prompt injection.",
        "description": "Настраивает формулировки для точности, стоимости и устойчивости поведения языковой модели.",
        "invoke": "промпт, prompt, улучшить ответы, Тиа Дальма",
        "accent": "#D46BFF",
        "accent2": "#5D2D7F",
        "hair": "#2f1d42",
        "hat": "#4A2A6B",
        "badge": "PR",
        "beard": False,
        "glasses": False,
    },
    {
        "slug": "captain_jack_sparrow",
        "name": "Капитан Джек Воробей",
        "title": "аналитик диалогов",
        "role": "Сценарии диалогов, аудит UX, user flow и edge cases.",
        "description": "Проектирует разговорные сценарии так, чтобы бот устойчиво проходил и стандартные, и сложные ветки.",
        "invoke": "сценарий, диалог, user flow, Джек Воробей",
        "accent": "#FF8C63",
        "accent2": "#6E2D1E",
        "hair": "#2d1f1a",
        "hat": "#4E2E24",
        "badge": "UX",
        "beard": True,
        "glasses": False,
    },
    {
        "slug": "israel_hands",
        "name": "Израэль Хэндс",
        "title": "SecOps-инженер",
        "role": "Деплой, Docker, Kubernetes, CI/CD, Yandex Cloud, подготовка к нагрузке.",
        "description": "Ведёт инфраструктуру и эксплуатацию: от контейнеров и пайплайнов до масштабирования и надёжности.",
        "invoke": "деплой, kubernetes, docker, Израэль Хэндс",
        "accent": "#4ABF8A",
        "accent2": "#1A5A45",
        "hair": "#202020",
        "hat": "#124636",
        "badge": "OPS",
        "beard": False,
        "glasses": True,
    },
    {
        "slug": "long_john_silver",
        "name": "Долговязый Джон Сильвер",
        "title": "DevOps-агент",
        "role": "Git-операции: ветки, коммиты, PR, релизы, CHANGELOG, версионирование.",
        "description": "Управляет логистикой кода: оформлением изменений, релизным циклом и дисциплиной репозитория.",
        "invoke": "коммит, ветка, PR, релиз, Джон Сильвер",
        "accent": "#D0A24B",
        "accent2": "#654619",
        "hair": "#2a2414",
        "hat": "#503615",
        "badge": "GIT",
        "beard": True,
        "glasses": False,
    },
    {
        "slug": "billy_bones",
        "name": "Билли Бонс",
        "title": "тестировщик",
        "role": "Тестирование: pytest, покрытие, SAST, безопасность ИИ, валидация.",
        "description": "Проверяет функциональность, стабильность и безопасность, чтобы изменения были воспроизводимыми и надёжными.",
        "invoke": "тесты, pytest, покрытие, Билли Бонс",
        "accent": "#6DA7FF",
        "accent2": "#2A3D78",
        "hair": "#1f1f1f",
        "hat": "#1E2E5E",
        "badge": "QA",
        "beard": True,
        "glasses": False,
    },
    {
        "slug": "joshamee_gibbs",
        "name": "Джошами Гиббс",
        "title": "писатель на Хабр",
        "role": "Технические статьи: туториалы, кейсы, архитектурные разборы.",
        "description": "Переводит технические решения в понятный нарратив с реальными примерами и инженерной ценностью.",
        "invoke": "статья, хабр, пост, Гиббс",
        "accent": "#7AC4FF",
        "accent2": "#2F5E84",
        "hair": "#28313a",
        "hat": "#25496B",
        "badge": "DOC",
        "beard": False,
        "glasses": False,
    },
    {
        "slug": "ben_gunn",
        "name": "Бен Ганн",
        "title": "документатор",
        "role": "Техническая документация: README, архитектура, руководства, конфигурация.",
        "description": "Строит структурированную документацию, по которой команда быстро понимает проект и его контуры.",
        "invoke": "документация, README, docs, Бен Ганн",
        "accent": "#8AD47A",
        "accent2": "#325C2B",
        "hair": "#2b331f",
        "hat": "#355A2A",
        "badge": "KB",
        "beard": True,
        "glasses": False,
    },
]


def _portrait_svg(agent: dict[str, object]) -> str:
    accent = agent["accent"]
    accent2 = agent["accent2"]
    hair = agent["hair"]
    hat = agent["hat"]
    badge = agent["badge"]
    name = agent["name"]
    title = agent["title"]
    beard = bool(agent["beard"])
    glasses = bool(agent["glasses"])

    glasses_svg = ""
    if glasses:
        glasses_svg = (
            "<rect x='430' y='380' width='70' height='45' rx='12' fill='none' stroke='#1a1a1a' stroke-width='6'/>"
            "<rect x='524' y='380' width='70' height='45' rx='12' fill='none' stroke='#1a1a1a' stroke-width='6'/>"
            "<line x1='500' y1='400' x2='524' y2='400' stroke='#1a1a1a' stroke-width='6'/>"
        )

    beard_svg = ""
    if beard:
        beard_svg = "<ellipse cx='512' cy='505' rx='120' ry='80' fill='%s' opacity='0.88'/>" % hair

    return f"""<svg xmlns='http://www.w3.org/2000/svg' width='1024' height='1024' viewBox='0 0 1024 1024'>
  <defs>
    <linearGradient id='bg' x1='0' y1='0' x2='1' y2='1'>
      <stop offset='0%' stop-color='{accent}'/>
      <stop offset='100%' stop-color='{accent2}'/>
    </linearGradient>
    <style>
      .name {{ font: 700 52px 'DejaVu Sans', Arial, sans-serif; fill: #f4f8ff; }}
      .title {{ font: 400 34px 'DejaVu Sans', Arial, sans-serif; fill: #dce8ff; }}
      .badge {{ font: 700 34px 'DejaVu Sans Mono', Consolas, monospace; fill: #13253d; }}
    </style>
  </defs>
  <rect x='0' y='0' width='1024' height='1024' fill='url(#bg)'/>
  <circle cx='512' cy='420' r='260' fill='none' stroke='rgba(255,255,255,0.35)' stroke-width='8'/>

  <ellipse cx='512' cy='680' rx='250' ry='190' fill='rgba(20,30,45,0.55)'/>
  <rect x='452' y='560' width='120' height='80' rx='24' fill='#d8a881'/>

  <circle cx='512' cy='420' r='170' fill='#e8b48c'/>
  <ellipse cx='512' cy='342' rx='190' ry='70' fill='{hat}'/>
  <path d='M360 340 Q512 220 664 340 L664 355 Q512 300 360 355 Z' fill='{hat}'/>
  <ellipse cx='512' cy='340' rx='150' ry='95' fill='{hair}' opacity='0.9'/>

  {beard_svg}

  <circle cx='454' cy='408' r='11' fill='#1f1f1f'/>
  <circle cx='570' cy='408' r='11' fill='#1f1f1f'/>
  <path d='M460 475 Q512 505 564 475' fill='none' stroke='#6f3e26' stroke-width='7' stroke-linecap='round'/>
  <path d='M500 450 Q512 442 524 450' fill='none' stroke='#a26b4f' stroke-width='5' stroke-linecap='round'/>
  {glasses_svg}

  <circle cx='840' cy='195' r='88' fill='#f7f1d4' stroke='rgba(0,0,0,0.15)' stroke-width='4'/>
  <text x='840' y='207' text-anchor='middle' class='badge'>{badge}</text>

  <rect x='80' y='830' width='864' height='150' rx='24' fill='rgba(10,18,30,0.45)'/>
  <text x='100' y='892' class='name'>{name}</text>
  <text x='100' y='940' class='title'>{title}</text>
</svg>
"""


def _profile_md(agent: dict[str, object]) -> str:
    return (
        f"# {agent['name']} — {agent['title']}\n\n"
        f"**Роль:** {agent['role']}\n\n"
        f"**Описание:** {agent['description']}\n\n"
        f"**Как вызвать:** `{agent['invoke']}`\n\n"
        "Источник: `docs/TEAM_HISPANIOLA.md`\n"
    )


def main() -> None:
    PORTRAITS_DIR.mkdir(parents=True, exist_ok=True)
    PROFILES_DIR.mkdir(parents=True, exist_ok=True)

    readme_lines = [
        "# Портреты и профили агентов",
        "",
        "Сгенерировано из `docs/TEAM_HISPANIOLA.md`.",
        "",
        "| Агент | Портрет | Профиль |",
        "|---|---|---|",
    ]

    for agent in AGENTS:
        slug = str(agent["slug"])
        portrait_path = PORTRAITS_DIR / f"{slug}.svg"
        profile_path = PROFILES_DIR / f"{slug}.md"

        portrait_path.write_text(_portrait_svg(agent), encoding="utf-8")
        profile_path.write_text(_profile_md(agent), encoding="utf-8")

        readme_lines.append(
            f"| {agent['name']} | `assets/agents_portraits/{slug}.svg` | `docs/agents_profiles/{slug}.md` |"
        )

    (PROFILES_DIR / "README.md").write_text("\n".join(readme_lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
