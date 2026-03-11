from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from xml.sax.saxutils import escape
import zipfile

OUT_PATH = Path("articles/vkuswill-bot-public-presentation-draft.pptx")

SLIDES = [
    {
        "title": "Покупки во ВкусВилл одной фразой",
        "lines": [
            "Telegram-бот, который собирает корзину по обычному сообщению.",
            "",
            "Обычно заказ продуктов - это десятки кликов. Мы сделали бота,",
            "который понимает естественный язык и готовит корзину автоматически.",
        ],
    },
    {
        "title": "Почему это нужно",
        "lines": [
            "Рутинный заказ занимает 15-20 минут.",
            "Нужно вручную искать и добавлять каждую позицию.",
            "Легко забыть часть продуктов.",
            "",
            "Люди знают, что им нужно, но не хотят тратить время на механику.",
        ],
    },
    {
        "title": "Что делает бот",
        "lines": [
            "Понимает запрос: «Собери завтрак на двоих».",
            "Находит подходящие товары.",
            "Формирует готовую ссылку на корзину.",
            "Показывает итоговую стоимость.",
        ],
    },
    {
        "title": "Как это выглядит для пользователя",
        "lines": [
            "1. Пользователь: «Нужно молоко, хлеб и сыр».",
            "2. Бот показывает подобранные товары и сумму.",
            "3. Бот дает кнопку «Открыть корзину».",
            "",
            "Одно сообщение - и понятный результат.",
        ],
    },
    {
        "title": "Что уже умеет продукт",
        "lines": [
            "Подбор продуктов по свободному тексту.",
            "Сборка корзины по рецепту.",
            "Учет предпочтений (например, безлактозное).",
            "Подсказки по КБЖУ и калорийности.",
            "Контекстный диалог и уточнения.",
            "Индикация прогресса сборки корзины.",
        ],
    },
    {
        "title": "Почему можно доверять",
        "lines": [
            "Проект минимизирует персональные данные.",
            "Имя и username из Telegram не сохраняются.",
            "Чувствительные данные маскируются в логах.",
            "Есть информированное согласие и команда /privacy.",
            "Есть rate limiting: защита от злоупотреблений.",
        ],
    },
    {
        "title": "Надежность",
        "lines": [
            "1231 автоматический тест.",
            "Отдельные проверки безопасности: SAST и AI safety.",
            "Мониторинг и аналитика качества ответов.",
            "CI/CD и воспроизводимый деплой.",
        ],
    },
    {
        "title": "Как это устроено простыми словами",
        "lines": [
            "Telegram-бот - общение с пользователем.",
            "ИИ-модель - понимает запрос и планирует шаги.",
            "Интеграция с каталогом - ищет товары и собирает корзину.",
            "",
            "Снаружи простой чат, внутри связка ИИ и сервисов каталога.",
        ],
    },
    {
        "title": "Свежие улучшения (версия 0.16.0)",
        "lines": [
            "Пошаговый прогресс сборки корзины в реальном времени.",
            "Более точный подбор ингредиентов для рецептов.",
            "Кнопки обратной связи после корзины.",
            "Админ-аналитика по качеству корзин.",
        ],
    },
    {
        "title": "Ограничения и честные ожидания",
        "lines": [
            "Это независимый open-source проект, не официальный продукт ВкусВилл.",
            "Наличие и финальная цена подтверждаются на стороне магазина.",
            "Иногда нужны уточнения пользователя для точного подбора.",
        ],
    },
    {
        "title": "Что дальше",
        "lines": [
            "Голосовые сообщения.",
            "Рекомендации на основе истории покупок.",
            "Поддержка других магазинов через тот же подход.",
            "Mini App для управления корзиной.",
        ],
    },
    {
        "title": "Попробуйте сами",
        "lines": [
            "Демо: @vkuswill_bot",
            "Репозиторий: github.com/mrPDA/vkuswill_bot",
            "Обратная связь: GitHub Issues и Discussions",
            "",
            "Лучший способ понять ценность - написать боту реальную задачу.",
        ],
    },
]


def _paragraph(text: str, size: int, bold: bool = False) -> str:
    attrs = [f'sz="{size}"', 'lang="ru-RU"']
    if bold:
        attrs.append('b="1"')
    attr_s = " ".join(attrs)
    escaped = escape(text)
    return (
        "<a:p>"
        f"<a:r><a:rPr {attr_s}/><a:t>{escaped}</a:t></a:r>"
        "<a:endParaRPr lang=\"ru-RU\"/>"
        "</a:p>"
    )


