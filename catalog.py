"""A browsable, cautious archive of FR/ES/IT public deal announcements.

SQLite and static HTML/CSV/JSON exports require only Python's standard library.
A source's advertised price is NEVER a live verified Amazon price.
"""
import csv
import html
import json
import re
import sqlite3
import media
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

SCHEMA = """
CREATE TABLE IF NOT EXISTS offers (
    source_url TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    title TEXT NOT NULL,
    category TEXT NOT NULL,
    claimed_discount INTEGER NOT NULL,
    advertised_price_eur REAL,
    amazon_product_url TEXT,
    price_scope TEXT NOT NULL,
    state TEXT NOT NULL,
    image_url TEXT,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    times_seen INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_offers_last_seen ON offers(last_seen DESC);
CREATE INDEX IF NOT EXISTS idx_offers_category ON offers(category);
"""
EXPORT_FIELDS = [
    "source_url", "source", "title", "category", "claimed_discount",
    "advertised_price_eur", "amazon_product_url", "price_scope", "state",
    "image_url", "first_seen", "last_seen", "times_seen",
]
EXCLUDED_BRICKSET = re.compile(
    r"^(?:discounts?|deals?|offers?|promotions?)\s+(?:at|on|from)\s+amazon\.(?:fr|es|it)\b",
    re.I,
)
COMPARISON_DOMAINS = {"lesnumeriques.com", "www.lesnumeriques.com"}
SOURCE_DOMAINS = {
    "dealabs.com", "www.dealabs.com", "chollometro.com", "www.chollometro.com",
    "quantospendo.app", "www.quantospendo.app",
    "lesnumeriques.com", "www.lesnumeriques.com",
    "brickset.com", "www.brickset.com",
}
ALLOWED_AMAZON = {"amazon.fr", "amazon.es", "amazon.it"}


def _source_host(link):
    try:
        p = urlsplit(link)
        if p.scheme != "https" or p.username or p.password or p.port not in (None, 443):
            return ""
        return (p.hostname or "").lower()
    except ValueError:
        return ""


def _clean(text):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", str(text or "")))).strip()


def collect_records(posts, candidates, category_fn, percent_fn, price_pair_fn,
                    headline_price_fn, blocked_fn, fresh_fn, amazon_link_fn):
    """Index relevant posts including below-alert-threshold watchlist entries."""
    candidate_by_url = {x["item"]["link"]: x for x in candidates}
    records = []
    for post in posts:
        source = str(post.get("link", ""))[:1400]
        host = _source_host(source)
        if host not in SOURCE_DOMAINS or not fresh_fn(post.get("pubDate")):
            continue
        title = _clean(post.get("title", ""))[:300]
        if host in ("brickset.com", "www.brickset.com") and EXCLUDED_BRICKSET.search(title):
            continue  # Roundups contain multiple prices/ASINs: no fake products.
        description = _clean(post.get("description", ""))[:850]
        text = title + " " + description
        if blocked_fn(text):
            continue
        groups = category_fn(text)
        candidate = candidate_by_url.get(source)
        if not groups and candidate is None:
            continue
        discount = max(percent_fn(title, description) or 0,
                       (price_pair_fn(text) or (0,))[0])
        price = headline_price_fn(title)
        price_scope = ("mixed_merchants_unverified" if host in COMPARISON_DOMAINS
                       else "unverified_public_offer")
        direct = candidate.get("amazon") if candidate else None
        # A direct link in an editorial PRICE COMPARISON does NOT imply that
        # its cheapest headline offer was on Amazon.
        if not direct and host not in COMPARISON_DOMAINS:
            combined = source + " " + description + " " + str(post.get("content", ""))[:1000]
            direct = amazon_link_fn(combined) if "amazon." in combined.lower() else None
        if direct:
            d = _source_host(direct)
            if d not in ALLOWED_AMAZON:
                direct = None
        state = ("ready_for_alert" if candidate and direct else
                 "editorial_review" if candidate or host in COMPARISON_DOMAINS else
                 "watchlist")
        records.append({
            "source_url": source, "source": host,
            "title": title, "category": ", ".join(groups) if groups else candidate["category"],
            "claimed_discount": int(discount),
            "advertised_price_eur": price,
            "amazon_product_url": direct,
            "price_scope": price_scope, "state": state,
            "image_url": str(post.get("image") or (candidate or {}).get("image") or "")[:1400],
        })
    return records


