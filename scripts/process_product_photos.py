"""Local catalogue photo processing. No generative image API or cloud inference.

Requires explicit --image selection or --all-gallery. Originals are read-only.
Masks and versioned outputs are cached by source, model, settings and code hashes.
"""
import argparse
import hashlib
import html
import importlib.metadata
import io
import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from PIL import Image, ImageCms, ImageDraw, ImageFilter, ImageOps

PROJECT = Path(__file__).resolve().parents[1]
DEFAULT_ARCHIVE = PROJECT / "data/mtech-product-archive"
MODEL_HOME = PROJECT / "data/photo-tools/rembg"
MODELS = {"birefnet-general", "birefnet-general-lite"}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def file_sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def dump(value):
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)


def atomic_write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    temp.write_bytes(data if isinstance(data, bytes) else data.encode("utf-8"))
    for attempt in range(10):
        try:
            temp.replace(path)
            return
        except PermissionError:
            if attempt == 9:
                raise
            time.sleep(.25 * (attempt + 1))


def save_image(path, image, format="PNG", **kwargs):
    stream = io.BytesIO()
    image.save(stream, format=format, **kwargs)
    atomic_write(path, stream.getvalue())


def original_path(root, relative):
    path = (root / relative).resolve()
    if not path.is_relative_to((root / "originals").resolve()) or not path.is_file():
        raise ValueError("Input must be an existing file inside this archive's originals/")
    return path


def read_source(path):
    with Image.open(path) as source:
        source.load()
        if getattr(source, "n_frames", 1) != 1:
            raise ValueError("Animated/multipage image needs separate review")
        image = ImageOps.exif_transpose(source).convert("RGB")
        icc = source.info.get("icc_profile")
        if icc:
            image = ImageCms.profileToProfile(image, ImageCms.ImageCmsProfile(io.BytesIO(icc)),
                                              ImageCms.createProfile("sRGB"), outputMode="RGB")
        return image


def read_source_alpha(path):
    """Keep an existing cutout, including PNG palette transparency."""
    with Image.open(path) as source:
        if "A" not in source.getbands() and "transparency" not in source.info:
            return None
        alpha = ImageOps.exif_transpose(source).convert("RGBA").getchannel("A")
        return alpha if alpha.getextrema()[0] < 255 else None


def choose_background_policy(image, source_alpha, requested="cutout", has_override=False):
    """Route by image evidence only, never product IDs, names or filenames.

    Preserve an existing cutout first. Preserve an already white studio image
    when near-white neutral pixels occupy almost every border pixel and form
    a substantial border-connected region. This avoids destroying pale rims,
    glass and white-on-white details that cannot reliably be resegmented.
    """
    if requested != "auto":
        return requested, {"reason": "explicit-policy", "requested": requested}
    if has_override:
        return "cutout", {"reason": "explicit-mask-override"}
    if source_alpha is not None:
        return "cutout", {"reason": "existing-source-transparency"}
    from scipy.ndimage import label
    rgb = np.asarray(image, dtype=np.int16)
    white = (rgb.min(axis=2) >= 245) & (np.ptp(rgb, axis=2) <= 10)
    sides = [white[0], white[-1], white[:, 0], white[:, -1]]
    ratios = [float(side.mean()) for side in sides]
    evidence = {"reason": "new-cutout-required", "white_border_fractions": [round(v, 4) for v in ratios]}
    if min(ratios) < .98:
        return "cutout", evidence
    regions, _ = label(white)
    edge_labels = np.unique(np.concatenate([regions[0], regions[-1], regions[:, 0], regions[:, -1]]))
    connected = np.isin(regions, edge_labels[edge_labels != 0])
    fraction = float(connected.mean())
    evidence["connected_white_fraction"] = round(fraction, 4)
    if fraction >= .25:
        evidence["reason"] = "existing-white-background-preserved"
        return "preserve-source", evidence
    return "cutout", evidence


def assess_scene_risk(image, candidate_mask):
    """Conservative abstention, not semantic recognition of product interiors.

    A small salient cutout surrounded by textured structure may be just one
    component of the product. Retain the scene instead of guessing ownership.
    All measurements are normalized to the image, independent of product data.
    """
    preview = image.convert("L")
    preview.thumbnail((512, 512))
    rgb = np.asarray(preview, dtype=np.float32)
    alpha_image = candidate_mask.convert("L").resize(preview.size, Image.Resampling.BILINEAR)
    alpha = np.asarray(alpha_image)
    if min(rgb.shape) < 2:
        return {"preserve_scene": True, "reason": "image-too-small-to-classify", "review_required": True}
    gy, gx = np.gradient(rgb)
    textured = np.hypot(gx, gy) > 12
    # Avoid counting the product silhouette as texture in its background.
    background = np.asarray(alpha_image.filter(ImageFilter.MaxFilter(7))) < 32
    fg_fraction = float((alpha >= 128).mean())
    background_texture = float(textured[background].mean()) if background.any() else 0
    strip = max(2, round(min(rgb.shape) * .05))
    borders = [textured[:strip], textured[-strip:], textured[:, :strip], textured[:, -strip:]]
    border_texture = [float(b.mean()) for b in borders]
    busy_sides = sum(v >= .10 for v in border_texture)
    foreground_edges = [alpha[0], alpha[-1], alpha[:, 0], alpha[:, -1]]
    touched_sides = sum(float((edge >= 128).mean()) >= .15 for edge in foreground_edges)
    reason = None
    if fg_fraction >= .85 or touched_sides >= 2:
        reason = "cropped-or-frame-filling-detail"
    elif fg_fraction < .18 and background_texture >= .10 and busy_sides >= 2:
        reason = "structured-scene-around-salient-part"
    elif fg_fraction < .18 and background_texture >= .06:
        reason = "uncertain-scene-preserved-for-review"
    return {"preserve_scene": reason is not None, "reason": reason or "isolated-subject-evidence",
            "foreground_fraction": round(fg_fraction, 4),
            "discarded_region_texture_fraction": round(background_texture, 4),
            "border_texture_fractions": [round(v, 4) for v in border_texture],
            "foreground_touched_sides": touched_sides,
            "review_required": reason is not None}


