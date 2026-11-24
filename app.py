#!/usr/bin/env python3
"""
EXIF Frame Studio (macOS Python CLI)
- View, tinker with, save, and edit EXIF frame templates (JSON)
- Extract camera/lens/exposure EXIF metadata
- Automatically render Fujifilm (and other brand) badges
- Preview framed images directly in macOS Preview or export final JPEGs
"""

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional

try:
    from PIL import ExifTags, Image, ImageDraw, ImageFont, ImageOps
except ImportError:
    print(
        "Pillow is required. Install it with:\n  pip install -r requirements.txt",
        file=sys.stderr,
    )
    sys.exit(1)

BASE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = BASE_DIR / "templates"

# Standard EXIF tag IDs
TAG_MAKE = 271
TAG_MODEL = 272
TAG_EXPOSURE_TIME = 33434
TAG_FNUMBER = 33437
TAG_ISO = 34855
TAG_FOCAL_LENGTH = 37386
TAG_LENS_MODEL = 42036


def load_template(name: str) -> Dict[str, Any]:
    path = TEMPLATES_DIR / f"{name}.json"
    if not path.exists():
        raise FileNotFoundError(f"Template '{name}' not found in {TEMPLATES_DIR}")
    return json.loads(path.read_text(encoding="utf-8"))


def save_template_file(name: str, data: Dict[str, Any]) -> Path:
    TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)
    path = TEMPLATES_DIR / f"{name}.json"
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return path


def list_templates() -> None:
    TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)
    files = sorted(TEMPLATES_DIR.glob("*.json"))
    if not files:
        print("No templates found.")
        return
    print("Available EXIF Frame Templates:")
    for f in files:
        data = json.loads(f.read_text(encoding="utf-8"))
        print(f"  - {data.get('name', f.stem)}: {data.get('display_name', f.stem)}")
        if data.get("description"):
            print(f"      {data['description']}")


def extract_exif(img: Image.Image) -> Dict[str, str]:
    exif_data = img.getexif()
    if not exif_data:
        return {}

    # Merge ExifIFD sub-tags (0x8769)
    exif_ifd = exif_data.get_ifd(0x8769) if hasattr(exif_data, "get_ifd") else {}
    merged: Dict[int, Any] = dict(exif_data)
    if isinstance(exif_ifd, dict):
        merged.update(exif_ifd)

    make = str(merged.get(TAG_MAKE, "")).strip()
    model = str(merged.get(TAG_MODEL, "")).strip()
    if make and model and not model.lower().startswith(make.lower()):
        camera = f"{make} {model}"
    else:
        camera = model or make

    lens = str(merged.get(TAG_LENS_MODEL, "")).strip()

    focal_raw = merged.get(TAG_FOCAL_LENGTH)
    focal = f"{round(float(focal_raw))}mm" if focal_raw else ""

    fnum_raw = merged.get(TAG_FNUMBER)
    aperture = f"f/{float(fnum_raw):.1f}".replace(".0", "") if fnum_raw else ""

    exp_raw = merged.get(TAG_EXPOSURE_TIME)
    shutter = ""
    if exp_raw:
        exp_val = float(exp_raw)
        if exp_val >= 1:
            shutter = f"{exp_val:.1f}s".replace(".0s", "s")
        elif exp_val > 0:
            shutter = f"1/{round(1.0 / exp_val)}s"

    iso_raw = merged.get(TAG_ISO)
    if isinstance(iso_raw, (list, tuple)) and iso_raw:
        iso_raw = iso_raw[0]
    iso = f"ISO {iso_raw}" if iso_raw else ""

    return {
        "make": make,
        "camera": camera,
        "lens": lens,
        "focal": focal,
        "aperture": aperture,
        "shutter": shutter,
        "iso": iso,
    }


def load_mac_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    candidates = (
        ["/System/Library/Fonts/SFNS.ttf", "/System/Library/Fonts/HelveticaNeue.ttc"]
        if not bold
        else ["/System/Library/Fonts/SFNS.ttf", "/System/Library/Fonts/HelveticaNeue.ttc"]
    )
    for font_path in candidates:
        if Path(font_path).exists():
            try:
                return ImageFont.truetype(font_path, size)
            except OSError:
                continue
    return ImageFont.load_default()


def draw_brand_badge(
    draw: ImageDraw.ImageDraw,
    make: str,
    x: int,
    center_y: int,
    size: int,
    accent: str,
    primary_text: str,
) -> int:
    """Draw a clean camera brand badge (with dedicated Fujifilm styling). Returns width used."""
    upper_make = make.upper()
    if not upper_make:
        return 0

    if "FUJIFILM" in upper_make:
        label = "FUJIFILM"
        badge_bg = accent or "#008E5B"
        badge_fg = "#FFFFFF"
    else:
        label = upper_make.split()[0]
        badge_bg = primary_text
        badge_fg = "#FFFFFF"

    font = load_mac_font(max(int(size * 0.65), 14), bold=True)
    bbox = draw.textbbox((0, 0), label, font=font)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]
    pad_x = max(int(size * 0.45), 10)
    pad_y = max(int(size * 0.25), 6)

    box_w = text_w + pad_x * 2
    box_h = text_h + pad_y * 2
    top = int(center_y - box_h / 2)

    draw.rounded_rectangle(
        [x, top, x + box_w, top + box_h],
        radius=max(int(box_h * 0.2), 4),
        fill=badge_bg,
    )
    draw.text(
        (x + pad_x, center_y),
        label,
        fill=badge_fg,
        font=font,
        anchor="lm",
    )
    return box_w + int(size * 0.5)


