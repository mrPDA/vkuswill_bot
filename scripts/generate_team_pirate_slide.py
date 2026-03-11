from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
OUT_PATH = ROOT / "articles" / "team-hispaniola-pirate-slide.pptx"
BASE_SCRIPT = ROOT / "scripts" / "generate_public_presentation_pptx.py"


PIRATE_LINES = [
    "Капитан Смоллетт — держит штурвал задач и ведет экипаж точно по курсу.",
    "Доктор Ливси — чертит карты архитектуры и выбирает самый верный фарватер решений.",
    "Гектор Барбосса — хранитель кодекса, не пропускает слабые места в коде.",
    "Уилл Тернер — перековывает ржавый код в острые и чистые инженерные клинки.",
    "Дэйви Джонс — выпускает Кракена AppSec и топит уязвимости до релиза.",
    "Тиа Дальма — плетет промпт-заклинания, чтобы ИИ говорил метко и безопасно.",
    "Джек Воробей — прокладывает диалоговые тропы через любые штормы UX.",
    "Израэль Хэндс — держит рангоут инфраструктуры: деплой, CI/CD и облака.",
    "Джон Сильвер — квартирмейстер Git: ветки, PR, релизы и порядок на борту.",
    "Билли Бонс — осматривает каждый трюм тестами и не дает багам спрятаться.",
    "Джошами Гиббс — превращает технику в морские истории для Хабра.",
    "Бен Ганн — рисует карты документации, чтобы любой нашел сокровище в коде.",
]


def _load_base_generator():
    spec = importlib.util.spec_from_file_location("pptx_gen", BASE_SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError("Cannot load base PPTX generator script")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> None:
    module = _load_base_generator()
    module.SLIDES = [
        {
            "title": "Команда Испаньолы — пиратский экипаж проекта",
            "lines": PIRATE_LINES,
        }
    ]
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    module.build_pptx(OUT_PATH)
    print(f"Generated: {OUT_PATH}")


if __name__ == "__main__":
    main()