def save_records(rows, db_path="catalog.sqlite", now=None):
    """Store one row per original article, refreshing sightings, not prices.

    Prices are best-effort third-party publication claims and aren't treated
    as a time-series of independently observed Amazon prices.
    """
    when = now or datetime.now(timezone.utc).isoformat(timespec="seconds")
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as db:
        db.executescript(SCHEMA)
        for row in rows:
            db.execute("""
                INSERT INTO offers (
                    source_url,source,title,category,claimed_discount,
                    advertised_price_eur,amazon_product_url,price_scope,
                    state,image_url,first_seen,last_seen,times_seen
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,1)
                ON CONFLICT(source_url) DO UPDATE SET
                    source=excluded.source, title=excluded.title,
                    category=excluded.category,
                    claimed_discount=excluded.claimed_discount,
                    advertised_price_eur=excluded.advertised_price_eur,
                    amazon_product_url=COALESCE(excluded.amazon_product_url,
                                                offers.amazon_product_url),
                    price_scope=excluded.price_scope,
                    state=excluded.state,
                    image_url=CASE WHEN excluded.image_url != ''
                        THEN excluded.image_url ELSE offers.image_url END,
                    last_seen=excluded.last_seen,
                    times_seen=offers.times_seen+1
            """, tuple(row.get(k) for k in EXPORT_FIELDS[:-3]) + (when, when))
        # Bound storage by date and count. Fresh publication sightings are not
        # proof of continued stock or the current price.
        cutoff = (datetime.fromisoformat(when) - timedelta(days=60)).isoformat(
            timespec="seconds")
        db.execute("DELETE FROM offers WHERE last_seen < ?", (cutoff,))
        db.execute("""
            DELETE FROM offers WHERE source_url NOT IN (
                SELECT source_url FROM offers ORDER BY last_seen DESC LIMIT 6000
            )""")
        db.commit()
        return db.execute("SELECT count(*) FROM offers").fetchone()[0]


