"""Read-only catalogue audit; write evidence and an offline Ukrainian review report."""
import collections
import hashlib
import html
import json
import re
from html.parser import HTMLParser
from pathlib import Path

from export_mtech_catalogue import code_conflicts_name, normalized

ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / 'sites/mtech-design-refresh/dist'
OUT = ROOT / 'outputs/mtech-product-audit-20261028'


class Content(HTMLParser):
    def __init__(self):
        super().__init__()
        self.text, self.rows, self.row, self.cell, self.images = [], [], None, None, []

    def handle_starttag(self, tag, attrs):
        if tag == 'tr':
            self.row = []
        elif tag in ('td', 'th') and self.row is not None:
            self.cell = []
        if tag == 'img':
            self.images.append(dict(attrs).get('src', ''))

    def handle_data(self, value):
        self.text.append(value)
        if self.cell is not None:
            self.cell.append(value)

    def handle_endtag(self, tag):
        if tag in ('td', 'th') and self.cell is not None:
            self.row.append(' '.join(' '.join(self.cell).split()))
            self.cell = None
        elif tag == 'tr' and self.row is not None:
            self.rows.append(self.row)
            self.row = None


def name_key(value):
    # Preserve Cyrillic letters: an ASCII-only model normalizer loses whole words.
    return re.sub(r'[^\w]+', ' ', value.casefold(), flags=re.UNICODE).strip()


def model_tokens(value):
    return {normalized(m) for m in re.findall(r'\b(?:FLE|FLA|FL|F|L)\s?\d{4}\b', value, re.I)}