def _shape(sp_id: int, name: str, x: int, y: int, cx: int, cy: int, paragraphs: str) -> str:
    return f"""
    <p:sp>
      <p:nvSpPr>
        <p:cNvPr id=\"{sp_id}\" name=\"{escape(name)}\"/>
        <p:cNvSpPr/>
        <p:nvPr/>
      </p:nvSpPr>
      <p:spPr>
        <a:xfrm><a:off x=\"{x}\" y=\"{y}\"/><a:ext cx=\"{cx}\" cy=\"{cy}\"/></a:xfrm>
        <a:prstGeom prst=\"rect\"><a:avLst/></a:prstGeom>
        <a:noFill/>
        <a:ln><a:noFill/></a:ln>
      </p:spPr>
      <p:txBody>
        <a:bodyPr wrap=\"square\"/>
        <a:lstStyle/>
        {paragraphs}
      </p:txBody>
    </p:sp>
    """.strip()


def _slide_xml(title: str, lines: list[str]) -> str:
    title_xml = _shape(
        sp_id=2,
        name="Title",
        x=685800,
        y=342900,
        cx=10820400,
        cy=914400,
        paragraphs=_paragraph(title, size=4000, bold=True),
    )

    body_paragraphs = []
    for line in lines:
        if line:
            body_paragraphs.append(_paragraph(f"• {line}", size=2400))
        else:
            body_paragraphs.append("<a:p><a:endParaRPr lang=\"ru-RU\"/></a:p>")

    body_xml = _shape(
        sp_id=3,
        name="Body",
        x=685800,
        y=1371600,
        cx=10820400,
        cy=4800600,
        paragraphs="".join(body_paragraphs),
    )

    return f"""<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>
<p:sld xmlns:a=\"http://schemas.openxmlformats.org/drawingml/2006/main\"
       xmlns:r=\"http://schemas.openxmlformats.org/officeDocument/2006/relationships\"
       xmlns:p=\"http://schemas.openxmlformats.org/presentationml/2006/main\">
  <p:cSld>
    <p:spTree>
      <p:nvGrpSpPr>
        <p:cNvPr id=\"1\" name=\"\"/>
        <p:cNvGrpSpPr/>
        <p:nvPr/>
      </p:nvGrpSpPr>
      <p:grpSpPr>
        <a:xfrm>
          <a:off x=\"0\" y=\"0\"/>
          <a:ext cx=\"0\" cy=\"0\"/>
          <a:chOff x=\"0\" y=\"0\"/>
          <a:chExt cx=\"0\" cy=\"0\"/>
        </a:xfrm>
      </p:grpSpPr>
      {title_xml}
      {body_xml}
    </p:spTree>
  </p:cSld>
  <p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr>
</p:sld>
"""


def _slide_rels_xml() -> str:
    return """<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>
<Relationships xmlns=\"http://schemas.openxmlformats.org/package/2006/relationships\">
  <Relationship Id=\"rId1\"
    Type=\"http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideLayout\"
    Target=\"../slideLayouts/slideLayout1.xml\"/>
</Relationships>
"""


def _presentation_xml(slide_count: int) -> str:
    ids = []
    for idx in range(slide_count):
        ids.append(
            f'<p:sldId id="{256 + idx}" r:id="rId{idx + 2}"/>'
        )
    slide_ids = "".join(ids)

    return f"""<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>
<p:presentation xmlns:a=\"http://schemas.openxmlformats.org/drawingml/2006/main\"
    xmlns:r=\"http://schemas.openxmlformats.org/officeDocument/2006/relationships\"
    xmlns:p=\"http://schemas.openxmlformats.org/presentationml/2006/main\">
  <p:sldMasterIdLst>
    <p:sldMasterId id=\"2147483648\" r:id=\"rId1\"/>
  </p:sldMasterIdLst>
  <p:sldIdLst>{slide_ids}</p:sldIdLst>
  <p:sldSz cx=\"12192000\" cy=\"6858000\" type=\"screen16x9\"/>
  <p:notesSz cx=\"6858000\" cy=\"9144000\"/>
  <p:defaultTextStyle/>
</p:presentation>
"""


