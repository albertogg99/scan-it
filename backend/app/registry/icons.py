from io import BytesIO
import re
from xml.etree.ElementTree import tostring

from defusedxml.ElementTree import fromstring
from PIL import Image

from app.registry.service import error

MAX_ICON_BYTES = 1024 * 1024
SVG_TAGS = frozenset("svg g path rect circle ellipse line polyline polygon defs linearGradient radialGradient stop clipPath mask title desc use symbol".split())
SVG_ATTRIBUTES = frozenset("id viewBox width height x y x1 y1 x2 y2 cx cy r rx ry d points fill fill-opacity fill-rule stroke stroke-width stroke-linecap stroke-linejoin stroke-miterlimit stroke-dasharray stroke-dashoffset stroke-opacity opacity transform gradientTransform gradientUnits offset stop-color stop-opacity clip-path clip-rule mask preserveAspectRatio href version xmlns".split())


def validate_icon(data: bytes) -> tuple[bytes, str]:
    if not data or len(data) > MAX_ICON_BYTES:
        raise error(422, "icon_invalid", "Choose a PNG or SVG icon no larger than 1 MB.")
    try:
        if data.startswith(b"\x89PNG\r\n\x1a\n"):
            with Image.open(BytesIO(data)) as image:
                if image.format != "PNG" or max(image.size) > 2048 or image.is_animated:
                    raise ValueError("Unsupported PNG")
                image.verify()
            with Image.open(BytesIO(data)) as image:
                output = BytesIO()
                image.convert("RGBA").save(output, format="PNG")
                return output.getvalue(), "image/png"
        root = fromstring(data, forbid_dtd=True, forbid_entities=True, forbid_external=True)
        if root.tag != "{http://www.w3.org/2000/svg}svg":
            raise ValueError("Not SVG")
        for element in root.iter():
            if element.tag.removeprefix("{http://www.w3.org/2000/svg}") not in SVG_TAGS:
                raise ValueError("Unsafe SVG element")
            for attribute, value in element.attrib.items():
                name = attribute.removeprefix("{http://www.w3.org/1999/xlink}")
                if name not in SVG_ATTRIBUTES:
                    raise ValueError("Unsafe SVG attribute")
                if name == "href" and not re.fullmatch(r"#[A-Za-z_][\w:.-]*", value):
                    raise ValueError("External reference")
                if name in {"fill", "stroke", "clip-path", "mask"} and not re.fullmatch(
                    r"(?:none|currentColor|transparent|[A-Za-z]+|#[0-9A-Fa-f]{3,8}|rgba?\([0-9.,%\s]+\)|url\(#[A-Za-z_][\w:.-]*\))", value
                ):
                    raise ValueError("Unsafe paint reference")
        return tostring(root, encoding="utf-8"), "image/svg+xml"
    except Exception as exc:
        raise error(422, "icon_invalid", "Use a valid PNG (up to 2048px) or a plain SVG without styles, scripts, or external references.") from exc