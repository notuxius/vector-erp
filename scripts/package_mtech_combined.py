"""Build a portable reconciled catalogue with immutable originals and editor folders."""
import csv
import html
import io
import json
import re
import shutil
import zipfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from extract_mtech_sample import read_json, write, encoded, digest

DATA=Path(__file__).resolve().parents[1]/'data'
PROM=DATA/'mtech-product-archive'
MAIN=DATA/'mtech-main-archive'
OUT=DATA/'mtech-combined-archive'

def csvfile(path,rows,fields=None):
    fields=fields or list(rows[0])
    buf=io.StringIO(newline=''); w=csv.DictWriter(buf,fields);w.writeheader()
    for row in rows:
        vals={k:json.dumps(v,ensure_ascii=False) if isinstance(v,(list,dict)) else v for k,v in row.items()}
        w.writerow({k:"'"+str(v) if str(v).startswith(('=','+','-','@')) else v for k,v in vals.items()})
    write(path,b'\xef\xbb\xbf'+buf.getvalue().encode('utf-8'))

def copy_checked(src,dest,sha=None):
    data=src.read_bytes(); actual=digest(data)
    if sha and sha!=actual: raise ValueError('Hash mismatch: '+str(src))
    if not dest.exists() or digest(dest.read_bytes())!=actual: write(dest,data)
    return actual

def price(p):
    if not p:return None
    if p['source']=='mtech-cnc.com.ua': return p['price'].get('amount')
    return next((v['amount'] for v in p['price']['values'] if v['currency']=='UAH'),None)

