"""Archive the public main-site catalogue and retain source evidence."""
import argparse
import concurrent.futures
import json
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from extract_mtech_sample import Archive, encoded, write, read_json, text, digest

BASE = 'https://mtech.com.ua'
ROOT = Path(__file__).resolve().parents[1] / 'data/mtech-main-archive'
OLD = ROOT.parent / 'mtech-product-archive'

def fetch(url):
    for attempt in range(4):
        try:
            r = requests.get(url, timeout=50, headers={'User-Agent':'MTechProductArchive/1.0 (local catalogue archive)'})
            r.raise_for_status()
            return r.content
        except requests.RequestException:
            if attempt == 3: raise
            time.sleep(2 ** attempt)

def discover():
    urls, categories, visited, errors = set(), [BASE+'/shop/'], set(), []
    for name in ['sitemap_index.xml','product-sitemap.xml','product_cat-sitemap.xml']:
        try:
            content=fetch(BASE+'/'+name)
            write(ROOT/'source'/name,content)
            tree=ET.fromstring(content.strip())
            for element in tree.iter('{http://www.sitemaps.org/schemas/sitemap/0.9}loc'):
                u=element.text
                if urlparse(u).path.startswith('/product/'): urls.add(u)
                elif urlparse(u).path.startswith('/product-category/'): categories.append(u)
        except Exception as e: errors.append({'url':BASE+'/'+name,'error':str(e)})
    sitemap_urls=set(urls)
    listings={}
    while categories:
        url=categories.pop(0)
        if url in visited: continue
        visited.add(url)
        try:
            content=fetch(url)
            write(ROOT/'source/categories'/(digest(url.encode())[:20]+'.html'),content)
            s=BeautifulSoup(content,'html.parser')
            for li in s.select('li.product'):
                a=li.select_one('a[href]')
                if a and '/product/' in a['href']:
                    listings[a['href']]=text(li)
            for a in s.select('a[href]'):
                u=urljoin(url,a['href']).split('#')[0]
                p=urlparse(u)
                if p.hostname!='mtech.com.ua' or p.query: continue
                if p.path.startswith('/product/'): urls.add(u)
                elif p.path.startswith('/product-category/') or re.match('/shop/page/\\d+/',p.path):
                    if u not in visited and u not in categories: categories.append(u)
            if len(visited)%10==0: print(f'Discovery: {len(visited)} categories, {len(urls)} products',flush=True)
        except Exception as e: errors.append({'url':url,'error':str(e)})
    inventory={'captured_at':datetime.now(timezone.utc).isoformat(),'products':sorted(urls),
               'sitemap_count':len(sitemap_urls),'categories':sorted(visited),
               'extra_from_categories':sorted(urls-sitemap_urls),'errors':errors,'listing_text':listings}
    write(ROOT/'source/inventory.json',encoded(inventory))
    return inventory

