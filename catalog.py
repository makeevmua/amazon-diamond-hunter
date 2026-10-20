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
        pair = price_pair_fn(text)
        discount = max(percent_fn(title, description) or 0,
                       (pair or (0,))[0])
        price = headline_price_fn(title)
        # An explicitly stated "X € instead of Y €" pair gives a
        # single advertised offer price when the title alone has none.
        # Never claim it has been checked on Amazon.
        if price is None and pair is not None:
            price = pair[1]
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
        scope = ("Цена из сравнения магазинов (не обязательно Amazon)"
                 if row["price_scope"] == "mixed_merchants_unverified"
                 else "Цена из объявления — не проверена на Amazon")
        photo = media.safe_image_url(row["image_url"])
        image = ('<img loading="lazy" alt="Иллюстрация объявления" '
                 'src="' + html.escape(photo, quote=True) + '">'
                 if photo else '<span class="photo-placeholder">◇</span>')
        state = html.escape(row["state"], quote=True)
        # Even when an Amazon ASIN is known, the headline price is NOT
        # verified on Amazon. The button always says "check", not "buy".
        if target:
            action = (f'<a class="action buy" href="{target}" target="_blank" '
                      'rel="noopener noreferrer">Проверить на Amazon ↗</a>'
                      f'<a class="action secondary" href="{source}" target="_blank" '
                      'rel="noopener noreferrer">Объявление ↗</a>')
        else:
            action = (f'<a class="action buy" href="{source}" target="_blank" '
                      'rel="noopener noreferrer">Открыть предложение ↗</a>')
        price_text = price if row["advertised_price_eur"] is not None else "Цена не указана"
        discount_chip = ('<span class="discount">' + claimed + '*</span>'
                         if row["claimed_discount"] else "")
        content.append(
            '<article class="card" data-category="' + html.escape(row["category"], quote=True) +
            '" data-status="' + state + '" data-claim="' +
            str(int(row["claimed_discount"])) + '" data-price="' +
            (str(row["advertised_price_eur"]) if row["advertised_price_eur"] is not None else "") +
            '"><div class="photo">' + image +
            '<span class="market">' + html.escape(row["source"]) + '</span>' +
            discount_chip + '</div><div class="info">' +
            '<div class="category">' + cat + '</div>' +
            '<h2>' + name + '</h2><div class="pricing">' + html.escape(price_text) +
            '</div><div class="fineprint">' + html.escape(scope) +
            '</div><div class="actions">' + action +
            '</div></div></article>')
    template = """<!doctype html>
<html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="theme-color" content="#101520">
<meta name="description" content="Diamond Hunter: найденные предложения из Франции, Испании и Италии.">
<title>Diamond Hunter — Витрина находок</title>
<style>
:root{font-family:Inter,system-ui,-apple-system,"Segoe UI",sans-serif;color-scheme:dark;--bg:#0c111b;--card:#1b2635;--edge:#304055;--text:#f2f6ff;--dim:#a7b7cc;--gold:#ffca71;--mint:#83e8c6}
*{box-sizing:border-box}body{margin:0;background:radial-gradient(ellipse at 70% -20%,#2b3b55,transparent 48%),var(--bg);color:var(--text)}
a{color:inherit;text-decoration:none}button,select,input{font:inherit}button{cursor:pointer}
.wrap{max-width:1490px;margin:auto;padding:0 clamp(16px,3vw,45px)}
header{height:78px;border-bottom:1px solid #ffffff16;display:flex;align-items:center;justify-content:space-between;gap:12px}
.brand{display:flex;align-items:center;gap:12px;font-size:18px;font-weight:850;letter-spacing:-.04em}
.brand .mark{display:grid;place-items:center;width:42px;height:42px;border-radius:13px;background:linear-gradient(135deg,#ffe1a0,#f6aa36);font-size:25px}
.brand em{color:var(--gold);font-style:normal}.toplink{color:#c5d5ed;font-size:13px;padding:10px 13px;background:#253245;border:1px solid #3b4c65;border-radius:11px}
.hero{padding:62px 0 34px}.eyebrow{letter-spacing:.15em;font-weight:850;color:var(--gold);font-size:12px}
h1{font-size:clamp(36px,5vw,68px);line-height:1.05;letter-spacing:-.055em;margin:15px 0 17px}
.lead{color:var(--dim);max-width:790px;line-height:1.65;font-size:15px}
.stats{display:flex;flex-wrap:wrap;gap:10px;margin:24px 0 8px}
.stat{background:#182335;border:1px solid var(--edge);border-radius:14px;padding:13px 19px;min-width:140px}
.stat b{font-size:26px;letter-spacing:-.04em;display:block}.stat span{font-size:11px;color:var(--dim)}
.toolbar{display:flex;gap:10px;flex-wrap:wrap;background:#172232;padding:14px;border:1px solid #34435a;border-radius:17px;margin:5px 0 19px}
input,select{border:1px solid #465873;background:#0e1521;color:white;outline:none;border-radius:10px;padding:12px 13px;min-height:46px;font-size:13px}
input:focus,select:focus{border-color:var(--gold)}input[type=search]{flex:1 1 270px;min-width:160px;font-size:15px}
.tabs{display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap;margin:16px 0 20px}
.tablist{display:flex;gap:8px;flex-wrap:wrap}
.tablist button{padding:9px 15px;border-radius:40px;background:#1c2a3d;border:1px solid #36475d;color:#c4d3e7;font-size:12px;font-weight:700}
.tablist button.active{background:var(--gold);color:#262017;border-color:var(--gold)}
#counter{color:#a3b6cb;font-size:12px}
.grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:17px}
.card{display:flex;flex-direction:column;border:1px solid #354359;border-radius:18px;background:linear-gradient(145deg,#1e2a3b,#151e2b);overflow:hidden;min-width:0;transition:transform .17s,border .17s}
.card:hover{transform:translateY(-3px);border-color:#69809d}
.photo{height:215px;background:radial-gradient(ellipse at 70% 20%,#485b77,#1f2b3e);position:relative;display:flex;align-items:center;justify-content:center;overflow:hidden}
.photo img{width:100%;height:100%;object-fit:contain;background:#eef1f6}
.photo-placeholder{font-size:75px;color:#8ba0b9;opacity:.4;font-weight:200}
.market{position:absolute;left:12px;top:12px;background:#122030df;color:#f6f8fc;border:1px solid #ffffff2e;padding:6px 9px;max-width:70%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;border-radius:8px;font-size:10px}
.discount{position:absolute;right:12px;top:12px;background:var(--gold);color:#2d2415;padding:6px 9px;border-radius:9px;font-weight:850;font-size:13px}
.info{padding:16px;flex:1;display:flex;flex-direction:column}
.category{font-size:11px;color:#94b5d4;margin-bottom:9px;overflow:hidden;white-space:nowrap;text-overflow:ellipsis}
.info h2{font-size:15px;line-height:1.45;font-weight:750;letter-spacing:-.012em;margin:0 0 18px;display:-webkit-box;-webkit-box-orient:vertical;-webkit-line-clamp:3;overflow:hidden;min-height:64px}
.pricing{font-size:24px;font-weight:850;letter-spacing:-.04em;margin:auto 0 5px}
.fineprint{font-size:11px;color:#d7be92;min-height:28px;line-height:1.35}
.actions{display:flex;gap:7px;flex-wrap:wrap;margin-top:13px}
.action{display:flex;align-items:center;justify-content:center;text-align:center;border-radius:10px;font-size:12px;font-weight:750;min-height:42px;padding:10px 12px;flex:1 1 125px}
.action.buy{background:var(--gold);color:#25221c}.action.secondary{background:#26374d;color:#e9f2ff;border:1px solid #47617b}
.action:hover{filter:brightness(1.1)}
#more{display:block;margin:32px auto 0;padding:14px 32px;background:#23354a;border:1px solid #5c708b;color:#edf5ff;border-radius:12px;font-weight:750}
#more[hidden],.card[hidden]{display:none}
#noresult{display:none;text-align:center;padding:50px 15px;background:#182233;border:1px dashed #40536f;border-radius:14px;color:#b9c7db}
.note{margin:40px 0 55px;padding:20px 24px;background:#172437;border:1px solid #354861;border-radius:15px;color:#c1d0e2;line-height:1.7;font-size:13px}
.note b{color:var(--gold)}footer{border-top:1px solid #ffffff18;padding:25px 0 40px;color:#91a3ba;font-size:12px;display:flex;gap:12px;justify-content:space-between;flex-wrap:wrap}
footer a{color:#e7c681}
@media(max-width:1090px){.grid{grid-template-columns:repeat(3,minmax(0,1fr))}}
@media(max-width:710px){.grid{grid-template-columns:repeat(2,minmax(0,1fr));gap:11px}.photo{height:175px}.info{padding:12px}.pricing{font-size:21px}.info h2{font-size:13px;min-height:62px}.hero{padding-top:36px}.brand{font-size:14px}header{height:67px}.stat{min-width:100px;padding:10px 13px}.stat b{font-size:21px}}
@media(max-width:440px){.photo{height:145px}.market{font-size:9px;padding:5px}.discount{font-size:11px;padding:5px}.action{flex-basis:100%;font-size:11px}.toolbar{padding:10px}.stat{min-width:91px}}
@media(prefers-reduced-motion:reduce){.card{transition:none}}
</style></head><body>
<header class="wrap"><div class="brand"><span class="mark">💎</span><span>DIAMOND <em>HUNTER</em></span></div>
<a class="toplink" href="https://github.com/makeevmua/amazon-diamond-hunter" target="_blank" rel="noopener noreferrer">GitHub ↗</a></header>
<div class="wrap">
<section class="hero"><div class="eyebrow">ВИТРИНА НАХОДОК</div>
<h1>Хорошие вещи.<br>Интересные цены.</h1>
<p class="lead">Дизайнерский интерьер, LEGO, электроника и необычные предложения — из Франции, Испании и Италии. Найденная цена <strong>не проверена на Amazon</strong>. Открой карточку и уточни продавца, комплектацию и конечную стоимость доставки.</p>
<div class="stats"><div class="stat"><b>COUNT</b><span>объявлений в базе</span></div>
<div class="stat"><b id="amazon-count">—</b><span>с прямой ссылкой Amazon</span></div>
<div class="stat"><b id="priced-count">—</b><span>с заявленной ценой</span></div></div></section>
<main>
<div class="toolbar">
<input id="search" type="search" placeholder="Найти LEGO, Bauhaus, Dyson…" aria-label="Поиск">
<select id="category" aria-label="Фильтр категорий"><option value="">Все категории</option></select>
<select id="status" aria-label="Фильтр ссылок"><option value="">Все ссылки</option><option value="ready_for_alert">Есть карточка Amazon</option><option value="watchlist">Список наблюдения</option><option value="editorial_review">Редакционные находки</option></select>
<select id="sort" aria-label="Сортировка"><option value="new">Новые находки</option><option value="discount">Максимальная скидка</option><option value="cheap">Сначала дешевле</option></select>
</div>
<div class="tabs"><div class="tablist"><button class="active" type="button" data-tab="all">Все</button><button type="button" data-tab="amazon">С Amazon</button><button type="button" data-tab="priced">С ценой</button><button type="button" data-tab="review">На проверку</button></div><span id="counter">Загрузка…</span></div>
<div class="grid" id="cards">ROWS</div>
<div id="noresult">Здесь пока нет предложений. Попробуй другие фильтры.</div>
<button id="more" hidden type="button">Показать ещё ↓</button>
<aside class="note"><b>Важно:</b> цифры и проценты на этой странице — заявления из сторонних публикаций, не проверенные текущие цены Amazon. В редакционных статьях может сравниваться несколько магазинов, как в случае Samsung S26+ и Joybuy. Даже если есть ссылка на Amazon, цена статьи может отличаться от выбранной версии товара. Кнопка «Проверить на Amazon» не оформляет покупку.</aside>
</main></div>
<footer class="wrap"><span>Diamond Hunter · автоматическая публикация после сканирований</span><div><a href="finds.csv" download>CSV</a> · <a href="finds.json" download>JSON</a></div></footer>
<script>
"use strict";
const cards=[...document.querySelectorAll("#cards .card")];
const category=document.getElementById("category"),status=document.getElementById("status"),
sort=document.getElementById("sort"),search=document.getElementById("search"),wrap=document.getElementById("cards");
const allCategories=[...new Set(cards.flatMap(c=>c.dataset.category.split(", ").filter(Boolean)))].sort((a,b)=>a.localeCompare(b));
for(const name of allCategories){const option=document.createElement("option");option.value=name;option.textContent=name;category.add(option)}
let mode="all",limit=48;
document.getElementById("amazon-count").textContent=cards.filter(x=>x.dataset.status==="ready_for_alert").length;
document.getElementById("priced-count").textContent=cards.filter(x=>x.dataset.price!=="").length;
const filter=()=>{
const q=search.value.trim().toLocaleLowerCase(),cat=category.value,st=status.value;
const visible=cards.filter(el=>(!q||el.textContent.toLocaleLowerCase().includes(q))&&(!cat||el.dataset.category.split(", ").includes(cat))&&(!st||el.dataset.status===st)&&
(mode!=="amazon"||el.dataset.status==="ready_for_alert")&&(mode!=="priced"||el.dataset.price!=="")&&(mode!=="review"||el.dataset.status==="editorial_review"));
if(sort.value==="discount")visible.sort((a,b)=>Number(b.dataset.claim)-Number(a.dataset.claim));
if(sort.value==="cheap")visible.sort((a,b)=>(a.dataset.price===""?Infinity:Number(a.dataset.price))-(b.dataset.price===""?Infinity:Number(b.dataset.price)));
for(const el of visible)wrap.append(el);
const selected=new Set(visible.slice(0,limit));for(const el of cards)el.hidden=!selected.has(el);
document.getElementById("counter").textContent="Показано "+Math.min(limit,visible.length)+" из "+visible.length;
document.getElementById("more").hidden=visible.length<=limit;
document.getElementById("noresult").style.display=visible.length?"none":"block";
};
for(const el of [search,category,status,sort])el.addEventListener(el===search?"input":"change",()=>{limit=48;filter()});
for(const b of document.querySelectorAll("[data-tab]"))b.addEventListener("click",()=>{mode=b.dataset.tab;limit=48;for(const t of document.querySelectorAll("[data-tab]"))t.classList.toggle("active",t===b);filter()});
document.getElementById("more").addEventListener("click",()=>{limit+=48;filter()});
filter();
</script></body></html>"""
    html_out = template.replace("COUNT", str(total)).replace(
        "MAXROWS", str(len(rows))).replace("ROWS", "\n".join(content))
    (directory / "index.html").write_text(html_out, encoding="utf-8")
    return {"total": total, "exported": len(rows), "path": str(directory)}