def assess_annotation_risk(image, candidate_mask):
    """Detect discarded high-contrast label blocks on pale studio backgrounds.

    No OCR, product lookup or fixed text strings. Abstain from cutting the
    frame when disconnected annotation-like marks would be lost, preserving
    both the label and any fine leader lines in their original positions.
    """
    from scipy.ndimage import binary_opening, distance_transform_edt, label, find_objects
    preview = image.convert("L")
    preview.thumbnail((1280, 1280))
    gray = np.asarray(preview)
    alpha = np.asarray(candidate_mask.convert("L").resize(preview.size, Image.Resampling.BILINEAR))
    outside = alpha < 32
    pale_fraction = float((gray[outside] > 210).mean()) if outside.any() else 0
    evidence = {"preserve_frame": False, "pale_background_fraction": round(pale_fraction, 4),
                "candidate_boxes": [], "method": "discarded-annotation-blocks-v1"}
    if pale_fraction < .8:
        return evidence
    # Suppress narrow silhouette remnants and leader lines before finding blocks.
    far = distance_transform_edt(outside) > 3
    marks = binary_opening((gray < 100) & far, structure=np.ones((3, 3)))
    regions, _ = label(marks)
    height, width = gray.shape
    for number, region in enumerate(find_objects(regions), 1):
        if region is None:
            continue
        yy, xx = region
        w, h = xx.stop - xx.start, yy.stop - yy.start
        pixels = regions[region] == number
        count = int(pixels.sum())
        if (count >= 24 and w >= 8 and h >= 5 and 1.2 <= w / h <= 12
                and count / (w * h) >= .5 and count < .03 * width * height
                and 0 < xx.start and xx.stop < width and 0 < yy.start and yy.stop < height
                and float(gray[region][pixels].std()) <= 30):
            evidence["candidate_boxes"].append([xx.start, yy.start, xx.stop, yy.stop])
    evidence["preserve_frame"] = bool(evidence["candidate_boxes"])
    evidence["review_required"] = evidence["preserve_frame"]
    evidence["analysis_size"] = list(preview.size)
    return evidence


def assess_graphic_risk(image, candidate_mask):
    """Keep illustration frames containing discarded UI text or colored leaders.

    This conservative opt-in guard is shared by all description illustrations.
    It uses pixel evidence, never filenames, product IDs, or hand-drawn regions.
    """
    from scipy.ndimage import binary_opening, distance_transform_edt, label, find_objects
    preview = image.convert("RGB")
    preview.thumbnail((1280, 1280))
    rgb = np.asarray(preview, dtype=np.int16)
    gray = np.asarray(preview.convert("L"))
    alpha = np.asarray(candidate_mask.resize(preview.size, Image.Resampling.BILINEAR))
    outside = alpha < 32
    far = distance_transform_edt(outside) > 2
    regions, _ = label((gray < 140) & far)
    glyphs = []
    for number, region in enumerate(find_objects(regions), 1):
        if region is None:
            continue
        yy, xx = region
        w, h = xx.stop - xx.start, yy.stop - yy.start
        area = int((regions[region] == number).sum())
        if 1 <= w <= 30 and 2 <= h <= 18 and 2 <= area <= 180:
            glyphs.append([xx.start, yy.start, xx.stop, yy.stop])
    spread = False
    if len(glyphs) >= 25:
        boxes = np.asarray(glyphs)
        spread = (int(boxes[:, 2].max() - boxes[:, 0].min()) > .35 * preview.width
                  and int(boxes[:, 3].max() - boxes[:, 1].min()) > .25 * preview.height)
    colored = ((rgb.max(2) - rgb.min(2) > 80) & (rgb.max(2) > 140)
               & (rgb.min(2) < 140) & outside)
    horizontal = binary_opening(colored, structure=np.ones((1, max(25, int(preview.width * .12)))))
    vertical = binary_opening(colored, structure=np.ones((max(25, int(preview.height * .12)), 1)))
    lines = int((horizontal | vertical).sum())
    reason = "discarded-interface-text" if spread else "discarded-colored-annotation" if lines >= 25 else None
    return {"preserve_frame": reason is not None, "reason": reason,
            "discarded_glyph_count": len(glyphs), "discarded_colored_line_pixels": lines,
            "method": "illustration-content-guard-v1"}


def recover_colored_annotations(image, candidate_mask):
    """Separate long colored annotations from a plain pale backdrop.

    Return a mask overlay, leaving model segmentation and original RGB intact.
    A busy backdrop or excessive colored content falls back to frame preservation.
    """
    from scipy.ndimage import binary_opening, binary_dilation, label
    rgb = np.asarray(image.convert("RGB"), dtype=np.int16)
    alpha = np.asarray(candidate_mask.convert("L"))
    outside = alpha < 32
    chroma = rgb.max(2) - rgb.min(2)
    pale = (rgb.min(2) > 210) & (chroma < 30)
    fraction = float(pale[outside].mean()) if outside.any() else 0
    info = {"recovered": False, "pale_background_fraction": round(fraction, 4)}
    if fraction < .8:
        return None, {**info, "skipped": "Background is not predominantly plain pale"}
    height, width = alpha.shape
    saturated = (chroma > 80) & (rgb.max(2) > 140) & (rgb.min(2) < 140)
    discarded = saturated & outside
    horizontal = binary_opening(discarded, structure=np.ones((1, max(25, int(width * .12)))))
    vertical = binary_opening(discarded, structure=np.ones((max(25, int(height * .12)), 1)))
    seeds = horizontal | vertical
    if int(seeds.sum()) < 25:
        return None, {**info, "skipped": "No long colored annotation evidence"}
    # Include the entire connected colored stroke, not just the portion the model lost.
    regions, _ = label((chroma > 40) & (rgb.max(2) > 100), structure=np.ones((3, 3)))
    ids = np.unique(regions[seeds])
    ids = ids[ids != 0]
    strokes = np.isin(regions, ids)
    if strokes.mean() > .15:
        return None, {**info, "skipped": "Colored content exceeds annotation budget"}
    overlay = np.zeros(alpha.shape, dtype=np.uint8)
    overlay[strokes] = 255
    # Only a one-pixel colored fringe; do not grow into neutral white surfaces.
    fringe = binary_dilation(strokes) & ~strokes & (chroma > 12)
    overlay[fringe] = np.rint(np.clip(chroma[fringe] / 40, 0, 1) * 255).astype(np.uint8)
    return Image.fromarray(overlay), {**info, "recovered": True,
                                    "stroke_pixels": int(strokes.sum()),
                                    "fringe_pixels": int(fringe.sum())}


