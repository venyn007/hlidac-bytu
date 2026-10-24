"""Personal rental watcher: public pages, deterministic filters, no paid APIs."""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import time
import unicodedata
from urllib.parse import urlencode, urljoin, urlsplit, urlunsplit, parse_qsl
from datetime import datetime, timezone
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent
CFG = json.loads((ROOT / 'config.json').read_text())

def norm(s):
    return re.sub(r'\s+', ' ', ''.join(c for c in unicodedata.normalize('NFKD', str(s))
                                    if not unicodedata.combining(c) and unicodedata.category(c) != 'Cf')).strip().lower()

def number(s):
    return float(re.sub(r'[\s.\u200b\u200d]', '', str(s)).replace(',', '.'))

def amount(s):
    s = ''.join(c for c in str(s) if unicodedata.category(c) != 'Cf')
    m = re.search(r'(\d[\d .\u00a0]*\d|\d)\s*(?:,-\s*)?Kč', s, re.I)
    return number(m[1]) if m else None

def get(url):
    # Credentials, when present, are passed only through stdin, never command arguments.
    p = subprocess.run(['curl', '--silent', '--location', '--max-time', '25',
                        '--max-filesize', '12000000', '--config', '-', '--write-out', '\n%{http_code}'],
                       input='url = ' + json.dumps(url) + '\n', capture_output=True, text=True, timeout=30)
    if p.returncode:
        raise RuntimeError('network error ' + str(p.returncode))
    body, status = p.stdout.rsplit('\n', 1)
    if status != '200':
        raise RuntimeError('HTTP ' + status)
    return body

def page(url):
    soup = BeautifulSoup(get(url), 'html.parser')
    if soup.title and any(x in norm(soup.title.text) for x in ('access denied', 'just a moment', 'captcha', 'not found')):
        raise RuntimeError('page blocked or missing')
    return soup

def nextdata(soup):
    tag = soup.find('script', id='__NEXT_DATA__')
    if not tag:
        raise ValueError('structured page data missing')
    return json.loads(tag.string)['props']['pageProps']

def sr_data(soup, key):
    p = nextdata(soup)
    if p.get('routeName') == 'NOT_FOUND':
        raise ValueError('search/detail not found')
    for q in p.get('dehydratedState', {}).get('queries', []):
        if q['queryKey'][0] == key:
            return q['state']['data']
    raise ValueError('expected search/detail data missing')

def sr_url(e):
    loc = e['locality']
    part = e['categorySubCb']['name'].lower().replace(' ', '-')
    location = '-'.join(filter(None, (loc.get('citySeoName'), loc.get('cityPartSeoName'), loc.get('streetSeoName'))))
    return 'https://www.sreality.cz/detail/pronajem/byt/' + part + '/' + location + '/' + str(e['id'])

def update_query(url, **kw):
    u = urlsplit(url)
    query = dict(parse_qsl(u.query))
    query.update({k: str(v) for k,v in kw.items()})
    return urlunsplit((u.scheme,u.netloc,u.path,urlencode(query),''))

def location_ok(address, lat=None, lon=None):
    x1,y1,x2,y2 = CFG['bbox']
    if lat is not None and lon is not None:
        if not (x1 <= float(lon) <= x2 and y1 <= float(lat) <= y2):
            return False
    a = norm(address)
    # A nearby landmark in a description is never a location match. GPS alone is
    # insufficient for streets in unrelated quarters within a broad rectangle.
    known = any(norm(street) in a for street in CFG['streets'])
    if any(x in a for x in ('stare mesto','nove mesto','smichov')):
        return False
    return known or (lat is not None and lon is not None and 'bubenec' in a)

def layout(s):
    m = re.search(r'([3-9])\s*\+\s*(kk|1)', norm(s))
    if m:
        return m[1] + '+' + m[2]
    if re.search(r'\b[6-9]\s+a\s+vice\b',norm(s)):
        return '6+'
    return 'atypický' if 'atyp' in norm(s) else None

def area_from(s):
    m = re.search(r'(\d+(?:[.,]\d+)?)\s*m[²2]', str(s), re.I)
    return float(m[1].replace(',', '.')) if m else None

