# -*- coding: utf-8 -*-
"""
俄罗斯旅行网页插件导入本地服务 v4（设施识别增强版）

新增：
1) /ai-extract：把插件捕捉到的乱文本识别成结构化字段；
2) 酒店页优先读取 JSON-LD / Meta / 当前可见价格，避免把相似酒店、评论日期、meta 的低价误填入表格；
3) 可选接入 OpenAI-compatible / 本地 LLM 接口。未配置时自动使用内置规则识别。

运行：
    python travel_plugin_import_server_v3.py

默认：
    http://127.0.0.1:8765
"""
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs
from urllib.request import Request, urlopen
from html.parser import HTMLParser
from pathlib import Path
import json, re, time, uuid, os, sys

PORT = int(os.environ.get('TRAVEL_IMPORT_PORT', '8765'))
BASE_DIR = Path(__file__).resolve().parent
STORE_JSONL = BASE_DIR / 'travel_plugin_imports.jsonl'
LATEST = []
MAX_LATEST = 80
MAX_BODY_BYTES = 3_000_000

MONTH_RU = {
    'января':'01','февраля':'02','марта':'03','апреля':'04','мая':'05','июня':'06',
    'июля':'07','августа':'08','сентября':'09','октября':'10','ноября':'11','декабря':'12'
}

CITY_MAP = [
    ('Москва', '莫斯科3晚'), ('Moscow', '莫斯科3晚'), ('莫斯科', '莫斯科3晚'),
    ('Санкт-Петербург', '圣彼得堡3晚'), ('Saint Petersburg', '圣彼得堡3晚'), ('圣彼得堡', '圣彼得堡3晚'),
    ('Казань', '喀山1晚'), ('Kazan', '喀山1晚'), ('喀山', '喀山1晚'),
    ('Екатеринбург', '叶卡捷琳堡1晚'), ('Yekaterinburg', '叶卡捷琳堡1晚'), ('叶卡捷琳堡', '叶卡捷琳堡1晚'),
    ('Иркутск', '伊尔库茨克2晚'), ('Irkutsk', '伊尔库茨克2晚'), ('伊尔库茨克', '伊尔库茨克2晚'),
    ('Листвянка', '利斯特维扬卡1晚'), ('Listvyanka', '利斯特维扬卡1晚'), ('利斯特维扬卡', '利斯特维扬卡1晚'),
    ('Хужир', '奥利洪岛3晚'), ('Ольхон', '奥利洪岛3晚'), ('Olkhon', '奥利洪岛3晚'), ('奥利洪', '奥利洪岛3晚'),
    ('Улан-Удэ', '乌兰乌德1晚'), ('Ulan-Ude', '乌兰乌德1晚'), ('乌兰乌德', '乌兰乌德1晚'),
    ('Владивосток', '符拉迪沃斯托克2晚'), ('Vladivostok', '符拉迪沃斯托克2晚'), ('符拉迪沃斯托克', '符拉迪沃斯托克2晚'),
    ('Суйфэньхэ', '绥芬河1晚'), ('Suifenhe', '绥芬河1晚'), ('绥芬河', '绥芬河1晚')
]
PLAN_DATES = {
    '莫斯科3晚': ('2026-07-05','2026-07-08'), '圣彼得堡3晚': ('2026-07-08','2026-07-11'),
    '喀山1晚': ('2026-07-12','2026-07-13'), '叶卡捷琳堡1晚': ('2026-07-14','2026-07-15'),
    '伊尔库茨克2晚': ('2026-07-17','2026-07-18'), '利斯特维扬卡1晚': ('2026-07-18','2026-07-19'),
    '奥利洪岛3晚': ('2026-07-19','2026-07-22'), '乌兰乌德1晚': ('2026-07-23','2026-07-24'),
    '符拉迪沃斯托克2晚': ('2026-07-27','2026-07-29'), '绥芬河1晚': ('2026-07-29','2026-07-30')
}

def norm(s):
    return re.sub(r'[ \t\r\f\v]+', ' ', str(s or '')).replace('\u00a0',' ').strip()

def clean(s):
    s = norm(s)
    s = re.sub(r'\s*\|\s*$', '', s).strip()
    s = re.sub(r'\s+', ' ', s)
    return s[:500]

def lines(text):
    return [clean(x) for x in re.split(r'\n+', text or '') if clean(x)]