def render_framed_image(
    image_path: Path,
    template: Dict[str, Any],
    overrides: Optional[Dict[str, str]] = None,
) -> Image.Image:
    raw_img = Image.open(image_path)
    exif = extract_exif(raw_img)
    if overrides:
        for k, v in overrides.items():
            if v is not None:
                exif[k] = v

    img = ImageOps.exif_transpose(raw_img).convert("RGB")
    w, h = img.size
    short_side = min(w, h)

    theme = template.get("theme", {})
    layout = template.get("layout", {})

    bg_color = theme.get("background", "#FFFFFF")
    primary_color = theme.get("primary_text", "#111827")
    secondary_color = theme.get("secondary_text", "#6B7280")
    accent_color = theme.get("accent", "#008E5B")

    pad = int(short_side * (float(layout.get("border_percent", 4.0)) / 100.0))
    footer_h = max(int(short_side * (float(layout.get("footer_height_percent", 11.5)) / 100.0)), 110)

    canvas = Image.new("RGB", (w + pad * 2, h + pad + footer_h), bg_color)
    canvas.paste(img, (pad, pad))

    draw = ImageDraw.Draw(canvas)
    center_y = pad + h + footer_h // 2

    title_size = max(int(short_side * 0.024), 22)
    sub_size = max(int(short_side * 0.017), 16)
    title_font = load_mac_font(title_size, bold=True)
    sub_font = load_mac_font(sub_size)

    left_x = pad
    if layout.get("show_brand_logo", True) and exif.get("make"):
        left_x += draw_brand_badge(
            draw,
            exif["make"],
            left_x,
            center_y,
            title_size,
            accent_color,
            primary_color,
        )

    camera_text = exif.get("camera", "")
    lens_text = exif.get("lens", "") if layout.get("show_lens", True) else ""

    if camera_text and lens_text:
        draw.text((left_x, center_y - int(title_size * 0.5)), camera_text, fill=primary_color, font=title_font, anchor="lm")
        draw.text((left_x, center_y + int(title_size * 0.55)), lens_text, fill=secondary_color, font=sub_font, anchor="lm")
    elif camera_text or lens_text:
        draw.text((left_x, center_y), camera_text or lens_text, fill=primary_color, font=title_font, anchor="lm")

    sep = layout.get("separator", " · ")
    settings_parts = [p for p in [exif.get("focal"), exif.get("aperture"), exif.get("shutter"), exif.get("iso")] if p]
    settings_text = sep.join(settings_parts)
    signature = template.get("signature", "") if layout.get("show_signature", True) else ""

    right_x = canvas.width - pad
    if settings_text and signature:
        draw.text((right_x, center_y - int(title_size * 0.5)), settings_text, fill=primary_color, font=title_font, anchor="rm")
        draw.text((right_x, center_y + int(title_size * 0.55)), signature, fill=secondary_color, font=sub_font, anchor="rm")
    elif settings_text or signature:
        draw.text((right_x, center_y), settings_text or signature, fill=primary_color, font=title_font, anchor="rm")

    return canvas


def main() -> None:
    parser = argparse.ArgumentParser(description="EXIF Frame Studio for macOS")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list-templates", help="View all available templates")

    show_p = sub.add_parser("show-template", help="Inspect a template JSON")
    show_p.add_argument("name")

    edit_p = sub.add_parser("edit-template", help="Tinker with and save a template")
    edit_p.add_argument("name", help="Source template name")
    edit_p.add_argument("--save-as", help="Optional new template name")
    edit_p.add_argument("--background", help="Hex background color (e.g. #FFFFFF)")
    edit_p.add_argument("--accent", help="Hex accent color (e.g. #008E5B)")
    edit_p.add_argument("--border-percent", type=float)
    edit_p.add_argument("--signature", help="Custom signature / handle")

    prev_p = sub.add_parser("preview", help="Preview framed image in macOS Preview")
    prev_p.add_argument("image", type=Path)
    prev_p.add_argument("--template", default="fujifilm_signature")

    ren_p = sub.add_parser("render", help="Export framed JPEG")
    ren_p.add_argument("image", type=Path)
    ren_p.add_argument("--template", default="fujifilm_signature")
    ren_p.add_argument("--output", type=Path)

    args = parser.parse_args()

    if args.command == "list-templates":
        list_templates()
    elif args.command == "show-template":
        print(json.dumps(load_template(args.name), indent=2))
    elif args.command == "edit-template":
        tpl = load_template(args.name)
        target_name = args.save_as or args.name
        tpl["name"] = target_name
        if args.background:
            tpl.setdefault("theme", {})["background"] = args.background
        if args.accent:
            tpl.setdefault("theme", {})["accent"] = args.accent
        if args.border_percent is not None:
            tpl.setdefault("layout", {})["border_percent"] = args.border_percent
        if args.signature is not None:
            tpl["signature"] = args.signature
        saved_path = save_template_file(target_name, tpl)
        print(f"Saved template to {saved_path}")
    elif args.command == "preview":
        tpl = load_template(args.template)
        framed = render_framed_image(args.image, tpl)
        with tempfile.NamedTemporaryFile(suffix="-preview.jpg", delete=False) as tmp:
            framed.save(tmp.name, quality=92)
            print(f"Opening preview: {tmp.name}")
            subprocess.run(["open", "-a", "Preview", tmp.name], check=False)
    elif args.command == "render":
        tpl = load_template(args.template)
        framed = render_framed_image(args.image, tpl)
        out_path = args.output or args.image.with_name(f"{args.image.stem}-framed.jpg")
        framed.save(out_path, quality=96)
        print(f"Exported framed image to {out_path}")


if __name__ == "__main__":
    main()