def short_text(soup):
    for tag in soup(['script','style','nav','footer','form']):
        tag.decompose()
    return soup.get_text('\n', strip=True)

def item(source, url, title, address, area, rent, description, costs='', lat=None, lon=None):
    return dict(source=source,url=url,title=re.sub(r'\s+', ' ', title),address=address,
                area=area,rent=rent,description=description,costs=costs,lat=lat,lon=lon)

def sr_detail(url):
    e = sr_data(page(url), 'estate')
    if e.get('categoryTypeCb',{}).get('value') != 2:
        return None
    loc,p = e['locality'],e['params']
    return item('sreality',url,e['name'],', '.join(filter(None,[loc.get('street'),loc.get('city'),loc.get('cityPart')])),
                p.get('usableArea'),e.get('priceCzk'),e.get('description',''),
                str(p.get('priceNote') or '') + ' ' + str(p.get('costOfLiving') or ''),loc.get('latitude'),loc.get('longitude'))

def bez_detail(url):
    a = nextdata(page(url)).get('origAdvert')
    if not a:
        raise ValueError('advert missing')
    if not a.get('active') or a.get('archived') or a.get('reserved') or a.get('offerType') != 'PRONAJEM' or a.get('shortTerm'):
        return None
    if a.get('currency') != 'CZK':
        raise ValueError('non-CZK price')
    services, utilities = a.get('serviceCharges'), a.get('utilityCharges')
    costs = ('Služby '+str(services)+' Kč' if services else 'Služby nevyčíslené') + '; '
    costs += ('Energie '+str(utilities)+' Kč' if utilities else 'Energie nevyčíslené') + '. '
    costs += ' '.join(str(a.get(k) or '') for k in ('serviceChargesNote','utilityChargesNote'))
    gps = a.get('gps') or {}
    return item('bezrealitky',url,a['pageTitle'],a['address'],a.get('surface'),a.get('price'),
                a.get('description',''),costs,gps.get('lat'),gps.get('lng'))

def html_detail(source,url):
    soup = page(url)
    if not soup.h1:
        raise ValueError('title missing')
    title = soup.h1.get_text(' ',strip=True)
    note_node = soup.select_one('.wrapper-price-notes')
    price_note = note_node.get_text(' ',strip=True).replace('Poznámka k ceně:','').strip() if note_node else ''
    # Stop before recommendations and contact forms so other flats cannot supply parameters.
    text = short_text(soup)
    if source == 'svoboda':
        start = text.find('Byt,')
        end = text.find('Kontaktujte nás',start)
        core = text[start:end] if start >= 0 and end > start else text
        if any(x in norm(title+' '+text[:2800]) for x in ('pronajato','rezervovano')):
            return None
        rent = amount(re.search(r'Měsíční nájemné\s+([^\n]+)',core)[1]) if 'Měsíční nájemné' in core else None
        m = re.search(r'Podlahová plocha\s+([\d.,]+)\s*m',core)
        area = float(m[1].replace(',','.')) if m else None
        m = re.search(r'Poplatky za služby\s+(.+?)(?=\nPodlahová|\nCelková|\nParkování)',core,re.S)
        costs = m[1].strip() if m else ''
        address = title.split('Praha',1)[-1]
        return item(source,url,title,'Praha'+address,area,rent,core.split('Přehledová tabulka')[0],costs)
    h = text.find(title)
    core = text[h:] if h >= 0 else text
    core = core.split('Kontaktujte prodejce')[0].split('Podobné nabídky')[0]
    if any(x in norm(core[:1600]) for x in ('inzerat jiz neni','nabidka jiz neni','rezervovano','pronajato')):
        return None
    after = core[len(title):].strip().splitlines()
    address = after[0] if after else ''
    rent = amount('\n'.join(after[:4]))
    costs = price_note
    return item(source,url,title,address,area_from(title),rent,core,costs)

