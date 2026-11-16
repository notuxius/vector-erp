"""Apply approved external photo replacements while retaining original assets."""
import hashlib
import json
import shutil
from pathlib import Path


def load_overrides(site):
    content = site.parent / 'content'
    manifest = content / 'photo-overrides.json'
    if not manifest.exists():
        return {}
    overrides = json.loads(manifest.read_text(encoding='utf-8'))['images']
    for digest, item in overrides.items():
        source = (content / item['file']).resolve()
        target = (site / item['src']).resolve()
        assert source.is_relative_to(content.resolve())
        assert target.is_relative_to((site / 'assets/catalogue-edited').resolve())
        assert hashlib.sha256(source.read_bytes()).hexdigest() == item['sha256']
        assert len(digest) == 64
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    return overrides


def load_gallery_exclusions(site):
    manifest = site.parent / 'content/photo-overrides.json'
    return json.loads(manifest.read_text(encoding='utf-8')).get('gallery_exclusions', {}) if manifest.exists() else {}


def apply_overrides(product, overrides, gallery_exclusions=None):
    replacements = {}
    for item in product.get('gallery', []) + product.get('description_images', []):
        override = overrides.get(item['source_sha256'])
        if not override:
            continue
        if product['source_product_id'] in override.get('exclude_products', []):
            replacements[item['src']] = item['original_src']
            item['src'] = item['original_src']
            item.pop('edited_sha256', None)
            if 'width' in item:
                item['width'], item['height'] = override['original_width'], override['original_height']
            continue
        replacements[item['original_src']] = override['src']
        item['src'] = override['src']
        item['edited_sha256'] = override['sha256']
        if 'width' in item:
            item['width'], item['height'] = override['width'], override['height']
    for block in [product] + product.get('source_details', []):
        if 'description_html' in block:
            for original, edited in replacements.items():
                block['description_html'] = block['description_html'].replace(
                    'src="' + original + '"', 'src="' + edited + '"')
    if replacements:
        edited = any(item['src'] != item['original_src'] for item in product.get('gallery', []) + product.get('description_images', []))
        product['photo_recipe'] = {'pipeline': 'originals-with-external-v1' if edited else 'originals', 'sharpening': None}
    excluded = (gallery_exclusions or {}).get(product['source_product_id'], [])
    gallery = product.get('gallery', [])
    retained = [item for item in gallery if item['source_sha256'] not in excluded]
    removed = len(retained) != len(gallery)
    gallery[:] = retained
    return bool(replacements) or removed


def main():
    site = Path(__file__).resolve().parents[1] / 'sites/mtech-design-refresh/dist'
    overrides = load_overrides(site)
    gallery_exclusions = load_gallery_exclusions(site)
    index_path = site / 'catalog-products.json'
    index = json.loads(index_path.read_text(encoding='utf-8'))
    changed = []
    for row in index:
        path = site / row['detail_url']
        product = json.loads(path.read_text(encoding='utf-8'))
        if apply_overrides(product, overrides, gallery_exclusions):
            path.write_text(json.dumps(product, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
            row['gallery'] = product['gallery'][:1]
            changed.append(product['source_product_id'])
    index_path.write_text(json.dumps(index, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    (site / 'catalog-products.js').write_text('window.catalogProducts = ' + json.dumps(
        index, ensure_ascii=False, separators=(',', ':')).replace('<', '\\u003c') + ';\n', encoding='utf-8')
    print(json.dumps({'edited_images': len(overrides), 'updated_products': changed}))


if __name__ == '__main__':
    main()