def export(db_path="catalog.sqlite", directory="catalog_export"):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as db:
        db.row_factory = sqlite3.Row
        rows = [dict(row) for row in db.execute(
            "SELECT * FROM offers ORDER BY last_seen DESC LIMIT 1500")]
        total = db.execute("SELECT count(*) FROM offers").fetchone()[0]
    payload = {
        "note": "Public advertisements only. NOT verified Amazon prices, sellers or delivery.",
        "sources": "France, Spain, Italy (no US/UK)",
        "total_saved": total, "exported": len(rows),
        "offers": rows,
    }
    (directory / "finds.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    with (directory / "finds.csv").open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=EXPORT_FIELDS)
        writer.writeheader()
        writer.writerows([{k: row.get(k) for k in EXPORT_FIELDS} for row in rows])
    content = []
    for row in rows:
        name = html.escape(row["title"])
        source = html.escape(row["source_url"], quote=True)
        target = html.escape(row["amazon_product_url"] or "", quote=True)
        cat = html.escape(row["category"])
        price = (f'{row["advertised_price_eur"]:.2f} €'
                 if row["advertised_price_eur"] is not None else "—")
        claimed = (f'−{row["claimed_discount"]}%' if row["claimed_discount"] else "—")
        links = f'<a href="{source}" target="_blank" rel="noopener">Источник</a>'
        if target:
            links += f' · <a href="{target}" target="_blank" rel="noopener">Amazon</a>'
        scope = "Сравнение магазинов" if row["price_scope"] == "mixed_merchants_unverified" else "Цена не проверена"
        photo = media.safe_image_url(row["image_url"])
        image = ('<img loading="lazy" width="72" height="72" alt="Фото публикации" '
                 'src="' + html.escape(photo, quote=True) + '">'
                 if photo else '<span class="no-photo">Нет фото</span>')
        state = html.escape(row["state"], quote=True)
        content.append(
            '<tr data-category="' + html.escape(row["category"], quote=True) +
            '" data-status="' + state + '" data-claim="' +
            str(int(row["claimed_discount"])) + '"><td><div class="product">' +
            image + '<div><b>' + name + '</b><small>' +
            html.escape(row["source"]) + '</small></div></div></td><td>' +
            cat + '</td><td>' + price + '</td><td>' + claimed +
            '</td><td>' + state + '<small>' + scope +
            '</small></td><td>' + links + '</td></tr>')
    template = """<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Diamond Hunter — База находок</title>
<style>
:root{font-family:system-ui,-apple-system,sans-serif;color:#edf4ff;background:#0c1220}
body{max-width:1320px;margin:auto;padding:28px 16px}
h1{font-size:clamp(25px,4vw,42px);margin:4px 0 8px}
p{color:#b8c5d9;line-height:1.5}
section{background:#182338;border:1px solid #2a3852;border-radius:18px;padding:16px}
input,select{background:#0c1220;color:white;padding:13px 15px;border-radius:10px;
border:1px solid #576984;font-size:16px}
input{width:min(96%,520px)}
.toolbar{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:10px}
.product{display:flex;gap:12px;align-items:center;min-width:280px}
.product img,.no-photo{border-radius:12px;width:72px;height:72px;object-fit:cover;
background:#253049;flex-shrink:0}
.no-photo{display:flex;align-items:center;justify-content:center;color:#94a6c1;font-size:11px}
table{border-collapse:collapse;width:100%;font-size:14px}
td,th{text-align:left;border-bottom:1px solid #2f3b51;padding:13px 8px;vertical-align:top}
th{color:#98bafa}tr:hover{background:#223049}small{display:block;color:#90a4c0;margin-top:5px}
a{color:#9abfff}a:hover{color:white}
.scroller{overflow:auto}footer{color:#8393ae;margin-top:24px}
</style></head><body><header><p>💎 AMAZON DIAMOND HUNTER</p><h1>База находок</h1>
<p>Всего сохранено: COUNT. В таблице показаны последние MAXROWS публикаций.
Это предложения из открытых источников, а не проверенные актуальные цены Amazon.
США и Великобритания исключены. Скидки сравнения магазинов не относятся автоматически к Amazon.</p></header>
<section><div class="toolbar">
<input id="search" placeholder="Поиск: LEGO, Bauhaus, Samsung, источник…"
aria-label="Поиск по базе">
<select id="category" aria-label="Фильтр по категории"><option value="">Все категории</option></select>
<select id="status" aria-label="Статус проверки">
<option value="">Все находки</option>
<option value="ready_for_alert">Есть ссылка Amazon</option>
<option value="watchlist">Наблюдение</option>
<option value="editorial_review">Нужно проверить источник</option>
</select>
<select id="sorting" aria-label="Сортировка"><option value="new">Сначала новые</option>
<option value="discount">По заявленной скидке</option></select>
</div><p id="counter">Загружено строк: MAXROWS</p>
<div class="scroller"><table><thead><tr><th>Товар</th><th>Категория</th>
<th>Заявленная цена</th><th>Скидка</th><th>Статус</th><th>Ссылки</th>
</tr></thead><tbody id="results">ROWS</tbody></table></div></section>
<footer>Файлы finds.csv и finds.json содержат исходные данные. Заявленные цены
могли устареть, наличие и доставка во Францию не проверены.</footer>
<script>
const rows=[...document.querySelectorAll('#results tr')];
const cat=document.getElementById('category');
const choices=[...new Set(rows.flatMap(r=>r.dataset.category.split(', ')))].sort();
for(const choice of choices){const option=document.createElement('option');
option.value=choice;option.textContent=choice;cat.appendChild(option)}
const search=document.getElementById('search'),status=document.getElementById('status');
const tbody=document.getElementById('results');
function update(){
const q=search.value.trim().toLocaleLowerCase(),c=cat.value,st=status.value;
let count=0;
for(const row of rows){
let match=row.textContent.toLocaleLowerCase().includes(q) &&
(!c||row.dataset.category.split(', ').includes(c)) &&
(!st||row.dataset.status===st);
row.hidden=!match;if(match)count++;
}
document.getElementById('counter').textContent='Найдено в таблице: '+count;
}
function sortRows(){
let sorted=document.getElementById('sorting').value==='discount'?
[...rows].sort((a,b)=>Number(b.dataset.claim)-Number(a.dataset.claim)):rows;
for(const row of sorted)tbody.appendChild(row);
}
search.addEventListener('input',update);
cat.addEventListener('change',update);
status.addEventListener('change',update);
document.getElementById('sorting').addEventListener('change',sortRows);
</script></body></html>"""
    html_out = template.replace("COUNT", str(total)).replace(
        "MAXROWS", str(len(rows))).replace("ROWS", "\n".join(content))
    (directory / "index.html").write_text(html_out, encoding="utf-8")
    return {"total": total, "exported": len(rows), "path": str(directory)}
