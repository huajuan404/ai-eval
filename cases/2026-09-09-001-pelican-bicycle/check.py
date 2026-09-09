# /// script
# requires-python = ">=3.12"
# dependencies = ["CairoSVG==2.8.2"]
# ///
"""Validate standalone SVG and rasterize it; never infer drawing quality from XML."""

from __future__ import annotations

import math
import os
import re
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
        for ref in re.findall(r"url\s*\((.*?)\)", css, re.IGNORECASE):
            if not ref.strip(" \t\r\n\"'").startswith("#"):
                raise ValueError("CSS resources must be internal SVG fragments")
    if not shapes:
        raise ValueError("SVG has no drawable elements")


def main() -> int:
    source = Path("pelican.svg")
    output = Path("render.png")
    try:
        # Never let a failed check leave stale or contestant-supplied visual evidence.
        if output.is_symlink() or output.is_file():
            output.unlink()
        if source.is_symlink() or not source.is_file() or source.stat().st_size > 512000:
            raise ValueError("pelican.svg must be a local file no larger than 512 KB")
        source_text = source.read_text(encoding="utf-8")
        validate(source_text)
        if sys.platform == "darwin":
            # Scoped to this checker process; no shell/profile/system configuration changes.
            libraries = [p for p in ("/opt/homebrew/lib", "/usr/local/lib") if Path(p, "libcairo.dylib").exists()]
            if libraries:
                os.environ["DYLD_FALLBACK_LIBRARY_PATH"] = os.pathsep.join(libraries)
        import cairosvg

        png = cairosvg.svg2png(
            bytestring=source_text.encode("utf-8"), output_width=1024, output_height=768,
            background_color="white",
        )
        output.write_bytes(png)
        print("PASS: standalone SVG parsed and render.png generated (1024x768, CairoSVG 2.8.2).")
        print("This is a format/render gate, not a visual-quality verdict.")
        return 0
    except (OSError, UnicodeError, ValueError, ET.ParseError, ImportError) as exc:
        print(f"FAIL: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