def parse(url,content,listing):
    s=BeautifulSoup(content,'html.parser')
    body=s.select_one('div.product[id^="product-"]')
    if not body or not body.select_one('h1'): raise ValueError('No product container')
    pid=body['id'].split('-')[-1]
    summary=body.select_one('.summary')
    desc=body.select_one('#tab-description')
    price=summary.select_one('.price')
    raw=text(price)
    price_values=[]
    for m in re.finditer(r'\$\s*([\d,]+(?:\.\d+)?)|([\d\s\xa0]+(?:[.,]\d+)?)\s*(?:грн|₴)',raw):
        amount=re.sub(r'\s','',m[1] or m[2]).replace(',','') if m[1] else re.sub(r'\s','',m[2]).replace(',','.')
        price_values.append({'amount':amount,'currency':'USD' if m[1] else 'UAH','basis':'visible-dollar-symbol' if m[1] else 'visible-UAH-label'})
    meta=lambda key: summary.select_one(f'[itemprop="{key}"]')
    schema={k:(meta(k).get('content') or meta(k).get('href')) if meta(k) else None for k in ['price','priceCurrency','availability']}
    imgs=[]
    for a in body.select('.images a[href]'):
        if a.find('img'):
            imgs.append({'url':urljoin(url,a['href']),'alt':a.img.get('alt',''),'role':'gallery'})
    for region in [desc,summary.select_one('[itemprop="description"]')]:
        for im in region.select('img') if region else []:
            u=im.get('src') or im.get('data-src')
            if u:
                candidates=[]
                for item in im.get('srcset','').split(','):
                    parts=item.strip().split()
                    if len(parts)==2 and parts[1].endswith('w'):
                        try: candidates.append((int(parts[1][:-1]),parts[0]))
                        except ValueError: pass
                if candidates: u=max(candidates)[1]
                imgs.append({'url':urljoin(url,u),'alt':im.get('alt',''),'role':'description'})
    unique={}
    for im in imgs: unique.setdefault((im['role'],im['url']),im)
    specs=[]
    for row in body.select('.shop_attributes tr'):
        cells=row.find_all(['th','td'])
        if len(cells)>1: specs.append({'name':text(cells[0]),'value':text(cells[1])})
    cats=[{'name':text(a),'url':a['href']} for a in summary.select('.posted_in a')]
    links=[{'label':text(a),'url':urljoin(url,a['href'])} for a in desc.select('a[href]')] if desc else []
    return {'source':'mtech.com.ua','source_product_id':pid,'source_url':url,'language':'ru',
            'name':text(body.select_one('h1')),'code':text(summary.select_one('.sku')),
            'listing_text':listing,'category_id':cats[-1]['url'].rstrip('/').split('/')[-1] if cats else None,
            'categories':cats,'description_html':str(desc or ''),'description_text':desc.get_text('\n',strip=True) if desc else '',
            'short_description_html':str(summary.select_one('[itemprop="description"]') or ''),
            'specifications':specs,'description_tables':[[[text(c) for c in tr.find_all(['td','th'],recursive=False)] for tr in tb.find_all('tr')] for tb in desc.select('table')] if desc else [],
            'price':{'raw':raw,'values':price_values,'schema':schema},
            'availability':{'raw':text(summary.select_one('.stock')),'schema_status':schema['availability'],
                            'css_status':[x for x in body.get('class',[]) if x in ['instock','outofstock','onbackorder']],
                            'displayed_quantity':None,'is_erp_stock':False},
            'source_image_requests':list(unique.values()),'description_links':links,
            'document_links':[x for x in links if re.search(r'\.(pdf|docx?|xlsx?|zip)(?:$|\?)',x['url'],re.I)],
            'videos':[x.get('src') for x in body.select('iframe[src]')],
            'alternate_languages':[{k:l.get(k) for k in ['hreflang','href']} for l in s.select('link[hreflang]')],
            'captured_at':datetime.now(timezone.utc).isoformat()}

def main():
    parser=argparse.ArgumentParser(); parser.add_argument('--resume',action='store_true'); args=parser.parse_args()
    inv=read_json(ROOT/'source/inventory.json',{}) if args.resume else {}
    if not inv: inv=discover()
    errors=list(inv['errors'])
    existing=read_json(ROOT/'page-records.json',{}) if args.resume else {}
    def job(url):
        content=fetch(url)
        record=parse(url,content,inv['listing_text'].get(url,''))
        write(ROOT/'source/products'/(record['source_product_id']+'.html'),content)
        return record
    todo=[u for u in inv['products'] if u not in existing]
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        futures={pool.submit(job,u):u for u in todo}
        for f in concurrent.futures.as_completed(futures):
            u=futures[f]
            try: existing[u]=f.result()
            except Exception as e: errors.append({'phase':'product','url':u,'error':str(e)})
            if len(existing)%20==0:
                write(ROOT/'page-records.json',encoded(existing)); print(f'Parsed {len(existing)}/{len(inv["products"])}',flush=True)
    write(ROOT/'page-records.json',encoded(existing))
    archive=Archive(argparse.Namespace(output=ROOT,refresh_images=False))
    old_assets=read_json(OLD/'asset-index.json',{})
    for k,v in old_assets.items():
        if k not in archive.assets and (OLD/v['path']).exists():
            archive.assets[k]={**v,'path':'../mtech-product-archive/'+v['path']}
    products=[]
    for i,p in enumerate(existing.values(),1):
        p['gallery']=[]; p['description_images']=[]; p['missing_images']=[]
        for order,im in enumerate(p['source_image_requests'],1):
            try:
                image=archive.asset('wp-'+p['source_product_id'],im['url'],None,role=im['role'],order=order,alt=im['alt'])
                p['gallery' if im['role']=='gallery' else 'description_images'].append(image)
            except Exception as e:
                p['missing_images'].append(im); errors.append({'phase':'image','id':p['source_product_id'],'url':im['url'],'error':str(e)})
        products.append(p)
        write(ROOT/'products'/p['source_product_id']/'current.json',encoded(p))
        if i%20==0: print(f'Images {i}/{len(existing)}: downloaded {archive.report["downloaded"]}, reused {archive.report["reused"]}',flush=True)
    write(ROOT/'manifest.json',encoded({'products':products}))
    write(ROOT/'report.json',encoded({'products':len(products),'discovered':len(inv['products']),'errors':errors,'assets':archive.report}))
    print(f'DONE {len(products)} products, {len(errors)} errors',flush=True)

if __name__=='__main__': main()