def main():
    old=read_json(PROM/'manifest.json',{})['products']
    main=read_json(MAIN/'manifest.json',{})['products']
    mapping=read_json(MAIN/'approved-matches.json',{})
    decisions=mapping['matches']
    ru=read_json(MAIN/'prom-ru.json',{})
    groups={p['source_product_id']:{'id':p['source_product_id'],'sources':[p],'matches':[]} for p in old}
    for p in main:
        match=decisions.get(p['source_product_id'])
        if match:
            key=match['prom_id']; groups[key]['sources'].append(p);groups[key]['matches'].append(match)
        else:
            key='wp-'+p['source_product_id'];groups[key]={'id':key,'sources':[p],'matches':[]}
    ordered=sorted(groups.values(),key=lambda g:(g['id'].startswith('wp-'),int(g['id'].replace('wp-',''))))
    rows=[]; photos=[]; products=[]; comparison=[]; missing=[]; price_conflicts=[]
    for index,g in enumerate(ordered,1):
        g['index']=index;g['folder']=f"{index} - {g['id']}"
        folder=g['folder']; primary=g['sources'][0];g['name']=primary['name'];g['code']=primary.get('code','')
        (OUT/'edited/external-v1'/folder).mkdir(parents=True,exist_ok=True)
        (OUT/'originals'/folder).mkdir(parents=True,exist_ok=True)
        image_map={};g['images']=[];g['existing_edits']=[]
        for source in g['sources']:
            is_prom=source['source']=='mtech-cnc.com.ua'; root=PROM if is_prom else MAIN
            pid=source['source_product_id'];key=('prom-' if is_prom else 'wp-')+pid
            local=Path('products')/folder/key
            write(OUT/local/'source-record.json',encoded(source))
            write(OUT/local/'description.html',source.get('description_html',''))
            write(OUT/local/'description.txt',source.get('description_text',''))
            if is_prom:
                original=root/'products'/pid/'revisions'/source['revision']/'source.html'
                if pid in ru:
                    write(OUT/local/'default-language-source.json',encoded(ru[pid]))
                    rus=MAIN/'source/prom-ru'/(pid+'.html')
                    if rus.exists():copy_checked(rus,OUT/local/'source-default-language.html')
            else: original=root/'source/products'/(pid+'.html')
            if original.exists():copy_checked(original,OUT/local/'source.html')
            else: missing.append({'id':g['id'],'issue':'Missing source HTML','source':str(original)})
            source['package_record']=(local/'source-record.json').as_posix()
            source['package_html']=(local/'source.html').as_posix()
            for image in source.get('gallery',[])+source.get('description_images',[]):
                sha=image['sha256']
                if sha not in image_map:
                    seq=len(image_map)+1; image_id=image.get('source_image_id') or sha[:12]
                    filename=f'{seq:03d} - {image_id} - {sha[:12]}'+Path(image['path']).suffix
                    relative=Path('originals')/folder/filename
                    copy_checked(root/image['path'],OUT/relative,sha)
                    ret=Path('edited/external-v1')/folder/Path(filename).with_suffix('.png')
                    item={'path':relative.as_posix(),'return_path':ret.as_posix(),'sha256':sha,
                          'width':image['width'],'height':image['height'],'bytes':image['bytes'],'references':[]}
                    image_map[sha]=item;g['images'].append(item)
                item=image_map[sha]
                item['references'].append({'site':source['source'],'product_id':pid,'source_image_id':image.get('source_image_id'),
                    'role':image['role'],'order':image['order'],'url':image['source_url'],'download_url':image['download_url'],
                    'previous_archive_path':str((root/image['path']).resolve())})
                image['package_path']=item['path']
            for im in source.get('missing_images',[])+source.get('missing_description_images',[]):
                missing.append({'id':g['id'],'issue':'Source image unavailable','source':im})
            if is_prom:
                for file in sorted((PROM/'edited'/pid).glob('*')):
                    if file.is_file():
                        rel=Path('existing-edits')/folder/file.name
                        sha=copy_checked(file,OUT/rel)
                        g['existing_edits'].append({'path':rel.as_posix(),'sha256':sha,'status':'Preserved existing draft; see edit-record.json'})
        for im in g['images']:
            photos.append({'index':index,'product_id':g['id'],'folder':folder,'name':g['name'],
                'original_path':im['path'],'return_path':im['return_path'],'sha256':im['sha256'],
                'width':im['width'],'height':im['height'],'bytes':im['bytes'],'references':im['references']})
        pr=next((s for s in g['sources'] if s['source']=='mtech-cnc.com.ua'),None)
        wp=[s for s in g['sources'] if s['source']=='mtech.com.ua']
        g['match_status']='matched' if pr and wp else 'main-only' if wp else 'prom-only'
        g['review_candidates']=[r for r in mapping.get('review',[]) if r['main_id'] in [s['source_product_id'] for s in wp]]
        issues=[]
        for s in g['sources']:
            if not s.get('gallery'):issues.append(s['source']+': no product gallery; source shows a placeholder')
        if pr and pr['source_product_id']=='2470247861':issues.append('Prom title L1610 conflicts with code L1410')
        if pr and pr['source_product_id']=='2471377835':issues.append('Prom title 86HSE8N-BC38 conflicts with code 57HSE2N-D25')
        if pr and pr['source_product_id']=='2371312067':issues.append('Prom title F2513 conflicts with code F1616')
        if pr and pr['source_product_id'] in ['2336761969','2336764017']:issues.append('Prom title DSG16H conflicts with product code '+pr['code'])
        if g['review_candidates']:issues.append('Possible cross-site match remains separate; see match-review.csv')
        if pr and wp and any(price(s)!=price(pr) for s in wp):issues.append('UAH prices differ')
        for s in wp:
            if s['price']['schema'].get('priceCurrency')=='AUD' and any(v['currency']=='USD' for v in s['price']['values']):
                issues.append('Main-site metadata says AUD while visible price uses $')
            if not s['availability']['raw'] and s['availability'].get('schema_status'):
                issues.append('Main-site availability is metadata only; quantity unknown')
        g['data_issues']=list(dict.fromkeys(issues))
        row={'index':index,'product_id':g['id'],'folder':folder,'code':g['code'],'name':g['name'],'match_status':g['match_status'],
             'prom_id':pr['source_product_id'] if pr else '', 'main_ids':' | '.join(s['source_product_id'] for s in wp),
             'prom_name':pr['name'] if pr else '', 'main_names':' | '.join(s['name'] for s in wp),
             'prom_price_UAH':price(pr),'main_prices_UAH':' | '.join(str(price(s) or '') for s in wp),
             'main_visible_prices':' | '.join(s['price']['raw'] for s in wp),
             'prom_availability':pr['availability']['raw'] if pr else '',
             'prom_quantity':pr['availability']['displayed_quantity'] if pr else '',
             'main_availability':' | '.join(s['availability']['raw'] or ('metadata: '+str(s['availability']['schema_status'])) for s in wp),
             'photo_count':len(g['images']),'existing_edit_count':len(g['existing_edits']),
             'prom_url':pr['source_url'] if pr else '', 'main_urls':' | '.join(s['source_url'] for s in wp),
             'match_evidence':' | '.join(x['reason'] for x in g['matches']),
             'data_issues':' | '.join(g['data_issues']),
             'prom_description':pr['description_text'] if pr else '',
             'main_descriptions':'\n\n'.join(s['description_text'] for s in wp),
             'prom_category':pr['category_id'] if pr else '',
             'main_categories':' | '.join(s['category_id'] or '' for s in wp),
             'source_checked_at':' | '.join(s.get('last_checked_at',s['captured_at']) for s in g['sources']),
             'details_path':f'products/{folder}/product.json'}
        rows.append(row)
        for s in wp:
            if pr:
                comparison.append({'index':index,'prom_id':pr['source_product_id'],'main_id':s['source_product_id'],
                    'code':g['code'],'prom_name':pr['name'],'main_name':s['name'],'prom_price_UAH':price(pr),'main_price_UAH':price(s),
                    'price_difference_UAH':round(float(price(pr))-float(price(s)),2) if price(pr) and price(s) else '',
                    'prom_availability':pr['availability']['raw'],'main_availability':s['availability']['raw'],
                    'main_schema_availability':s['availability']['schema_status'],
                    'prom_photo_count':len(pr['gallery'])+len(pr['description_images']),
                    'main_photo_count':len(s['gallery'])+len(s['description_images']),
                    'prom_description_chars':len(pr['description_text']),'main_description_chars':len(s['description_text']),
                    'prom_document_links':pr.get('document_links',[]),'main_document_links':s.get('document_links',[]),
                    'data_issues':' | '.join(g['data_issues']),
                    'prom_url':pr['source_url'],'main_url':s['source_url']})
        write(OUT/'products'/folder/'product.json',encoded(g));products.append(g)
    summary={'created_at':datetime.now(timezone.utc).isoformat(),'source_counts':{'mtech-cnc.com.ua':len(old),'mtech.com.ua':len(main)},
             'combined_count':len(products),'matched_main_pages':len(decisions),
             'status_counts':dict(Counter(p['match_status'] for p in products)),
             'photo_files':len(photos),'unique_photo_hashes':len({p['sha256'] for p in photos}),
             'existing_edit_files':sum(len(p['existing_edits']) for p in products),
             'review_pairs':len(mapping.get('review',[])),'missing':missing,
             'products_without_gallery':[{'site':s['source'],'id':s['source_product_id'],'name':s['name']} for p in products for s in p['sources'] if not s.get('gallery')],
             'prom_extraction':read_json(PROM/'reports/latest.json',{}),
             'main_extraction':read_json(MAIN/'report.json',{})}
    write(OUT/'manifest.json',encoded({'summary':summary,'products':products}))
    write(OUT/'summary.json',encoded(summary))
    csvfile(OUT/'catalog.csv',rows)
    csvfile(OUT/'comparison.csv',comparison)
    csvfile(OUT/'photo-map.csv',photos)
    csvfile(OUT/'editing-log.csv',[{'original_path':r['original_path'],'return_path':r['return_path'],'status':'','notes':''} for r in photos])
    csvfile(OUT/'match-review.csv',mapping.get('review',[]),['main_id','main_name','prom_id','prom_name','reason'])
    csvfile(OUT/'variant-map.csv',mapping.get('variant_relationships',[]),['main_id','main_name','prom_id','prom_name','relationship'])
    write(OUT/'match-decisions.json',encoded(mapping))
    write(OUT/'coverage.json',encoded(read_json(MAIN/'coverage.json',{})))
    for site,root in [('prom',PROM),('main',MAIN)]:
        inv=root/'source/inventory.json'
        copy_checked(inv,OUT/'source-inventories'/(site+'.json'))
    instructions=f'''MTech — об'єднаний каталог та оригінальні фото
Дата: {summary['created_at']}
Товарів: {len(products)}. Файлів фото: {len(photos)}.

catalog.csv — спільний список. comparison.csv — порівняння зіставлених сторінок.
manifest.json — повні описи, характеристики, ціни, наявність, фото та джерела.
review/index.html — автономний перегляд каталогу з пошуком.
match-review.csv — непевні відповідності; ці записи залишені окремо.
variant-map.csv — зв'язки родини ER20 з окремими розмірами, без злиття артикулів.

Нумерація: індекс - ID товару. Спершу Prom ID за зростанням, потім wp-ID.
wp- позначає ID сторінки основного сайту, а не артикул виробника.
Артикул у колонці code збережений окремо. Один артикул може повторюватися.

originals/<індекс - ID>/ — незмінені завантажені фото та ілюстрації описів.
edited/external-v1/<індекс - ID>/ — заздалегідь створені ПОРОЖНІ папки.
Результат редагування PNG зберігайте за return_path у photo-map.csv.
Оригінали не змінюйте. Заповніть editing-log.csv: edited / unchanged / needs-review / skipped.
Фото товару, схеми та таблиці мають різне призначення; перевіряйте role у photo-map.csv.
Не змінюйте геометрію, маркування та комплектність товару при редагуванні.
existing-edits/ зберігає попередні чернетки та їхні статуси. Вони не є схваленими фото.

Дані кожного сайту збережено окремо. Ціни й наявність — публічний знімок,
а не залишки ERP. Значення AUD у метаданих основного сайту конфліктує з видимим $;
UAH взято з видимого підпису грн. Відсутня кількість не означає нуль.
Ідентичні назви та перевірені моделі зіставлено; родини, комплекти й розміри
не об'єднуються лише за подібністю. Подробиці доказів — match-decisions.json.
HTML джерел збережений без змін; локальний огляд показує екранований текст.
Посилання на документи та відео збережені; зовнішні документи й відео не завантажувалися.
Відсутні зображення та помилки джерел перелічені в summary.json.
'''
    write(OUT/'README.uk.txt',instructions)
    build_preview(products,summary)
    # Verify every source page is represented exactly once, and editor folders are empty.
    actual=[(s['source'],s['source_product_id']) for g in products for s in g['sources']]
    assert len(actual)==len(set(actual))==len(old)+len(main)
    assert products[0]['folder']=='1 - 745040041'
    assert all(not list((OUT/'edited/external-v1'/g['folder']).iterdir()) for g in products)
    verification={'source_records':len(actual),'all_source_records_represented_once':True,
                  'empty_editor_folders':len(products),'verified_originals':len(photos)}
    write(OUT/'verification.json',encoded(verification))
    dest=DATA/'mtech-combined-originals-external-v1.zip'
    with zipfile.ZipFile(dest.with_suffix('.zip.tmp'),'w',zipfile.ZIP_DEFLATED,compresslevel=5) as z:
        for p in sorted(OUT.rglob('*')):
            rel=p.relative_to(OUT).as_posix()
            if p.is_dir():
                if not any(p.iterdir()): z.writestr(rel+'/',b'')
            else:z.write(p,rel)
    with zipfile.ZipFile(dest.with_suffix('.zip.tmp')) as z:
        assert z.testzip() is None
        names=set(z.namelist())
        for g in products:assert 'edited/external-v1/'+g['folder']+'/' in names
        for row in photos:assert digest(z.read(row['original_path']))==row['sha256']
    dest.with_suffix('.zip.tmp').replace(dest)
    print(json.dumps({k:v for k,v in summary.items() if k not in ['prom_extraction','main_extraction','missing']},indent=2))
    print('ZIP',dest,round(dest.stat().st_size/1048576,1),'MiB')

