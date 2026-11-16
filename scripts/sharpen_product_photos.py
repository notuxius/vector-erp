"""Local sharpening trials on versioned photo masters; never replaces a baseline.

Examples:
  python scripts/sharpen_product_photos.py --scan --run blur-candidates
  python scripts/sharpen_product_photos.py --baseline-run local-blur-baseline-v8 --run local-sharpen-pilot

The scan ranks softness candidates, not a semantic blur diagnosis. Select and
review candidates before processing. No generative reconstruction or upscaling.
"""
import argparse
import html
import json
import re
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps
from scipy import ndimage as nd

import process_product_photos as photos

SETTINGS = {
    'mild': {'radius': .85, 'amount': .8, 'threshold': 1.5, 'max_change': 10},
    'stronger': {'radius': 1.25, 'amount': 1.1, 'threshold': 1.5, 'max_change': 16},
}


def sharpen(image, settings):
    """Bounded luminance sharpening with exact alpha and geometry preservation."""
    rgba = np.asarray(image.convert('RGBA'))
    rgb = rgba[:, :, :3].astype(np.float32)
    alpha = rgba[:, :, 3]
    lum = rgb @ np.array([.2126, .7152, .0722], dtype=np.float32)
    detail = lum - nd.gaussian_filter(lum, settings['radius'])
    # Soft threshold suppresses small flat-area fluctuations rather than boosting them.
    detail = np.sign(detail) * np.maximum(np.abs(detail) - settings['threshold'], 0)
    delta = np.clip(detail * settings['amount'], -settings['max_change'], settings['max_change'])
    if np.any(alpha < 255):
        interior = nd.distance_transform_edt(np.pad(alpha >= 250, 1))[1:-1, 1:-1]
        delta *= np.clip((interior - 2) / 2, 0, 1)
    # Do not create luminance overshoot outside the existing local edge range.
    low = nd.minimum_filter(lum, 3)
    high = nd.maximum_filter(lum, 3)
    delta = np.clip(delta, low - lum, high - lum)
    adjusted = np.rint(np.clip(rgb + delta[:, :, None], 0, 255)).astype(np.uint8)
    result = np.dstack([adjusted, alpha])
    change = np.abs(adjusted.astype(np.int16) - rgb.astype(np.int16))
    return Image.fromarray(result), {
        'changed_pixels': int(np.any(change, axis=2).sum()),
        'max_channel_change': int(change.max()),
        'alpha_unchanged': bool(np.array_equal(result[:, :, 3], alpha)),
        'size_unchanged': True,
    }


def scan(root):
    manifest = json.loads((root / 'manifest.json').read_text(encoding='utf-8'))
    seen, rows = set(), []
    for product in manifest['products']:
        for asset in product['gallery']:
            path = asset['path']
            if path in seen:
                continue
            seen.add(path)
            source = photos.original_path(root, path)
            rgb = photos.read_source(source)
            alpha = photos.read_source_alpha(source)
            if alpha is not None:
                rgb = photos.composite(photos.apply_alpha(rgb, alpha), 'white')
            small = ImageOps.contain(rgb, (640, 640)).convert('L')
            gray = np.asarray(small, dtype=float)
            grad = np.hypot(nd.sobel(gray, axis=0) / 8, nd.sobel(gray, axis=1) / 8)
            edge = grad > 4
            if edge.sum() < 100:
                continue
            score = np.mean(nd.laplace(gray)[edge] ** 2) / (np.mean(grad[edge] ** 2) + 1e-6)
            rows.append({'path': path, 'name': product['name'], 'score': round(float(score), 4),
                         'size': list(rgb.size), 'edge_fraction': round(float(edge.mean()), 4)})
    return sorted(rows, key=lambda row: row['score'])


