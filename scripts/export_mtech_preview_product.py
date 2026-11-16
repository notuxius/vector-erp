"""Export one archived product and verified v8/mild photos into the static preview."""
import argparse
import html
import json
import re
import shutil
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse

from PIL import Image
import process_product_photos as photos


class DescriptionHTML(HTMLParser):
    allowed = {'div', 'p', 'span', 'strong', 'b', 'em', 'i', 'u', 'h2', 'h3', 'h4',
               'ul', 'ol', 'li', 'table', 'thead', 'tbody', 'tr', 'th', 'td', 'br', 'a', 'img'}
    blocked = {'script', 'style', 'iframe', 'object', 'svg'}

    def __init__(self, images):
        super().__init__(convert_charrefs=True)
        self.images, self.output, self.blocked_depth = images, [], 0

    def handle_starttag(self, tag, attrs):
        if tag in self.blocked:
            self.blocked_depth += 1
        if self.blocked_depth or tag not in self.allowed:
            return
        attrs = dict(attrs)
        safe = ''
        if tag == 'img':
            path = self.images.get(attrs.get('src'))
            if not path:
                raise ValueError('Description image is not in the local archive')
            safe = f' src="{html.escape(path, quote=True)}" alt="{html.escape(attrs.get("alt", ""), quote=True)}" loading="lazy"'
        elif tag == 'a':
            url = attrs.get('href', '')
            if urlparse(url).scheme in {'http', 'https'}:
                safe = f' href="{html.escape(url, quote=True)}" target="_blank" rel="noopener noreferrer"'
        elif tag in {'td', 'th'}:
            for key in ['colspan', 'rowspan']:
                if attrs.get(key, '').isdigit():
                    safe += f' {key}="{min(int(attrs[key]), 30)}"'
        self.output.append(f'<{tag}{safe}>')

    def handle_endtag(self, tag):
        if tag in self.blocked:
            self.blocked_depth = max(0, self.blocked_depth - 1)
            return
        if not self.blocked_depth and tag in self.allowed and tag not in {'img', 'br'}:
            self.output.append(f'</{tag}>')

    def handle_data(self, data):
        if not self.blocked_depth:
            self.output.append(html.escape(data))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--product-id', required=True)
    parser.add_argument('--sharpen-run', required=True)
    parser.add_argument('--site-root', required=True, type=Path)
    parser.add_argument('--photo-preset', choices=['catalogue-v8', 'catalogue-v8-theme'], default='catalogue-v8')
    parser.add_argument('--description-sharpen-run')
    parser.add_argument('--description-photo-preset', choices=['catalogue-v8', 'catalogue-v8-theme', 'catalogue-v8-illustrations', 'catalogue-v8-annotations'])
    args = parser.parse_args()
    if not args.product_id.isdigit() or not re.fullmatch(r'[A-Za-z0-9_-]+', args.sharpen_run):
        parser.error('Invalid product ID or run name')
    if args.description_sharpen_run and not re.fullmatch(r'[A-Za-z0-9_-]+', args.description_sharpen_run):
        parser.error('Invalid description run name')
    root = photos.DEFAULT_ARCHIVE
    site = args.site_root.resolve()
    if not (site / 'catalogue.html').is_file():
        parser.error('--site-root must contain the existing catalogue.html')
    manifest = json.loads((root / 'manifest.json').read_text(encoding='utf-8'))
    source = next(p for p in manifest['products'] if p['source_product_id'] == args.product_id)
    trials = json.loads((root / 'reports' / args.sharpen_run / 'sharpening-summary.json').read_text(encoding='utf-8'))
    selected = {r['source_path']: r for r in trials if r['variant'] == 'mild'}
    description_selected = {}
    if args.description_sharpen_run:
        description_trials = json.loads((root / 'reports' / args.description_sharpen_run / 'sharpening-summary.json').read_text(encoding='utf-8'))
        description_selected = {r['source_path']: r for r in description_trials if r['variant'] == 'mild'}
    preset = json.loads((Path(__file__).parent / 'photo_presets' / (args.photo_preset + '.json')).read_text(encoding='utf-8'))
    description_preset_name = args.description_photo_preset or args.photo_preset
    description_preset = json.loads((Path(__file__).parent / 'photo_presets' / (description_preset_name + '.json')).read_text(encoding='utf-8'))
    target = Path('assets/products') / args.product_id
    (site / target).mkdir(parents=True, exist_ok=True)
    gallery, image_map, description_images = [], {}, []
    for asset in source['gallery']:
        record = selected[asset['path']]
        baseline_files = (root / 'reports' / record['baseline_run'] / 'records').rglob('*.json')
        baseline = next(r for f in baseline_files for r in [json.loads(f.read_text(encoding='utf-8'))] if r['source_path'] == asset['path'])
        assert baseline['recipe']['preset'] == preset and not baseline['recipe'].get('mask_edits') and not baseline['recipe'].get('override_sha256')
        assert photos.file_sha(root / asset['path']) == asset['sha256'] == record['source_sha256']
        master = root / record['outputs']['master']['path']
        assert photos.file_sha(master) == record['outputs']['master']['sha256']
        filename = master.stem + '.webp'
        relative = target / filename
        with Image.open(master) as im:
            photos.save_image(site / relative, im, 'WEBP', lossless=True, exact=True, method=6)
        original_relative = target / ('original-' + Path(asset['path']).name)
        shutil.copy2(root / asset['path'], site / original_relative)
        gallery.append({'src': relative.as_posix(), 'alt': asset['alt'], 'order': asset['order'],
                        'original_src': original_relative.as_posix(),
                        'width': asset['width'], 'height': asset['height'], 'source_sha256': asset['sha256'],
                        'processed_sha256': photos.file_sha(site / relative)})
    for asset in source['description_images']:
        original = photos.original_path(root, asset['path'])
        assert photos.file_sha(original) == asset['sha256']
        relative = target / ('description-' + original.name)
        shutil.copy2(original, site / relative)
        original_relative = relative
        if args.description_sharpen_run:
            record = description_selected[asset['path']]
            baseline_files = (root / 'reports' / record['baseline_run'] / 'records').rglob('*.json')
            baseline = next(r for f in baseline_files for r in [json.loads(f.read_text(encoding='utf-8'))] if r['source_path'] == asset['path'])
            assert baseline['recipe']['preset'] == description_preset and not baseline['recipe'].get('mask_edits') and not baseline['recipe'].get('override_sha256')
            assert record['source_sha256'] == asset['sha256']
            master = root / record['outputs']['master']['path']
            assert photos.file_sha(master) == record['outputs']['master']['sha256']
            relative = target / ('description-' + master.stem + '.webp')
            with Image.open(master) as im:
                photos.save_image(site / relative, im, 'WEBP', lossless=True, exact=True, method=6)
        for key in ['source_url', 'download_url']:
            image_map[asset[key]] = relative.as_posix()
        description_images.append({**asset, 'src': relative.as_posix(), 'original_src': original_relative.as_posix(),
                                   'processed_sha256': photos.file_sha(site / relative)})
    sanitized = DescriptionHTML(image_map)
    sanitized.feed(source['description_html'])
    description_html = ''.join(sanitized.output)
    # Keep all original text and all table cells; remove only source styling and unsafe tags.
    video_urls = sorted(set(re.findall(r'https://(?:www\.)?youtube\.com/watch\?v=[A-Za-z0-9_-]+', source['description_text'])))
    product = {key: source.get(key) for key in ['source_product_id', 'source_url', 'source', 'name', 'code',
               'category_id', 'breadcrumbs', 'price', 'availability', 'labels', 'language', 'captured_at',
               'last_checked_at', 'revision', 'description_text', 'description_tables', 'specifications',
               'description_links', 'document_links', 'videos']}
    product.update({'gallery': gallery, 'description_images': description_images,
                    'description_html': description_html, 'video_urls': video_urls,
                    'photo_recipe': {'pipeline': args.photo_preset, 'sharpening': 'mild', 'run': args.sharpen_run,
                                     'description_run': args.description_sharpen_run,
                                     'description_preset': description_preset_name if args.description_sharpen_run else None}})
    photos.atomic_write(site / target / 'product.json', photos.dump(product))
    index_path = site / 'catalog-products.json'
    index = json.loads(index_path.read_text(encoding='utf-8')) if index_path.exists() else []
    index = [p for p in index if p['source_product_id'] != args.product_id] + [product]
    photos.atomic_write(index_path, photos.dump(index))
    photos.atomic_write(site / 'catalog-products.js', 'window.catalogProducts = ' + json.dumps(index, ensure_ascii=False).replace('<', '\\u003c') + ';\n')
    print(json.dumps({'product': product['name'], 'gallery': len(gallery), 'description_images': len(description_images),
                      'tables': len(product['description_tables']), 'exported_products': len(index)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
