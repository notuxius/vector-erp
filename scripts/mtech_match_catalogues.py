"""Prepare evidence for conservative cross-site product matching."""
import concurrent.futures
import json
import re
import unicodedata
from pathlib import Path
from difflib import SequenceMatcher
from bs4 import BeautifulSoup
from extract_mtech_main import fetch
from extract_mtech_sample import write, encoded, read_json

DATA=Path(__file__).resolve().parents[1]/'data'
OLD=DATA/'mtech-product-archive'
MAIN=DATA/'mtech-main-archive'

def norm(value):
    value=unicodedata.normalize('NFKC',value).lower().replace('ё','е').replace('×','x').replace('х','x').replace(',','.')
    return re.sub(r'[^a-zа-яіїєґ0-9]+','',value)

def tokens(value):
    value=unicodedata.normalize('NFKC',value).upper().replace('Х','X').replace('×','X').replace(',','.')
    return set(re.findall(r'[A-ZА-ЯІЇЄҐ]*\d[\w./-]*',value))

def get_ru():
    ps=read_json(OLD/'manifest.json',{})['products']
    records=read_json(MAIN/'prom-ru.json',{})
    def job(p):
        u=p['source_url'].replace('/ua/','/')
        content=fetch(u); s=BeautifulSoup(content,'html.parser')
        desc=s.select_one('[data-qaid="product_description"]')
        if not s.h1: raise ValueError('No product title')
        write(MAIN/'source/prom-ru'/(p['source_product_id']+'.html'),content)
        return {'id':p['source_product_id'],'url':u,'name':s.h1.get_text(' ',strip=True),
                'description_text':desc.get_text('\n',strip=True) if desc else '',
                'description_html':str(desc or '')}
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        fs={pool.submit(job,p):p for p in ps if p['source_product_id'] not in records}
        for f in concurrent.futures.as_completed(fs):
            p=fs[f]
            try: records[p['source_product_id']]=f.result()
            except Exception as e: print('ERROR',p['source_product_id'],e,flush=True)
            if len(records)%25==0:
                write(MAIN/'prom-ru.json',encoded(records));print('RU',len(records),flush=True)
    write(MAIN/'prom-ru.json',encoded(records))

def compare():
    prom=read_json(OLD/'manifest.json',{})['products']
    wp=list(read_json(MAIN/'page-records.json',{}).values())
    ru=read_json(MAIN/'prom-ru.json',{})
    result=[]
    for w in wp:
        wn=norm(w['name']); wt=tokens(w['name'])
        candidates=[]
        for p in prom:
            pn=ru.get(p['source_product_id'],{}).get('name',p['name']); pt=tokens(pn)
            score=SequenceMatcher(None,wn,norm(pn)).ratio()
            code=norm(p['code'])
            code_match=bool(code and len(code)>=4 and code in wn)
            overlap=wt & pt
            exact=wn==norm(pn)
            # Candidate generation only; approval requires reviewed identity evidence.
            if exact or score>.48 or code_match or overlap:
                rank=score+(0.3 if code_match else 0)+(.4 if wt==pt and wt else 0)
                candidates.append({'prom_id':p['source_product_id'],'prom_name':pn,'code':p['code'],
                                   'similarity':round(score,3),'rank':round(rank,3),
                                   'code_in_name':code_match,'same_tokens':bool(wt and wt==pt),
                                   'main_tokens':sorted(wt),'prom_tokens':sorted(pt),'exact_name':exact})
        candidates.sort(key=lambda x:x['rank'],reverse=True)
        result.append({'main_id':w['source_product_id'],'main_name':w['name'],'category':w['category_id'],'candidates':candidates[:5]})
    write(MAIN/'match-candidates.json',encoded(result))
    lines=[]
    for r in sorted(result,key=lambda x:(x['category'] or '',int(x['main_id']))):
        lines.append(f"\nWP {r['main_id']} [{r['category']}] {r['main_name']}")
        for c in r['candidates'][:3]:
            lines.append(f"  {c['prom_id']} ({c['similarity']},code={c['code_in_name']},tokens={c['same_tokens']}) {c['prom_name']} [SKU {c['code']}]")
    write(MAIN/'match-review.txt','\n'.join(lines))
    print('Compared',len(wp),len(prom))

if __name__=='__main__':
    import sys
    get_ru() if '--fetch-ru' in sys.argv else compare()