def process(root, baseline_run, run):
    records = sorted((root / 'reports' / baseline_run / 'records').rglob('*.json'))
    if not records:
        raise ValueError('No baseline records found')
    summaries = []
    for file in records:
        baseline = json.loads(file.read_text(encoding='utf-8'))
        original = photos.original_path(root, baseline['source_path'])
        master_info = baseline['outputs']['master']
        master_path = (root / master_info['path']).resolve()
        if not master_path.is_relative_to((root / 'edited').resolve()):
            raise ValueError('Baseline must be an archived edited master')
        if photos.file_sha(original) != baseline['source_sha256'] or photos.file_sha(master_path) != master_info['sha256']:
            raise ValueError('Source or baseline hash changed')
        with Image.open(master_path) as im:
            master = im.convert('RGBA')
        for name, settings in SETTINGS.items():
            recipe = {'settings': settings, 'baseline_sha256': master_info['sha256'],
                      'code_sha256': photos.file_sha(__file__), 'Pillow': photos.importlib.metadata.version('Pillow'),
                      'numpy': np.__version__, 'scipy': photos.importlib.metadata.version('scipy')}
            fingerprint = photos.sha(photos.dump(recipe).encode())
            folder = original.parent.name
            key = f'{original.stem}--{fingerprint[:16]}-{name}'
            record_path = root / 'reports' / run / 'records' / folder / (key + '.json')
            if record_path.exists():
                saved = json.loads(record_path.read_text(encoding='utf-8'))
                for info in saved['outputs'].values():
                    if photos.file_sha(root / info['path']) != info['sha256']:
                        raise ValueError('Existing trial output changed; refusing overwrite')
                summaries.append(saved)
                continue
            enhanced, metrics = sharpen(master, settings)
            assert np.array_equal(np.asarray(enhanced)[:, :, 3], np.asarray(master)[:, :, 3])
            canvas = photos.catalogue_canvas(enhanced, baseline['recipe']['preset'])
            outputs = {}
            for role, relative, image, fmt in [
                ('master', Path('edited') / run / folder / (key + '.png'), enhanced, 'PNG'),
                ('white', Path('catalogue') / run / folder / (key + '-white.webp'), photos.composite(canvas, 'white'), 'WEBP'),
                ('same_frame_white', Path('review') / run / folder / (key + '-white.png'), photos.composite(enhanced, 'white'), 'PNG'),
                ('same_frame_dark', Path('review') / run / folder / (key + '-dark.png'), photos.composite(enhanced, '#26323c'), 'PNG'),
            ]:
                target = root / relative
                if target.exists():
                    raise ValueError('Untracked trial output exists; use a new run')
                photos.save_image(target, image, format=fmt, **({'quality': 95} if fmt == 'WEBP' else {}))
                outputs[role] = {'path': relative.as_posix(), 'sha256': photos.file_sha(target)}
            record = {'source_path': baseline['source_path'], 'source_sha256': baseline['source_sha256'],
                      'baseline_run': baseline_run, 'baseline_master': master_info, 'names': baseline['names'],
                      'variant': name, 'recipe': recipe, 'metrics': metrics, 'outputs': outputs,
                      'status': 'needs-visual-review'}
            if photos.file_sha(original) != baseline['source_sha256'] or photos.file_sha(master_path) != master_info['sha256']:
                raise ValueError('Input changed during processing')
            photos.atomic_write(record_path, photos.dump(record))
            summaries.append(record)
    photos.atomic_write(root / 'reports' / run / 'sharpening-summary.json', photos.dump(summaries))
    make_review(root, baseline_run, run, summaries)
    print(f'{len(records)} baseline photos; {len(summaries)} versioned sharpening trials; originals unchanged.')