def _presentation_rels_xml(slide_count: int) -> str:
    rels = [
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideMaster" '
        'Target="slideMasters/slideMaster1.xml"/>'
    ]
    for idx in range(slide_count):
        rels.append(
            f'<Relationship Id="rId{idx + 2}" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide" '
            f'Target="slides/slide{idx + 1}.xml"/>'
        )

    return (
        "<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>"
        "<Relationships xmlns=\"http://schemas.openxmlformats.org/package/2006/relationships\">"
        + "".join(rels)
        + "</Relationships>"
    )


def _content_types_xml(slide_count: int) -> str:
    overrides = [
        '<Override PartName="/ppt/presentation.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"/>',
        '<Override PartName="/ppt/slideMasters/slideMaster1.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.presentationml.slideMaster+xml"/>',
        '<Override PartName="/ppt/slideLayouts/slideLayout1.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.presentationml.slideLayout+xml"/>',
        '<Override PartName="/ppt/theme/theme1.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.theme+xml"/>',
        '<Override PartName="/docProps/core.xml" '
        'ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>',
        '<Override PartName="/docProps/app.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>',
    ]
    for idx in range(slide_count):
        overrides.append(
            f'<Override PartName="/ppt/slides/slide{idx + 1}.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.presentationml.slide+xml"/>'
        )

    return (
        "<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>"
        "<Types xmlns=\"http://schemas.openxmlformats.org/package/2006/content-types\">"
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        + "".join(overrides)
        + "</Types>"
    )


def _root_rels_xml() -> str:
    return """<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>
<Relationships xmlns=\"http://schemas.openxmlformats.org/package/2006/relationships\">
  <Relationship Id=\"rId1\"
    Type=\"http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument\"
    Target=\"ppt/presentation.xml\"/>
  <Relationship Id=\"rId2\"
    Type=\"http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties\"
    Target=\"docProps/core.xml\"/>
  <Relationship Id=\"rId3\"
    Type=\"http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties\"
    Target=\"docProps/app.xml\"/>
</Relationships>
"""


def _app_xml(slide_count: int) -> str:
    return f"""<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>
<Properties xmlns=\"http://schemas.openxmlformats.org/officeDocument/2006/extended-properties\"
            xmlns:vt=\"http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes\">
  <Application>Codex</Application>
  <PresentationFormat>Widescreen</PresentationFormat>
  <Slides>{slide_count}</Slides>
  <Notes>0</Notes>
  <HiddenSlides>0</HiddenSlides>
  <MMClips>0</MMClips>
  <ScaleCrop>false</ScaleCrop>
  <Company></Company>
  <LinksUpToDate>false</LinksUpToDate>
  <SharedDoc>false</SharedDoc>
  <HyperlinksChanged>false</HyperlinksChanged>
  <AppVersion>16.0000</AppVersion>
</Properties>
"""


def _core_xml() -> str:
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    return f"""<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>
<cp:coreProperties xmlns:cp=\"http://schemas.openxmlformats.org/package/2006/metadata/core-properties\"
    xmlns:dc=\"http://purl.org/dc/elements/1.1/\"
    xmlns:dcterms=\"http://purl.org/dc/terms/\"
    xmlns:dcmitype=\"http://purl.org/dc/dcmitype/\"
    xmlns:xsi=\"http://www.w3.org/2001/XMLSchema-instance\">
  <dc:title>VkusVill Bot: презентация для широкой аудитории</dc:title>
  <dc:subject>Продуктовая презентация</dc:subject>
  <dc:creator>Codex</dc:creator>
  <cp:lastModifiedBy>Codex</cp:lastModifiedBy>
  <dcterms:created xsi:type=\"dcterms:W3CDTF\">{now}</dcterms:created>
  <dcterms:modified xsi:type=\"dcterms:W3CDTF\">{now}</dcterms:modified>
</cp:coreProperties>
"""


def _slide_layout_xml() -> str:
    return """<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>
<p:sldLayout xmlns:a=\"http://schemas.openxmlformats.org/drawingml/2006/main\"
             xmlns:r=\"http://schemas.openxmlformats.org/officeDocument/2006/relationships\"
             xmlns:p=\"http://schemas.openxmlformats.org/presentationml/2006/main\"
             type=\"blank\" preserve=\"1\">
  <p:cSld name=\"Blank\">
    <p:spTree>
      <p:nvGrpSpPr>
        <p:cNvPr id=\"1\" name=\"\"/>
        <p:cNvGrpSpPr/>
        <p:nvPr/>
      </p:nvGrpSpPr>
      <p:grpSpPr>
        <a:xfrm>
          <a:off x=\"0\" y=\"0\"/>
          <a:ext cx=\"0\" cy=\"0\"/>
          <a:chOff x=\"0\" y=\"0\"/>
          <a:chExt cx=\"0\" cy=\"0\"/>
        </a:xfrm>
      </p:grpSpPr>
    </p:spTree>
  </p:cSld>
  <p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr>
</p:sldLayout>
"""