def discover(source, base):
    found = {}
    limit = CFG['max_pages_per_search']
    for n in range(1,limit+1):
        u = update_query(base,**({'strana':n} if source=='sreality' else {'page':n-1} if source=='idnes' else {'page':n}))
        soup = page(u)
        if source=='sreality':
            data = sr_data(soup,'estatesSearch')
            entries = data['results']
            for e in entries:
                loc = e.get('locality') or {}
                if e.get('categoryTypeCb',{}).get('value')==2 and layout(e['name']) and location_ok(
                    ', '.join(str(loc.get(k) or '') for k in ['street','city','cityPart']),loc.get('latitude'),loc.get('longitude')):
                    found[sr_url(e)] = e['name']
            total = data['pagination']['total']
            if n * data['pagination']['limit'] >= total:
                return found
        elif source=='bezrealitky':
            p = nextdata(soup)
            cache = p['apolloCache']
            root = cache['ROOT_QUERY']
            lists = [v for k,v in root.items() if k.startswith('listAdverts(')]
            if not lists:
                raise ValueError('advert list missing')
            data = lists[0]
            for ref in data.get('list',[]):
                a = cache[ref['__ref']]
                addr = next((v for k,v in a.items() if k.startswith('address(')),'')
                disp = a.get('disposition','').replace('DISP_','').replace('_','+')
                if a.get('surface',0)>=CFG['min_area'] and (layout(disp) or 'ATYP' in disp) and not a.get('reserved'):
                    found['https://www.bezrealitky.cz/nemovitosti-byty-domy/'+a['uri']] = str(addr)
            if n*15 >= data['totalCount']:
                return found
        else:
            pattern = r'/pronajem/byty/detail/\d' if source=='svoboda' else r'/detail/pronajem/byt/'
            links = [a for a in soup.find_all('a',href=True) if re.search(pattern,a['href'])]
            if not links:
                raise ValueError('no listing links; coverage unknown')
            before = len(found)
            for a in links:
                label = re.sub(r'\s+',' ',a.get_text(' ',strip=True))
                if layout(label) and (area_from(label) is None or area_from(label)>=CFG['min_area']):
                    found[urljoin(base,a['href']).split('?')[0]] = label
            # Follow actual pagination links, never inventing pages beyond those shown.
            more = any(re.search(r'(?:page|strana)='+str(n if source=='idnes' else n+1)+r'(?:&|$)',a['href'])
                       for a in soup.find_all('a',href=True))
            if not more:
                return found
    raise ValueError('pagination limit reached; coverage incomplete')

def cost_lower_bound(e):
    # Count only explicit recurring additions, never deposits, commissions or optional parking.
    costs = e['costs']
    numbers = []
    for clause in re.split(r'[;\n]',costs):
        if any(x in norm(clause) for x in ('kauce','jistot','proviz','garaz','parkov')):
            continue
        for m in re.finditer(r'(\d[\d .\u00a0]*\d|\d)\s*(?:,-\s*)?Kč',clause,re.I):
            v = number(m[1])
            if v>0:
                numbers.append(v)
    # If the note explicitly includes charges in rent, additions are not inferred.
    if re.search(r'vcetne\s+(?:vsech\s+)?(?:poplatku|sluzeb|energii)',norm(costs)) and not re.search(r'\d[\d .]*\s*Kč',costs):
        return e['rent']
    lower = e['rent'] + sum(numbers)
    d = norm(e['description'])
    # Explicit monthly totals in the body take precedence over a vague price note.
    totals = re.findall(r'(?:celkove\s+(?:predpokladane\s+)?mesicni\s+naklady|mesicni\s+naklady\s+celkem)[^\d]{0,70}(\d[\d .]*\d)\s*kc',d)
    if totals:
        lower = max(lower, min(number(v) for v in totals))
    # An explicit recurring services/energy charge in the body is another lower bound.
    additions = re.findall(r'(?:poplatky|zalohy)\s+(?:vcetne\s+energii|na\s+veskere\s+sluzby\s+a\s+energie)?\s*[:=]?\s*(?:celkove|cini|predstavuji|priblizne|cca)?\s*(\d[\d .]*\d)\s*kc',d)
    if additions:
        lower = max(lower,e['rent']+max(number(v) for v in additions))
    return lower

