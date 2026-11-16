"""Publish the reconciled archive with originals and approved photo replacements."""
import argparse
import collections
import hashlib
import html
import json
import re
import shutil
from decimal import Decimal
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urljoin, urlparse
from mtech_photo_overrides import load_overrides, apply_overrides, load_gallery_exclusions

ROOT = Path(__file__).resolve().parents[1]
ARCHIVE = ROOT / 'data/mtech-combined-archive'
CATEGORY_MAP = {
    'buttons-sensors-other':'switches-sensors-wiring','dsp-kontrollery':'controllers',
    'dvigateli':'stepper-motors','kabel-kanal':'cable-chains','krepleniya-gaek':'ball-screws',
    'lazernye-lampy':'laser-tubes','lazernye-stanki-s-chpu':'co2-lasers',
    'lineynie-napravlyaushie':'linear-guides-bearings','lineynie-podshipniki':'linear-guides-bearings',
    'linzy-i-zerkala':'lenses-mirrors','metal-cutting-laser-machines':'metal-lasers',
    'opory-shvp':'ball-screws','power-supply-units':'power-supplies','shpindeli':'spindles',
    'sistemy-oxlazhdeniya':'cooling-systems','sotoviy-stol-dlya-lazernogo-stanka':'honeycomb-tables',
    'stepper-motor-drivers':'stepper-drivers','vacuum-pump':'vacuum-pumps',
    'vytyazhka-dlya-lazernogo-stanka':'laser-extraction','zubchataya-peredacha':'gear-drives',
    'laser-welding':'laser-welding-cleaning','lazernye-golovki-komplektuyushhie':'laser-cutting-heads',
    'mufty':'flexible-couplings','cangi':'collets','invertori':'frequency-converters',
    'bloki-vysokogo-napryazheniya':'high-voltage-supplies','systemy-smazki':'lubrication-systems',
    'pneumatic':'pneumatics','bkbf':'ball-screws','gayki':'ball-screws','sfu3210':'ball-screws',
    'laser-marker':'laser-markers','gibochnye-pressa':'press-brakes','stabilizatory':'voltage-stabilizers',
}