def _slide_layout_rels_xml() -> str:
    return """<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>
<Relationships xmlns=\"http://schemas.openxmlformats.org/package/2006/relationships\">
  <Relationship Id=\"rId1\"
    Type=\"http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideMaster\"
    Target=\"../slideMasters/slideMaster1.xml\"/>
</Relationships>
"""


def _slide_master_xml() -> str:
    return """<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>
<p:sldMaster xmlns:a=\"http://schemas.openxmlformats.org/drawingml/2006/main\"
             xmlns:r=\"http://schemas.openxmlformats.org/officeDocument/2006/relationships\"
             xmlns:p=\"http://schemas.openxmlformats.org/presentationml/2006/main\">
  <p:cSld>
    <p:bg>
      <p:bgPr>
        <a:solidFill><a:schemeClr val=\"bg1\"/></a:solidFill>
        <a:effectLst/>
      </p:bgPr>
    </p:bg>
    <p:spTree>
      <p:nvGrpSpPr>
        <p:cNvPr id=\"1\" name=\"\"/>
        <p:cNvGrpSpPr/>
        <p:nvPr/>
      </p:nvGrpSpPr>
      <p:grpSpPr>
        <a:xfrm>
          <a:off x=\"0\" y=\"0\"/>
          <a:ext cx=\"0\" cy=\"0\"/>
          <a:chOff x=\"0\" y=\"0\"/>
          <a:chExt cx=\"0\" cy=\"0\"/>
        </a:xfrm>
      </p:grpSpPr>
    </p:spTree>
  </p:cSld>
  <p:clrMap bg1=\"lt1\" tx1=\"dk1\" bg2=\"lt2\" tx2=\"dk2\" accent1=\"accent1\" accent2=\"accent2\" accent3=\"accent3\" accent4=\"accent4\" accent5=\"accent5\" accent6=\"accent6\" hlink=\"hlink\" folHlink=\"folHlink\"/>
  <p:sldLayoutIdLst>
    <p:sldLayoutId id=\"2147483649\" r:id=\"rId1\"/>
  </p:sldLayoutIdLst>
  <p:txStyles>
    <p:titleStyle>
      <a:lvl1pPr algn=\"l\"><a:defRPr sz=\"4400\" b=\"1\"/></a:lvl1pPr>
    </p:titleStyle>
    <p:bodyStyle>
      <a:lvl1pPr marL=\"0\" indent=\"0\"><a:defRPr sz=\"2400\"/></a:lvl1pPr>
      <a:lvl2pPr marL=\"457200\" indent=\"0\"><a:defRPr sz=\"2200\"/></a:lvl2pPr>
    </p:bodyStyle>
    <p:otherStyle/>
  </p:txStyles>
</p:sldMaster>
"""


def _slide_master_rels_xml() -> str:
    return """<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>
<Relationships xmlns=\"http://schemas.openxmlformats.org/package/2006/relationships\">
  <Relationship Id=\"rId1\"
    Type=\"http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideLayout\"
    Target=\"../slideLayouts/slideLayout1.xml\"/>
  <Relationship Id=\"rId2\"
    Type=\"http://schemas.openxmlformats.org/officeDocument/2006/relationships/theme\"
    Target=\"../theme/theme1.xml\"/>
</Relationships>
"""