def evaluate(e):
    if not e or not e.get('area') or e['area']<CFG['min_area'] or not e.get('rent') or not layout(e['title']):
        return False
    if not location_ok(e['address'],e.get('lat'),e.get('lon')):
        return False
    if e['rent']>CFG['max_monthly'] or cost_lower_bound(e)>CFG['max_monthly']:
        return False
    e['notes'] = ['Celková cena včetně všech energií k ověření; uvedené částky nejsou potvrzený konečný součet.']
    if e.get('lat') is None:
        e['notes'].append('Poloha ověřena jen podle ulice, přesná adresa/hranice oblasti k ověření.')
    else:
        e['notes'].append('Oblast podle přibližného obdélníku mapy; hraniční adresy k ověření.')
    d = norm(e['description'])
    work = re.search(r'pracovn\w*|galeri\w*|hal[auy]|pracovni\s+kout\w*',d)
    e['work'] = 'Text zmiňuje místo pro práci/halu/galerii; dvě samostatné ložnice a oddělení od obýváku ověřit.' if work else 'Samostatné místo pro práci není v textu potvrzené.'
    e['notes'].append('Vnitřní plocha bez příslušenství k ověření, pokud ji inzerát nerozlišuje.')
    return True

def signature(e):
    return hashlib.sha256(json.dumps([e['rent'],e['costs'],e['area'],layout(e['title'])],ensure_ascii=False).encode()).hexdigest()

def identity(e):
    # Conservative cross-site merge: same street, area, layout AND syndicated description similarity.
    street = next((norm(s) for s in CFG['streets'] if norm(s) in norm(e['address'])),norm(e['address']))
    return (street, round(e['area']), layout(e['title']))

def shingles(e):
    words = re.findall(r'\w+',norm(e['description']))
    return {hashlib.sha256(' '.join(words[i:i+5]).encode()).hexdigest()[:16] for i in range(max(0,len(words)-4))}

def duplicate(e,others):
    s = shingles(e)
    for o in others:
        if tuple(o['identity']) == identity(e):
            t = set(o['shingles'])
            if len(s&t)/max(1,min(len(s),len(t))) >= .65:
                return o
    return None

def message(e):
    cost = re.sub(r'\s+',' ',e['costs'])[:320] or 'Poplatky a energie nejsou vyčíslené.'
    return (f"Byt K OVĚŘENÍ: {e['address']}\n{layout(e['title'])}, {e['area']:g} m²\n"
            f"Nájem {e['rent']:,.0f} Kč/měs.\n{cost}\n{e['work']}\n"
            f"Konečný součet do 70 000 Kč a vnitřní plocha k ověření.\n{e['url']}")

def send(text):
    phone,key = os.environ.get('CALLMEBOT_PHONE',''),os.environ.get('CALLMEBOT_APIKEY','')
    if not phone.isdigit() or not key.isdigit():
        raise RuntimeError('CallMeBot secrets missing or invalid')
    url = 'https://api.callmebot.com/whatsapp.php?' + urlencode(dict(phone=phone,apikey=key,text=text))
    body = get(url).lower()
    if any(x in body for x in ['error','failed','invalid']) or not any(x in body for x in ['message queued','message sent','success']):
        raise RuntimeError('CallMeBot acceptance not confirmed; inspect service before retrying')