def dump(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')

def normalized(value):
    return re.sub(r'[^A-Z0-9+]', '', value.upper().translate(str.maketrans('АВСЕНКМОРТХ', 'ABCEHKMOPTX')))

def resolve_code(name, code):
    """Correct explicit contradictory model/dimension tokens, not internal SKU shorthand."""
    if not code:
        return code
    patterns = [
        r'\bMTech\s+((?:FLE|FLA|FL|FW|FС|FC|F|L)\s*\d{4})',
        r'\bOREE\s+(F\d{4})', r'\b(DSG\d+H)\b',
        r'\b(\d+HSE\d+N-[A-Z]+\d+)\b', r'\bSFU\s*(\d{4}-C7)\b',
    ]
    for pattern in patterns:
        m = re.search(pattern, name, re.I)
        if not m:
            continue
        model = re.sub(r'\s+', '', m[1])
        if 'SFU' in pattern:
            model = 'SFU' + model
        a, b = normalized(code), normalized(model)
        # A shortened but compatible code, or a documented suffix, is not a conflict.
        if (a.startswith(b) or b.startswith(a)) and not ('SFU' in pattern and a != b):
            return code
        # Kits contain both motor and driver identifiers; don't replace them with one part.
        if 'комплект' in name.lower():
            return code
        if 'MTech' in pattern or 'OREE' in pattern:
            suffix = re.match(r'\s*(Pro(?:\s+M\+)?|ATC|HR|lite)\b', name[m.end():], re.I)
            if suffix:
                model += ' ' + suffix[1]
                if name[m.end():].strip().upper().endswith('M+') and not model.upper().endswith('M+'):
                    model += ' M+'
        return model
    m = re.search(r'(?:лінза|линза).*?\b(f-\d+)', name, re.I)
    if m and normalized(code) != normalized(m[1]):
        return m[1]
    m = re.search(r'Щітка на шпиндель\s+(\d+)\s*мм', name, re.I)
    if m and re.fullmatch(r'ASP\d+', code, re.I) and code.upper() != 'ASP' + m[1]:
        return 'ASP' + m[1]
    return code

def category(source):
    cat = source['category_id']
    if re.search(r'Обробка\s+ШВП', source['name'], re.I):
        return 'ball-screw-machining'
    if cat.startswith('shvp'):
        return 'ball-screws'
    if cat == 'frezernye-stanki':
        return 'hr-routers' if re.search(r'\bHR\b|\blite\b', source['name'], re.I) else 'pro-routers'
    if cat == 'services':
        return 'metalworking' if re.search(r'послуга|різання|гнуття',source['name'],re.I) else 'production-equipment'
    return CATEGORY_MAP.get(cat, cat)

def code_conflicts_name(name, code):
    if resolve_code(name,code) != code:
        return True
    title_variant=re.search(r'\b(?:PRO|HR|LITE|ATC)\b',name,re.I)
    code_variant=re.search(r'(?:PRO|HR|LITE|ATC)',code,re.I)
    return bool('MTECH' in name.upper() and title_variant and code_variant and title_variant[0].upper()!=code_variant[0].upper())

def price_candidates(sources):
    found = []
    for s in sources:
        p = s['price']
        for v in p.get('values', [p]):
            if v.get('currency') == 'UAH' and v.get('amount') is not None:
                found.append({'amount':str(Decimal(v['amount'])), 'source':s['source'], 'url':s['source_url'], 'raw':p['raw']})
    return found

def money(amount):
    return f'{Decimal(amount):,.2f}'.rstrip('0').rstrip('.').replace(',', ' ').replace('.', ',') + ' ₴'

def select_fields(sources):
    primary = next((s for s in sources if s['source']=='mtech.com.ua'), sources[0])
    secondary = next((s for s in sources if s is not primary), None)
    chosen = price_candidates([primary])[0]
    # Missing fields may be supplemented, but never overwrite an explicit main-site value.
    unit = primary['price'].get('unit') or (secondary or {}).get('price',{}).get('unit')
    explicit_code = primary.get('code','')
    code = explicit_code if primary['source']=='mtech.com.ua' else resolve_code(primary['name'],explicit_code)
    code_source = primary['source']
    if not code and secondary and secondary.get('code'):
        code = resolve_code(primary['name'],secondary['code'])
        code_source = secondary['source']+' (поле відсутнє на mtech.com.ua)'
    availability = dict(primary['availability'])
    if not availability.get('status') and not availability.get('raw'):
        schema=availability.get('schema_status','').rsplit('/',1)[-1]
        availability['status']={'InStock':'В наявності','OutOfStock':'Немає в наявності','PreOrder':'Під замовлення','BackOrder':'Під замовлення'}.get(schema,'Наявність уточнюйте')
        availability['raw']='Кількість уточнюйте' if schema=='InStock' else availability['status']
        availability['basis']='structured-source-status'
    return primary, chosen, unit, code, code_source, availability

def image_key(url):
    # WordPress HTML often uses a resized derivative while the archive stores the full original.
    return re.sub(r'-\d+x\d+(?=\.[a-zA-Z]+$)', '', unquote(url).split('?')[0])

class SafeDescription(HTMLParser):
    allowed = set('div p span strong b em i u h1 h2 h3 h4 h5 h6 ul ol li table thead tbody tfoot tr th td br a img dl dt dd sub sup blockquote hr'.split())
    blocked = {'script','style','iframe','object','svg','noscript'}
    def __init__(self, images, base, links):
        super().__init__(convert_charrefs=True)
        self.images, self.base, self.links = images, base, links
        self.output, self.depth, self.missing = [], 0, []
    def handle_starttag(self, tag, attributes):
        if tag in self.blocked:
            self.depth += 1
        if self.depth or tag not in self.allowed:
            return
        attrs = dict(attributes)
        safe = ''
        if tag == 'img':
            if not attrs.get('src'):
                return
            url = urljoin(self.base, attrs.get('src',''))
            path = self.images.get(url) or self.images.get(image_key(url))
            if not path:
                self.missing.append(url)
                self.output.append('<p>Зображення відсутнє в архіві.</p>')
                return
            safe = f' src="{html.escape(path, quote=True)}" alt="{html.escape(attrs.get("alt", ""), quote=True)}" loading="lazy"'
        elif tag == 'a':
            url = urljoin(self.base, attrs.get('href',''))
            if urlparse(url).scheme in {'http','https'}:
                target = self.links.get(url, url)
                safe = f' href="{html.escape(target, quote=True)}"' + ('' if target.startswith('catalogue') else ' target="_blank" rel="noopener noreferrer"')
        elif tag in {'th','td'}:
            for key in ['colspan','rowspan']:
                if str(attrs.get(key,'')).isdigit():
                    safe += f' {key}="{min(int(attrs[key]),30)}"'
        self.output.append(f'<{tag}{safe}>')
    def handle_endtag(self, tag):
        if tag in self.blocked:
            self.depth = max(0,self.depth-1)
        elif not self.depth and tag in self.allowed and tag not in {'img','br','hr'}:
            self.output.append(f'</{tag}>')
    def handle_data(self, data):
        if not self.depth:
            self.output.append(html.escape(data))

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--site-root', type=Path, default=ROOT/'sites/mtech-design-refresh/dist')
    args = parser.parse_args()
    site = args.site_root.resolve()
    assert (site/'catalogue.html').is_file()
    photo_overrides = load_overrides(site)
    gallery_exclusions = load_gallery_exclusions(site)
    manifest = json.loads((ARCHIVE/'manifest.json').read_text(encoding='utf-8'))
    entries = manifest['products']
    links = {s['source_url']:f'catalogue.html?product={p["id"]}' for p in entries for s in p['sources']}
    image_paths, hashes, copied = {}, {}, set()
    for p in entries:
        for asset in p['images']:
            source = (ARCHIVE/asset['path']).resolve()
            assert source.is_relative_to(ARCHIVE.resolve())
            digest = asset['sha256']
            if digest not in hashes:
                assert hashlib.sha256(source.read_bytes()).hexdigest() == digest, source
                target = Path('assets/catalogue-originals')/(digest + source.suffix.lower())
                (site/target).parent.mkdir(parents=True, exist_ok=True)
                if not (site/target).exists() or hashlib.sha256((site/target).read_bytes()).hexdigest() != digest:
                    shutil.copy2(source, site/target)
                hashes[digest] = target.as_posix()
                copied.add(digest)
            image_paths[asset['path']] = hashes[digest]
    products, resolutions, notes, unavailable_images = [], [], [], []
    for p in entries:
        sources = p['sources']
        primary, chosen, unit, code, code_source, availability = select_fields(sources)
        candidates = price_candidates(sources)
        name = primary['name']
        price = {'amount':chosen['amount'] if Decimal(chosen['amount'])>0 else None,'currency':'UAH',
                 'raw':money(chosen['amount']) + (('/'+unit) if unit else '') if Decimal(chosen['amount'])>0 else 'Ціну уточнюйте',
                 'unit':unit,'source_url':chosen['url']}
        if len({Decimal(c['amount']) for c in candidates})>1:
            resolutions.append({'id':p['id'],'name':name,'type':'Ціна','before':'; '.join(c['source']+': '+money(c['amount']) for c in candidates),'after':price['raw'],'reason':'Використано видиму ціну UAH з mtech.com.ua, незалежно від того, вища вона чи нижча.'})
        main_site = primary['source']=='mtech.com.ua'
        if main_site and len(sources)>1:
            for field,label in [('name','Назва'),('code','Код')]:
                values=[(s['source'],s.get(field,'')) for s in sources]
                if all(v for _,v in values) and len({v for _,v in values})>1:
                    resolutions.append({'id':p['id'],'name':name,'type':label,'before':'\n'.join(site+': '+value for site,value in values),'after':primary[field],'reason':'Пріоритет mtech.com.ua відповідно до уточнення клієнта. Значення збережено без виправлення за іншим джерелом.'})
            secondary=next(s for s in sources if s is not primary)
            if not primary.get('code') and code != secondary.get('code',''):
                resolutions.append({'id':p['id'],'name':name,'type':'Код','before':secondary['source']+': '+secondary.get('code',''),'after':code,'reason':'На головному сайті окремий код відсутній. Суперечливий код вторинного джерела узгоджено з моделлю в назві mtech.com.ua.'})
            def stock_text(s):
                a=s['availability']
                return ' / '.join(str(v) for v in [a.get('status'),a.get('raw'),a.get('schema_status'),', '.join(s.get('labels',[]))] if v)
            resolutions.append({'id':p['id'],'name':name,'type':'Наявність','before':'\n'.join(s['source']+': '+stock_text(s) for s in sources),'after':availability.get('status') or availability.get('raw'),'reason':'Використано статус mtech.com.ua. Кількість та позначки іншого сайту не перенесено до основних даних.'})
            if primary.get('description_text') != secondary.get('description_text') or primary.get('specifications') != secondary.get('specifications'):
                resolutions.append({'id':p['id'],'name':name,'type':'Опис і характеристики','before':'Два джерела містять різні описи або характеристики.','after':'Основні: mtech.com.ua. Інший опис — лише як архівне джерело.','reason':'Текст і параметри mtech.com.ua мають пріоритет. Значення з різних джерел не змішувалися.'})
        if main_site and primary.get('code') and code_conflicts_name(name,primary['code']):
            notes.append({'id':p['id'],'name':name,'type':'Код і назва головного сайту','text':f'На mtech.com.ua код «{primary["code"]}» відрізняється від моделі в назві «{name}». Збережено обидва значення головного сайту. Попереднє автоматичне виправлення коду за назвою скасовано; потрібне уточнення клієнта.'})
        if not main_site and code != primary.get('code',''):
            resolutions.append({'id':p['id'],'name':name,'type':'Код','before':primary['source']+': '+primary['code'],'after':code,'reason':'Перевіреного відповідника на mtech.com.ua немає. Збережено попереднє правило: код за моделлю в назві.'})
            old_model = re.search(r'(?:FLE|FL|F|L|DSG)\s*\d{2,4}',primary['code'],re.I)
            if old_model and normalized(old_model[0]) in normalized(primary.get('description_text','')):
                notes.append({'id':p['id'],'name':name,'type':'Опис суперечить назві','text':f'Код {code} узято з назви вторинного джерела, але його опис також містить {old_model[0]}. Перевіреного відповідника mtech.com.ua немає; параметри потребують уточнення.'})
        if Decimal(chosen['amount']) <= 0:
            notes.append({'id':p['id'],'name':name,'type':'Нульова ціна','text':'Пріоритетне джерело містить 0 грн. Показано «Ціну уточнюйте»; інший сайт не замінює цю ціну.'})
        gallery, seen, blocks, desc_images, videos, docs = [], set(), [], [], set(), []
        for s in sorted(sources, key=lambda s:s is not primary):
            urls = {}
            for a in s.get('gallery',[]) + s.get('description_images',[]):
                src = image_paths[a['package_path']]
                for key in ['source_url','download_url']:
                    if a.get(key):
                        urls[a[key]] = src
                        urls[urljoin(s['source_url'],a[key])] = src
                        urls[image_key(urljoin(s['source_url'],a[key]))] = src
            for a in sorted(s.get('gallery',[]),key=lambda a:a.get('order',0)):
                if a['sha256'] not in seen:
                    seen.add(a['sha256'])
                    src = image_paths[a['package_path']]
                    gallery.append({'src':src,'original_src':src,'alt':a.get('alt') or p['name'],'width':a['width'],'height':a['height'],'source_sha256':a['sha256'],'source':s['source']})
            for a in s.get('description_images',[]):
                src = image_paths[a['package_path']]
                desc_images.append({'src':src,'original_src':src,'source_sha256':a['sha256']})
            clean = SafeDescription(urls,s['source_url'],links)
            clean.feed(s.get('short_description_html','') + s.get('description_html',''))
            unavailable_images.extend({'id':p['id'],'url':u} for u in clean.missing)
            blocks.append({'source':s['source'],'source_url':s['source_url'],'name':s['name'],'code':s.get('code',''),
                          'resolved_code':s.get('code','') if s['source']=='mtech.com.ua' else resolve_code(s['name'],s.get('code','')),'price':s['price'],'availability':s['availability'],
                          'description_html':''.join(clean.output),'description_text':s.get('description_text',''),
                          'specifications':s.get('specifications',[]),'captured_at':s['captured_at']})
            video_text=json.dumps([s.get('videos',[]),s.get('description_links',[]),s.get('description_html',''),s.get('description_text','')],ensure_ascii=False)
            for vid in re.findall(r'(?:youtu\.be/|youtube\.com/(?:embed/|watch\?v=)|i\.ytimg\.com/vi/)([A-Za-z0-9_-]{11})',video_text):
                videos.add('https://www.youtube.com/watch?v='+vid)
            docs.extend(s.get('document_links',[]))
        if not gallery:
            notes.append({'id':p['id'],'name':p['name'],'type':'Фото','text':'Архів не містить фотографій. Показано заповнювач, без підміни зображенням іншої моделі.'})
        if p.get('review_candidates'):
            notes.append({'id':p['id'],'name':p['name'],'type':'Зіставлення','text':'Непевні відповідності залишено окремими товарами; автоматичного злиття за кодом немає.','candidates':p['review_candidates']})
        product = {'source_product_id':p['id'],'name':primary['name'],'code':code,'category_id':category(primary),
                   'source':primary['source'],'source_url':primary['source_url'],'captured_at':primary['captured_at'],
                   'source_policy':'mtech.com.ua-first','code_source':code_source,
                   'last_checked_at':primary.get('last_checked_at',primary['captured_at']), 'price':price,'price_candidates':candidates,
                   'availability':availability,'labels':primary.get('labels',[]),'gallery':gallery,'description_images':desc_images,
                   'description_html':blocks[0]['description_html'],'description_text':'\n'.join(b['description_text'] for b in blocks),
                   'specifications':blocks[0]['specifications'],'source_details':blocks,'document_links':docs,'video_urls':sorted(videos),
                   'photo_recipe':{'pipeline':'originals','sharpening':None},'match_status':p['match_status'],
                   'archive_issues':p.get('data_issues',[]),'review_candidates':p.get('review_candidates',[])}
        detail_path=f'assets/products/{p["id"]}/product.json'
        apply_overrides(product, photo_overrides, gallery_exclusions)
        dump(site/detail_path,product)
        compact={k:product[k] for k in ['source_product_id','name','code','category_id','captured_at','price','availability','labels','description_text']}
        compact.update({'gallery':gallery[:1], 'detail_url':detail_path})
        products.append(compact)
    assert not unavailable_images, unavailable_images
    for code, same in __import__('itertools').groupby(sorted((p for p in products if p['code']),key=lambda p:normalized(p['code'])),key=lambda p:normalized(p['code'])):
        same=list(same)
        if len(same)>1:
            notes.append({'id':same[0]['source_product_id'],'name':same[0]['name'],'type':'Повторний код','text':'Код '+same[0]['code']+' використано в кількох записах: '+', '.join(p['source_product_id'] for p in same)+'. Записи залишено окремими: код не є унікальним ідентифікатором товару.'})
    dump(site/'catalog-products.json',products)
    (site/'catalog-products.js').write_text('window.catalogProducts = '+json.dumps(products,ensure_ascii=False,separators=(',',':')).replace('<','\\u003c')+';\n',encoding='utf-8')
    summary={'products':len(products),'source_listings':sum(len(p['sources']) for p in entries), 'verified_merged_listings':sum(len(p['sources'])-1 for p in entries),
             'authoritative_source':'mtech.com.ua','main_source_products':sum(p['source']=='mtech.com.ua' for p in [select_fields(e['sources'])[0] for e in entries]),
             'unique_original_images':len(hashes),'price_resolutions':sum(r['type']=='Ціна' for r in resolutions),
             'code_resolutions':sum(r['type']=='Код' for r in resolutions),'resolution_types':dict(collections.Counter(r['type'] for r in resolutions)),'missing_images':unavailable_images,
             'products_without_gallery':[p['source_product_id'] for p in products if not p['gallery']],
             'categories':dict(collections.Counter(p['category_id'] for p in products)),
             'archive_sha256':hashlib.sha256((ARCHIVE/'manifest.json').read_bytes()).hexdigest()}
    report={'summary':summary,'resolutions':resolutions,'notes':notes}
    dump(ROOT/'.artifacts/mtech-full-transfer/report.json',report)
    report_html(report)
    print(json.dumps(summary,ensure_ascii=False,indent=2))

def report_html(report):
    esc=lambda v:html.escape(str(v),quote=True)
    archive=json.loads((ARCHIVE/'manifest.json').read_text(encoding='utf-8'))
    urls={p['id']:select_fields(p['sources'])[0]['source_url'] for p in archive['products']}
    def link(r):
        return f'<a href="{esc(urls[r["id"]])}" target="_blank" rel="noopener noreferrer">{esc(r["name"])}</a> ({esc(r["id"])})'
    rows=[]
    for r in report['resolutions']:
        rows.append(f'<li><h3>{link(r)}</h3><p><strong>{esc(r["type"])}</strong></p><p class="before">{esc(r["before"])}</p><p>Прийнято: <strong>{esc(r["after"])}</strong></p><p>{esc(r["reason"])}</p></li>')
    notes=[]
    for r in report['notes']:
        candidates=''
        if r.get('candidates'):
            candidates='<ul>'+''.join(f'<li>{esc(c.get("main_name",""))} ↔ {esc(c.get("prom_name",""))}</li>' for c in r['candidates'])+'</ul>'
        notes.append(f'<li>{link(r)}. {esc(r["text"])}{candidates}</li>')
    counts='; '.join(esc(k)+': '+str(v) for k,v in report['summary']['resolution_types'].items())
    body=f'''<!doctype html><html lang="uk"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>MTech — рішення за головним джерелом</title><style>body{{font:16px/1.65 system-ui;max-width:1100px;margin:40px auto;padding:0 24px;color:#182733;background:#f3f5f7}}a{{color:#185f89}}li{{margin:24px 0}}h3{{font-size:17px}}p{{margin:6px 0}}.before{{white-space:pre-line}}@media print{{body{{background:white;margin:0;padding:0;font-size:11pt}}li{{break-inside:avoid}}}}@media(max-width:600px){{body{{padding:0 16px}}h1{{font-size:26px}}}}</style><h1>Рішення щодо конфліктів каталогу</h1><p>Оновлено 28.10.2026. Джерело даних — архів від 26.10.2026.</p><p><strong>Головне джерело — mtech.com.ua.</strong> Це рішення замінює попереднє правило вибору вищої ціни. Усього {report['summary']['products']} записів, із них {report['summary']['main_source_products']} мають основні дані mtech.com.ua.</p><p>{counts}. Приміток для перевірки: {len(report['notes'])}.</p><h2>Правила</h2><ul><li>За перевіреної відповідності беруться назва, код, видима ціна UAH, опис, характеристики та статус наявності mtech.com.ua. Нижча ціна також має пріоритет.</li><li>Якщо головний сайт має власну суперечність між кодом і назвою, обидва вихідні значення збережено і винесено на уточнення. Код не переписується автоматично.</li><li>Коли головний сайт не має окремого коду або одиниці продажу, поле доповнюється даними перевіреного відповідника. За відсутності відповідника використовуються дані mtech-cnc.com.ua та попереднє правило коду за назвою.</li><li>Статус наявності головного сайту не означає залишок ERP. Кількість іншого сайту не додається. Нульова ціна означає «Ціну уточнюйте».</li><li>Оригінальні фото збережено, галерея головного сайту показується першою. Вторинні описи доступні лише як архівні відомості. Непевні відповідності не об’єднуються без підтвердження.</li></ul><h2>Прийняті рішення</h2><ol>{''.join(rows)}</ol><h2>Питання для уточнення</h2><ul>{''.join(notes)}</ul></html>'''
    (ROOT/'.artifacts/mtech-full-transfer/Звіт-перенесення.html').write_text(body,encoding='utf-8')
    target=ROOT/'outputs/mtech-client-conflicts-20261028/MTech — список конфліктів та рішень.html'
    target.parent.mkdir(parents=True,exist_ok=True)
    target.write_text(body,encoding='utf-8')

if __name__=='__main__':
    main()