def excerpt(text, token, radius=115):
    match = re.search(re.escape(token), text, re.I)
    if not match:
        return text[:230]
    return text[max(0, match.start()-radius):match.end()+radius]


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    index = json.loads((SITE/'catalog-products.json').read_text(encoding='utf-8'))
    products = [json.loads((SITE/p['detail_url']).read_text(encoding='utf-8')) for p in index]
    byid = {p['source_product_id']: p for p in products}
    archive = json.loads((ROOT/'data/mtech-combined-archive/manifest.json').read_text(encoding='utf-8'))['products']
    issues, hashes = [], {}

    def add(p, kind, level, evidence, action, related=None, confidence='Виявлено'):
        issues.append(dict(id=p['source_product_id'], name=p['name'], code=p['code'],
                           source=p['source'], source_url=p['source_url'], type=kind, priority=level,
                           confidence=confidence, evidence=evidence, action=action, related=related or []))

    assert len(byid) == len(products) == len(index) == len(archive)
    for row, p, original in zip(index, products, archive):
        primary = next((s for s in original['sources'] if s['source']=='mtech.com.ua'), original['sources'][0])
        for key in ('name', 'code', 'price', 'availability', 'category_id'):
            if row[key] != p[key]:
                add(p, 'Розбіжність індексу та картки', 'Високий', f'Поле {key}', 'Повторно сформувати індекс і картку.')
        assert p['source_product_id'] == original['id']
        assert p['name'] == primary['name'] and p['source_url'] == primary['source_url']
        if primary['source'] == 'mtech.com.ua' and primary.get('code'):
            assert p['code'] == primary['code']
        parsed = Content()
        parsed.feed(p['description_html'])
        text = ' '.join(' '.join(parsed.text).split())

        if p['code'] and code_conflicts_name(p['name'], p['code']):
            add(p, 'Код не відповідає моделі в назві', 'Високий',
                f'Назва: {p["name"]}. Код: {p["code"]}. Обидва значення походять із головного джерела.',
                'Уточнити чинну модель та артикул у клієнта; не обирати автоматично між назвою і кодом.')

        if primary.get('code') and p['code'] != primary['code']:
            add(p, 'Код змінений попереднім правилом перенесення', 'Високий',
                f'Код джерела: {primary["code"]}; код каталогу: {p["code"]}; назва: {p["name"]}. Опис: {text[:350] or "відсутній"}',
                'Переглянути попереднє виправлення за назвою. Якщо опис підтримує вихідний код, виправляти назву після підтвердження; не зливати записи за новим кодом.',
                confidence='Зміна підтверджена; правильний код потребує перевірки')

        title_models = model_tokens(p['name'])
        other_models = model_tokens(text) - title_models
        if title_models and other_models:
            snippets = []
            for model in sorted(other_models):
                match = re.search(r'\b'+re.escape(model[:len(model)-4])+r'\s?'+model[-4:]+r'\b', text, re.I)
                snippets.append(excerpt(text, match[0] if match else model))
            add(p, 'Інша модель в описі', 'Високий',
                f'Модель у назві: {", ".join(sorted(title_models))}. В описі: {", ".join(sorted(other_models))}. '+ ' / '.join(snippets),
                'Звірити заголовок, таблицю параметрів і опис; згадка іншої моделі може бути порівнянням або скопійованим текстом.',
                confidence='Потребує змістової перевірки')

        # Exact repeated single-value electrical fields; avoid multi-component kits and ranges.
        fields = collections.defaultdict(set)
        for cells in parsed.rows:
            if len(cells) == 2:
                fields[cells[0].strip(' :').casefold()].add(cells[1])
        for key in ('номинальный ток', 'номінальний струм'):
            values = fields.get(key, set())
            numbers = {float(re.search(r'\d+(?:[.,]\d+)?', v)[0].replace(',', '.'))
                       for v in values if re.search(r'\d+(?:[.,]\d+)?', v)}
            if len(numbers)>1:
                add(p, 'Суперечливі технічні характеристики', 'Високий',
                    f'Одне поле «{key}» має різні значення: '+ '; '.join(sorted(values)),
                    'Звірити паспорт конкретної моделі та виправити обидва блоки характеристик.')

        lengths = set(re.findall(r'длина\s+(\d+(?:[.,]\d+)?)\s*мм', text, re.I))
        if len(lengths)>1 and p['category_id']=='spindles':
            add(p, 'Суперечливі технічні характеристики', 'Високий',
                'Довжина шпинделя у різних частинах опису: '+', '.join(sorted(lengths))+' мм.',
                'Уточнити габарит за кресленням або паспортом; короткий і повний опис мають збігатися.')

        sfu = re.search(r'\bSFU\s*(\d{2})(\d{2})', p['name'], re.I)
        dims = re.search(r'Діаметр/крок\s*(\d+(?:[.,]\d+)?)\s*[хx×]\s*(\d+(?:[.,]\d+)?)', p['name'], re.I)
        if sfu and dims and (int(sfu[1]),int(sfu[2])) != tuple(float(d.replace(',','.')) for d in dims.groups()):
            add(p, 'Модель і розміри в одній назві', 'Високий',
                f'Назва містить {sfu[0]} та «Діаметр/крок {dims[1]}×{dims[2]}». Позначення моделі вказує на {int(sfu[1])}×{int(sfu[2])}.',
                'Звірити діаметр і крок із паспортом. Не виправляти десятковий знак або модель навмання.', confidence='Кандидат на технічну помилку')

        for absent, kind, action in [
            (not p['code'], 'Відсутній код', 'Призначити унікальний артикул або підтвердити, що він не потрібен.'),
            (not text, 'Відсутній опис', 'Додати опис і підтверджені характеристики.'),
            (not p['gallery'], 'Відсутні фото товару', 'Отримати оригінальні фото цієї моделі.'),
            (not p['price']['amount'] or float(p['price']['amount'])<=0, 'Ціну не задано', 'Уточнити ціну або свідомо залишити «Ціну уточнюйте».')]:
            if absent:
                add(p, kind, 'Заповнення', f'Картка {p["source_product_id"]}: {kind.lower()}.', action)

        status=(p['availability'].get('schema_status') or '').rsplit('/',1)[-1]
        label=p['availability'].get('status','')
        if status=='OutOfStock' and label=='В наявності' or status=='InStock' and label=='Немає в наявності':
            add(p,'Суперечлива наявність','Високий',f'{status}; {label}','Виправити відповідність статусу джерелу.')
        for a in p['gallery']+p['description_images']:
            path = (SITE/a['src']).resolve()
            if not path.is_relative_to(SITE.resolve()) or not path.is_file():
                add(p,'Недоступний файл фото','Високий',a['src'],'Відновити файл з архіву.')
            else:
                if a['src'] not in hashes:
                    hashes[a['src']]=hashlib.sha256(path.read_bytes()).hexdigest()
                assert hashes[a['src']]==a['source_sha256']
        for src in parsed.images:
            if not (SITE/src).is_file():
                add(p,'Недоступне фото в описі','Високий',src,'Відновити посилання на локальний оригінал.')

    for field, key_fn, kind in [('code',normalized,'Повторний код'),('name',name_key,'Однакові назви')]:
        groups=collections.defaultdict(list)
        for p in products:
            if p[field]: groups[key_fn(p[field])].append(p)
        for group in groups.values():
            if len(group)<2: continue
            evidence='\n'.join(f'{p["source_product_id"]}: {p["name"]}; код {p["code"] or "не задано"}; ціна {p["price"]["amount"] or "не задана"} ₴' for p in group)
            add(group[0],kind,'Перевірка',evidence,
                'Розрізнити варіанти за потужністю, розміром, кольором чи комплектацією. Спільний код або назва не доводять дублювання; за потреби створити унікальні артикули та уточнити назви.',
                related=[p['source_product_id'] for p in group[1:]],confidence='Група для перевірки, не підтверджений дубль')

    # Carry unresolved matching candidates forward; they are not proved duplicates.
    prior=json.loads((ROOT/'.artifacts/mtech-full-transfer/report.json').read_text(encoding='utf-8'))
    for note in prior['notes']:
        if note['type']=='Зіставлення':
            candidates=note.get('candidates',[])
            evidence='\n'.join(f'{c.get("main_name","")} ↔ {c.get("prom_name","")}; ID {c.get("main_id","")} / {c.get("prom_id","")}' for c in candidates)
            add(byid[note['id']], 'Непевна відповідність двох джерел', 'Перевірка', evidence or note['text'],
                'Підтвердити модель, розмір, потужність і комплектацію перед об’єднанням. Позиції збережено окремо.',
                confidence='Успадкований кандидат; не підтверджений дубль')

    rank={'Високий':0,'Перевірка':1,'Заповнення':2}
    issues.sort(key=lambda x:(rank[x['priority']],x['type'],x['id']))
    affected={i for x in issues for i in [x['id']]+x['related']}
    summary=dict(products=len(products), findings=len(issues), affected_products=len(affected),
                 by_type=dict(collections.Counter(x['type'] for x in issues)),
                 by_priority=dict(collections.Counter(x['priority'] for x in issues)),
                 original_files_verified=len(hashes), archive_date='2026-10-26', audit_date='2026-10-28',
                 scope='Локальний каталог та архів; без повторного обходу сайтів і без автоматичної зміни товарів.')
    data=dict(summary=summary,findings=issues)
    (OUT/'audit.json').write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    render(data)
    print(json.dumps(summary,ensure_ascii=False,indent=2))


