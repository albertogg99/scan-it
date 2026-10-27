#!/usr/bin/env python3
import argparse
from pathlib import Path
import xml.etree.ElementTree as ET

import qrcode
from qrcode.constants import ERROR_CORRECT_H


SVG_NS = "http://www.w3.org/2000/svg"
ET.register_namespace("", SVG_NS)
BLUE = "#0055a4"
EMT_SYMBOL = Path(__file__).resolve().parents[1] / "frontend/static/emt-symbol.svg"

# Service pictograms are from lucide-static 0.468.0, ISC licensed.
ICONS = {
    "bus": (
        ("path", {"d": "M4 6 2 7 M10 6h4 m8 1-2-1 M4 11h16 M8 15h.01 M16 15h.01 M6 19v2 M18 21v-2"}),
        ("rect", {"width": "16", "height": "16", "x": "4", "y": "3", "rx": "2"}),
    ),
    "ticket": (
        ("path", {"d": "M2 9a3 3 0 0 1 0 6v2a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-2a3 3 0 0 1 0-6V7a2 2 0 0 0-2-2H4a2 2 0 0 0-2 2Z M13 5v2 M13 17v2 M13 11v2"}),
    ),
    "bike": (
        ("circle", {"cx": "18.5", "cy": "17.5", "r": "3.5"}),
        ("circle", {"cx": "5.5", "cy": "17.5", "r": "3.5"}),
        ("circle", {"cx": "15", "cy": "5", "r": "1"}),
        ("path", {"d": "M12 17.5V14l-3-3 4-3 2 3h2"}),
    ),
}


def element(parent, name, **attributes):
    return ET.SubElement(parent, f"{{{SVG_NS}}}{name}", {key.replace("_", "-"): str(value) for key, value in attributes.items()})


def text(parent, value, **attributes):
    node = element(parent, "text", **attributes)
    node.text = value
    return node


def generate(url, label, subtitle, icon, output):
    code = qrcode.QRCode(error_correction=ERROR_CORRECT_H, border=4)
    code.add_data(url)
    code.make(fit=True)
    matrix = code.get_matrix()
    module = 520 / len(matrix)

    root = ET.Element(f"{{{SVG_NS}}}svg", {"width": "750", "height": "1000", "viewBox": "0 0 750 1000", "role": "img"})
    title = element(root, "title")
    title.text = f"{label}: {url}"
    element(root, "rect", width="750", height="1000", fill=BLUE)
    text(root, label.upper(), x="45", y="65", fill="white", font_family="sans-serif", font_size="16", font_weight="700")
    text(root, "Scan it!", x="36", y="196", fill="white", font_family="sans-serif", font_size="140", font_weight="700")

    logo = element(root, "g", transform="translate(654 38) scale(0.20)")
    for path in ET.parse(EMT_SYMBOL).getroot().iter(f"{{{SVG_NS}}}path"):
        element(logo, "path", d=path.attrib["d"], fill="white")

    element(root, "rect", x="45", y="290", width="15", height="95", fill="white")
    text(root, "EMT Madrid", x="78", y="800", fill="white", font_family="sans-serif", font_size="32", font_weight="700", transform="rotate(-90 78 800)")
    element(root, "rect", x="152", y="290", width="555", height="555", fill="white")
    modules = element(root, "g", fill=BLUE, shape_rendering="crispEdges")
    for row_index, row in enumerate(matrix):
        for column_index, dark in enumerate(row):
            if dark:
                element(modules, "rect", x=f"{169.5 + column_index * module:.4f}", y=f"{307.5 + row_index * module:.4f}", width=f"{module + 0.02:.4f}", height=f"{module + 0.02:.4f}")

    element(root, "rect", x="390", y="528", width="80", height="80", rx="8", fill="white")
    pictogram = element(root, "g", transform="translate(398 536) scale(2.67)", fill="none", stroke=BLUE, stroke_width="2", stroke_linecap="round", stroke_linejoin="round")
    for name, attributes in ICONS[icon]:
        ET.SubElement(pictogram, f"{{{SVG_NS}}}{name}", attributes)

    text(root, subtitle.upper(), x="152", y="913", fill="white", font_family="sans-serif", font_size="16", font_weight="700")
    text(root, "MADRID", x="45", y="956", fill="white", font_family="sans-serif", font_size="14", font_weight="700")
    element(root, "path", d="M660 905h45m-15-15 15 15-15 15", fill="none", stroke="white", stroke_width="3")
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    ET.ElementTree(root).write(output, encoding="utf-8", xml_declaration=True)


def main():
    parser = argparse.ArgumentParser(description="Generate an EMT-branded SVG QR poster.")
    parser.add_argument("url")
    parser.add_argument("--label", required=True)
    parser.add_argument("--subtitle", required=True)
    parser.add_argument("--icon", choices=ICONS, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    generate(args.url, args.label, args.subtitle, args.icon, args.output)
    print(args.output)


if __name__ == "__main__":
    main()