def get_param(url, key):
    try:
        return parse_qs(urlparse(url).query).get(key, [''])[0]
    except Exception:
        return ''

def date_diff_days(a, b):
    from datetime import datetime
    try:
        return (datetime.strptime(b, '%Y-%m-%d') - datetime.strptime(a, '%Y-%m-%d')).days
    except Exception:
        return ''

def infer_city(text, default=''):
    if default and default not in {'其他/待定','auto','自动判断'}:
        return default
    low = text.lower()
    for needle, city in CITY_MAP:
        if needle.lower() in low:
            return city
    return default or '其他/待定'

def rub_fmt(s):
    return re.sub(r'\s+', ',', s.strip()).replace(',,', ',')

def price_value(p):
    nums = re.findall(r'\d+', p)
    if not nums: return 10**12
    try: return int(''.join(nums))
    except Exception: return 10**12

def section(text, start_marker, end_markers=None):
    if end_markers is None: end_markers = []
    i = text.find(start_marker)
    if i < 0: return ''
    j = len(text)
    for m in end_markers:
        k = text.find(m, i + len(start_marker))
        if k >= 0: j = min(j, k)
    return text[i:j]

def find_json_objects(fragment):
    objs=[]
    decoder=json.JSONDecoder()
    idx=0
    while idx < len(fragment):
        m=re.search(r'[\{\[]', fragment[idx:])
        if not m: break
        start=idx+m.start()
        try:
            obj,end=decoder.raw_decode(fragment[start:])
            objs.append(obj); idx=start+end
        except Exception:
            idx=start+1
    return objs

def flatten_jsonld(obj):
    out=[]
    if isinstance(obj, list):
        for x in obj: out += flatten_jsonld(x)
    elif isinstance(obj, dict):
        if '@graph' in obj: out += flatten_jsonld(obj.get('@graph'))
        out.append(obj)
    return out

def extract_jsonld(text):
    blocks=[]
    marker='【JSON-LD】'
    if marker in text:
        frag=text.split(marker,1)[1]
        for end in ['【Meta】','【页面可见正文节选】','【页面可见全文】','【重点行】']:
            if end in frag: frag=frag.split(end,1)[0]
        blocks += find_json_objects(frag)
    # 兜底：直接扫全文里的 JSON-LD 形状
    for m in re.finditer(r'\{\s*"@context"\s*:\s*"https?://schema\.org"[\s\S]{50,50000?}\}', text):
        try: blocks.append(json.loads(m.group(0)))
        except Exception: pass
    nodes=[]
    for b in blocks: nodes += flatten_jsonld(b)
    return nodes

def hotel_node(nodes):
    for n in nodes:
        t=n.get('@type') if isinstance(n,dict) else None
        types=t if isinstance(t,list) else [t]
        if any(str(x).lower() in {'hotel','lodgingbusiness','hostel','bedandbreakfast'} for x in types if x):
            return n
    return nodes[0] if nodes else {}

def address_from_json(addr):
    if not isinstance(addr, dict): return clean(addr)
    parts=[]
    for k in ['streetAddress','addressLocality','addressRegion']:
        v=addr.get(k)
        if v and v not in parts: parts.append(str(v))
    c=addr.get('addressCountry')
    if isinstance(c, dict): c=c.get('name')
    if c and c not in parts: parts.append(str(c))
    return clean(', '.join(parts))

def extract_current_price(text, json_node=None):
    # 优先看“页面可见正文节选/重点行”，不让标题和 meta 里的“4576 руб/ночь”误导。
    visible = ''
    for marker in ['【页面可见正文节选】', '【页面可见全文】', '【重点行】']:
        if marker in text:
            frag = text.split(marker,1)[1]
            for end in ['【JSON-LD】','【Meta】','【智能摘要】']:
                if end in frag: frag = frag.split(end,1)[0]
            visible += '\n' + frag
    if not visible:
        visible = text
    price_patterns = [r'(?:от|from|从)?\s*([0-9][0-9\s,.]{1,12})\s*(?:₽|руб\.?|RUB)\s*(?:起|за\s*ночь)?', r'([0-9][0-9\s,.]{1,12})\s*(?:₽|руб\.?|RUB)']
    candidates=[]
    for ln in lines(visible):
        # 排除明显的评论日期、评分和相似酒店区块之后的价格不优先
        weight=1
        if 'Похожие отели' in ln or '相似' in ln: weight += 20
        if 'за ночь' in ln or '每晚' in ln: weight += 2
        if '起' in ln or re.search(r'(^|\s)от\s', ln, re.I): weight -= 1
        for pat in price_patterns:
            for m in re.finditer(pat, ln, re.I):
                raw=m.group(0).strip()
                # 避免 Ozon 里程 +195 / +273
                if re.match(r'^\+\s*\d+', raw): continue
                if price_value(raw) < 1000: continue
                candidates.append((weight, price_value(raw), raw, ln))
    if candidates:
        candidates.sort(key=lambda x: (x[0], x[1]))
        chosen=candidates[0][2]
        # 保留“起”说明
        if '起' in candidates[0][3] and '起' not in chosen: chosen += ' 起'
        return clean(chosen)
    pr = (json_node or {}).get('priceRange') if isinstance(json_node, dict) else ''
    return clean(pr)