def render(data):
    esc=lambda s:html.escape(str(s),quote=True)
    rows=[]
    for i,x in enumerate(data['findings'],1):
        links=' · '.join(f'<a href="https://mtech-design-preview.pages.dev/catalogue?product={esc(pid)}" target="_blank" rel="noopener">{esc(pid)}</a>' for pid in [x['id']]+x['related'])
        rows.append(f'<tr data-priority="{esc(x["priority"])}" data-type="{esc(x["type"])}"><td>{i}</td><td><b>{esc(x["priority"])}</b><br>{esc(x["type"])}<small>{esc(x["confidence"])}</small></td><td><strong>{esc(x["name"])}</strong><small>Код: {esc(x["code"] or "не задано")}</small>{links}<small><a href="{esc(x["source_url"])}" target="_blank" rel="noopener">{esc(x["source"])}</a></small></td><td class="evidence">{esc(x["evidence"])}</td><td>{esc(x["action"])}</td></tr>')
    summary=data['summary']
    options=''.join(f'<option>{esc(k)}</option>' for k in summary['by_type'])
    counts=''.join(f'<li>{esc(k)}: <b>{v}</b></li>' for k,v in summary['by_type'].items())
    page='''<!doctype html><html lang="uk"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>MTech — аудит товарів</title>
<style>:root{font-family:system-ui,sans-serif;color:#17303c;background:#f3f6f8}body{margin:0;padding:32px}main{max-width:1700px;margin:auto}h1{margin:0 0 8px;font-size:32px}p{line-height:1.6;max-width:1150px}a{color:#176187}.intro,.controls{background:white;padding:22px;border:1px solid #d8e2e8;border-radius:10px;margin:18px 0}.stats{display:flex;gap:14px;flex-wrap:wrap}.stat{background:#e5eff4;padding:15px 22px;border-radius:8px}.stat b{font-size:28px;display:block}.counts{columns:3;line-height:1.8;padding-left:20px}.controls{display:flex;gap:12px;flex-wrap:wrap;align-items:end;position:sticky;top:0}label{display:flex;flex-direction:column;gap:5px;font-size:13px}input,select,button{font:inherit;padding:9px;border:1px solid #a9becb;border-radius:5px;background:white;color:inherit}input{min-width:290px}.scroll{overflow:auto}table{border-collapse:collapse;background:white;width:100%;font-size:13px}th,td{text-align:left;padding:14px;vertical-align:top;border:1px solid #dce5ea}th{background:#204c61;color:white}td:nth-child(2){min-width:160px}td:nth-child(3){min-width:260px}td:nth-child(4){min-width:300px}.evidence{white-space:pre-line}small{display:block;color:#557180;margin:8px 0;line-height:1.5}tr[hidden]{display:none}tr[data-priority="Високий"] td:first-child{border-left:4px solid #bd543e}@media(max-width:750px){body{padding:14px}.counts{columns:1}.controls{position:static}}@media print{body{padding:0;background:white}.controls{display:none}.scroll{overflow:visible}table{font-size:9px}th,td{padding:6px;min-width:0!important}thead{display:table-header-group}.counts{columns:2}a{color:inherit}}
</style><main><h1>MTech — аудит товарів</h1><p>Перевірка каталогу від 28.10.2026 · архів джерел від 26.10.2026</p><section class="intro"><div class="stats">STATS</div><p><b>Головне джерело — mtech.com.ua.</b> Це звіт про проблеми, а не нові рішення щодо товарів. Значення каталогу не змінювалися. Коли головний сайт суперечить сам собі, потрібне уточнення клієнта. Повторний код, однакова назва та кандидат на зіставлення не є доказом дублювання.</p><p>Перевірено код і модель у назві, попередні зміни кодів, моделі в основному описі, окремі повторні технічні поля, унікальність ID, відповідність індексу карткам, пріоритет джерела, статуси наявності, пропущені поля та файли зображень. Згадки інших моделей і технічні розбіжності позначені для змістової перевірки. Це автоматизований аудит локального знімка, а не перевірка актуальних цін на сайтах, паспортів обладнання чи візуальної відповідності кожного фото. Відсутність зауважень не гарантує правильність усіх параметрів.</p><ul class="counts">COUNTS</ul></section>
<div class="controls"><label>Пошук<input id="q" type="search" placeholder="Назва, код, ID або фрагмент опису"></label><label>Пріоритет<select id="priority"><option value="">Усі</option><option>Високий</option><option>Перевірка</option><option>Заповнення</option></select></label><label>Тип<select id="kind"><option value="">Усі</option>OPTIONS</select></label><button id="print">Друк / PDF</button><span id="shown" aria-live="polite"></span></div>
<div class="scroll"><table><thead><tr><th>№</th><th>Пріоритет і питання</th><th>Товар і джерело</th><th>Доказ / виявлені значення</th><th>Що зробити</th></tr></thead><tbody>ROWS</tbody></table></div></main>
<script>const q=document.getElementById('q'),p=document.getElementById('priority'),k=document.getElementById('kind'),rows=[...document.querySelectorAll('tbody tr')];function filter(){let n=0;for(const r of rows){r.hidden=!!((p.value&&r.dataset.priority!==p.value)||(k.value&&r.dataset.type!==k.value)||!r.textContent.toLocaleLowerCase().includes(q.value.toLocaleLowerCase()));if(!r.hidden)n++}document.getElementById('shown').textContent=`Показано ${n} із ${rows.length}`}for(const el of [q,p,k])el.addEventListener('input',filter);document.getElementById('print').addEventListener('click',()=>window.print());filter();</script></html>'''
    stats=''.join(f'<div class="stat"><b>{v}</b>{label}</div>' for label,v in [('товарів перевірено',summary['products']),('записів у звіті',summary['findings']),('товарів із зауваженнями',summary['affected_products']),('оригінальних файлів перевірено',summary['original_files_verified'])])
    for key,value in [('STATS',stats),('COUNTS',counts),('OPTIONS',options),('ROWS',''.join(rows))]:
        page=page.replace(key,value)
    (OUT/'MTech — аудит товарів.html').write_text(page,encoding='utf-8')


if __name__=='__main__':
    main()