def _theme_xml() -> str:
    return """<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>
<a:theme xmlns:a=\"http://schemas.openxmlformats.org/drawingml/2006/main\" name=\"Custom Theme\">
  <a:themeElements>
    <a:clrScheme name=\"Office\">
      <a:dk1><a:sysClr val=\"windowText\" lastClr=\"000000\"/></a:dk1>
      <a:lt1><a:sysClr val=\"window\" lastClr=\"FFFFFF\"/></a:lt1>
      <a:dk2><a:srgbClr val=\"1F2937\"/></a:dk2>
      <a:lt2><a:srgbClr val=\"F3F4F6\"/></a:lt2>
      <a:accent1><a:srgbClr val=\"2563EB\"/></a:accent1>
      <a:accent2><a:srgbClr val=\"0EA5E9\"/></a:accent2>
      <a:accent3><a:srgbClr val=\"10B981\"/></a:accent3>
      <a:accent4><a:srgbClr val=\"F59E0B\"/></a:accent4>
      <a:accent5><a:srgbClr val=\"EF4444\"/></a:accent5>
      <a:accent6><a:srgbClr val=\"8B5CF6\"/></a:accent6>
      <a:hlink><a:srgbClr val=\"2563EB\"/></a:hlink>
      <a:folHlink><a:srgbClr val=\"7C3AED\"/></a:folHlink>
    </a:clrScheme>
    <a:fontScheme name=\"Office\">
      <a:majorFont>
        <a:latin typeface=\"Calibri\"/>
        <a:ea typeface=\"\"/>
        <a:cs typeface=\"\"/>
      </a:majorFont>
      <a:minorFont>
        <a:latin typeface=\"Calibri\"/>
        <a:ea typeface=\"\"/>
        <a:cs typeface=\"\"/>
      </a:minorFont>
    </a:fontScheme>
    <a:fmtScheme name=\"Office\">
      <a:fillStyleLst>
        <a:solidFill><a:schemeClr val=\"phClr\"/></a:solidFill>
        <a:gradFill rotWithShape=\"1\">
          <a:gsLst>
            <a:gs pos=\"0\"><a:schemeClr val=\"phClr\"><a:lumMod val=\"110000\"/><a:satMod val=\"105000\"/><a:tint val=\"67000\"/></a:schemeClr></a:gs>
            <a:gs pos=\"50000\"><a:schemeClr val=\"phClr\"><a:lumMod val=\"105000\"/><a:satMod val=\"103000\"/><a:tint val=\"73000\"/></a:schemeClr></a:gs>
            <a:gs pos=\"100000\"><a:schemeClr val=\"phClr\"><a:lumMod val=\"105000\"/><a:satMod val=\"109000\"/><a:tint val=\"81000\"/></a:schemeClr></a:gs>
          </a:gsLst>
          <a:lin ang=\"5400000\" scaled=\"0\"/>
        </a:gradFill>
      </a:fillStyleLst>
      <a:lnStyleLst>
        <a:ln w=\"9525\" cap=\"flat\" cmpd=\"sng\" algn=\"ctr\">
          <a:solidFill><a:schemeClr val=\"phClr\"/></a:solidFill>
          <a:prstDash val=\"solid\"/>
        </a:ln>
      </a:lnStyleLst>
      <a:effectStyleLst>
        <a:effectStyle><a:effectLst/></a:effectStyle>
      </a:effectStyleLst>
      <a:bgFillStyleLst>
        <a:solidFill><a:schemeClr val=\"phClr\"/></a:solidFill>
      </a:bgFillStyleLst>
    </a:fmtScheme>
  </a:themeElements>
  <a:objectDefaults/>
  <a:extraClrSchemeLst/>
</a:theme>
"""


def build_pptx(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    slide_count = len(SLIDES)

    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", _content_types_xml(slide_count))
        zf.writestr("_rels/.rels", _root_rels_xml())

        zf.writestr("docProps/app.xml", _app_xml(slide_count))
        zf.writestr("docProps/core.xml", _core_xml())

        zf.writestr("ppt/presentation.xml", _presentation_xml(slide_count))
        zf.writestr("ppt/_rels/presentation.xml.rels", _presentation_rels_xml(slide_count))

        zf.writestr("ppt/slideMasters/slideMaster1.xml", _slide_master_xml())
        zf.writestr("ppt/slideMasters/_rels/slideMaster1.xml.rels", _slide_master_rels_xml())

        zf.writestr("ppt/slideLayouts/slideLayout1.xml", _slide_layout_xml())
        zf.writestr("ppt/slideLayouts/_rels/slideLayout1.xml.rels", _slide_layout_rels_xml())

        zf.writestr("ppt/theme/theme1.xml", _theme_xml())

        for idx, slide in enumerate(SLIDES, start=1):
            zf.writestr(
                f"ppt/slides/slide{idx}.xml",
                _slide_xml(slide["title"], slide["lines"]),
            )
            zf.writestr(f"ppt/slides/_rels/slide{idx}.xml.rels", _slide_rels_xml())


if __name__ == "__main__":
    build_pptx(OUT_PATH)
    print(f"Created: {OUT_PATH}")
