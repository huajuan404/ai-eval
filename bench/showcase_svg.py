"""Native SVG presentation of the public bicycle run; keep the actual animations."""

from __future__ import annotations

import copy
import re
import xml.etree.ElementTree as ET
from pathlib import Path

from .report_overview import runner_color

NS = "http://www.w3.org/2000/svg"
ET.register_namespace("", NS)
ET.register_namespace("xlink", "http://www.w3.org/1999/xlink")
FONT = "-apple-system,BlinkMacSystemFont,Segoe UI,PingFang SC,Microsoft YaHei,sans-serif"


def node(parent: ET.Element, tag: str, **attrs: object) -> ET.Element:
    return ET.SubElement(parent, f"{{{NS}}}{tag}", {k.replace("_", "-"): str(v) for k, v in attrs.items()})


def label(parent: ET.Element, x: int, y: int, value: str, size: int = 18, **attrs: object) -> None:
    node(parent, "text", x=x, y=y, font_size=size, **attrs).text = value


def scoped_artwork(path: Path, prefix: str, x: int, y: int, width: int, height: int) -> ET.Element:
    """Inline the SVG without flattening motion or letting fragment IDs collide."""
    source = copy.deepcopy(ET.fromstring(path.read_bytes()))
    ids = {e.attrib["id"]: f"{prefix}-{e.attrib['id']}" for e in source.iter() if "id" in e.attrib}
    for element in source.iter():
        if element.tag.rsplit("}", 1)[-1] not in {"text", "tspan"}:
            if element.text is not None and not element.text.strip():
                element.text = None
            if element.tail is not None and not element.tail.strip():
                element.tail = None
        for key, value in list(element.attrib.items()):
            local = key.rsplit("}", 1)[-1]
            if local == "id":
                value = ids[value]
            elif local in {"href", "src"} and value.startswith("#"):
                value = "#" + ids[value[1:]]
            elif local in {"aria-labelledby", "aria-describedby"}:
                value = " ".join(ids.get(part, part) for part in value.split())
            else:
                value = re.sub(r"url\(\s*(['\"]?)#([^)'\"\s]+)\1\s*\)",
                               lambda m: f"url(#{ids[m[2]]})", value)
                if local in {"begin", "end"}:
                    for old, new in ids.items():
                        value = re.sub(rf"(?<![\w.-]){re.escape(old)}\.(?=begin|end|repeat)", new + ".", value)
            element.set(key, value)
    _, _, view_width, view_height = (float(v) for v in source.attrib["viewBox"].replace(",", " ").split())
    scale = min(width / view_width, height / view_height)
    content_width, content_height = view_width * scale, view_height * scale
    source.attrib.update(
        x=f"{x + (width - content_width) / 2:g}", y=f"{y + (height - content_height) / 2:g}",
        width=f"{content_width:g}", height=f"{content_height:g}", overflow="hidden",
    )
    source.attrib.setdefault("fill", "black")
    source.attrib.setdefault("font-family", "serif")
    source.attrib.setdefault("preserveAspectRatio", "xMidYMid meet")
    return source


def duration(milliseconds: int) -> str:
    seconds = round(milliseconds / 1000)
    return f"{seconds // 60} 分 {seconds % 60:02d} 秒"


def render_showcase(data: dict, site: Path, *, mobile: bool = False) -> bytes:
    width = 480 if mobile else 1200
    height = 1778 if mobile else 1062
    root = ET.Element(f"{{{NS}}}svg", {
        "viewBox": f"0 0 {width} {height}", "width": str(width), "height": str(height),
        "role": "img", "aria-labelledby": "showcase-title showcase-description",
        "font-family": FONT, "fill": "#202020",
    })
    node(root, "title", id="showcase-title").text = "ai-eval：两个真实任务，四份原生 SVG 动画"
    node(root, "desc", id="showcase-description").text = (
        "Kimi K3 与 GLM 5.3 的火烈鸟、水豚骑车作品和真实参考分、生成耗时。"
        "所有动物、车轮与踏板动画来自本次模型原始输出。"
    )
    node(root, "rect", width=width, height=height, rx=12, fill="#ffffff")
    margin = 28 if mobile else 36
    label(root, margin, 45, "AI EVAL", 24, font_weight=750, letter_spacing="-1")
    label(root, width - margin, 44, data["date"], 15, fill="#737373", text_anchor="end")
    node(root, "path", d=f"M{margin} 64H{width-margin}", stroke="#e4e4e4")
    label(root, margin, 111, "同一任务，把作品摆在一起。", 30 if mobile else 34, font_weight=650)
    label(root, margin, 145, "2 个模型 · 2 个任务 · 4 份真实交付", 20 if mobile else 18, fill="#666666")

    for ci, case in enumerate(data["cases"]):
        section_y = 198 + ci * (766 if mobile else 399)
        label(root, margin, section_y, f"0{ci+1} / {case['title']}", 23, font_weight=600)
        note = "车轮、踏板与双腿同步" if ci == 0 else "短腿要跟得上，表情还要淡定"
        label(root, margin, section_y + 28, note, 20 if mobile else 17, fill="#737373")
        records = sorted((r for r in data["records"] if r["case"] == case["id"]),
                         key=lambda r: data["runners"].index(r["runner_label"]))
        for ri, record in enumerate(records):
            card_w = width - 2 * margin if mobile else (width - 2 * margin - 24) // 2
            x = margin if mobile else margin + ri * (card_w + 24)
            y = section_y + 46 + (ri * 343 if mobile else 0)
            card = node(root, "g")
            node(card, "rect", x=x, y=y, width=card_w, height=323, rx=6,
                 fill="#ffffff", stroke="#e4e4e4")
            color = runner_color(record["runner_label"])
            node(card, "rect", x=x+16, y=y+19, width=9, height=9, rx=2, fill=color)
            name = "Kimi K3" if record["runner_label"] == "kimi-k3" else "GLM 5.3"
            label(card, x+34, y+31, name, 24 if mobile else 20, font_weight=600)
            judge = record["judge"]
            label(card, x+card_w-16, y+31, f"{judge['score']:g} / {judge['max']:g}",
                  26 if mobile else 22, font_weight=650, text_anchor="end", fill=color)
            label(card, x+16, y+58, duration(record["duration_ms"]), 21 if mobile else 17, fill="#666666")
            label(card, x+card_w-16, y+58, "裁判参考分", 18 if mobile else 15, fill="#737373", text_anchor="end")
            node(card, "path", d=f"M{x+16} {y+73}H{x+card_w-16}", stroke="#eeeeee")
            card.append(scoped_artwork(site / record["svg"], f"c{ci}-r{ri}",
                                       x+10, y+81, card_w-20, 205))
            sync = judge["dimensions"]["leg_pedal_sync"]
            label(card, x+16, y+309, f"腿脚同步  {sync:g} / 5", 20 if mobile else 16,
                  fill="#a63f50" if sync < 3 else "#666666")
            if not mobile:
                label(card, x+card_w-16, y+309, "原始 SVG · 持续循环", 15,
                      fill="#737373", text_anchor="end")

    bottom = height - 70
    node(root, "path", d=f"M{margin} {bottom}H{width-margin}", stroke="#e4e4e4")
    label(root, margin, bottom+30, "每格 1 次 · 耗时不含判分 · 不代表总体能力", 16, fill="#737373")
    label(root, margin, bottom+55, "点击进入完整报告：评分 / 耗时切换、作品放大、裁判依据 →",
          14 if mobile else 17, fill="#277d79")
    return ET.tostring(root, encoding="utf-8", xml_declaration=True) + b"\n"
