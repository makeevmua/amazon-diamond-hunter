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
import pricechecks


def _replace_once(text, before, after):
    if before not in text:
        raise ValueError("Unknown storefront version: " + before[:60])
    return text.replace(before, after, 1)


def enhance(directory, db_path, source_health=None):
    directory = Path(directory)
    html_path = directory / "index.html"
    json_path = directory / "finds.json"
    data = json.loads(json_path.read_text(encoding="utf-8"))
    cards = data.get("offers", [])
    if source_health:
        data["source_health"] = source_health
    with sqlite3.connect(db_path) as db:
        observations.ensure(db)
        for row in cards:
            observations.enrich(row, db)
    # Compare source-reported offers of the same ASIN only; matching an ASIN
    # does not verify country variants, stock, shipping or seller.
    by_asin = {}
    for row in cards:
        identity = pricechecks.identify(row.get("amazon_product_url"))
        if identity:
            market, asin = identity
            by_asin.setdefault(asin, {}).setdefault(market, row)
    for row in cards:
        identity = pricechecks.identify(row.get("amazon_product_url"))
        related = []
        if identity:
            market, asin = identity
            for other_market, other in by_asin.get(asin, {}).items():
                if other_market != market:
                    related.append({
                        "market": other_market,
                        "url": other["amazon_product_url"],
                        "advertised_price_eur": other.get("advertised_price_eur"),
                        "source_url": other["source_url"],
                        "disclaimer": "Price from a public post, not verified Amazon checkout",
                    })
        row["related_markets"] = related
    json_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    site = html_path.read_text(encoding="utf-8")
    if source_health:
        rss_ok = int(source_health.get("rss_ok", 0))
        rss_total = int(source_health.get("rss_total", 0))
        pepper_received = int(source_health.get("pepper", 0))
        amazon_direct_count = int(source_health.get("amazon_direct", 0))
        keepa_count = int(source_health.get("keepa_deals", 0))
        bright_count = int(source_health.get("brightdata_verified", 0))
        search_count = int(source_health.get("brightdata_search", 0))
        cdiscount_count = int(source_health.get("cdiscount_direct", 0))
        joybuy_count = int(source_health.get("joybuy_direct", 0))
        watch_asins = int(source_health.get("watch_asins", 0))
        amazon_api_ready = bool(source_health.get("amazon_creators_configured", False))
        amazon_search_ready = bool(source_health.get("amazon_brightdata_configured", False))
        amazon_search_pending = bool(source_health.get("amazon_brightdata_pending", False))
        amazon_search_error = str(source_health.get("amazon_brightdata_error") or "")[:100]
        retailer_health = source_health.get("retailer_health") or {}
        cstatus = (retailer_health.get("Cdiscount") or {}).get("status", "unknown")
        jstatus = (retailer_health.get("Joybuy") or {}).get("status", "unknown")
        def readable(status, shop):
            if status == "scanned":
                return f"{shop}: проверен"
            if status == "needs_web_unlocker":
                return f"{shop}: нужен доступ Bright Data Web Unlocker"
            if status == "unlocker_budget_exhausted":
                return f"{shop}: достигнут лимит запросов на разблокировку"
            if status == "no_product_links":
                return f"{shop}: нет подходящих карточек в проверенной витрине"
            if status == "unavailable":
                return f"{shop}: источник недоступен"
            return f"{shop}: ещё не проверен"
        amazon_status = (
            "Amazon Creators: подключён" if amazon_api_ready
            else "Amazon Creators: НЕТ ДОСТУПА"
        )
        amazon_status += (
            " · Bright Data Amazon: ожидает снимок" if amazon_search_pending
            else " · Bright Data Amazon: ограниченный поиск" if amazon_search_ready
            else " · Bright Data Amazon: отключён"
        )
        if amazon_search_error:
            amazon_status += " · Последняя ошибка: " + html.escape(amazon_search_error)
        site_status = '<p class="feed-health">' + html.escape(
            amazon_status.split(" · Последняя ошибка: ")[0]
        )
        if amazon_search_error:
            site_status += " · Последняя ошибка: " + html.escape(amazon_search_error)
        site_status += " · успешный запуск GitHub не подтверждает мониторинг Amazon</p>"
        detail = ("<p class=\"feed-health\">Статус: " +
                  html.escape(readable(cstatus, "Cdiscount")) + " · " +
                  html.escape(readable(jstatus, "Joybuy")) +
                  " · 0 находок не означает, что в магазине нет скидок</p>")
        summary = ('<p class="feed-health">Цены Amazon: Creators ' +
                   str(amazon_direct_count) + ' · Keepa Deals ' +
                   str(keepa_count) + ' · Bright Data ' +
                   str(bright_count) + ' сверок · Bright Search ' +
                   str(search_count) + ' карточек · Cdiscount ' +
                   str(cdiscount_count) + ' · Joybuy ' + str(joybuy_count) +
                   ' · ASIN под наблюдением: ' +
                   str(watch_asins) + ' · RSS: ' + str(rss_ok) + '/' +
                   str(rss_total) + ' · Pepper: ' + str(pepper_received) +
                   ' · итог корзины/доставка не проверены</p>')
        site = _replace_once(site, '<div class="stats">', site_status + summary + detail + '<div class="stats">')

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
        related = row.get("related_markets") or []
        if related:
            parts = []
            for other in related:
                price = other.get("advertised_price_eur")
                amount = (f'{price:.2f} € в объявлении' if price is not None
                          else 'цена не указана')
                parts.append('<a target="_blank" rel="noopener noreferrer" href="' +
                             html.escape(other["url"], quote=True) + '">' +
                             html.escape(other["market"] + ': ' + amount) + ' ↗</a>')
            details.append('<details class="cross-market"><summary>Другие страны для этого ASIN (' +
                           str(len(related)) + ')</summary>' +
                           ' · '.join(parts) +
                           '<small>Одинаковый ASIN не подтверждает одинаковую комплектацию или итоговую цену во Франции.</small></details>')
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
.feed-health{font-size:12px;color:#a9c1db;margin:12px 0 6px}
.favorite{position:absolute;right:10px;bottom:10px;width:36px;height:36px;border-radius:50%;font-size:25px;line-height:1;color:#fff;background:#17243bde;border:1px solid #ffffff60}
.favorite[aria-pressed=true]{color:#ffca71}
.keepa-note{font-size:11px;line-height:1.45;color:#86e3c2;margin-top:8px}
.cross-market{font-size:11px;margin-top:10px;color:#b9dbf2;line-height:1.55}
.cross-market summary{cursor:pointer;font-weight:700}
.cross-market a{text-decoration:underline;color:#ffcc7c}
.cross-market small{display:block;color:#a4b4ca;margin-top:5px}
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