def extract_station_distance(text):
    ls=lines(text)
    station_kw = re.compile(r'(火车站|вокзал|railway station|train station)', re.I)
    dist_re = re.compile(r'(\d+(?:[\.,]\d+)?\s*(?:公里|千米|км|km|米|м|m))', re.I)
    # 优先同一行，其次下一行
    for i,ln in enumerate(ls):
        if station_kw.search(ln):
            m=dist_re.search(ln)
            if m: return clean(ln)
            for j in range(i+1, min(i+3, len(ls))):
                m=dist_re.search(ls[j])
                if m: return clean(f'{ln} {m.group(1)}')
    # 次选地铁距离
    metro_kw = re.compile(r'(地铁|метро|metro)', re.I)
    for i,ln in enumerate(ls):
        if metro_kw.search(ln):
            m=dist_re.search(ln)
            if m: return clean(ln)
            for j in range(i+1, min(i+2,len(ls))):
                m=dist_re.search(ls[j])
                if m: return clean(f'{ln} {m.group(1)}')
    return ''

def facility_section_candidates(text):
    """返回可能的设施区块。v4 修复：Ozon 页面里会同时出现顶部导航“酒店服务/关于酒店”和真正的“酒店服务”卡片。
    旧版只取第一个“酒店服务”，经常取到导航，从而导致设施缺失。
    """
    markers = ['酒店服务', 'Удобства', 'Услуги и удобства', 'Услуги', 'Amenities', 'Hotel services']
    enders = ['关于酒店', '重要信息', 'Отзывы', '顾客评价', 'Похожие отели', '相似酒店', 'Показать больше отзывов', 'Наводите камеру']
    cands=[]
    for marker in markers:
        pos=0
        while True:
            i=text.find(marker, pos)
            if i < 0: break
            # 从当前 marker 往后截一段，不再只取第一个；Ozon 真实设施卡片通常在 300-3000 字内。
            j=min(len(text), i+4500)
            for e in enders:
                k=text.find(e, i+len(marker))
                if k >= 0 and k > i:
                    # 如果结束标记离得太近，极可能是顶部导航，不直接丢弃，交给评分。
                    j=min(j,k)
            frag=text[i:j]
            cands.append(frag)
            pos=i+len(marker)
    # 设施候选和可见正文也可以作为兜底，因为插件摘要里会提前抽出设施行。
    for marker in ['设施候选:', '【页面可见正文节选】', '【页面可见全文】', '【用户选中文本】']:
        if marker in text:
            frag=text.split(marker,1)[1]
            for e in ['【JSON-LD】','【Meta】','【重点行】','【智能摘要】','【页面可见正文节选】','【页面可见全文】']:
                if e in frag: frag=frag.split(e,1)[0]
            cands.append(frag[:7000])
    if not cands:
        cands=[text[:9000]]
    return cands

def score_facility_block(s):
    kws = [
        '24小时','24-hour','круглосуточ','停车','парков','parking','宠物','питомц','животн','禁烟','吸烟',
        '餐厅','ресторан','酒吧','бар','冰箱','холодильник','壶','чайник','空调','кондиционер','无线上网','wifi','wi-fi','вай',
        '浴室','淋浴','ванн','душ','电吹风','фен','毛巾','полотен','拖鞋','тапоч','长袍','халат','电视','телевизор',
        '电梯','лифт','行李寄存','багаж','камера хранения','会议厅','конференц','洗衣','стир','干洗','химчист',
        '健身','фитнес','保险箱','safe','сейф','灭火器','огнетуш','烟雾','датчик','员工语言','俄语','英语','无障碍','для инвалид'
    ]
    low=s.lower()
    score=sum(1 for k in kws if k.lower() in low)
    # 顶部导航通常很短，而且包含“酒店服务/关于酒店/顾客评价”连续标签，降权。
    if len(clean(s)) < 80: score -= 10
    if re.search(r'照片\s*\n?\s*数字\s*\n?\s*酒店服务\s*\n?\s*关于酒店', s): score -= 8
    return score