def validate_preset(p):
    if p.get("background_policy", "cutout") not in {"cutout", "preserve-source", "auto"}:
        raise ValueError("Unknown background policy")
    if p["model"] not in MODELS:
        raise ValueError("Only the explicitly supported local models are allowed")
    if not 0 <= p["mask_black_point"] < p["mask_white_point"] <= 255:
        raise ValueError("Invalid mask endpoints")
    for key, low, high in [("max_exposure_ev", 0, .5), ("shadow_lift", 0, .2),
                           ("contrast", .9, 1.1), ("sharpen_percent", 0, 100),
                           ("sharpen_radius", 0, 2), ("margin_fraction", 0, .3)]:
        if not low <= p[key] <= high:
            raise ValueError(f"{key} outside conservative bounds {low}..{high}")
    if len(p["white_balance_rgb"]) != 3 or not all(.8 <= x <= 1.2 for x in p["white_balance_rgb"]):
        raise ValueError("White balance requires three restrained channel gains")
    if len(p["canvas"]) != 2 or not all(isinstance(x, int) and 128 <= x <= 4096 for x in p["canvas"]):
        raise ValueError("Invalid output canvas")


def refine_mask(raw, p, edits=None):
    values = np.asarray(raw.convert("L"), dtype=np.float32)
    values = np.clip((values - p["mask_black_point"]) * 255 /
                     (p["mask_white_point"] - p["mask_black_point"]), 0, 255)
    mask = Image.fromarray(np.rint(values).astype(np.uint8))
    if edits:
        if edits.get("size") != list(raw.size):
            raise ValueError("Mask-edit coordinates do not match oriented source dimensions")
        # Polygon edits operate only on alpha. They never paint or synthesize RGB.
        scale = 4
        high = mask.resize((mask.width * scale, mask.height * scale), Image.Resampling.NEAREST)
        painter = ImageDraw.Draw(high)
        for op in edits.get("polygons", []):
            if op.get("action") not in {"include", "exclude"} or len(op.get("points", [])) < 3:
                raise ValueError("Invalid polygon edit")
            points = []
            for x, y in op["points"]:
                if not 0 <= x <= mask.width or not 0 <= y <= mask.height:
                    raise ValueError("Polygon point outside image")
                points.append((round(x * scale), round(y * scale)))
            painter.polygon(points, fill=255 if op["action"] == "include" else 0)
        edited = high.resize(mask.size, Image.Resampling.LANCZOS)
        # Only update polygon neighbourhoods; don't resample untouched mask edges.
        region = Image.new("L", high.size, 0)
        draw = ImageDraw.Draw(region)
        for op in edits.get("polygons", []):
            draw.polygon([(round(x * scale), round(y * scale)) for x, y in op["points"]], fill=255)
        region = region.resize(mask.size, Image.Resampling.LANCZOS).filter(ImageFilter.MaxFilter(3))
        mask = Image.composite(edited, mask, region)
    return mask


def recover_enclosed_surfaces(source, mask, settings):
    """Recover small enclosed pale surfaces supported by original image detail.

    Unlike blanket hole filling, require light material continuity at the rim
    AND dark interior detail away from the boundary. Plain gaps and dark cable
    loops are left open. This heuristic needs visual review, not auto-approval.
    """
    if not settings or not settings.get("enabled", False):
        return mask, {"enabled": False}
    from scipy.ndimage import binary_fill_holes, label, find_objects, binary_dilation, binary_erosion
    a = np.asarray(mask)
    foreground = a >= 128
    filled = binary_fill_holes(foreground)
    regions, _ = label(filled & ~foreground)
    # Protect the outer silhouette while repairing the soft inner fringe.
    safe_inner_fringe = binary_erosion(filled, iterations=2) & (a > 0)
    counts = np.bincount(regions.ravel())
    rgb = np.asarray(source, dtype=np.float32)
    lightness = rgb.mean(axis=2)
    pale = (lightness > 160) & (np.ptp(rgb, axis=2) < 40)
    revised = a.copy()
    info = {"enabled": True, "method": "enclosed-pale-surface-with-detail-v1",
            "restored_pixels": 0, "restored_regions": [], "review_required": True}
    for number, box in enumerate(find_objects(regions), 1):
        if box is None or not 20 <= counts[number] <= a.size * .003:
            continue
        yy, xx = box
        region_box = (slice(max(0, yy.start - 4), min(a.shape[0], yy.stop + 4)),
                      slice(max(0, xx.start - 4), min(a.shape[1], xx.stop + 4)))
        hole = regions[region_box] == number
        ring = binary_dilation(hole, iterations=3) & foreground[region_box]
        interior = binary_erosion(hole, iterations=3)
        local_pale = pale[region_box]
        pale_fraction = float(local_pale[hole].mean())
        pale_ring_fraction = float(local_pale[ring].mean()) if ring.any() else 0
        detail_pixels = int(((lightness[region_box] < 100) & interior).sum())
        if pale_fraction < .75 or pale_ring_fraction < .25 or detail_pixels < 6:
            continue
        hole_colour = np.median(rgb[region_box][hole & local_pale], axis=0)
        ring_colour = np.median(rgb[region_box][ring & local_pale], axis=0)
        difference = float(np.max(np.abs(hole_colour - ring_colour)))
        if difference > 35:
            continue
        recovery = hole | (binary_dilation(hole, iterations=3) & safe_inner_fringe[region_box])
        revised[region_box][recovery] = 255
        info["restored_regions"].append({"bounds": [xx.start, yy.start, xx.stop, yy.stop],
            "area": int(counts[number]), "pale_fraction": round(pale_fraction, 4),
            "pale_ring_fraction": round(pale_ring_fraction, 4),
            "interior_detail_pixels": detail_pixels, "rim_colour_difference": round(difference, 2)})
    changed = int(np.count_nonzero(revised != a))
    if changed > .01 * max(int(foreground.sum()), 1):
        return mask, {**info, "skipped": "Would restore over 1% of foreground"}
    info["restored_pixels"] = changed
    return Image.fromarray(revised), info


