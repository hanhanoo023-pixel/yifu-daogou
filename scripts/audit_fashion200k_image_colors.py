from __future__ import annotations

import argparse
import json
import re
import sqlite3
from collections import Counter, defaultdict, deque
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageOps


COLOR_LABELS = (
    "black",
    "white",
    "gray",
    "brown",
    "red",
    "orange",
    "yellow",
    "green",
    "blue",
    "purple",
    "pink",
)

CAPTION_COLOR_PATTERNS = {
    "black": (r"\bblack\b", r"\bcharcoal\b"),
    "white": (r"\bwhite\b", r"\bivory\b", r"\bcream\b"),
    "gray": (r"\bgr[ae]y\b", r"\bsilver\b"),
    "brown": (r"\bbrown\b", r"\btan\b", r"\bbeige\b", r"\bcamel\b"),
    "red": (r"\bred\b", r"\bburgundy\b", r"\bmaroon\b"),
    "orange": (r"\borange\b",),
    "yellow": (r"\byellow\b", r"\bgold(?:en)?\b", r"\bmustard\b"),
    "green": (r"\bgreen\b", r"\bolive\b", r"\bmint\b", r"\bteal\b"),
    "blue": (r"\bblue\b", r"\bnavy\b", r"\bteal\b", r"\bturquoise\b"),
    "purple": (
        r"\bpurple\b",
        r"\blavender\b",
        r"\blilac\b",
        r"\bplum\b",
        r"\bburgundy\b",
        r"\bmaroon\b",
    ),
    "pink": (r"\bpink\b", r"\bblush\b", r"\brose\b"),
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--image-dir", type=Path, required=True)
    parser.add_argument("--audit", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--sheet-dir", type=Path, required=True)
    parser.add_argument("--sheet-limit", type=int, default=180)
    parser.add_argument("--caption-files", nargs="+", type=Path, required=True)
    return parser.parse_args()


def connected_background_mask(rgb: np.ndarray) -> tuple[np.ndarray, list[int], float]:
    height, width, _ = rgb.shape
    border = np.concatenate(
        (rgb[0], rgb[-1], rgb[1:-1, 0], rgb[1:-1, -1]), axis=0
    ).astype(np.float32)
    background = np.median(border, axis=0)
    border_distance = np.linalg.norm(border - background, axis=1)
    consistency = float(np.mean(border_distance <= 30.0))
    distance = np.linalg.norm(rgb.astype(np.float32) - background, axis=2)
    candidate = distance <= 30.0
    connected = np.zeros((height, width), dtype=bool)
    queue: deque[tuple[int, int]] = deque()

    for x in range(width):
        if candidate[0, x]:
            connected[0, x] = True
            queue.append((0, x))
        if candidate[height - 1, x] and not connected[height - 1, x]:
            connected[height - 1, x] = True
            queue.append((height - 1, x))
    for y in range(1, height - 1):
        if candidate[y, 0]:
            connected[y, 0] = True
            queue.append((y, 0))
        if candidate[y, width - 1] and not connected[y, width - 1]:
            connected[y, width - 1] = True
            queue.append((y, width - 1))

    while queue:
        y, x = queue.popleft()
        for next_y, next_x in ((y - 1, x), (y + 1, x), (y, x - 1), (y, x + 1)):
            if (
                0 <= next_y < height
                and 0 <= next_x < width
                and candidate[next_y, next_x]
                and not connected[next_y, next_x]
            ):
                connected[next_y, next_x] = True
                queue.append((next_y, next_x))

    return connected, background.round().astype(int).tolist(), consistency


def rgb_to_hsv(rgb: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    normalized = rgb.astype(np.float32) / 255.0
    red, green, blue = normalized[:, 0], normalized[:, 1], normalized[:, 2]
    maximum = normalized.max(axis=1)
    minimum = normalized.min(axis=1)
    delta = maximum - minimum
    saturation = np.divide(
        delta,
        maximum,
        out=np.zeros_like(delta),
        where=maximum > 0,
    )
    hue = np.zeros_like(maximum)
    nonzero = delta > 1e-6
    red_max = nonzero & (maximum == red)
    green_max = nonzero & (maximum == green)
    blue_max = nonzero & (maximum == blue)
    hue[red_max] = ((green[red_max] - blue[red_max]) / delta[red_max]) % 6
    hue[green_max] = (blue[green_max] - red[green_max]) / delta[green_max] + 2
    hue[blue_max] = (red[blue_max] - green[blue_max]) / delta[blue_max] + 4
    return hue * 60.0, saturation, maximum


def classify_pixels(pixels: np.ndarray) -> Counter[str]:
    hue, saturation, value = rgb_to_hsv(pixels)
    labels = np.empty(len(pixels), dtype=object)

    black = value < 0.20
    white = (value >= 0.82) & (saturation < 0.14)
    gray = (~black) & (~white) & (saturation < 0.18)
    chromatic = ~(black | white | gray)
    brown = chromatic & (hue >= 12) & (hue < 48) & (value < 0.62)
    pink = chromatic & (
        ((hue >= 300) & (hue < 345))
        | (((hue < 12) | (hue >= 345)) & (value > 0.68) & (saturation < 0.62))
    )

    labels[black] = "black"
    labels[white] = "white"
    labels[gray] = "gray"
    labels[brown] = "brown"
    labels[pink] = "pink"
    remaining = chromatic & ~brown & ~pink
    labels[remaining & ((hue < 15) | (hue >= 345))] = "red"
    labels[remaining & (hue >= 15) & (hue < 48)] = "orange"
    labels[remaining & (hue >= 48) & (hue < 75)] = "yellow"
    labels[remaining & (hue >= 75) & (hue < 170)] = "green"
    labels[remaining & (hue >= 170) & (hue < 260)] = "blue"
    labels[remaining & (hue >= 260) & (hue < 300)] = "purple"
    labels[remaining & (hue >= 300) & (hue < 345)] = "pink"
    return Counter(str(label) for label in labels)


def caption_colors(caption: str) -> tuple[list[str], bool]:
    first_sentence = caption.lower().split(".", 1)[0]
    colors = [
        color
        for color, patterns in CAPTION_COLOR_PATTERNS.items()
        if any(re.search(pattern, first_sentence) for pattern in patterns)
    ]
    explicit_multicolor = bool(
        re.search(r"\b(multicolor(?:ed)?|multi-colored|colorful)\b", first_sentence)
    )
    return colors, explicit_multicolor


def combine_caption_evidence(
    pixel_result: dict[str, object], expected: str, caption: str
) -> dict[str, object]:
    colors, explicit_multicolor = caption_colors(caption)
    significant = list(pixel_result["significant_colors"])
    distribution = {
        str(item["color"]): float(item["ratio"])
        for item in list(pixel_result["detected_colors"])
    }
    expected_ratio = float(pixel_result["expected_ratio"])

    if expected == "multicolor":
        caption_supports = explicit_multicolor or len(colors) >= 2
        if caption_supports and len(significant) >= 2:
            status = "PASS"
            confidence = 0.9
            reason = "image caption and pixel distribution both support multiple colors"
        elif caption_supports:
            status = "WARN"
            confidence = 0.5
            reason = "image caption supports multiple colors but pixels are visually dominated"
        elif len(colors) == 1 and len(significant) < 2:
            status = "FAIL"
            confidence = max(0.7, distribution.get(colors[0], 0.0))
            reason = f"image caption and pixels indicate a single {colors[0]} color"
        else:
            status = str(pixel_result["status"])
            confidence = float(pixel_result["confidence"])
            reason = str(pixel_result["reason"])
    else:
        caption_supports = expected in colors
        contradicting = [color for color in colors if color != expected]
        contradiction_ratio = max(
            (distribution.get(color, 0.0) for color in contradicting),
            default=0.0,
        )
        if caption_supports:
            status = "PASS"
            confidence = max(0.75, min(1.0, expected_ratio / 0.35))
            reason = "image-specific caption explicitly supports the expected color"
        elif contradicting and expected_ratio < 0.07 and contradiction_ratio >= 0.18:
            status = "FAIL"
            confidence = max(0.7, contradiction_ratio)
            reason = (
                "image caption and pixel evidence support "
                + "/".join(contradicting)
                + " instead"
            )
        elif contradicting:
            status = "WARN"
            confidence = 0.5
            reason = "image caption contains a different color but pixel evidence is ambiguous"
        else:
            status = str(pixel_result["status"])
            confidence = float(pixel_result["confidence"])
            reason = str(pixel_result["reason"])

    return {
        **pixel_result,
        "status": status,
        "confidence": round(confidence, 6),
        "reason": reason,
        "image_caption": caption,
        "caption_colors": colors,
        "caption_explicit_multicolor": explicit_multicolor,
    }


def audit_image(path: Path, expected: str) -> dict[str, object]:
    with Image.open(path) as source:
        source.load()
        original_size = source.size
        image = source.convert("RGB")
    image.thumbnail((128, 128), Image.Resampling.LANCZOS)
    rgb = np.asarray(image)
    background_mask, background_rgb, background_consistency = connected_background_mask(rgb)

    height, width, _ = rgb.shape
    inset = np.zeros((height, width), dtype=bool)
    margin_y = max(1, round(height * 0.04))
    margin_x = max(1, round(width * 0.04))
    inset[margin_y : height - margin_y, margin_x : width - margin_x] = True
    foreground_mask = (~background_mask) & inset
    pixels = rgb[foreground_mask]
    low_segmentation_confidence = len(pixels) < 256
    if len(pixels) < 256:
        pixels = rgb[inset]

    counts = classify_pixels(pixels)
    total = sum(counts.values())
    ratios = {label: counts[label] / total for label in COLOR_LABELS}
    ordered = sorted(ratios.items(), key=lambda item: (-item[1], item[0]))
    expected_ratio = ratios.get(expected, 0.0)
    significant = [label for label, ratio in ordered if ratio >= 0.10]
    multicolor_score = sum(ratio for _, ratio in ordered[:3]) if len(significant) >= 2 else 0.0

    if low_segmentation_confidence:
        status = "WARN"
        confidence = 0.0
        reason = "foreground cannot be separated reliably from a similar-color background"
    elif expected == "multicolor":
        if len(significant) >= 3 and multicolor_score >= 0.60:
            status = "PASS"
            confidence = min(1.0, multicolor_score)
            reason = "three or more significant visual color groups"
        elif len(significant) >= 2:
            status = "WARN"
            confidence = min(1.0, multicolor_score)
            reason = "two significant visual color groups"
        else:
            status = "FAIL"
            confidence = 1.0 - ordered[0][1]
            reason = "image is visually dominated by one color group"
    elif expected_ratio >= 0.18:
        status = "PASS"
        confidence = min(1.0, expected_ratio / 0.50)
        reason = "expected color has clear pixel evidence"
    elif expected_ratio >= 0.07:
        status = "WARN"
        confidence = min(1.0, expected_ratio / 0.18)
        reason = "expected color is present but not dominant"
    elif ordered[0][1] >= 0.42:
        status = "FAIL"
        confidence = min(1.0, ordered[0][1] - expected_ratio)
        reason = f"{ordered[0][0]} dominates and expected color evidence is weak"
    else:
        status = "WARN"
        confidence = 0.25
        reason = "no dominant color and expected color evidence is weak"

    return {
        "status": status,
        "confidence": round(confidence, 6),
        "reason": reason,
        "expected_ratio": round(expected_ratio, 6),
        "detected_colors": [
            {"color": label, "ratio": round(ratio, 6)}
            for label, ratio in ordered
            if ratio >= 0.01
        ],
        "significant_colors": significant,
        "foreground_ratio": round(float(np.mean(foreground_mask)), 6),
        "low_segmentation_confidence": low_segmentation_confidence,
        "background_rgb": background_rgb,
        "background_consistency": round(background_consistency, 6),
        "original_width": original_size[0],
        "original_height": original_size[1],
    }


def write_contact_sheets(
    rows: list[dict[str, object]], image_dir: Path, sheet_dir: Path, limit: int
) -> list[str]:
    if sheet_dir.exists():
        raise FileExistsError(sheet_dir)
    sheet_dir.mkdir(parents=True)
    font = ImageFont.load_default()
    selected = sorted(
        (row for row in rows if row["status"] != "PASS"),
        key=lambda row: (
            0 if row["status"] == "FAIL" else 1,
            float(row["expected_ratio"]),
            str(row["product_id"]),
        ),
    )[:limit]
    columns = 5
    rows_per_sheet = 5
    cell_width, cell_height = 220, 270
    per_sheet = columns * rows_per_sheet
    outputs: list[str] = []
    for page, start in enumerate(range(0, len(selected), per_sheet), 1):
        canvas = Image.new(
            "RGB",
            (columns * cell_width, rows_per_sheet * cell_height),
            "white",
        )
        draw = ImageDraw.Draw(canvas)
        for offset, row in enumerate(selected[start : start + per_sheet]):
            x = (offset % columns) * cell_width
            y = (offset // columns) * cell_height
            product_id = str(row["product_id"])
            with Image.open(image_dir / f"{product_id}.jpg") as source:
                thumbnail = ImageOps.contain(
                    source.convert("RGB"),
                    (cell_width - 12, 205),
                    Image.Resampling.LANCZOS,
                )
            image_x = x + (cell_width - thumbnail.width) // 2
            canvas.paste(thumbnail, (image_x, y + 4))
            top_colors = ", ".join(
                f"{item['color']}:{float(item['ratio']):.2f}"
                for item in list(row["detected_colors"])[:3]
            )
            lines = (
                f"{row['status']}  expected={row['expected_color']}",
                f"ratio={float(row['expected_ratio']):.3f}",
                top_colors,
                product_id,
            )
            for line_index, line in enumerate(lines):
                draw.text((x + 5, y + 210 + line_index * 14), line, fill="black", font=font)
        output = sheet_dir / f"low_confidence_{page:02d}.jpg"
        canvas.save(output, quality=90)
        outputs.append(str(output))
    return outputs


def main() -> None:
    args = parse_args()
    for output in (args.audit, args.report):
        if output.exists():
            raise FileExistsError(output)

    connection = sqlite3.connect(f"file:{args.database}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    products = connection.execute(
        """
        SELECT parent_asin, title, color_raw, color_norm, image_url
        FROM products
        WHERE parent_asin LIKE 'F200K_%'
        ORDER BY parent_asin
        """
    ).fetchall()
    connection.close()

    captions: dict[str, str] = {}
    for caption_file in args.caption_files:
        for row in (
            json.loads(line)
            for line in caption_file.read_text(encoding="utf-8").splitlines()
        ):
            product_id = str(row["product_id"])
            if product_id in captions:
                raise RuntimeError(f"duplicate caption for {product_id}")
            captions[product_id] = str(row["image_caption"])
    missing_captions = [
        str(product["parent_asin"])
        for product in products
        if str(product["parent_asin"]) not in captions
    ]
    if missing_captions:
        raise RuntimeError(f"missing captions for {missing_captions[:10]}")

    results: list[dict[str, object]] = []
    for index, product in enumerate(products, 1):
        product_id = str(product["parent_asin"])
        path = args.image_dir / f"{product_id}.jpg"
        pixel_result = audit_image(path, str(product["color_norm"]))
        result = {
            "product_id": product_id,
            "title": product["title"],
            "expected_color": product["color_norm"],
            "color_raw": product["color_raw"],
            "image_url": product["image_url"],
            **combine_caption_evidence(
                pixel_result,
                str(product["color_norm"]),
                captions[product_id],
            ),
        }
        results.append(result)
        if index % 500 == 0 or index == len(products):
            print(json.dumps({"completed": index, "total": len(products)}), flush=True)

    args.audit.parent.mkdir(parents=True, exist_ok=True)
    with args.audit.open("w", encoding="utf-8", newline="\n") as target:
        for result in results:
            target.write(json.dumps(result, ensure_ascii=False, sort_keys=True) + "\n")

    sheets = write_contact_sheets(results, args.image_dir, args.sheet_dir, args.sheet_limit)
    statuses = Counter(str(row["status"]) for row in results)
    by_color: dict[str, Counter[str]] = defaultdict(Counter)
    for row in results:
        by_color[str(row["expected_color"])][str(row["status"])] += 1
    report = {
        "method": "foreground-connected HSV evidence cross-checked with image-specific Fashion200K captions",
        "scope": "Fashion200K products currently present in products_complete.db",
        "products": len(results),
        "status_counts": dict(statuses),
        "status_rates": {
            key: round(value / len(results), 6) for key, value in statuses.items()
        },
        "by_expected_color": {
            color: dict(counts) for color, counts in sorted(by_color.items())
        },
        "audit_jsonl": str(args.audit),
        "contact_sheets": sheets,
        "interpretation": {
            "PASS": "clear automated pixel evidence",
            "WARN": "ambiguous; requires human or vision-model review",
            "FAIL": "strong contradictory pixel evidence; do not auto-delete without review",
        },
    }
    args.report.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