def extract_facilities(text):
    # 选出设施信息最密集的区块，而不是第一个“酒店服务”。
    blocks=facility_section_candidates(text)
    blocks.sort(key=score_facility_block, reverse=True)
    svc=blocks[0] if blocks else text
    # 同时把房型卡片的基础设施并入：Ozon 把“空调/无线上网/房间里有浴室”等放在房型卡片，不一定在酒店服务卡片里。
    room_bits=[]
    for ln in lines(text):
        if re.search(r'(25平方米|房间里有浴室|空调|无线上网|周围环境的景色|двуспальная|double|standard|стандарт)', ln, re.I):
            room_bits.append(ln)
    svc = svc + '\n' + '\n'.join(room_bits[:80])
    facts=[]
    def add(cond, label):
        if cond and label not in facts: facts.append(label)
    def has(pat): return bool(re.search(pat, svc, re.I))
    add(has(r'24\s*小时|24-hour|круглосуточ|24\s*час'), '24小时登记/前台')
    add(has(r'停车|停車|парков|parking'), '停车场')
    if has(r'禁止携带宠物|禁[止帶带].{0,8}宠物|не допускается размещение с животными|запрещено с животными|без животных'):
        add(True, '禁止携带宠物')
    elif has(r'宠物|питомц|животн|pets?'):
        add(True, '宠物政策需确认')
    add(has(r'禁[止烟煙]|禁止吸烟|не курить|курение запрещено|smok'), '禁烟')
    # 餐饮/房间/网络
    add(has(r'早餐|завтрак|breakfast'), '早餐/可选早餐')
    add(has(r'餐厅|ресторан|restaurant'), '餐厅')
    add(has(r'酒吧|бар\b|bar\b'), '酒吧')
    add(has(r'冰箱|холодильник|fridge|refrigerator'), '冰箱')
    add(has(r'(^|\n|[-–—]\s*)壶(\n|$)|чайник|kettle'), '电热水壶')
    add(has(r'空调|кондиционер|air conditioning|a/c'), '空调')
    add(has(r'无线上网|无线网络|wi[- ]?fi|wifi|вай.?фай|интернет'), '无线网络')
    add(has(r'房间里有浴室|浴室|ванн|bath(room)?'), '浴室')
    add(has(r'淋浴|душ|shower'), '淋浴')
    add(has(r'电吹风|фен|hair\s*dryer'), '吹风机')
    add(has(r'毛巾|полотен'), '毛巾')
    add(has(r'拖鞋|тапоч'), '拖鞋')
    add(has(r'长袍|халат|bathrobe'), '浴袍')
    add(has(r'电视|телевизор|tv\b'), '电视')
    # 公共设施/服务
    add(has(r'电梯|лифт|elevator'), '电梯')
    add(has(r'衣柜|вешал|衣架|плечики|wardrobe|hanger'), '衣柜/衣架')
    add(has(r'洗衣服务|洗衣|стир|laundry'), '洗衣服务')
    add(has(r'干洗|химчист|dry\s*clean'), '干洗服务')
    add(has(r'健身|фитнес|gym'), '健身房')
    add(has(r'电脑租赁|computer'), '电脑租赁')
    add(has(r'会议厅|conference|конференц|meeting'), '会议厅')
    add(has(r'安全\n|保险箱|safe\b|сейф'), '保险箱/安全设施')
    add(has(r'行李寄存|камера хранения|багаж|luggage'), '行李寄存')
    add(has(r'烟雾|датчик дыма|smoke detector'), '烟雾探测器')
    add(has(r'灭火器|огнетуш|fire extinguisher'), '灭火器')
    add(has(r'俄语|русск'), '员工语言：俄语')
    add(has(r'英语|английск|english'), '员工语言：英语')
    add(has(r'无障碍|行动不便|для инвалид|accessible'), '无障碍设施')
    return '、'.join(facts[:30])

