"""Export a local list of secondary-source listings without a verified main-site match."""
import collections
import html
import json
import re
from pathlib import Path
from decimal import Decimal

ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / 'sites/mtech-design-refresh/dist'
OUT = ROOT / 'outputs/mtech-secondary-only-20261028'


def main():
    archive = json.loads((ROOT/'data/mtech-combined-archive/manifest.json').read_text(encoding='utf-8'))['products']
    index = {p['source_product_id']: p for p in json.loads((SITE/'catalog-products.json').read_text(encoding='utf-8'))}
    categories = dict(re.findall(r"\['([^']+)', '([^']+)',", (SITE/'catalog-data.js').read_text(encoding='utf-8')))
    possible = collections.defaultdict(dict)
    main_sources = {str(s['source_product_id']): s for p in archive for s in p['sources'] if s['source']=='mtech.com.ua'}
    for p in archive:
        for c in p.get('review_candidates', []):
            main = main_sources[str(c['main_id'])]
            possible[str(c['prom_id'])][str(c['main_id'])] = dict(name=main['name'], url=main['source_url'], main_id=str(c['main_id']))
    products = []
    for p in archive:
        if any(s['source']=='mtech.com.ua' for s in p['sources']):
            continue
        s = next(s for s in p['sources'] if s['source']=='mtech-cnc.com.ua')
        row = index[p['id']]
        products.append(dict(id=p['id'], name=s['name'], source_code=s.get('code') or '',
            catalogue_code=row['code'], category=categories[row['category_id']],
            price=s['price'], availability=s['availability'].get('raw') or s['availability'].get('status') or 'Уточнюйте',
            source_url=s['source_url'], possible_matches=list(possible[str(s['source_product_id'])].values())))
    products.sort(key=lambda p:(p['category'],p['name'],p['id']))
    assert len(products)==95 and len({p['id'] for p in products})==95
    flagged = sum(bool(p['possible_matches']) for p in products)
    result=dict(summary=dict(total=len(products),with_possible_match=flagged,without_recorded_candidate=len(products)-flagged,
        archive_date='2026-10-26',report_date='2026-10-28',
        scope='Товари mtech-cnc.com.ua без підтвердженого відповідника mtech.com.ua у спільному архіві. Відсутність на актуальному сайті не перевірялася.'),products=products)
    OUT.mkdir(parents=True,exist_ok=True)
    (OUT/'products.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    esc=lambda v:html.escape(str(v),quote=True)
    rows=[]
    for p in products:
        amount=p['price'].get('amount')
        price=(format(Decimal(amount),',.2f').replace(',',' ').replace('.',',')+' ₴') if amount and Decimal(amount)>0 else 'Ціну уточнюйте'
        if p['price'].get('unit'):price+='/'+p['price']['unit']
        code=esc(p['source_code'] or 'Не задано')
        if p['catalogue_code']!=p['source_code']:
            code+=f'<small>У новому каталозі: {esc(p["catalogue_code"] or "не задано")}<br>Код змінено попереднім правилом; потрібна перевірка.</small>'
        candidates=''.join(f'<a href="{esc(c["url"])}" target="_blank" rel="noopener">{esc(c["name"])}</a>' for c in p['possible_matches'])
        status=('<b>Є можлива відповідність</b><small>Зіставлення не підтверджено. Перевірити модель, розміри, потужність та комплектацію.</small>'+candidates) if candidates else '<b>Відповідник не знайдений в архіві</b><small>Це не підтвердження відсутності на актуальному сайті.</small>'
        rows.append(f'<tr data-category="{esc(p["category"])}" data-match="{"possible" if candidates else "none"}"><td><a href="{esc(p["source_url"])}" target="_blank" rel="noopener"><strong>{esc(p["name"])}</strong></a><small>ID: {esc(p["id"])}</small></td><td>{esc(p["category"])}</td><td>{code}</td><td class="price">{esc(price)}<small>{esc(p["availability"])}</small></td><td>{status}</td></tr>')
    counts=collections.Counter(p['category'] for p in products)
    options=''.join(f'<option>{esc(c)}</option>' for c in sorted(counts))
    category_counts=''.join(f'<li>{esc(c)} <b>{n}</b></li>' for c,n in sorted(counts.items()))
    page='''<!doctype html><html lang="uk"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>MTech — товари без підтвердженого відповідника</title>
<style>body{font:15px system-ui,sans-serif;background:#f4f7f9;color:#213c4b;margin:0;padding:32px}main{max-width:1500px;margin:auto}h1{font-size:30px;margin:0 0 12px}p{line-height:1.65;max-width:1100px}section,.controls{background:white;border:1px solid #d7e2e8;border-radius:10px;padding:22px;margin:20px 0}.stats{display:flex;gap:18px;flex-wrap:wrap}.stats div{background:#edf3f7;border-radius:7px;padding:12px 20px}.stats b{font-size:30px;display:block}.counts{columns:3;line-height:1.9}.counts li{break-inside:avoid;padding-right:15px}.controls{display:flex;gap:14px;flex-wrap:wrap;align-items:end;position:sticky;top:0}label{display:flex;flex-direction:column;gap:5px;font-size:13px}input,select,button{font:inherit;background:white;color:inherit;border:1px solid #abc0cd;border-radius:5px;padding:9px}input{min-width:270px}.scroll{overflow:auto}table{border-collapse:collapse;width:100%;background:white;font-size:13px}th,td{padding:16px;text-align:left;vertical-align:top;border:1px solid #d7e2e8}th{background:#204b60;color:white}td:first-child{min-width:300px}td:last-child{min-width:270px}small{display:block;color:#607681;line-height:1.5;margin-top:8px}a{color:#176184;text-underline-offset:3px}td:last-child a{display:block;margin-top:12px}.price{min-width:110px}tr[hidden]{display:none}@media(max-width:750px){body{padding:14px}.counts{columns:1}.controls{position:static}}@media print{body{padding:0;background:white}.controls{display:none}.scroll{overflow:visible}table{font-size:9px}th,td{padding:7px;min-width:0!important}thead{display:table-header-group}.counts{columns:2}a{color:inherit}}</style>
<main><h1>Товари mtech-cnc.com.ua без підтвердженого відповідника на mtech.com.ua</h1><p>Список для клієнта · 28.10.2026 · дані архіву від 26.10.2026</p><section><div class="stats"><div><b>95</b>товарних позицій</div><div><b>FLAGGED</b>із можливим відповідником</div><div><b>UNMATCHED</b>без записаного кандидата</div></div><p>Ці позиції є в архіві <b>mtech-cnc.com.ua</b>, але для них не підтверджено відповідник у каталозі <b>mtech.com.ua</b>. Частина має схожі товари на головному сайті, проте параметри або моделі відрізняються чи потребують уточнення. Список не доводить відсутності товарів на актуальному сайті.</p><p>Назви, вихідні коди, ціни й наявність наведені з mtech-cnc.com.ua на дату архівування. Якщо код у новому каталозі вже був змінений, показано обидва значення. Посилання на назву відкриває вихідний товар. Жоден товар не видалено.</p><details><summary>Розподіл за категоріями</summary><ul class="counts">COUNTS</ul></details></section>
<div class="controls"><label>Пошук<input id="q" type="search" placeholder="Назва, код або ID"></label><label>Категорія<select id="category"><option value="">Усі категорії</option>OPTIONS</select></label><label>Зіставлення<select id="match"><option value="">Усі позиції</option><option value="possible">Є можлива відповідність</option><option value="none">Немає записаного кандидата</option></select></label><button id="print">Друк / PDF</button><span id="shown" aria-live="polite"></span></div>
<div class="scroll"><table><thead><tr><th>Товар і вихідна сторінка</th><th>Категорія</th><th>Код джерела</th><th>Ціна / наявність</th><th>Перевірка mtech.com.ua</th></tr></thead><tbody>ROWS</tbody></table></div></main>
<script>const q=document.getElementById('q'),c=document.getElementById('category'),m=document.getElementById('match'),rows=[...document.querySelectorAll('tbody tr')];function filter(){let n=0;for(const r of rows){r.hidden=!!((c.value&&r.dataset.category!==c.value)||(m.value&&r.dataset.match!==m.value)||!r.textContent.toLocaleLowerCase().includes(q.value.toLocaleLowerCase()));if(!r.hidden)n++}document.getElementById('shown').textContent=`Показано ${n} із ${rows.length}`}for(const el of [q,c,m])el.addEventListener('input',filter);document.getElementById('print').addEventListener('click',()=>window.print());filter();</script></html>'''
    for key,val in [('FLAGGED',str(flagged)),('UNMATCHED',str(len(products)-flagged)),('COUNTS',category_counts),('OPTIONS',options),('ROWS',''.join(rows))]:page=page.replace(key,val)
    (OUT/'MTech — товари лише з mtech-cnc.com.ua.html').write_text(page,encoding='utf-8')
    print(json.dumps(result['summary'],ensure_ascii=False,indent=2))


if __name__=='__main__':
    main()
