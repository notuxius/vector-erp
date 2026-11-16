"""Check source coverage, prices, text, image integrity and safe HTML for the full export."""
import hashlib
import json
import re
from decimal import Decimal
from pathlib import Path
from html.parser import HTMLParser

ROOT=Path(__file__).resolve().parents[1]
SITE=ROOT/'sites/mtech-design-refresh/dist'

class AuditHTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.data,self.nodes,self.depth=[],[],0
    def handle_starttag(self,tag,attrs):
        self.nodes.append((tag,dict(attrs)))
        if tag in {'script','style','iframe','object','svg','noscript'}:
            self.depth+=1
    def handle_endtag(self,tag):
        if tag in {'script','style','iframe','object','svg','noscript'}:
            self.depth=max(0,self.depth-1)
    def handle_data(self,data):
        if not self.depth:self.data.append(data)

def text(html):
    parsed=AuditHTML();parsed.feed(html)
    return re.sub(r'\s+','',''.join(parsed.data))

def main():
    entries=json.loads((ROOT/'data/mtech-combined-archive/manifest.json').read_text(encoding='utf-8'))['products']
    index=json.loads((SITE/'catalog-products.json').read_text(encoding='utf-8'))
    assert len(index)==len({p['source_product_id'] for p in index})==len(entries)
    assert {p['source_product_id'] for p in index}=={p['id'] for p in entries}
    cats=set(re.findall(r"\['([a-z0-9-]+)',",(SITE/'catalog-data.js').read_text(encoding='utf-8')))
    verified_images=set()
    overrides_path=SITE.parent/'content/photo-overrides.json'
    overrides=json.loads(overrides_path.read_text(encoding='utf-8'))['images'] if overrides_path.exists() else {}
    exclusions=json.loads(overrides_path.read_text(encoding='utf-8')).get('gallery_exclusions',{}) if overrides_path.exists() else {}
    verified_edits=set()
    source_count=0
    for original,row in zip(entries,index):
        assert row['source_product_id']==original['id']
        assert row['category_id'] in cats,(row['source_product_id'],row['category_id'])
        product=json.loads((SITE/row['detail_url']).read_text(encoding='utf-8'))
        primary=next((s for s in original['sources'] if s['source']=='mtech.com.ua'),original['sources'][0])
        assert product['source_url']==primary['source_url']
        assert product['name']==primary['name']
        assert product['specifications']==primary.get('specifications',[])
        assert text(product['description_html'])==text(primary.get('short_description_html','')+primary.get('description_html',''))
        assert product['source_details'][0]['source_url']==primary['source_url']
        assert product['labels']==primary.get('labels',[])
        if primary['source']=='mtech.com.ua':
            if primary.get('code'):assert product['code']==primary['code']
            assert product['availability'].get('displayed_quantity')==primary['availability'].get('displayed_quantity')
            if primary['availability']['schema_status'].endswith('/InStock'):
                assert product['availability']['status']=='В наявності'
            if primary['availability']['schema_status'].endswith('/OutOfStock'):
                assert product['availability']['status']=='Немає в наявності'
        if primary.get('gallery'):
            assert product['gallery'][0]['source_sha256']==primary['gallery'][0]['sha256']
        amounts=[]
        for source in original['sources']:
            price=source['price']
            amounts.extend(Decimal(v['amount']) for v in price.get('values',[price]) if v.get('currency')=='UAH' and v.get('amount') is not None)
            transferred=next(s for s in product['source_details'] if s['source_url']==source['source_url'])
            assert transferred['specifications']==source.get('specifications',[])
            assert transferred['price']==price
            assert transferred['availability']==source['availability']
            assert text(transferred['description_html'])==text(source.get('short_description_html','')+source.get('description_html','')),source['source_url']
            parsed=AuditHTML();parsed.feed(transferred['description_html'])
            for tag,attrs in parsed.nodes:
                assert tag not in {'script','style','iframe','object','svg'}
                assert not any(k.startswith('on') for k in attrs)
                if tag=='img':
                    assert (attrs['src'].startswith('assets/catalogue-originals/') or attrs['src'] in {o['src'] for o in overrides.values()}) and (SITE/attrs['src']).is_file()
            source_count+=1
        price=primary['price']
        expected=next(Decimal(v['amount']) for v in price.get('values',[price]) if v.get('currency')=='UAH' and v.get('amount') is not None)
        assert (Decimal(product['price']['amount']) if product['price']['amount'] is not None else Decimal(0))==expected
        expected_gallery={a['sha256'] for s in original['sources'] for a in s.get('gallery',[])}
        expected_gallery-=set(exclusions.get(original['id'],[]))
        assert {a['source_sha256'] for a in product['gallery']}==expected_gallery
        for a in product['gallery']+product['description_images']:
            override=overrides.get(a['source_sha256'])
            if override and product['source_product_id'] in override.get('exclude_products',[]):
                override=None
            assert a['src']==(override['src'] if override else a['original_src'])
            if override:
                assert hashlib.sha256((SITE/a['src']).read_bytes()).hexdigest()==override['sha256']==a['edited_sha256']
                verified_edits.add(a['src'])
            if a['original_src'] not in verified_images:
                assert hashlib.sha256((SITE/a['original_src']).read_bytes()).hexdigest()==a['source_sha256']
                verified_images.add(a['original_src'])
        assert row['gallery']==product['gallery'][:1]
    expected={'2371312067':'F2513 Pro','2470247861':'L1610','2471377835':'86HSE8N-BC38','2695712295':'F2613 Pro M+','2739431147':'F3015','2798948908':'ASP85','2872984602':'f-38'}
    byid={p['source_product_id']:p for p in index}
    for id,code in expected.items():
        assert byid[id]['code']==code,(id,byid[id]['code'])
    assert byid['2479060431']['code']=='FL3015' # Valid shared family code, different wattage.
    assert byid['2371314638']['code']=='F3020 PRO' # Exact main-site code overrides title-based correction.
    assert byid['745040041']['price']['amount']=='29250' # Lower authoritative price wins over 29272.
    assert byid['2337803974']['code']=='HSS57/57HSE2N-D25' # Do not reduce a kit to one component.
    assert byid['2889212782']['code']=='FL6020х2' # Keep legitimate exchange-table suffix.
    assert byid['745073123']['price']['unit']=='м'
    assert byid['wp-5334']['price']['amount'] is None and not byid['wp-5334']['gallery']
    assert not (SITE/'catalogue-transfer-report.html').exists()
    assert not (SITE/'catalogue-transfer-report.json').exists()
    for digest, override in overrides.items():
        assert hashlib.sha256((SITE/override['src']).read_bytes()).hexdigest()==override['sha256']
        verified_edits.add(override['src'])
        originals=list((SITE/'assets/catalogue-originals').glob(digest+'.*'))
        assert len(originals)==1 and hashlib.sha256(originals[0].read_bytes()).hexdigest()==digest
        verified_images.add(originals[0].relative_to(SITE).as_posix())
    assert len(verified_edits)==len(overrides)
    result={'source_policy':'mtech.com.ua-first','products_verified':len(index),'source_descriptions_verified':source_count,'original_image_hashes_verified':len(verified_images),'edited_images_verified':len(verified_edits),'failures':[]}
    (ROOT/'.artifacts/mtech-full-transfer/verification.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps(result,indent=2))

if __name__=='__main__':
    main()