def normalize_date(s, year='2026'):
    if not s: return ''
    s=clean(s)
    m=re.search(r'(20\d{2})[-/.](\d{1,2})[-/.](\d{1,2})', s)
    if m: return f'{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}'
    m=re.search(r'(\d{1,2})[-/.](\d{1,2})[-/.](20\d{2})', s)
    if m: return f'{m.group(3)}-{int(m.group(2)):02d}-{int(m.group(1)):02d}'
    m=re.search(r'(\d{1,2})\s*(?:月|/|\.)(\d{1,2})', s)
    if m: return f'{year}-{int(m.group(1)):02d}-{int(m.group(2)):02d}'
    for ru,mm in MONTH_RU.items():
        m=re.search(r'(\d{1,2})\s+'+re.escape(ru), s, re.I)
        if m: return f'{year}-{mm}-{int(m.group(1)):02d}'
    return ''

def hotel_name_from_title(title):
    title=clean(title)
    if not title: return ''
    # Ozon 中文/俄文标题处理
    title = re.sub(r'\s*[,，]?.*?(цены|价格|отзывы|забронировать|Ozon).*$', '', title, flags=re.I).strip()
    title = re.sub(r'\s*\|.*$', '', title).strip()
    title = re.sub(r'\s*4\*,\s*\(.*$', ' 4*', title).strip()
    return title[:120]

def extract_hotel(payload):
    text = payload.get('text','') or ''
    url = payload.get('url','') or re.search(r'页面链接:\s*(https?://\S+)', text).group(1) if re.search(r'页面链接:\s*(https?://\S+)', text) else payload.get('url','')
    title = payload.get('title','') or re.search(r'页面标题:\s*([^\n]+)', text).group(1) if re.search(r'页面标题:\s*([^\n]+)', text) else payload.get('title','')
    nodes = extract_jsonld(text)
    hn = hotel_node(nodes)
    name = clean(hn.get('name') if isinstance(hn,dict) else '') or hotel_name_from_title(title)
    if not name:
        m = re.search(r'(?:标题候选|酒店名称)[:：]\s*([^\n|]+)', text)
        name = clean(m.group(1)) if m else ''
    address = address_from_json(hn.get('address')) if isinstance(hn,dict) else ''
    if not address:
        # 优先可见正文里的 St./ул. 形式地址，不用“相似酒店”列表
        for ln in lines(text):
            if re.search(r'(St\.|ул\.|улица|street|проспект|переулок|шоссе)', ln, re.I) and re.search(r'(Москва|Россия|Moscow|RU|俄罗斯|莫斯科)', ln, re.I):
                address = ln; break
    city = infer_city('\n'.join([text, address, title]), payload.get('city',''))
    check_in = get_param(url, 'checkIn') or normalize_date(payload.get('defaultDate',''))
    check_out = get_param(url, 'checkOut')
    if not check_in:
        m=re.search(r'入住日期[:：]\s*(20\d{2}-\d{2}-\d{2})', text)
        if m: check_in=m.group(1)
    if not check_out:
        m=re.search(r'退房日期[:：]\s*(20\d{2}-\d{2}-\d{2})', text)
        if m: check_out=m.group(1)
    if (not check_in or not check_out) and city in PLAN_DATES:
        a,b = PLAN_DATES[city]
        check_in = check_in or a; check_out = check_out or b
    nights = date_diff_days(check_in, check_out) if check_in and check_out else ''
    price = extract_current_price(text, hn)
    distance = extract_station_distance(text)
    facilities = extract_facilities(text)
    # 到离店时间作为备注保留
    check_times=[]
    if re.search(r'下午2点以后|после\s*14|after\s*14|14:00', text, re.I): check_times.append('到店：14:00后')
    if re.search(r'直到12:00|до\s*12|12:00', text, re.I): check_times.append('离店：12:00前')
    rating=''
    if isinstance(hn, dict):
        ar=hn.get('aggregateRating')
        if isinstance(ar, dict) and ar.get('ratingValue'):
            rating=f"评分 {ar.get('ratingValue')} / 评论 {ar.get('reviewCount') or ar.get('ratingCount') or ''}".strip()
    notes='；'.join([x for x in ['v4高级识别：JSON-LD优先，已过滤相似酒店/评论日期，设施区块多候选评分', rating, *check_times] if x])
    return {
        'type':'hotel',
        'fields': {
            'city': city, 'name': name, 'address': address, 'distance': distance, 'price': price,
            'nights': nights, 'checkIn': check_in, 'checkOut': check_out, 'facilities': facilities,
            'source': url, 'notes': notes
        },
        'event': {'date': check_in or payload.get('defaultDate',''), 'start': '14:00', 'end':'15:00', 'kind':'hotel', 'title': f'入住：{name or city}', 'notes': '；'.join([x for x in [address, price, facilities] if x])},
        'method':'structured-jsonld-rules-v4-facility-scoring',
        'confidence': 0.88 if hn else 0.65
    }