def cleanup_edges(source, mask, settings):
    """Conservative colour-guided alpha trimming; never changes source RGB.

    Only a narrow silhouette band is eligible. Stable nearby foreground and
    background colours must explain a pixel as a mixture. Thin parts lacking
    a nearby solid interior and ambiguous/low-contrast edges are left alone.
    This is a heuristic, not a guarantee that all product details are retained.
    """
    if not settings or not settings.get("enabled", False):
        return mask, {"enabled": False}
    from scipy.ndimage import distance_transform_edt, uniform_filter, gaussian_filter
    band = float(settings.get("band_pixels", 2.5))
    if not .5 <= band <= 3:
        raise ValueError("Edge cleanup band must be between 0.5 and 3 pixels")
    a = np.asarray(mask, dtype=np.float32) / 255
    solid = a >= .5
    info = {"enabled": True, "band_pixels": band, "changed_pixels": 0,
            "review_required": True, "method": "neutral-light-panel-alpha-trim-v1"}
    if not solid.any() or solid.all():
        return mask, {**info, "skipped": "No usable foreground/background separation"}
    inside = distance_transform_edt(np.pad(solid, 1))[1:-1, 1:-1]
    outside = distance_transform_edt(~solid)
    core = (inside > band + 3.5) & (a >= .98)
    backdrop = outside > 2
    if not core.any() or not backdrop.any():
        return mask, {**info, "skipped": "No reliable interior/background samples"}
    core_distance, fi = distance_transform_edt(~core, return_indices=True)
    _, bi = distance_transform_edt(~backdrop, return_indices=True)
    rgb = np.asarray(source, dtype=np.float32)
    fg, bg = rgb[tuple(fi)], rgb[tuple(bi)]
    # Reject colour changes in the reference areas (texture, labels, reflections).
    mean = uniform_filter(rgb, size=(3, 3, 1), mode="nearest")
    variance = np.maximum(0, uniform_filter(rgb * rgb, size=(3, 3, 1), mode="nearest") - mean * mean)
    deviation = np.sqrt(variance).max(axis=2)
    direction = fg - bg
    separation = np.sum(direction * direction, axis=2)
    mixture = np.sum((rgb - bg) * direction, axis=2) / np.maximum(separation, 1)
    residual = np.sqrt(np.mean((rgb - (bg + mixture[..., None] * direction)) ** 2, axis=2))
    fg_light = fg @ np.array([.2126, .7152, .0722], dtype=np.float32)
    bg_light = bg @ np.array([.2126, .7152, .0722], dtype=np.float32)
    # Start with the reliable case: neutral light panels against darker scenery.
    # Dark trim, saturated parts and reflections need different evidence; abstain.
    light_panel = (fg_light >= 140) & (fg_light - bg_light >= 60) & (np.ptp(fg, axis=2) <= 35)
    eligible = ((a > 0) & (inside <= band) & (outside <= 1.5)
                & (core_distance <= band + 5) & (separation >= 80 ** 2)
                & light_panel & (deviation[tuple(fi)] <= 8) & (deviation[tuple(bi)] <= 20)
                & (residual <= 10) & (mixture >= -.1) & (mixture < .98))
    # Mixed source pixels already contain backdrop RGB. Prefer a slight inset
    # over recolouring them; keep confident foreground pixels fully intact.
    coverage = np.clip((mixture - .65) / .33, 0, 1)
    coverage = coverage * coverage * (3 - 2 * coverage)
    revised = np.where(eligible, np.minimum(a, coverage), a)
    # Supress pixel-to-pixel threshold steps without expanding the silhouette.
    softened = gaussian_filter(revised, sigma=.45)
    affected = gaussian_filter(eligible.astype(np.float32), sigma=.45) > .01
    revised = np.where(affected & (inside <= band), np.minimum(a, softened), a)
    removed_fraction = float((a - revised).sum() / max(a.sum(), 1))
    info.update(candidate_pixels=int(eligible.sum()), removed_alpha_fraction=round(removed_fraction, 6))
    if removed_fraction > .01:
        return mask, {**info, "skipped": "Would remove over 1% of foreground; review manually"}
    result = np.rint(revised * 255).astype(np.uint8)
    info["changed_pixels"] = int(np.count_nonzero(result != np.asarray(mask)))
    return Image.fromarray(result), info