def save(path,state):
    tmp=path.with_suffix('.tmp')
    tmp.write_text(json.dumps(state,ensure_ascii=False,indent=2)+'\n')
    tmp.replace(path)

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--dry-run',action='store_true')
    ap.add_argument('--test-notification',action='store_true')
    ap.add_argument('--source',choices=CFG['sources'])
    args=ap.parse_args()
    if args.test_notification:
        send('Test cloudového hlídače bytů: GitHub Actions odesílá zprávy přes CallMeBot. Mac nemusí být zapnutý.')
        print('Test accepted by CallMeBot (phone delivery not confirmed).')
        return 0
    if not args.dry_run and not (os.environ.get('CALLMEBOT_PHONE','').isdigit() and os.environ.get('CALLMEBOT_APIKEY','').isdigit()):
        print('Set CALLMEBOT_PHONE and CALLMEBOT_APIKEY in GitHub Actions secrets first. No alerts attempted.')
        return 1
    path=ROOT / ('dry-state.json' if args.dry_run else 'state.json')
    state=json.loads(path.read_text()) if path.exists() else {'seen':{},'announced':[],'health':{}}
    seed=json.loads((ROOT/'seed.json').read_text())
    now=datetime.now(timezone.utc).isoformat()
    report={'checked_at':now,'sources':{},'candidates':[],'notifications':[]}
    candidates={}
    for source,bases in CFG['sources'].items():
        if args.source and source!=args.source:
            continue
        health={'pages':len(bases),'discovered':0,'errors':[]}
        urls={}
        for base in bases:
            try:
                urls.update(discover(source,base))
            except Exception as exc:
                health['errors'].append(f'{base}: {type(exc).__name__}: {str(exc)[:120]}')
        health['discovered']=len(urls)
        for url in urls:
            try:
                e=sr_detail(url) if source=='sreality' else bez_detail(url) if source=='bezrealitky' else html_detail(source,url)
                if evaluate(e):
                    candidates[url]=e
                time.sleep(.2)
            except Exception as exc:
                health['errors'].append(f'detail {url}: {type(exc).__name__}')
        report['sources'][source]=health
        print(source+': '+str(len(urls))+' detail links, '+str(len(health['errors']))+' errors')
    # Seed known flats across portals before considering new candidates.
    known_identities = {tuple(x) for x in seed.get('identities',[])}
    for url,e in candidates.items():
        if (any(str(k) in url for k in seed['ids']) or identity(e) in known_identities) and not duplicate(e,state['announced']):
            state['announced'].append({'identity':identity(e),'shingles':sorted(shingles(e)),'url':url,'rent':e['rent']})
    for url,e in candidates.items():
        report['candidates'].append({k:v for k,v in e.items() if k!='description'})
        sig=signature(e)
        previous=state['seen'].get(url)
        seeded=any(str(k) in url for k in seed['ids']) or identity(e) in known_identities
        same=duplicate(e,state['announced'])
        is_new=not previous and not seeded and not same
        cheaper=(previous and e['rent']<previous['rent']) or (not previous and same and e['rent']<same['rent'])
        state['seen'][url]={'signature':sig,'rent':e['rent'],'checked_at':now}
        if previous and previous.get('notification'):
            state['seen'][url]['notification']=previous['notification']
        if not (is_new or cheaper):
            if seeded and not same:
                state['announced'].append({'identity':identity(e),'shingles':sorted(shingles(e)),'url':url,'rent':e['rent']})
            continue
        if len(report['notifications'])>=CFG['max_alerts_per_run']:
            if previous:
                state['seen'][url]=previous
            else:
                del state['seen'][url]
            continue
        if args.dry_run:
            report['notifications'].append({'url':url,'status':'dry-run'})
            continue
        # Persist intent before side effect: ambiguous responses must not cause automatic duplicate messages.
        state['seen'][url]['notification']='attempting'
        save(path,state)
        try:
            send(('Zlevnění!\n' if cheaper else '')+message(e))
            status='accepted'
        except Exception:
            status='uncertain_or_failed'
        state['seen'][url]['notification']=status
        state['announced'].append({'identity':identity(e),'shingles':sorted(shingles(e)),'url':url,'rent':e['rent']})
        report['notifications'].append({'url':url,'status':status})
        save(path,state)
        time.sleep(3)
    state['last_check']=now
    state['health']=report['sources']
    save(path,state)
    (ROOT/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    summary=f"# Kontrola bytů {now}\n\nNadějných nabídek: {len(candidates)}. Žádná není automaticky potvrzena ve všech požadavcích.\n"
    for src,h in report['sources'].items():
        summary+=f"\n- {src}: {h['discovered']} detailů, {len(h['errors'])} chyb.\n"
        summary+=''.join(f"  - {x}\n" for x in h['errors'][:8])
    for e in candidates.values():
        summary+='\n'+message(e)+'\n'
    (ROOT/'report.md').write_text(summary)
    if os.environ.get('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'],'a') as f:
            f.write(summary)
    # A blocked source is a visible failed job, never an empty successful check.
    return 1 if any(h['errors'] for h in report['sources'].values()) or any(
        n['status']=='uncertain_or_failed' for n in report['notifications']) else 0

if __name__=='__main__':
    raise SystemExit(main())