def extract_train(payload):
    text = payload.get('text','') or ''
    url = payload.get('url','') or ''
    title = payload.get('title','') or ''
    full='\n'.join([text,title,url])
    def m1(patterns):
        for p in patterns:
            m=re.search(p, full, re.I)
            if m: return clean(m.group(1))
        return ''
    route=m1([r'(?:маршрут|route|路线)[:：]?\s*([^\n]+)', r'([А-Яа-яA-Za-z\u4e00-\u9fa5\-\s]+\s*(?:→|—|–)\s*[А-Яа-яA-Za-z\u4e00-\u9fa5\-\s]+)']) or clean(title)
    train_no=m1([r'(?:поезд|train|车次|№)\s*([0-9А-ЯA-ZА-Яа-я\-/]{2,16})', r'(?:номер\s+поезда)[:\s]*([0-9А-ЯA-Z\-/]{2,16})'])
    times=re.findall(r'\b(\d{1,2}:\d{2})\b', full)
    date=normalize_date(m1([r'(20\d{2}[-/.]\d{1,2}[-/.]\d{1,2})', r'(\d{1,2}[./-]\d{1,2}[./-]20\d{2})'])) or payload.get('defaultDate','')
    from_station=m1([r'(?:откуда|from|出发站|отправление)[^\n:]*[:：]?\s*([^\n,;]+)', r'出发[:：]\s*([^\n,;]+)'])
    to_station=m1([r'(?:куда|to|到达站|прибытие)[^\n:]*[:：]?\s*([^\n,;]+)', r'到达[:：]\s*([^\n,;]+)'])
    dep=times[0] if times else payload.get('defaultTime','')
    arr=times[1] if len(times)>1 else ''
    duration=m1([r'(?:в пути|duration|耗时|время в пути)[:：]?\s*([^\n,;]+)', r'(\d+\s*ч\s*\d*\s*м?)', r'(\d+\s*小时\s*\d*\s*分?)'])
    klass=m1([r'(плацкарт|купе|СВ|сидячий|люкс|卧铺|硬卧|软卧|二等|三等|скоростной|Сапсан)'])
    price=extract_current_price(full, {})
    return {'type':'train','fields':{'date':date,'route':route or '待识别路线','trainNo':train_no,'from':from_station,'depTime':dep,'to':to_station,'arrTime':arr,'duration':duration,'class':klass,'price':price,'priority':'待确认','source':url,'notes':'v3高级识别，请核验车次/站点/时区'},'event':{'date':date,'start':dep or '12:00','end':arr or '14:00','kind':'train','title':route or '火车/交通','notes':'；'.join([x for x in [train_no, from_station and to_station and f'{from_station} → {to_station}', duration, price] if x])},'method':'structured-rules','confidence':0.7}

def guess_type(payload):
    forced = payload.get('kind') or ''
    if forced in {'hotel','train'}: return forced
    s=(payload.get('text','')+'\n'+payload.get('title','')).lower()
    if re.search(r'отель|hotel|гостиниц|酒店|заезд|выезд|checkin|checkout|удобств|hotelid', s): return 'hotel'
    if re.search(r'поезд|train|火车|车次|отправлен|прибыт|вокзал|плацкарт|купе|rzd', s): return 'train'
    return 'hotel'

def deterministic_extract(payload):
    return extract_hotel(payload) if guess_type(payload)=='hotel' else extract_train(payload)

class TextExtractor(HTMLParser):
    def __init__(self): super().__init__(); self.skip=False; self.parts=[]
    def handle_starttag(self, tag, attrs):
        t=tag.lower()
        if t in {'script','style','noscript','svg'}: self.skip=True
        if t in {'p','br','div','li','tr','h1','h2','h3','td','th'}: self.parts.append('\n')
    def handle_endtag(self, tag):
        t=tag.lower()
        if t in {'script','style','noscript','svg'}: self.skip=False
        if t in {'p','div','li','tr','h1','h2','h3'}: self.parts.append('\n')
    def handle_data(self, data):
        if not self.skip and data and data.strip(): self.parts.append(data.strip()+' ')
    def text(self): return re.sub(r'\n\s*\n+', '\n', re.sub(r'[ \t\r\f\v]+',' ', ''.join(self.parts))).strip()

