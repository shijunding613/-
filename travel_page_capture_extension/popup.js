const $ = (id) => document.getElementById(id);
const statusBox = $('status');
function setStatus(msg, cls='ok') { statusBox.className = cls; statusBox.textContent = msg; }
function updateCounter(){ $('counter').textContent = `当前正文长度：${$('pageText').value.length.toLocaleString()} 字符`; }
async function loadSettings(){ const s = await chrome.storage.local.get(['endpoint','kind','city','defaultDate','defaultTime']); if (s.endpoint) $('endpoint').value = s.endpoint; if (s.kind) $('kind').value = s.kind; if (s.city) $('city').value = s.city; if (s.defaultDate) $('defaultDate').value = s.defaultDate; if (s.defaultTime) $('defaultTime').value = s.defaultTime; }
async function saveSettings(){ await chrome.storage.local.set({ endpoint:$('endpoint').value.trim(), kind:$('kind').value, city:$('city').value, defaultDate:$('defaultDate').value, defaultTime:$('defaultTime').value }); }
async function captureCurrentPage(mode='smart'){
  await saveSettings(); const [tab] = await chrome.tabs.query({active:true,currentWindow:true}); if (!tab?.id) throw new Error('没有找到当前标签页');
  const [{result}] = await chrome.scripting.executeScript({ target: {tabId: tab.id}, args: [mode], func: async (mode) => {
      const wait = (ms) => new Promise(r => setTimeout(r, ms));
      // v4：读取前尽量展开 Ozon/Yandex/Ostrovok 页面里的“显示全部/阅读更多”，否则酒店设施经常只露出前几项。
      try {
        const expandRe = /(显示全部|阅读更多|展开|Показать все|Показать ещё|Подробнее|Read more|Show all)/i;
        const candidates = Array.from(document.querySelectorAll('button, a, [role=button], span, div')).filter(el => {
          const t=(el.innerText || el.textContent || '').trim();
          if (!t || t.length > 40) return false;
          if (/显示\s*\d+\s*个|选择|登录|收藏|数字/i.test(t)) return false;
          return expandRe.test(t);
        }).slice(0, 12);
        for (const el of candidates) { try { el.click(); await wait(90); } catch(e){} }
      } catch(e){}
      const clean = (s) => (s || '').replace(/[\t\r\f\v]+/g,' ').replace(/\u00a0/g,' ').replace(/\n\s*\n+/g,'\n').trim();
      const one = (s) => clean(s).replace(/\s+/g,' ').trim();
      const uniq = (arr, max=999) => { const seen = new Set(), out=[]; for (const x of arr) { const v = one(x); const k = v.toLowerCase(); if (!v || seen.has(k)) continue; seen.add(k); out.push(v); if(out.length>=max) break; } return out; };
      const qp = {}; try { new URL(location.href).searchParams.forEach((v,k)=>{qp[k]=v}); } catch(e) {}
      const selected = window.getSelection ? String(window.getSelection()) : ''; const body = document.body ? document.body.innerText || '' : ''; const title = document.title || ''; const url = location.href;
      const meta = Array.from(document.querySelectorAll('meta')).map(m => `${m.getAttribute('name')||m.getAttribute('property')||''}: ${m.getAttribute('content')||''}`).filter(x=>x && !/^:\s*$/.test(x));
      const jsonLdRaw = Array.from(document.querySelectorAll('script[type="application/ld+json"]')).map(s => s.textContent || '').filter(Boolean);
      let jsonSummaries=[];
      for (const raw of jsonLdRaw) { try { const obj=JSON.parse(raw); const arr=Array.isArray(obj)?obj:(obj['@graph']||[obj]); for (const n of arr) { const typ=n['@type']; const types=Array.isArray(typ)?typ:[typ]; if(types.some(t=>/Hotel|LodgingBusiness|Hostel/i.test(String(t)))) { const a=n.address||{}; jsonSummaries.push(['JSON-LD酒店名称: '+(n.name||''),'JSON-LD地址: '+[a.streetAddress,a.addressLocality,a.addressRegion,typeof a.addressCountry==='object'?a.addressCountry.name:a.addressCountry].filter(Boolean).join(', '),'JSON-LD价格区间: '+(n.priceRange||''),'JSON-LD评分: '+((n.aggregateRating&&n.aggregateRating.ratingValue)||'')].filter(Boolean).join('\n')); } } } catch(e){} }
      const h = Array.from(document.querySelectorAll('h1,h2,h3')).map(e=>e.innerText || e.textContent || '');
      const lines = uniq(body.split(/\n+/).map(one).filter(Boolean), 1500);
      const deny = /^(ozon|каталог|корзина|избранное|войти|login|sign in|профиль|реклама|cookie|cookies|скачать|app|приложение|помощь|support|меню|search|поиск|найти|главная|назад|далее|показать ещё|читать полностью)$/i;
      const compact = lines.filter(l => l.length >= 2 && l.length <= 260 && !deny.test(l));
      const hotelKw = /(отель|гостиниц|апарт|hotel|hostel|адрес|заезд|выезд|ноч|гост|номер|удобств|wi[-\s]?fi|завтрак|парков|ванн|душ|кондиционер|ресторан|круглосуточ|москва|санкт|казань|иркутск|владивосток|ул\.|улица|проспект|переулок|наб\.|шоссе|₽|руб|RUB|check\s*in|check\s*out|address|price|booking|酒店|入住|退房|房间|火车站|地铁|公里)/i;
      const trainKw = /(поезд|train|рейс|маршрут|отправление|прибытие|в пути|вокзал|станция|купе|плацкарт|сидяч|сапсан|номер поезда|билет|₽|руб|Москва|Санкт-Петербург|Казань|Екатеринбург|Иркутск|Улан|Владивосток|Гродеково|Суйфэньхэ|火车|车次|出发|到达)/i;
      const focusLines = compact.filter(l => hotelKw.test(l) || trainKw.test(l));
      const priceLines = compact.filter(l => /[\d\s,.]{2,}(?:₽|руб\.?|RUB)/i.test(l));
      const addressLines = compact.filter(l => /(адрес|address|Россия,|Москва|Санкт[-\s]?Петербург|Казань|ул\.|улица|проспект|переулок|наб\.|шоссе|St\.|火车站|地铁|公里|俄罗斯|莫斯科)/i.test(l));
      const dateLines = compact.filter(l => /(заезд|выезд|check\s*in|check\s*out|\d{1,2}[.\-/]\d{1,2}[.\-/]\d{2,4}|2026-\d{2}-\d{2}|月\d{1,2}日|日至)/i.test(l));
      const amenitiesLines = compact.filter(l => /(酒店服务|登记|24小时|wi[-\s]?fi|вай|интернет|завтрак|парков|кондиционер|ванн|душ|трансфер|кухн|ресторан|бар|холодильник|чайник|фен|полотен|тапоч|халат|телевизор|лифт|багаж|сейф|огнетуш|стирал|химчист|питомц|family|семейн|停车|停車|早餐|空调|无线上网|无线网络|浴室|淋浴|宠物|禁烟|餐厅|酒吧|冰箱|壶|电吹风|毛巾|拖鞋|长袍|电视|电梯|行李寄存|保险箱|灭火器|洗衣|干洗|健身|电脑租赁|会议厅|员工语言|俄语|英语|无障碍|可用性)/i.test(l));
      const timeLines = compact.filter(l => /(\d{1,2}:\d{2}|отправление|прибытие|в пути|поезд|train|вокзал|станция|直到|以后)/i.test(l));
      const summary = ['【智能摘要】', `来源网站: ${location.hostname}`, `页面标题: ${title}`, `页面链接: ${url}`, qp.checkIn ? `入住日期: ${qp.checkIn}` : '', qp.checkOut ? `退房日期: ${qp.checkOut}` : '', qp.hotelId ? `hotelId: ${qp.hotelId}` : '', jsonSummaries.join('\n'), h.length ? `标题候选: ${uniq(h,8).join(' | ')}` : '', priceLines.length ? `价格候选: ${uniq(priceLines,12).join(' | ')}` : '', addressLines.length ? `地址候选: ${uniq(addressLines,12).join(' | ')}` : '', dateLines.length ? `日期候选: ${uniq(dateLines,12).join(' | ')}` : '', amenitiesLines.length ? `设施候选: ${uniq(amenitiesLines,30).join(' | ')}` : '', timeLines.length ? `火车/时间候选: ${uniq(timeLines,18).join(' | ')}` : ''].filter(Boolean).join('\n');
      const keyBlock = ['【重点行】', ...uniq([...focusLines, ...priceLines, ...addressLines, ...dateLines, ...amenitiesLines, ...timeLines], 330)].join('\n');
      const selectedBlock = selected ? `【用户选中文本】\n${clean(selected)}` : ''; const metaBlock = meta.length ? `【Meta】\n${uniq(meta,80).join('\n')}` : ''; const jsonBlock = jsonLdRaw.length ? `【JSON-LD】\n${jsonLdRaw.join('\n').slice(0, 90000)}` : '';
      const fullBlock = mode === 'full' ? `【页面可见全文】\n${clean(body).slice(0, 220000)}` : `【页面可见正文节选】\n${uniq(compact.slice(0,360),360).join('\n')}`;
      const text = clean([selectedBlock, summary, keyBlock, jsonBlock, metaBlock, fullBlock].filter(Boolean).join('\n\n'));
      return {title, url, text: text.slice(0, 300000), capturedAt: new Date().toISOString(), mode};
    }});
  $('pageTitle').value = result.title || ''; $('pageUrl').value = result.url || ''; $('pageText').value = result.text || ''; updateCounter(); setStatus(result.mode === 'full' ? '已全文兜底读取。正文可能较长，请必要时删改后发送。' : '已智能读取。v4 会在时间表页面用 JSON-LD/字段识别和设施区块评分再次清洗。', 'ok');
}
async function sendImport(){ await saveSettings(); const endpoint = $('endpoint').value.trim(); const payload = { source: 'travel-page-capture-extension-v4', kind: $('kind').value, city: $('city').value, defaultDate: $('defaultDate').value, defaultTime: $('defaultTime').value, title: $('pageTitle').value, url: $('pageUrl').value, text: $('pageText').value, sentAt: new Date().toISOString() }; if (!payload.text || payload.text.length < 20) throw new Error('正文太短，请先读取页面或手动粘贴内容'); const res = await fetch(endpoint, { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload) }); const data = await res.json().catch(()=>({})); if (!res.ok) throw new Error(data.error || `发送失败：HTTP ${res.status}`); setStatus(`已发送。导入ID：${data.id || 'unknown'}。回到 HTML 页面点击“读取插件导入”。`, 'ok'); }
$('captureBtn').addEventListener('click', () => captureCurrentPage('smart').catch(e => setStatus(e.message, 'err'))); $('fullCaptureBtn').addEventListener('click', () => captureCurrentPage('full').catch(e => setStatus(e.message, 'err'))); $('sendBtn').addEventListener('click', () => sendImport().catch(e => setStatus(e.message, 'err'))); $('pageText').addEventListener('input', updateCounter); ['endpoint','kind','city','defaultDate','defaultTime'].forEach(id => $(id).addEventListener('change', saveSettings)); loadSettings().then(()=>setStatus('准备好了：打开酒店/火车页面后，先点“智能读取当前页”。v4 会先尝试展开“显示全部/阅读更多”。','warn'));
