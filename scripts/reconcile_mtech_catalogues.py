"""Reviewed matching rules. Fuzzy similarity only generates review candidates."""
import re
from collections import Counter
from pathlib import Path
from extract_mtech_sample import read_json, write, encoded
from mtech_match_catalogues import norm, tokens

D=Path(__file__).resolve().parents[1]/'data'
M=D/'mtech-main-archive'; P=D/'mtech-product-archive'

def main():
    raw=read_json(M/'manifest.json',{})['products']
    # The site has a second URL resolving to the same WordPress product ID.
    unique={}
    for p in raw:
        pid=p['source_product_id']
        if pid in unique:
            unique[pid].setdefault('alias_urls',[]).append(p['source_url'])
        else: unique[pid]=p
    wp=list(unique.values())
    write(M/'manifest.json',encoded({'products':wp}))
    proms=read_json(P/'manifest.json',{})['products']; pd={p['source_product_id']:p for p in proms}
    candidates=read_json(M/'match-candidates.json',[]);cd={r['main_id']:r for r in candidates}
    categories={
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
    }
    matches={}; review=[]
    for w in wp:
        wid=w['source_product_id'];cat=w['category_id'];r=cd[wid]
        for c in r['candidates']:
            p=pd[c['prom_id']]
            compatible=p['category_id']==categories.get(cat)
            if cat.startswith('shvp') or cat=='sfu3210':compatible=p['category_id']=='ball-screws'
            if cat=='frezernye-stanki':compatible=p['category_id'] in ['hr-routers','pro-routers']
            if not compatible or not c['same_tokens']:continue
            if c['similarity']<.55:continue
            if cat=='frezernye-stanki':
                variants=lambda n:set(re.findall(r'\b(?:HR|PRO|ATC|LITE)\b',n.upper()))
                if variants(w['name'])!=variants(p['name']):continue
                if ('M+' in w['name'].upper())!=('M+' in p['name'].upper()):continue
            if wid=='6112' and p['source_product_id']!='2337860553':continue
            if wid=='2478' and p['code']!='DSG16H':continue
            matches[wid]={'prom_id':p['source_product_id'],'reason':'Same product category and complete model/dimension tokens; names and variant suffixes checked.',
                          'main_name':w['name'],'prom_name':p['name'],'main_tokens':sorted(tokens(w['name'])),'prom_tokens':sorted(tokens(p['name']))}
            break
    manual={
        '3528':('2334443262','Same tool-height sensor with movable contact and magnet; generic model name.'),
        '3530':('2334443983','Same plain tool-height sensor; separate from magnetic/movable-contact model.'),
        '2204':('2335530829','Same ER20 13-piece collet set, 0.5–13 mm; not an individual collet.'),
        '6119':('2337803974','Same kit explicitly containing HSS57 driver and 57HSE2N-D25 motor.'),
        '6221':('2602680777','Same HBK-4000 industrial vacuum; categorized differently on the sites.'),
        '2202':('1028400984','Same complete SBR20LUU extended-bearing designation; 20 mm guide diameter.'),
        '2267':('2333331680','Same complete SBR12LUU extended-bearing designation; 12 mm guide diameter.'),
        '2114':('2336698590','Same SFU2010-C7 single nut; 20 mm diameter, 10 mm lead, C7 precision.'),
        '2248':('2336702512','Same SFU2510-C7 single nut; 25 mm diameter, 10 mm lead, C7 precision.'),
        '2013':('2337717830','HY/HYCNC 2.2 kW water-cooled spindle: both descriptions agree on ER20, 6000–24000 rpm, 220 V, 400 Hz, length 233 mm and diameter 80 mm.'),
        '5983':('2425465698','Both descriptions specify the same manual lubrication tank with approximately 0.65 L capacity.'),
        '5986':('2425462126','Both descriptions specify the automatic lubrication tank with one display and approximately 1 L capacity.'),
        '5988':('2425462688','Both descriptions offer automatic lubrication tanks in 1.8 L and 2 L variants; kept separate from the 1 L listing.'),
        '3128':('2336858850','Descriptions agree on 1:5 belt reducer, M1.25 helical rack, NEMA34, 140 mm wheel, 14 mm keyed shaft and HTD 550 5M belt.'),
        '5927':('2394142763','Main description explicitly identifies PLF090; both specify 1:40 ratio, 19 mm input and 20 mm output shafts.'),
        '5721':('2334427563','Both describe HYCNC 12 mm round inductive sensor, 4 mm range, NPN NO output and IP67.'),
        '5705':('2334357261','Same blue YIJIA latching illuminated button, 22 mm mounting and normally-open contacts.'),
        '5789':('2341127001','Same 550 W centrifugal exhaust fan for laser machines.'),
        '5794':('2341147859','Same 750 W centrifugal exhaust fan for laser machines.'),
    }
    for wid,(pid,reason) in manual.items():
        matches[wid]={'prom_id':pid,'reason':reason,'main_name':unique[wid]['name'],'prom_name':pd[pid]['name']}
    # Explicit source contradictions and family/variant relationships require review.
    uncertain={
      '2186':('1164965094','Same YL-U1 model, but power range differs: 80–130 versus 80–120 W.'),
      '2242':('1164980305','Same YL-U2 model, but power range differs: 130–180 versus 120–180 W.'),
      '2278':('1167077138','Generic E-stop versus specified LAY39 model; identity not fully established.'),
      '2281':('2334446362','Same 0–30 mA range, but main site does not specify HUA 85C1 model.'),
      '5705':('2334357261','Both blue latching buttons; exact manufacturer/model not established.'),
      '5721':('2334427563','Generic HYCNC inductive sensor versus explicitly 12 mm sensor.'),
      '2206':('2335496381','Main listing is an ER20 0.5–13 mm family; Prom has separate size SKUs. Keep parent separate.'),
      '2128':('2336825709','RDC6445G versus RDC6445S controller suffix differs.'),
      '6122':('2471377835','Prom SKU says 57HSE2N-D25 but its title says 86HSE8N-BC38.'),
      '3860':('2371312067','Prom code F1616 conflicts with its title F2513 Pro.'),
      '2107':('2336631176','SFU1605-C7 model agrees but Prom dimensions say 16x0.5 rather than 16x5.'),
      '2759':('2336630728','SFU1204-C7 model agrees but Prom dimensions say 12x0.5 rather than 12x4.'),
      '2911':('2336761969','Prom code DSG20H conflicts with title DSG16H.'),
      '2912':('2336764017','Prom code DSG25H conflicts with title DSG16H.'),
      '2803':('2979107502','Same nominal 30 W / 200x200 marking class; brand/model not confirmed.'),
      '6344':('2701678701','Both 5 W UV markers; exact model and marking area not confirmed.'),
      '3011':('2479062221','Same FLE3015 enclosed machine family; main title does not fix the 6000 W configuration.'),
      '6289':('2739412052','AORE versus OREE brand spelling, similar F3015 3 kW; identity unconfirmed.'),
      '6295':('2739431147','AORE versus OREE brand spelling, similar F3015 2 kW; identity unconfirmed.'),
      '6363':('2791920301','Same 4V210-08 valve family; compare coil voltage before merging.'),
      '5971':('2425454061','48 V GY supply descriptions conflict on power: 0.8 kW main versus 0.6 kW Prom.'),
      '2013':('2337717830','Same nominal 2.2 kW water-cooled spindle; exact HY model equivalence unconfirmed.'),
      '2014':('2337720537','Main title 3 kW versus Prom 3.2 kW; model/power conflict.'),
      '2249':('2337692399','Same nominal HQD 2.2 kW air-cooled spindle; source frequency/model needs verification.'),
      '3533':('2337699963','Same power 3.5 kW but main model GDF46 differs from Prom GDF53.'),
      '3538':('2337702451','Same power 4.5 kW but main model GDF46 differs from Prom GDF60.'),
      '5983':('2425465698','Generic manual lubrication tank; check capacity and exact model.'),
      '5986':('2425462126','Generic automatic lubrication tank; distinguish two main-site listings.'),
      '5988':('2425462126','Generic automatic lubrication tank; distinguish two main-site listings.'),
      '3128':('2336858850','Same rack module M1.25; main ratio 1:5 needs confirmation on Prom.'),
      '3133':('2336842418','Rack length differs: 670 mm versus 640 mm.'),
      '5927':('2394142763','Same 1:40 ratio but main title lacks PLF090 model.'),
    }
    for wid,(pid,reason) in uncertain.items():
        if wid in manual:continue
        if pid not in pd:
            # Do not invent product identifiers: use category/model candidates only.
            print('UNRESOLVED REVIEW ID',wid,pid);continue
        matches.pop(wid,None)
        review.append({'main_id':wid,'main_name':unique[wid]['name'],'prom_id':pid,'prom_name':pd[pid]['name'],'reason':reason})
    jaw_pairs={'2765':'2334538536','2772':'745902297','2773':'2334538894','2774':'2334539585',
               '5742':'2351992435','5744':'2351996146','5745':'2351996342','5746':'2351997227',
               '5748':'2351998470','5749':'2352000411','5750':'2352049738','5751':'2352050899',
               '5752':'2352051324','5753':'2352052035'}
    for wid,pid in jaw_pairs.items():
        assert 'Кулачков' in unique[wid]['name'] and 'Кулачков' in pd[pid]['description_text']
        matches[wid]={'prom_id':pid,'reason':'Jaw-coupling type confirmed in both descriptions; exact body diameter/length and both shaft bores agree (bore order may be reversed).',
                      'main_name':unique[wid]['name'],'prom_name':pd[pid]['name']}
    # Keep the remaining couplings separate where bore size or structural type conflicts.
    for w in wp:
        if w['category_id']=='mufty' and w['source_product_id'] not in matches:
            c=cd[w['source_product_id']]['candidates'][0]
            review.append({'main_id':w['source_product_id'],'main_name':w['name'],'prom_id':c['prom_id'],'prom_name':c['prom_name'],
                'reason':'Check coupling type and exact body/bore dimensions; similar size alone is not identity.'})
    result={'policy':'Conservative model-and-variant matching; ambiguous source conflicts retained as separate records.',
            'matches':matches,'review':review,'main_unique_products':len(wp),'source_aliases':{p['source_product_id']:p['alias_urls'] for p in wp if p.get('alias_urls')}}
    result['variant_relationships']=[{'main_id':'2206','main_name':unique['2206']['name'],
        'prom_id':p['source_product_id'],'prom_name':p['name'],'relationship':'Main-site size-family listing; Prom individual size variant. Not merged.'}
        for p in proms if p['name'].startswith('Цанга ER20 ')]
    write(M/'approved-matches.json',encoded(result))
    unmatched=[p for p in wp if p['source_product_id'] not in matches]
    write(M/'unmatched-main.txt','\n'.join(f"{p['source_product_id']} [{p['category_id']}] {p['name']}" for p in sorted(unmatched,key=lambda x:(x['category_id'] or '',int(x['source_product_id'])))))
    print('Unique main',len(wp),'matched',len(matches),'review',len(review),'unmatched',len(unmatched))

if __name__=='__main__':main()