def reduce_edge_spill(source, mask, settings):
    """Reduce bright neutral spill near a stable saturated material edge.

    Alpha/geometry are untouched. No correction of neutral panels, glass,
    thin parts without a solid core, or pixels far from the silhouette.
    Limited RGB interpolation is a retouch, not reconstruction of lost detail.
    """
    if not settings or not settings.get("enabled", False):
        return source, {"enabled": False}
    from scipy.ndimage import distance_transform_edt, uniform_filter, gaussian_filter
    rgb = np.asarray(source, dtype=np.float32)
    a = np.asarray(mask)
    solid = a >= 128
    info = {"enabled": True, "method": "bounded-saturated-edge-spill-v1", "changed_pixels": 0,
            "alpha_changed": False, "review_required": True}
    if not solid.any() or solid.all():
        return source, info
    inside = distance_transform_edt(np.pad(solid, 1))[1:-1, 1:-1]
    outside = distance_transform_edt(~solid)
    core = (inside > 5) & (a >= 250)
    background = outside > 3
    if not core.any() or not background.any():
        return source, info
    core_distance, fi = distance_transform_edt(~core, return_indices=True)
    _, bi = distance_transform_edt(~background, return_indices=True)
    fg, bg = rgb[tuple(fi)], rgb[tuple(bi)]
    mean = uniform_filter(rgb, size=(3, 3, 1), mode="nearest")
    std = np.sqrt(np.maximum(0, uniform_filter(rgb * rgb, size=(3, 3, 1), mode="nearest") - mean * mean)).max(axis=2)
    sat = np.ptp(rgb, axis=2) / np.maximum(rgb.max(axis=2), 1)
    fg_sat = np.ptp(fg, axis=2) / np.maximum(fg.max(axis=2), 1)
    weights = np.array([.2126, .7152, .0722], dtype=np.float32)
    light, fg_light, bg_light = rgb @ weights, fg @ weights, bg @ weights
    direction = bg - fg
    mix = np.clip(np.sum((rgb - fg) * direction, axis=2) / np.maximum(np.sum(direction * direction, axis=2), 1), 0, 1)
    residual = np.sqrt(np.mean((rgb - (fg + mix[..., None] * direction)) ** 2, axis=2))
    eligible = ((a >= 32) & (inside <= 4) & (outside <= 1)
                & (core_distance <= 7) & (std[tuple(fi)] <= 25)
                & (fg_sat >= .4) & (np.ptp(fg, axis=2) >= 35)
                & (fg_sat - sat >= .12) & (light - fg_light >= 25)
                & (bg_light >= 170) & (bg_light - fg_light >= 60) & (residual <= 40))
    strength = .65 * np.clip((5 - inside) / 4, 0, 1)
    correction = np.clip((rgb - fg) * strength[..., None], -64, 64)
    correction *= eligible[..., None]
    # Feather the correction, not alpha, to avoid visibly mottled decisions.
    correction = gaussian_filter(correction, sigma=(.8, .8, 0))
    support = (a >= 32) & (inside <= 4) & (outside <= 1) & (fg_sat >= .4)
    revised = np.where(support[..., None], rgb - correction, rgb)
    result = np.rint(np.clip(revised, 0, 255)).astype(np.uint8)
    changed = np.any(result != np.asarray(source), axis=2)
    fraction = float(changed.sum() / max(int(solid.sum()), 1))
    info.update(candidate_pixels=int(eligible.sum()), changed_foreground_fraction=round(fraction, 6))
    if fraction > .01:
        return source, {**info, "skipped": "Would retouch over 1% of foreground"}
    info["changed_pixels"] = int(changed.sum())
    info["max_channel_change"] = int(np.abs(result.astype(np.int16) - np.asarray(source).astype(np.int16)).max())
    return Image.fromarray(result), info


def correct_tones(source, mask, p):
    rgb = np.asarray(source, dtype=np.float32) / 255
    selected = np.asarray(mask) >= 245
    if selected.sum() < 100:
        raise ValueError("Insufficient foreground; mask requires review")
    rgb *= np.array(p["white_balance_rgb"], dtype=np.float32)
    luminance = rgb @ np.array([.2126, .7152, .0722], dtype=np.float32)
    high = float(np.percentile(luminance[selected], 95))
    ev = float(np.clip(np.log2(max(p["target_highlight"] / 255, .01) / max(high, .01)),
                       -p["max_exposure_ev"], p["max_exposure_ev"])) if p["auto_exposure"] else 0.0
    rgb *= 2 ** ev
    # Lift shadows gently using a common multiplier, preserving channel ratios.
    light = np.clip(rgb @ np.array([.2126, .7152, .0722], dtype=np.float32), 0, 1)
    rgb *= (1 + p["shadow_lift"] * (1 - light) ** 2)[..., None]
    rgb = (rgb - .5) * p["contrast"] + .5
    corrected = Image.fromarray(np.rint(np.clip(rgb, 0, 1) * 255).astype(np.uint8))
    if p["sharpen_percent"]:
        sharpened = corrected.filter(ImageFilter.UnsharpMask(radius=p["sharpen_radius"],
                    percent=p["sharpen_percent"], threshold=p["sharpen_threshold"]))
        # Keep sharpening away from the cutout boundary to avoid amplifying backdrop colors.
        interior = mask.point(lambda value: 255 if value >= 250 else 0).filter(ImageFilter.MinFilter(5))
        corrected = Image.composite(sharpened, corrected, interior)
    return corrected, {"exposure_ev": round(ev, 5), "source_foreground_p95": round(high * 255, 2),
                        "white_balance_rgb": p["white_balance_rgb"], "white_balance_mode": "explicit_gains_no_automatic_guess"}


def apply_alpha(rgb, mask):
    if rgb.size != mask.size:
        raise ValueError("RGB/mask size mismatch")
    cutout = rgb.convert("RGBA")
    cutout.putalpha(mask)
    return cutout


def composite(cutout, color):
    backdrop = Image.new("RGBA", cutout.size, color)
    backdrop.alpha_composite(cutout)
    return backdrop.convert("RGB")


