"""Production enhancements to static storefront, requiring no backend.

Favorites are kept locally in each visitor's browser. Price-history snippets
carry explicit provenance and are not mislabeled as live Amazon savings.
"""
import hashlib
import html
import json
import re
import sqlite3
from pathlib import Path
import observations


def _replace_once(text, before, after):
    if before not in text:
        raise ValueError("Unknown storefront version: " + before[:60])
    return text.replace(before, after, 1)


def enhance(directory, db_path):
    directory = Path(directory)
    html_path = directory / "index.html"
    json_path = directory / "finds.json"
    data = json.loads(json_path.read_text(encoding="utf-8"))
    cards = data.get("offers", [])
    with sqlite3.connect(db_path) as db:
        observations.ensure(db)
        for row in cards:
            observations.enrich(row, db)
    json_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    site = html_path.read_text(encoding="utf-8")

    articles = re.findall(r'<article class="card"[^>]*>.*?</article>', site, flags=re.S)
    if len(articles) != len(cards):
        raise ValueError("Website cards do not match JSON export")
    for markup, row in zip(articles, cards):
        key = hashlib.sha256(row["source_url"].encode("utf-8")).hexdigest()[:24]
        enriched = markup.replace(
            '<article class="card"',
            '<article class="card" data-key="' + key +
            '" data-date="' + html.escape(row["last_seen"], quote=True) +
            '" data-keepa="' + ("yes" if row.get("keepa") else "no") + '"',
            1
        )
        enriched = enriched.replace(
            '</div><div class="info">',
            '<button class="favorite" type="button" aria-pressed="false" '
            'aria-label="Добавить в избранное">♡</button></div><div class="info">',
            1
        )
        details = []
        keepa = row.get("keepa")
        if keepa:
            kind = "Цена Amazon" if keepa["price_type"] == "amazon" else "Новые товары других продавцов Amazon"
            reference = keepa.get("reference_90d_eur")
            details.append(
                '<div class="keepa-note">Keepa · ' + html.escape(kind) +
                ': ' + f'{keepa["current_eur"]:.2f} €' +
                (f' · средняя за 90 дней {reference:.2f} €' if reference else
                 ' · истории за 90 дней недостаточно') +
                ' · доставка и продавец не проверены</div>'
            )
        prices = row.get("public_price_changes", [])
        if len(prices) > 1:
            values = ", ".join(f'{p["advertised_price_eur"]:.2f} €' for p in prices[:4])
            details.append('<div class="history-note">Цены, указанные в публикации: ' +
                           html.escape(values) + ' (не история Amazon)</div>')
        if details:
            enriched = enriched.replace('<div class="actions">', "".join(details) +
                                        '<div class="actions">', 1)
        site = site.replace(markup, enriched, 1)

    site = _replace_once(site, '<select id="status" aria-label="Фильтр ссылок">',
                         '<select id="freshness" aria-label="Свежесть"><option value="">Любая дата</option><option value="2">За 48 часов</option><option value="7">За 7 дней</option></select>\n'
                         '<select id="status" aria-label="Фильтр ссылок">')
    site = _replace_once(site, '<button type="button" data-tab="review">На проверку</button>',
                         '<button type="button" data-tab="review">На проверку</button>'
                         '<button type="button" data-tab="favorites">Избранное ♡</button>'
                         '<button type="button" data-tab="keepa">С Keepa</button>')
    site = _replace_once(site, '</style></head>', """
.favorite{position:absolute;right:10px;bottom:10px;width:36px;height:36px;border-radius:50%;font-size:25px;line-height:1;color:#fff;background:#17243bde;border:1px solid #ffffff60}
.favorite[aria-pressed=true]{color:#ffca71}
.keepa-note{font-size:11px;line-height:1.45;color:#86e3c2;margin-top:8px}
.history-note{font-size:11px;line-height:1.45;color:#afc0da;margin-top:8px}
</style></head>""")
    site = _replace_once(site, 'const category=document.getElementById("category"),status=document.getElementById("status"),',
                         'const freshness=document.getElementById("freshness");\n'
                         'const category=document.getElementById("category"),status=document.getElementById("status"),')
    site = _replace_once(site, 'let mode="all",limit=48;', """
let mode="all",limit=48;
let favorites=new Set();
try{favorites=new Set(JSON.parse(localStorage.getItem("diamond-favs-v1")||"[]"))}catch(e){}
for(const item of cards){
 const btn=item.querySelector(".favorite");
 if(!btn)continue;
 const paint=()=>{const f=favorites.has(item.dataset.key);btn.textContent=f?"♥":"♡";
    btn.setAttribute("aria-pressed",String(f));btn.setAttribute("aria-label",f?"Убрать из избранного":"Добавить в избранное")};
 btn.addEventListener("click",()=>{
  if(favorites.has(item.dataset.key))favorites.delete(item.dataset.key);else favorites.add(item.dataset.key);
  try{localStorage.setItem("diamond-favs-v1",JSON.stringify([...favorites]))}catch(e){}
  paint();filter();
 });paint();
}
""")
    site = _replace_once(site, 'const q=search.value.trim().toLocaleLowerCase(),cat=category.value,st=status.value;',
                         'const q=search.value.trim().toLocaleLowerCase(),cat=category.value,st=status.value;\n'
                         'const ageLimit=freshness.value?Number(freshness.value)*86400000:Infinity;')
    site = _replace_once(site, 'const visible=cards.filter(el=>(!q||el.textContent.toLocaleLowerCase().includes(q))&&',
                         'const visible=cards.filter(el=>(Date.now()-Date.parse(el.dataset.date)<=ageLimit)&&'
                         '(mode!=="favorites"||favorites.has(el.dataset.key))&&'
                         '(mode!=="keepa"||el.dataset.keepa==="yes")&&'
                         '(!q||el.textContent.toLocaleLowerCase().includes(q))&&')
    site = _replace_once(site, 'for(const el of [search,category,status,sort])',
                         'for(const el of [search,category,status,sort,freshness])')
    site = _replace_once(site, 'if(document.hidden||search.value||category.value||status.value||window.scrollY>200)return;',
                         'if(document.hidden||search.value||category.value||status.value||freshness.value||mode!=="all"||window.scrollY>200)return;')
    site = site.replace(
        'В редакционных статьях может сравниваться несколько магазинов, как в случае Samsung S26+ и Joybuy.',
        'Редакционные статьи могут сравнивать несколько магазинов и не уточнять реального продавца.'
    )
    html_path.write_text(site, encoding="utf-8")
    return {"cards": len(cards), "keepa": sum(bool(row.get("keepa")) for row in cards)}