def make_review(root, baseline_run, run, summaries):
    """Comparable native-resolution images plus identical 200% center crops."""
    baseline = {r['source_path']: r for f in (root / 'reports' / baseline_run / 'records').rglob('*.json')
                for r in [json.loads(f.read_text(encoding='utf-8'))]}
    grouped = {}
    for record in summaries:
        grouped.setdefault(record['source_path'], {})[record['variant']] = record
    selection = root / 'reports' / run / 'selection.json'
    order = [x['path'] for x in json.loads(selection.read_text(encoding='utf-8'))] if selection.exists() else list(grouped)
    order = list(dict.fromkeys([x for x in order if x in grouped] + list(grouped)))
    page = ['''<!doctype html><html lang="uk"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Проба підвищення різкості</title><style>
body{font:16px system-ui;color:#20303b;background:#eaf0f4;margin:0;padding:24px}main{max-width:1800px;margin:auto}
header,article{background:white;border-radius:14px;padding:24px;margin-bottom:24px}h1{margin:0}
.grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px}.crops{grid-template-columns:repeat(3,minmax(0,1fr))}
figure{margin:0;overflow:auto}figure img{display:block;max-width:100%;height:360px;object-fit:contain;margin:auto}
figcaption{padding:8px 0;font-weight:600}.crops img{width:320px;height:320px;object-fit:contain;image-rendering:pixelated}
a{color:#086d9d}summary{cursor:pointer;padding:16px 0}.note{background:#fff3da;padding:14px;border-radius:8px}
@media(max-width:900px){.grid{grid-template-columns:repeat(2,minmax(0,1fr))}}@media(max-width:550px){.grid{grid-template-columns:1fr}}
</style><main><header><h1>Проба підвищення різкості</h1>
<p>М'які та малодетальні фото: оригінал → v8 → помірна різкість → посилена різкість. Обидва варіанти використовують спільні налаштування для всіх вибраних фото.</p>
<p>Розмір, прозорість і геометрія майстрів незмінні. Оригінали та v8 збережено. Натисніть на фото для перегляду в початковому розмірі; фрагменти нижче збільшено до 200% лише для оцінки.</p>
<p class="note">Різкість підсилює наявні краї, але не відновлює втрачені деталі. Посилений режим може зробити зерно, стиснення та світлі обводки помітнішими. Це тестові версії; профіль v8 за замовчуванням не змінено.</p>
</header>''']
    for i, source in enumerate(order, 1):
        base = baseline[source]
        variants = grouped[source]
        records = [base, variants['mild'], variants['stronger']]
        labels = ['v8 — до підвищення різкості', 'Помірна різкість', 'Посилена різкість']
        paths = [r['outputs']['same_frame_white']['path'] for r in records]
        page.append(f'<article><h2>{i}. {html.escape(base["names"][0])}</h2><p>{base["source_size"][0]} × {base["source_size"][1]} px</p><div class="grid">')
        for label, path in zip(['Оригінал'] + labels, [source] + paths):
            url = '../../' + html.escape(path, quote=True)
            page.append(f'<figure><figcaption>{label}</figcaption><a href="{url}" target="_blank"><img src="{url}" alt="{label}" loading="lazy"></a></figure>')
        page.append('</div><details open><summary>Однаковий фрагмент кожного варіанта, 200%</summary><div class="grid crops">')
        for j, (label, path) in enumerate(zip(labels, paths)):
            with Image.open(root / path) as im:
                w, h = im.size
                half = min(80, w // 2, h // 2)
                crop = im.crop((w // 2 - half, h // 2 - half, w // 2 + half, h // 2 + half))
                crop = crop.resize((half * 4, half * 4), Image.Resampling.NEAREST)
            relative = Path('review') / run / 'details' / f'{i}-{j}.png'
            photos.save_image(root / relative, crop)
            page.append(f'<figure><figcaption>{label}</figcaption><img src="../../{relative.as_posix()}" alt="Фрагмент: {label}" loading="lazy"></figure>')
        page.append('</div></details><p>')
        for name, label in [('mild', 'Помірний'), ('stronger', 'Посилений')]:
            r = variants[name]
            page.append(f'{label}: <a href="../../{r["outputs"]["master"]["path"]}">PNG</a> · <a href="../../{r["outputs"]["white"]["path"]}">WEBP</a> &nbsp; ')
        page.append('</p></article>')
    page.append('</main></html>')
    photos.atomic_write(root / 'review' / run / 'assessment.html', ''.join(page))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive', type=Path, default=photos.DEFAULT_ARCHIVE)
    parser.add_argument('--run', required=True)
    parser.add_argument('--baseline-run')
    parser.add_argument('--scan', action='store_true')
    args = parser.parse_args()
    for value in [args.run, args.baseline_run]:
        if value is not None and not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]*', value):
            parser.error('Run names must use only letters, numbers, underscores or hyphens')
    if args.scan == bool(args.baseline_run):
        parser.error('Choose --scan or --baseline-run')
    root = args.archive.resolve()
    if args.scan:
        rows = scan(root)
        photos.atomic_write(root / 'reports' / args.run / 'blur-candidates.json', photos.dump(rows))
        print(f'Ranked {len(rows)} unique gallery photos; visual confirmation required.')
    else:
        process(root, args.baseline_run, args.run)


if __name__ == '__main__':
    main()