def catalogue_canvas(cutout, p):
    bounds = cutout.getchannel("A").getbbox()
    if not bounds:
        raise ValueError("Empty mask")
    crop = cutout.crop(bounds)
    width, height = p["canvas"]
    margin = p["margin_fraction"]
    scale = min(width * (1 - 2 * margin) / crop.width, height * (1 - 2 * margin) / crop.height)
    if not p["allow_upscale"]:
        scale = min(1.0, scale)
    if scale != 1:
        # Premultiplied-alpha resize prevents dark/colored fringes.
        crop = crop.convert("RGBa").resize((max(1, round(crop.width * scale)), max(1, round(crop.height * scale))),
                                          Image.Resampling.LANCZOS).convert("RGBA")
    canvas = Image.new("RGBA", (width, height), (255, 255, 255, 0))
    canvas.alpha_composite(crop, ((width - crop.width) // 2, (height - crop.height) // 2))
    return canvas


def quality_notes(mask, original):
    a = np.asarray(mask)
    foreground = a > 127
    fraction = float(foreground.mean())
    notes = ["Visual review required: heuristics cannot confirm that every product part is retained."]
    if fraction < .05 or fraction > .95:
        notes.append("Suspicious foreground coverage")
    if any((edge > 127).any() for edge in [a[0], a[-1], a[:, 0], a[:, -1]]):
        notes.append("Foreground touches a source border")
    if min(original.size) < 600:
        notes.append("Source short edge below 600 px")
    return {"foreground_fraction": round(fraction, 4),
            "partially_transparent_fraction": round(float(((a > 0) & (a < 255)).mean()), 4),
            "bounds": mask.getbbox(), "notes": notes}


class MaskEngine:
    def __init__(self, model, device):
        os.environ["REMBG_HOME"] = str(MODEL_HOME)
        # Keep model downloads within the project's isolated tool data.
        os.environ.pop("U2NET_HOME", None)
        from rembg.sessions import sessions_class
        self.model = model
        self.device = device
        self.cls = next(c for c in sessions_class if c.name() == model)
        self.path = Path(self.cls.download_models())
        self.model_sha = file_sha(self.path)
        self.session = None
        self.providers = []

    def predict(self, image):
        if self.session is None:
            import onnxruntime as ort
            from rembg import new_session
            opts = ort.SessionOptions()
            opts.intra_op_num_threads = 4
            opts.inter_op_num_threads = 1
            providers = ["CPUExecutionProvider"]
            if self.device != "cpu":
                ort.preload_dlls(directory="")
                providers.insert(0, ("CUDAExecutionProvider", {"cudnn_conv_algo_search": "HEURISTIC",
                                                               "cudnn_conv_use_max_workspace": "0"}))
            self.session = new_session(self.model, sess_opts=opts, providers=providers)
            self.providers = self.session.inner_session.get_providers()
            if self.device == "cuda" and "CUDAExecutionProvider" not in self.providers:
                raise RuntimeError("CUDA unavailable; use --device cpu explicitly or repair the local GPU environment")
            print("Active inference providers: " + ", ".join(self.providers), flush=True)
        return self.session.predict(image)[0].convert("L")


def process_one(root, entry, preset, engine, run, edits, mask_override, versions):
    started = time.perf_counter()
    source_path = original_path(root, entry["path"])
    source_sha = file_sha(source_path)
    if source_sha != entry["sha256"]:
        raise ValueError("Original differs from extraction manifest; source was not processed")
    signature = {"source_sha256": source_sha, "model_sha256": engine.model_sha,
                 "preset": preset, "versions": versions, "code_sha256": file_sha(Path(__file__)),
                 "mask_edits": edits, "override_sha256": file_sha(mask_override) if mask_override else None}
    fingerprint = sha(dump(signature).encode())
    relative = Path(entry["path"]).relative_to("originals")
    key = relative.stem + "--" + fingerprint[:16]
    folder = relative.parent
    record_path = root / "reports" / run / "records" / folder / (key + ".json")
    if record_path.exists():
        record = json.loads(record_path.read_text(encoding="utf-8"))
        for artifact in record["outputs"].values():
            path = root / artifact["path"]
            if not path.is_file() or file_sha(path) != artifact["sha256"]:
                raise RuntimeError("Existing output was changed or removed. Use a new --run; never overwrite an edited result.")
        print("SKIP verified existing result: " + entry["path"], flush=True)
        return record, True
    image = read_source(source_path)
    source_alpha = read_source_alpha(source_path)
    background_policy, routing = choose_background_policy(
        image, source_alpha, preset.get("background_policy", "cutout"), bool(edits or mask_override))
    preserve_source = background_policy == "preserve-source"
    if preserve_source and (edits or mask_override):
        raise ValueError("Preserve-source policy cannot be combined with mask overrides or edits")
    cache_id = sha(dump({"source": source_sha, "model": engine.model_sha,
                        "rembg": versions["rembg"], "pillow": versions["Pillow"]}).encode())
    raw_path = root / "masks" / "_cache" / (cache_id + ".png")
    raw_meta = raw_path.with_suffix(".json")
    mask_seconds = 0.0
    providers = []
    if preserve_source:
        raw = source_alpha if source_alpha is not None else Image.new("L", image.size, 255)
        mask_origin = "preserved-source-background"
    elif mask_override:
        with Image.open(mask_override) as supplied:
            raw = supplied.convert("L")
        mask_origin = "explicit-mask-override"
    elif source_alpha is not None:
        raw = source_alpha
        mask_origin = "existing-source-transparency"
    elif raw_path.exists() and raw_meta.exists():
        meta = json.loads(raw_meta.read_text(encoding="utf-8"))
        if file_sha(raw_path) != meta["sha256"]:
            raise ValueError("Cached raw mask was modified; remove the cache entry or supply it as an explicit override")
        with Image.open(raw_path) as cached:
            raw = cached.convert("L")
        providers = meta["providers"]
        mask_origin = "cached-local-inference"
    else:
        inference_started = time.perf_counter()
        raw = engine.predict(image)
        mask_seconds = time.perf_counter() - inference_started
        save_image(raw_path, raw)
        providers = engine.providers
        atomic_write(raw_meta, dump({"sha256": file_sha(raw_path), "model_sha256": engine.model_sha,
                                    "source_sha256": source_sha, "providers": providers}))
        mask_origin = "local-inference"
    if raw.size != image.size:
        raise ValueError("Mask dimensions differ from oriented source")
    if edits and edits.get("source_sha256") != source_sha:
        raise ValueError("Mask edits belong to a different original")
    annotation_overlay = None
    if (preset.get("graphic_guard", {}).get("enabled", False)
            and not preserve_source and source_alpha is None and not (edits or mask_override)):
        graphic = assess_graphic_risk(image, raw)
        routing["graphic_guard"] = graphic
        if (graphic["reason"] == "discarded-colored-annotation"
                and preset.get("graphic_guard", {}).get("recover_colored_annotations", False)):
            annotation_overlay, recovery = recover_colored_annotations(image, raw)
            graphic["annotation_recovery"] = recovery
            if recovery["recovered"]:
                graphic["preserve_frame"] = False
                routing["reason"] = "cutout-with-colored-annotations"
        if graphic["preserve_frame"]:
            preserve_source = True
            background_policy = "preserve-source"
            routing["reason"] = "illustration-frame-preserved"
            routing["candidate_mask_origin"] = mask_origin
            mask_origin = "preserved-illustration-frame"
            raw = Image.new("L", image.size, 255)
    if (preset.get("annotation_guard", {}).get("enabled", False)
            and not preserve_source and source_alpha is None and not (edits or mask_override)):
        annotation = assess_annotation_risk(image, raw)
        routing["annotation_guard"] = annotation
        if annotation["preserve_frame"]:
            preserve_source = True
            background_policy = "preserve-source"
            routing["reason"] = "technical-annotation-frame-preserved"
            routing["candidate_mask_origin"] = mask_origin
            mask_origin = "preserved-annotation-frame"
            raw = Image.new("L", image.size, 255)
    if (preset.get("scene_guard", {}).get("enabled", False)
            and not preserve_source and source_alpha is None and not (edits or mask_override)):
        scene = assess_scene_risk(image, raw)
        routing["scene_guard"] = scene
        if scene["preserve_scene"]:
            preserve_source = True
            background_policy = "preserve-source"
            routing["reason"] = "scene-or-detail-preserved"
            routing["candidate_mask_origin"] = mask_origin
            mask_origin = "preserved-complete-scene"
            raw = Image.new("L", image.size, 255)
    surface_recovery = {"enabled": False, "skipped": "Preserved or explicitly supplied mask"}
    if preserve_source:
        mask = raw
        edge_cleanup = {"enabled": False, "skipped": "Source background and pixels preserved"}
    elif mask_origin == "existing-source-transparency":
        mask = refine_mask(raw, {**preset, "mask_black_point": 0, "mask_white_point": 255}, edits)
        edge_cleanup = {"enabled": False, "skipped": "Preserved existing source transparency"}
    else:
        mask = refine_mask(raw, preset, edits)
        if not (mask_override or edits):
            mask, surface_recovery = recover_enclosed_surfaces(image, mask, preset.get("surface_recovery"))
        mask, edge_cleanup = cleanup_edges(image, mask, preset.get("edge_cleanup"))
    if annotation_overlay is not None and not preserve_source:
        mask = Image.fromarray(np.maximum(np.asarray(mask), np.asarray(annotation_overlay)))
    if preserve_source:
        corrected, tone = image.copy(), {"mode": "source-pixels-preserved", "exposure_ev": 0.0}
        edge_spill = {"enabled": False, "skipped": "Preserved full source frame"}
    else:
        if source_alpha is not None or mask_override or edits:
            retouched, edge_spill = image, {"enabled": False, "skipped": "Existing or explicitly supplied cutout"}
        else:
            retouched, edge_spill = reduce_edge_spill(image, mask, preset.get("edge_spill"))
        corrected, tone = correct_tones(retouched, mask, preset)
    cutout = apply_alpha(corrected, mask)
    canvas = catalogue_canvas(cutout, preset)
    outputs = {}
    generated = {
        "mask": (Path("masks") / run / folder / (key + ".png"), mask, "PNG", {}),
        "master": (Path("edited") / run / folder / (key + ".png"), cutout, "PNG", {}),
        "white": (Path("catalogue") / run / folder / (key + "-white.webp"), composite(canvas, "white"), "WEBP", {"quality": 95}),
        "same_frame_white": (Path("review") / run / folder / (key + "-white.png"), composite(cutout, "white"), "PNG", {}),
        "same_frame_dark": (Path("review") / run / folder / (key + "-dark.png"), composite(cutout, "#26323c"), "PNG", {}),
    }
    for role, (relative_path, output, fmt, kwargs) in generated.items():
        target = root / relative_path
        if target.exists():
            raise RuntimeError("Output already exists without a complete record; use a new --run to preserve partial work")
        save_image(target, output, fmt, **kwargs)
        outputs[role] = {"path": relative_path.as_posix(), "sha256": file_sha(target), "size": list(output.size)}
    quality = quality_notes(mask, image)
    if preserve_source:
        quality["notes"] = ["Source background retained intentionally; no new transparent cutout.",
                            "Visual review required for background suitability and framing."]
        if min(image.size) < 600:
            quality["notes"].append("Source short edge below 600 px")
    record = {"schema_version": 1, "created_at": datetime.now(timezone.utc).isoformat(),
              "status": "needs-visual-review", "source_path": entry["path"], "source_sha256": source_sha,
              "source_size": list(image.size), "product_ids": entry["product_ids"], "names": entry["names"],
              "fingerprint": fingerprint, "recipe": signature, "mask_origin": mask_origin,
              "mask_inference_seconds": round(mask_seconds, 3), "providers": providers,
              "tone_adjustments": tone, "quality": quality, "outputs": outputs,
              "edge_cleanup": edge_cleanup,
              "edge_spill": edge_spill,
              "surface_recovery": surface_recovery,
              "background_policy": background_policy, "background_routing": routing,
              "elapsed_seconds": round(time.perf_counter() - started, 3)}
    if file_sha(source_path) != source_sha:
        raise RuntimeError("Original changed during processing")
    atomic_write(record_path, dump(record))
    print(dump({"source": entry["path"], "mask": mask_origin, "seconds": record["elapsed_seconds"],
                "exposure_ev": tone["exposure_ev"], "master": outputs["master"]["path"]}), flush=True)
    return record, False


def make_review(root, run):
    records = [json.loads(p.read_text(encoding="utf-8")) for p in sorted((root / "reports" / run / "records").rglob("*.json"))]
    esc = lambda value: html.escape(str(value), quote=True)
    cards = []
    for record in records:
        figure = lambda label, path, cls="": f'<figure><figcaption>{esc(label)}</figcaption><a href="../../{esc(path)}" target="_blank"><img class="{cls}" src="../../{esc(path)}" alt="{esc(label)}" loading="lazy"></a></figure>'
        pictures = figure("Оригінал", record["source_path"])
        pictures += figure("Обробка · те саме кадрування", record["outputs"]["same_frame_white"]["path"])
        preserved = record.get("background_policy") == "preserve-source"
        pictures += figure("Збережене тло оригіналу · без нової вирізки" if preserved else "Перевірка на темному тлі", record["outputs"]["same_frame_dark"]["path"])
        pictures += figure("Непрозорий кадр · вихідну сцену збережено повністю" if preserved else "Маска: біле — товар, чорне — тло", record["outputs"]["mask"]["path"])
        pictures += figure("Картка каталогу · 4:3", record["outputs"]["white"]["path"])
        master_label = "Відкрити PNG зі збереженим тлом" if preserved else "Відкрити PNG із прозорістю"
        cards.append(f'<article><h2>{esc(record["names"][0])}</h2><p>Локальна обробка · потрібна візуальна перевірка · {esc(record["fingerprint"][:16])}</p><div class="grid">{pictures}</div><p><a href="../../{esc(record["outputs"]["master"]["path"])}">{master_label}</a></p></article>')
    page = '''<!doctype html><html lang="uk"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>MTech — локальна обробка фото</title><style>*{box-sizing:border-box}body{margin:0;background:#eef1f3;color:#1c2d36;font:16px system-ui}main{max-width:1480px;padding:28px;margin:auto}h1{font-size:32px}h2{font-size:23px}p{line-height:1.6}article{background:white;border:1px solid #d5dfe4;padding:22px;border-radius:12px;margin:24px 0}.grid{display:grid;grid-template-columns:1fr 1fr;gap:16px}figure{margin:0;border:1px solid #d5dfe4;border-radius:8px;overflow:hidden}figcaption{padding:12px;font-weight:650}img{display:block;width:100%;height:auto}a{color:#006957}a:focus-visible{outline:3px solid #d8a400;outline-offset:2px}@media(max-width:700px){main{padding:14px}.grid{grid-template-columns:1fr}article{padding:12px}}</style><main><h1>Локальна обробка фото</h1><p>Оригінальні пікселі товару, маска тла та помірні корекції. Без генеративного перемальовування. Перевірте тонкі деталі, кабельні ланцюги, екран, опори й краї перед застосуванням до каталогу.</p>'''
    atomic_write(root / "review" / run / "index.html", page + ''.join(cards) + '</main></html>')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, default=DEFAULT_ARCHIVE)
    choice = parser.add_mutually_exclusive_group(required=True)
    choice.add_argument("--image", action="append", help="Exact archive-relative original path; repeat to select several")
    choice.add_argument("--all-gallery", action="store_true", help="Explicitly process unique gallery images; review results before use")
    parser.add_argument("--preset", type=Path, default=PROJECT / "scripts/photo_presets/catalogue-v8.json")
    parser.add_argument("--run", default="local-v1", help="Separate output batch name")
    parser.add_argument("--device", choices=["cuda", "cpu", "auto"], default="cuda")
    parser.add_argument("--mask-edits", type=Path, help="Source-hash-bound polygon edits for a single selected photo")
    parser.add_argument("--mask-override", type=Path, help="Native-size grayscale alpha for a single selected photo")
    args = parser.parse_args()
    if not re.fullmatch(r"local-[a-z0-9][a-z0-9-]*", args.run):
        parser.error("Run name must start local- and contain only lowercase letters, digits and hyphens")
    if (args.mask_edits or args.mask_override) and (args.all_gallery or len(args.image or []) != 1):
        parser.error("Mask overrides/edits require exactly one --image")
    root = args.archive.resolve()
    products = json.loads((root / "manifest.json").read_text(encoding="utf-8"))["products"]
    known, gallery_paths = {}, set()
    for p in products:
        for a in p["gallery"] + p["description_images"]:
            entry = known.setdefault(a["path"], {"path": a["path"], "sha256": a["sha256"], "product_ids": [], "names": []})
            if p["source_product_id"] not in entry["product_ids"]:
                entry["product_ids"].append(p["source_product_id"])
                entry["names"].append(p["name"])
            if a["role"] == "gallery":
                gallery_paths.add(a["path"])
    selected = sorted(gallery_paths) if args.all_gallery else list(dict.fromkeys(args.image))
    for path in selected:
        if path not in known:
            parser.error("Image is absent from the extraction manifest: " + path)
        original_path(root, path)
    preset = json.loads(args.preset.read_text(encoding="utf-8"))
    validate_preset(preset)
    versions = {p: importlib.metadata.version(p) for p in ["rembg", "onnxruntime-gpu", "Pillow", "numpy", "scipy"]}
    edits = json.loads(args.mask_edits.read_text(encoding="utf-8")) if args.mask_edits else None
    print(f"Selected {len(selected)} unique image(s); local model {preset['model']}", flush=True)
    engine = MaskEngine(preset["model"], args.device)
    result = {"processed": 0, "skipped": 0, "errors": []}
    for path in selected:
        try:
            _, skipped = process_one(root, known[path], preset, engine, args.run, edits, args.mask_override, versions)
            result["skipped" if skipped else "processed"] += 1
        except Exception as exc:
            result["errors"].append({"source": path, "error": str(exc)})
            print("FAILED: " + str(exc), flush=True)
        atomic_write(root / "reports" / args.run / "last-run.json", dump(result))
    make_review(root, args.run)
    print(dump(result), flush=True)
    return bool(result["errors"])


if __name__ == "__main__":
    raise SystemExit(main())