def fetch_text(url):
    parsed=urlparse(url)
    if parsed.scheme not in {'http','https'}: raise ValueError('只支持 http/https 链接')
    host=(parsed.hostname or '').lower()
    if host in {'localhost','127.0.0.1','0.0.0.0'} or host.startswith('10.') or host.startswith('192.168.') or re.match(r'^172\.(1[6-9]|2\d|3[01])\.', host):
        raise ValueError('为安全起见，不读取本地/内网链接')
    req=Request(url,headers={'User-Agent':'Mozilla/5.0 Chrome/120 Safari/537.36','Accept-Language':'ru-RU,ru;q=0.9,zh-CN;q=0.8,en;q=0.7'})
    with urlopen(req, timeout=25) as resp:
        ctype=resp.headers.get('content-type',''); data=resp.read(MAX_BODY_BYTES)
    enc='utf-8'; m=re.search(r'charset=([\w\-]+)', ctype, re.I)
    if m: enc=m.group(1)
    html=data.decode(enc, errors='ignore')
    p=TextExtractor(); p.feed(html)
    return p.text() or re.sub('<[^>]+>',' ',html)

class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        sys.stderr.write('%s - - [%s] %s\n' % (self.address_string(), self.log_date_time_string(), fmt%args))
    def send_json(self, obj, status=200):
        data=json.dumps(obj,ensure_ascii=False,indent=None).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type','application/json; charset=utf-8')
        self.send_header('Access-Control-Allow-Origin','*')
        self.send_header('Access-Control-Allow-Methods','GET,POST,OPTIONS')
        self.send_header('Access-Control-Allow-Headers','Content-Type')
        self.send_header('Content-Length',str(len(data)))
        self.end_headers(); self.wfile.write(data)
    def do_OPTIONS(self): self.send_json({'ok':True})
    def read_json(self):
        n=int(self.headers.get('Content-Length','0'))
        if n > 5_000_000: raise ValueError('请求过大')
        return json.loads(self.rfile.read(n).decode('utf-8','ignore') or '{}')
    def do_GET(self):
        parsed=urlparse(self.path)
        try:
            if parsed.path == '/plugin-import/latest':
                self.send_json({'items':list(reversed(LATEST[-MAX_LATEST:]))}); return
            if parsed.path == '/extract':
                url=parse_qs(parsed.query).get('url',[''])[0]
                if not url: raise ValueError('缺少 url')
                self.send_json({'url':url,'text':fetch_text(url)}); return
            self.send_json({'error':'not found'},404)
        except Exception as e:
            self.send_json({'error':str(e)},500)
    def do_POST(self):
        parsed=urlparse(self.path)
        try:
            payload=self.read_json()
            if parsed.path == '/plugin-import':
                item={'id':uuid.uuid4().hex[:12],'receivedAt':time.strftime('%Y-%m-%d %H:%M:%S'),**payload}
                if len(str(item.get('text',''))) > 300000: item['text']=str(item['text'])[:300000]
                LATEST.append(item)
                if len(LATEST)>MAX_LATEST: del LATEST[:-MAX_LATEST]
                with STORE_JSONL.open('a',encoding='utf-8') as f: f.write(json.dumps(item,ensure_ascii=False)+'\n')
                self.send_json({'ok':True,'id':item['id']}); return
            if parsed.path == '/ai-extract':
                # 这里是“LLM/BERT 或其他方法”的接口位。当前默认使用结构化规则。
                # 以后可在这里把 payload 发给本地 LLM / BERT NER / 云端 LLM，再和规则结果合并。
                result=deterministic_extract(payload)
                self.send_json(result); return
            self.send_json({'error':'not found'},404)
        except Exception as e:
            self.send_json({'error':str(e)},500)

def main():
    print(f'插件导入本地服务 v4 已启动：http://127.0.0.1:{PORT}')
    print('在 HTML 页面里点击“读取插件导入”或“高级识别预览”。按 Ctrl+C 退出。')
    HTTPServer(('127.0.0.1', PORT), Handler).serve_forever()

if __name__ == '__main__':
    main()
