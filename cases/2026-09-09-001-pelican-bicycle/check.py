"""Validate standalone SVG and rasterize it; never infer drawing quality from XML."""

from __future__ import annotations

import math
import re
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

SVG_NS = "{http://www.w3.org/2000/svg}"
FORBIDDEN = {"script", "foreignObject", "image", "iframe", "audio", "video"}
SHAPES = {"path", "rect", "circle", "ellipse", "line", "polyline", "polygon", "text", "use"}


def validate(source: str) -> None:
    if "<!DOCTYPE" in source.upper() or "<!ENTITY" in source.upper():
        raise ValueError("DOCTYPE / entity declarations are not allowed")
    root = ET.fromstring(source)
    if root.tag != SVG_NS + "svg":
        raise ValueError("root must be SVG with the SVG namespace")
    viewport = root.attrib.get("viewBox", "")
    if viewport:
        values = [float(x) for x in re.split(r"[\s,]+", viewport.strip())]
        if len(values) != 4 or not all(math.isfinite(x) for x in values) or not all(0 < x <= 10000 for x in values[2:]):
            raise ValueError("invalid or excessive viewBox")
    shapes = 0
    for element in root.iter():
        if not element.tag.startswith(SVG_NS):
            raise ValueError("non-SVG elements are not allowed")
        tag = element.tag.removeprefix(SVG_NS)
        if tag in FORBIDDEN:
            raise ValueError(f"forbidden element: {tag}")
        shapes += tag in SHAPES
        for key, value in element.attrib.items():
            local = key.rsplit("}", 1)[-1].lower()
            if local.startswith("on"):
                raise ValueError("event handlers are not allowed")
            if local in {"href", "src"} and not value.startswith("#"):
                raise ValueError("external references are not allowed")
        css = " ".join(element.attrib.values()) + (element.text or "")
        if "\\" in css or "@import" in css.lower():
            raise ValueError("external or escaped CSS is not allowed")
        for ref in re.findall(r"url\s*\((.*?)\)", css, re.I):
            if not ref.strip(" \t\r\n\"'").startswith("#"):
                raise ValueError("CSS resources must be internal SVG fragments")
    if not shapes:
        raise ValueError("SVG has no drawable elements")


def main() -> int:
    source = Path("pelican.svg")
    output = Path("render.png")
    try:
        if source.is_symlink() or not source.is_file() or source.stat().st_size > 512000:
            raise ValueError("pelican.svg must be a local file no larger than 512 KB")
        if output.is_symlink():
            raise ValueError("render.png must not be a symlink")
        validate(source.read_text(encoding="utf-8"))
        renderer = shutil.which("magick")
        if renderer is None:
            raise ValueError("ImageMagick is required to render SVG evidence")
        result = subprocess.run(
            [renderer, "-background", "white", "MSVG:pelican.svg", "-resize", "1024x768",
             "-gravity", "center", "-extent", "1024x768", "PNG32:render.png"],
            capture_output=True, text=True, timeout=60,
        )
        if result.returncode != 0 or not output.is_file():
            raise ValueError("SVG rasterization failed: " + result.stderr[:400])
        print("PASS: standalone SVG parsed and render.png generated (1024x768, ImageMagick MSVG).")
        print("This is a format/render gate, not a visual-quality verdict.")
        return 0
    except (OSError, UnicodeError, ValueError, ET.ParseError, subprocess.TimeoutExpired) as exc:
        print(f"FAIL: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