def build_preview(products,summary):
    esc=lambda x:html.escape(str(x or ''),quote=True)
    cards=[]
    for p in products:
        images=''.join(f'<a href="../{esc(im["path"])}"><img loading="lazy" src="../{esc(im["path"])}" alt="{esc(p["name"])}"></a>' for im in p['images'])
        sources=[]
        for s in p['sources']:
            specs=''.join(f'<tr><th>{esc(x.get("name"))}</th><td>{esc(x.get("value"))}</td></tr>' for x in s.get('specifications',[]))
            sources.append(f'<section><h3><a href="{esc(s["source_url"])}">{esc(s["source"])}</a></h3><b>{esc(s["name"])}</b><p>{esc(s["price"]["raw"])} · {esc(s["availability"]["raw"] or s["availability"].get("schema_status"))}</p><details><summary>Опис і характеристики</summary><div class="desc">{esc(s["description_text"])}</div><table>{specs}</table></details></section>')
        cards.append(f'<article data-status="{esc(p["match_status"])}"><small>{esc(p["folder"])} · {esc(p["match_status"])}</small><h2>{esc(p["name"])}</h2><p>Артикул: {esc(p["code"])} · {len(p["images"])} фото</p><div class="photos">{images}</div><div class="sources">{"".join(sources)}</div><p class="issues">{esc("; ".join(p["data_issues"]))}</p></article>')
    page='''<!doctype html><html lang="uk"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>MTech — об'єднаний каталог</title><style>
    *{box-sizing:border-box}body{font:16px system-ui;margin:0;background:#edf1f4;color:#142735}main{max-width:1300px;margin:auto;padding:32px}header{position:sticky;top:0;background:#edf1f4f5;padding:16px 0;z-index:2}h1{margin:0 0 10px}h2{font-size:21px}input,select{font:inherit;padding:12px;border:1px solid #a4b5bf;border-radius:6px}input{width:65%}article{background:#fff;padding:24px;border:1px solid #d1dde2;border-radius:10px;margin:22px 0}.photos{display:flex;gap:12px;overflow:auto;padding:12px 0}.photos img{height:160px;width:190px;object-fit:contain;border:1px solid #ddd}.sources{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:24px}section{min-width:0}.desc{white-space:pre-wrap;overflow-wrap:anywhere;line-height:1.6}summary{cursor:pointer;padding:12px 0;font-weight:600}a{color:#006b78}td,th{text-align:left;border-bottom:1px solid #ddd;padding:8px}small,.issues{color:#586b76;font-size:13px}table{width:100%}@media(max-width:600px){main{padding:12px}input{width:100%}.sources{display:block}}</style><main><header><h1>MTech — об'єднаний каталог</h1>'''+f'<p>{len(products)} записів · {summary["source_counts"]["mtech-cnc.com.ua"]} сторінок Prom · {summary["source_counts"]["mtech.com.ua"]} сторінок основного сайту</p>'+'''<input id="q" type="search" placeholder="Назва, артикул або ID"><select id="filter"><option value="">Усі записи</option><option>matched</option><option>prom-only</option><option>main-only</option></select><span id="count"></span></header>'''+''.join(cards)+'''</main><script>const q=document.querySelector('#q'), f=document.querySelector('#filter'), cards=[...document.querySelectorAll('article')];function search(){let n=0;let v=q.value.toLowerCase();for(const c of cards){c.hidden=!(c.textContent.toLowerCase().includes(v)&&(!f.value||c.dataset.status===f.value));if(!c.hidden)n++}document.querySelector('#count').textContent=' '+n+' записів'}q.oninput=search;f.onchange=search;search();</script></html>'''
    write(OUT/'review/index.html',page)

if __name__=='__main__':main()
