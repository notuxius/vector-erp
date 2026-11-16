"""Package each distinct original once, preserving combined-catalogue indexes."""
import csv
import hashlib
import io
import json
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

DATA = Path(__file__).resolve().parents[1] / 'data'
SOURCE = DATA / 'mtech-combined-archive'
OUTPUT = DATA / 'mtech-unique-photos-external-v1'
ZIP = DATA / 'mtech-unique-originals-external-v1.zip'


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def csv_data(rows, fields):
    buffer = io.StringIO(newline='')
    writer = csv.DictWriter(buffer, fieldnames=fields)
    writer.writeheader()
    for row in rows:
        values = {key: json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else value
                  for key, value in row.items()}
        writer.writerow({key: "'" + value if isinstance(value, str) and value.startswith(('=', '+', '-', '@')) else value
                         for key, value in values.items()})
    return b'\xef\xbb\xbf' + buffer.getvalue().encode('utf-8')


def main():
    if OUTPUT.exists() or ZIP.exists():
        raise FileExistsError('Unique package already exists; preserve it and any returned edits.')
    source_bytes = (SOURCE / 'manifest.json').read_bytes()
    source = json.loads(source_bytes)
    old_path = DATA / 'mtech-product-archive/handoff/external-v1-manifest.json'
    old = json.loads(old_path.read_text(encoding='utf-8'))
    old_images = {}
    for image in old['images']:
        old_images.setdefault(image['original_sha256'], []).append(image)
    unique = {}
    expected_uses = set()
    for product in sorted(source['products'], key=lambda item: item['index']):
        for image in product['images']:
            original_path = image['path']
            return_path = image['return_path']
            for path in [original_path, return_path]:
                parsed = PurePosixPath(path)
                assert not parsed.is_absolute() and '..' not in parsed.parts
            assert PurePosixPath(original_path).parts[1] == product['folder']
            assert PurePosixPath(return_path).parts[:2] == ('edited', 'external-v1')
            assert PurePosixPath(return_path).parts[2] == product['folder']
            expected_uses.add((product['id'], original_path))
            entry = unique.setdefault(image['sha256'], {
                'original_sha256': image['sha256'], 'original_path': original_path,
                'return_path': return_path, 'owner_index': product['index'],
                'owner_product_id': product['id'], 'owner_folder': product['folder'],
                'owner_product_name': product['name'], 'width': image['width'],
                'height': image['height'], 'bytes': image['bytes'],
                'previous_package_images': old_images.get(image['sha256'], []), 'product_uses': [],
            })
            entry['product_uses'].append({
                'index': product['index'], 'product_id': product['id'], 'folder': product['folder'],
                'name': product['name'], 'code': product.get('code', ''),
                'combined_original_path': original_path, 'combined_return_path': return_path,
                'source_references': image['references'],
            })

    entries = list(unique.values())
    assert len(entries) == source['summary']['unique_photo_hashes']
    assert len(expected_uses) == source['summary']['photo_files']
    assert len({entry['return_path'] for entry in entries}) == len(entries)
    actual_uses = {(use['product_id'], use['combined_original_path'])
                   for entry in entries for use in entry['product_uses']}
    assert actual_uses == expected_uses
    OUTPUT.mkdir(parents=True)
    for entry in entries:
        content = (SOURCE / entry['original_path']).read_bytes()
        assert sha256(content) == entry['original_sha256'], entry['original_path']
        assert len(content) == entry['bytes']
        dest = OUTPUT / entry['original_path']
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(content)
        (OUTPUT / entry['return_path']).parent.mkdir(parents=True, exist_ok=True)
    owner_folders = sorted({entry['owner_folder'] for entry in entries})
    summary = {
        'package': 'mtech-unique-originals-external-v1',
        'created_at': datetime.now(timezone.utc).isoformat(),
        'deduplication_method': 'Exact original file bytes compared by full SHA-256',
        'source_manifest_sha256': sha256(source_bytes),
        'unique_originals': len(entries), 'product_image_uses': len(actual_uses),
        'duplicate_copies_omitted': len(actual_uses) - len(entries),
        'original_folders': len(owner_folders), 'empty_edited_folders': len(owner_folders),
        'combined_product_count': len(source['products']),
        'reused_from_previous_photo_package': sum(bool(e['previous_package_images']) for e in entries),
        'new_since_previous_photo_package': sum(not e['previous_package_images'] for e in entries),
        'owner_selection': 'First occurrence in ascending combined-catalogue product index; existing paths retained.',
    }
    (OUTPUT / 'package-manifest.json').write_text(json.dumps({'summary': summary, 'images': entries}, ensure_ascii=False, indent=2), encoding='utf-8')
    rows = [{
        'index': e['owner_index'], 'product_id': e['owner_product_id'], 'folder': e['owner_folder'],
        'name': e['owner_product_name'], 'original_path': e['original_path'], 'return_path': e['return_path'],
        'original_sha256': e['original_sha256'], 'width': e['width'], 'height': e['height'],
        'roles': ' | '.join(sorted({r['role'] for u in e['product_uses'] for r in u['source_references']})),
        'product_use_count': len(e['product_uses']),
        'all_product_folders': ' | '.join(u['folder'] for u in e['product_uses']),
        'in_previous_package': bool(e['previous_package_images']),
    } for e in entries]
    (OUTPUT / 'photo-map.csv').write_bytes(csv_data(rows, list(rows[0])))
    logs = [{'original_path': e['original_path'], 'return_path': e['return_path'], 'status': '', 'notes': ''} for e in entries]
    (OUTPUT / 'editing-log.csv').write_bytes(csv_data(logs, list(logs[0])))
    uses = [{
        'original_sha256': e['original_sha256'], 'edit_once_original_path': e['original_path'],
        'edit_once_return_path': e['return_path'], 'product_index': u['index'],
        'product_id': u['product_id'], 'product_folder': u['folder'],
        'combined_original_path': u['combined_original_path'], 'combined_return_path': u['combined_return_path'],
    } for e in entries for u in e['product_uses']]
    (OUTPUT / 'reuse-map.csv').write_bytes(csv_data(uses, list(uses[0])))
    instructions = f'''MTech — унікальні оригінальні фото для редагування

У пакеті {len(entries)} різних вихідних зображень. Кожен файл потрібно опрацювати лише один раз.
Повторні копії видалено за повним SHA-256. Схожі фото з різними байтами не об'єднувалися.

1. Відкрийте originals/<індекс - ID товару>/. Індекси, назви папок та імена файлів
   збережено з об'єднаного каталогу. Пропуски в нумерації очікувані: повтори вилучено.
2. Збережіть результат PNG у відповідну порожню папку edited/external-v1/<індекс - ID товару>/.
   Залиште ім'я файлу до розширення незмінним. Точний шлях наведено у photo-map.csv,
   колонка return_path. Наприклад:
   {entries[0]['original_path']}
   → {entries[0]['return_path']}
3. Не копіюйте результат до інших товарів. Після повернення пакета його можна
   повторно використати для всіх товарів, указаних у reuse-map.csv.
4. За потреби заповніть editing-log.csv: edited / unchanged / needs-review / skipped.
   У notes вкажіть зауваження або причину пропуску.
5. Не змінюйте конструкцію, пропорції, маркування та комплектацію товару.
   У пакеті також є схеми, таблиці та ілюстрації описів: не видаляйте з них важливий текст.
6. Поверніть ZIP з папкою edited/external-v1/ і заповненим editing-log.csv.
   Зберігайте цей package-manifest.json разом із результатами для точного зіставлення.

Один оригінал залишено в папці товару з найменшим наявним індексом.
Є лише ті {len(owner_folders)} папки originals/ та edited/, у яких призначено унікальні фото.
Усі {len(actual_uses)} зв'язків фото з товарами збережені у reuse-map.csv та package-manifest.json.

{summary['reused_from_previous_photo_package']} оригінали також були в попередньому фотопакеті,
{summary['new_since_previous_photo_package']} — додані під час об'єднання сайтів. Якщо попередні
результати вже готові, їх можна повторно використати за хешем відповідного оригіналу.
У цьому пакеті папки edited/ порожні; попередні чернетки сюди не переносилися.
'''
    (OUTPUT / 'README.uk.txt').write_text(instructions, encoding='utf-8')
    assert len(list((OUTPUT / 'originals').rglob('*.*'))) == len(entries)
    assert not any(p.is_file() for p in (OUTPUT / 'edited').rglob('*'))
    temp = ZIP.with_suffix('.zip.tmp')
    with zipfile.ZipFile(temp, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for file in sorted(OUTPUT.rglob('*')):
            relative = file.relative_to(OUTPUT).as_posix()
            if file.is_file():
                archive.write(file, relative)
            elif relative.startswith('edited/external-v1/'):
                archive.writestr(relative + '/', b'')
    with zipfile.ZipFile(temp) as archive:
        assert archive.testzip() is None
        names = archive.namelist()
        assert len(names) == len(set(names))
        images = [name for name in names if name.startswith('originals/') and not name.endswith('/')]
        assert len(images) == len(entries)
        checked_hashes = set()
        for entry in entries:
            actual = sha256(archive.read(entry['original_path']))
            assert actual == entry['original_sha256'] and actual not in checked_hashes
            checked_hashes.add(actual)
            assert str(PurePosixPath(entry['return_path']).parent) + '/' in names
        assert not any(name.startswith('edited/') and not name.endswith('/') for name in names)
    temp.replace(ZIP)
    verification = {**summary, 'zip_crc_passed': True, 'all_original_hashes_verified': True,
        'all_product_uses_mapped': True, 'all_editing_folders_empty': True,
        'zip_bytes': ZIP.stat().st_size, 'zip_sha256': sha256(ZIP.read_bytes())}
    (OUTPUT / 'verification.json').write_text(json.dumps(verification, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'zip': str(ZIP), **verification}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
