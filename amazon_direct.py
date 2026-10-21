"""First-party Amazon deal discovery via the official Amazon Creators API.

No scraping, proxies, fake prices, unofficial endpoints or legacy PA-API.
Needs approved Associates access + valid marketplace partner tags; without
credentials this source is disabled, and third-party fallback remains active.

SearchItems observes Amazon's featured new offer (which may be from a
third-party seller), not checkout totals or unconditional savings.
"""
import json
import math
import os
import re
from urllib.parse import urlsplit
import time
from datetime import datetime, timezone
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

import policy

MARKETS = ("amazon.fr", "amazon.de", "amazon.es", "amazon.it")
ASIN = re.compile(r"^[A-Z0-9]{10}$", re.I)
AUTH_URL = "https://api.amazon.co.uk/auth/o2/token"   # LwA EU v3.2
API_URL = "https://creatorsapi.amazon/catalog/v1/searchItems"
GETITEMS_URL = "https://creatorsapi.amazon/catalog/v1/getItems"
_TOKEN_CACHE = {"value": None, "expires": 0}
RESOURCES = [
    "itemInfo.title", "images.primary.medium",
    "offersV2.listings.price", "offersV2.listings.condition",
    "offersV2.listings.availability", "offersV2.listings.dealDetails",
    "offersV2.listings.isBuyBoxWinner", "offersV2.listings.merchantInfo",
]
# Search from Amazon itself. Rotate small batches on each run to remain within
# authorized request quotas. Queries are *discovery*, not user-supplied ASINs.
QUERIES = {
    "amazon.fr": [
        "ssd nvme", "PS5 manette", "lego", "lampe design", "mobilier design",
        "casque bluetooth", "Ryzen", "RTX", "Dyson", "Apple AirPods",
        "table basse design", "Nintendo Switch", "machine a cafe",
        "fauteuil scandinave", "monitor OLED", "soins visage",
        "jeux PlayStation", "souris gaming", "tapis design", "aspirateur robot",
    ],
    "amazon.de": [
        "SSD NVMe", "PS5 Controller", "LEGO", "Bauhaus Lampe", "Design Möbel",
        "Kopfhörer", "Ryzen", "RTX", "Dyson", "Apple AirPods",
        "Design Tisch", "Nintendo Switch", "Kaffeemaschine",
        "Skandinavischer Sessel", "OLED Monitor", "PlayStation Spiele",
    ],
    "amazon.es": [
        "SSD NVMe", "mando PS5", "LEGO", "lampara diseño", "muebles diseño",
        "auriculares", "Ryzen", "RTX", "Dyson", "Apple AirPods",
        "mesa diseño", "Nintendo Switch", "cafetera",
        "sillon escandinavo", "monitor OLED", "juegos PlayStation",
    ],
    "amazon.it": [
        "SSD NVMe", "controller PS5", "LEGO", "lampada design", "mobili design",
        "cuffie bluetooth", "Ryzen", "RTX", "Dyson", "Apple AirPods",
        "tavolino design", "Nintendo Switch", "macchina caffe",
        "poltrona scandinava", "monitor OLED", "giochi PlayStation",
    ],
}


def enabled(env=None):
    env = env or os.environ
    return bool(env.get("AMAZON_CREATORS_CLIENT_ID")
                and env.get("AMAZON_CREATORS_CLIENT_SECRET")
                and any(env.get("AMAZON_PARTNER_TAG_" + m[-2:].upper())
                        for m in MARKETS))


def _post_json(url, payload, headers=None, timeout=12):
    req = Request(url, data=json.dumps(payload).encode("utf-8"),
                  headers={"Content-Type": "application/json", **(headers or {})},
                  method="POST")
    with urlopen(req, timeout=timeout) as res:
        return json.loads(res.read(2_000_000).decode("utf-8"))


def get_token(env=None):
    env = env or os.environ
    now = time.monotonic()
    if _TOKEN_CACHE["value"] and now + 120 < _TOKEN_CACHE["expires"]:
        return _TOKEN_CACHE["value"]
    doc = _post_json(AUTH_URL, {
        "grant_type": "client_credentials",
        "client_id": env["AMAZON_CREATORS_CLIENT_ID"],
        "client_secret": env["AMAZON_CREATORS_CLIENT_SECRET"],
        "scope": "creatorsapi::default",
    })
    token = doc.get("access_token")
    if not isinstance(token, str) or not token:
        raise ValueError("Amazon Creators did not issue an access token")
    ttl = doc.get("expires_in", 3600)
    _TOKEN_CACHE.update({"value": token, "expires": now + min(int(ttl), 3600)})
    return token


def search_items(token, market, partner_tag, keywords, saving_min=None):
    locale = "www." + market
    payload = {
        "keywords": keywords, "searchIndex": "All",
        "itemCount": 10, "itemPage": 1, "condition": "New",
        "partnerTag": partner_tag, "marketplace": locale,
        "resources": RESOURCES,
    }
    if saving_min is not None:
        payload["minSavingPercent"] = int(saving_min)
    return _post_json(API_URL, payload, headers={
        "Authorization": "Bearer " + token,
        "x-marketplace": locale,
    })


def get_items(token, market, partner_tag, asins):
    locale = "www." + market
    return _post_json(GETITEMS_URL, {
        "itemIds": asins, "itemIdType": "ASIN", "condition": "New",
        "partnerTag": partner_tag, "marketplace": locale,
        "resources": RESOURCES,
    }, headers={"Authorization": "Bearer " + token, "x-marketplace": locale})


def add_watchlist(state, canonical_urls):
    """Store only product identifiers, not long-lived Amazon advertising content."""
    watched = state.setdefault("direct_watch_asins", {})
    for url in canonical_urls:
        if not isinstance(url, str):
            continue
        try:
            p = urlsplit(url)
            market = (p.hostname or "").lower()
            asin = p.path.strip("/").split("/")
            if (p.scheme == "https" and market in MARKETS and len(asin) == 2
                    and asin[0] == "dp" and ASIN.fullmatch(asin[1])):
                store = watched.setdefault(market, [])
                upper = asin[1].upper()
                if upper not in store:
                    store.append(upper)
                watched[market] = store[-150:]
        except (ValueError, TypeError):
            pass
    return sum(len(x) for x in watched.values())


def _positive_euro(obj):
    if not isinstance(obj, dict) or obj.get("currency") != "EUR":
        return None
    n = obj.get("amount")
    if type(n) not in (float, int) or not math.isfinite(n) or n <= 0 or n > 100000:
        return None
    return round(n, 2)


def normalize_item(item, market, observed_at):
    """Return only exact ASIN offers with a valid new EUR featured price."""
    if market not in MARKETS or not isinstance(item, dict):
        return None
    asin = item.get("asin")
    if not isinstance(asin, str) or not ASIN.fullmatch(asin):
        return None
    title = (((item.get("itemInfo") or {}).get("title") or {}).get("displayValue") or "").strip()
    if not title or not policy.is_allowed(title):
        return None
    listings = ((item.get("offersV2") or {}).get("listings") or [])
    if not isinstance(listings, list):
        return None
    eligible = []
    for offer in listings:
        if not isinstance(offer, dict):
            continue
        if ((offer.get("condition") or {}).get("value") or "").lower() != "new":
            continue
        if (offer.get("availability") or {}).get("type") == "OUT_OF_STOCK":
            continue
        price = _positive_euro((offer.get("price") or {}).get("money"))
        if price is None:
            continue
        eligible.append((not offer.get("isBuyBoxWinner", False), price, offer))
    if not eligible:
        return None
    # Prefer Buy Box if identified. If not, choose cheapest *listed* new offer.
    _, current, selected = min(eligible, key=lambda x: (x[0], x[1]))
    price_meta = selected.get("price") or {}
    base = _positive_euro((price_meta.get("savingBasis") or {}).get("money"))
    savings_pct = (price_meta.get("savings") or {}).get("percentage")
    claimed = int(savings_pct) if type(savings_pct) is int and 0 < savings_pct < 100 else 0
    deal = selected.get("dealDetails") or {}
    if claimed == 0 and base and base > current:
        claimed = round(100 * (base - current) / base)
    img = (((item.get("images") or {}).get("primary") or {}).get("medium") or {}).get("url") or ""
    link = f"https://{market}/dp/{asin.upper()}"
    official_link = item.get("detailPageURL")
    affiliate_url = link
    if isinstance(official_link, str):
        try:
            parts = urlsplit(official_link)
            if (parts.scheme == "https" and parts.hostname in (market, "www." + market)
                    and parts.path == "/dp/" + asin.upper()):
                affiliate_url = official_link
        except ValueError:
            pass
    return {
        "title": title, "description": "", "content": "", "link": link, "key": link,
        "pubDate": observed_at, "image": img,
        "direct_source": "Amazon Creators API",
        "direct_asin": asin.upper(), "direct_current_eur": current,
        "direct_list_price_eur": base, "direct_claimed_percent": max(0, min(99, claimed)),
        "direct_offer_type": selected.get("type") or "REGULAR",
        "direct_merchant": ((selected.get("merchantInfo") or {}).get("name") or ""),
        "direct_affiliate_url": affiliate_url,
        "direct_deal_badge": str(deal.get("badge") or "")[:70],
    }


def observe_price(history, item, now_epoch):
    """Spot short-lived price drops from the previous first-party API quote.

    One earlier valid sample 60+ seconds old is enough: requiring 15 minutes
    and several samples would miss the exact 1–2 minute glitches we seek.
    Return *observed* change, not independently verified historical savings.
    """
    key = item["link"]
    current = item["direct_current_eur"]
    recorded = history.get(key, [])
    valid = [r for r in recorded if isinstance(r, list) and len(r) == 2
             and type(r[0]) in (int, float) and type(r[1]) in (int, float)
             and 60 <= now_epoch - r[0] <= 23 * 3600 and
             0 < r[1] <= 100000]
    latest = max(valid, key=lambda r: r[0]) if valid else None
    decline = (round(100 * (latest[1] - current) / latest[1])
               if latest and current <= latest[1] - 5 and
               current < latest[1] else None)
    # Store successive checks for fast updates (not 20-min spaced),
    # while bounding content lifetime in line with API cache rules.
    valid_recent = [r for r in recorded if isinstance(r, list)
                    and len(r) == 2 and type(r[0]) in (int, float)
                    and type(r[1]) in (int, float) and
                    0 <= now_epoch - r[0] <= 23 * 3600 and
                    0 < r[1] <= 100000]
    valid_recent.append([int(now_epoch), current])
    history[key] = valid_recent[-32:]
    return decline


def discover(state, env=None, now=None, sleep_between=1.1):
    """Round-robin across EU markets; small bounded traffic and zero scraping.

    Returns first-party product observations; no credentials => empty result.
    Does not treat a successful authorization as proof of a specific deal.
    """
    env = env or os.environ
    if not enabled(env):
        print("AMAZON DIRECT: unavailable — approved Creators API credentials not configured.")
        return []
    now = now or datetime.now(timezone.utc)
    when = now.strftime("%a, %d %b %Y %H:%M:%S GMT")
    # Limit request budget to avoid unexpectedly exhausting an affiliate quota.
    try:
        limit = max(1, min(int(env.get("DIRECT_MAX_QUERIES", "8")), 16))
    except (ValueError, TypeError):
        limit = 8
    cursor = int(state.get("creators_cursor", 0))
    market_queries = [(market, q) for market in MARKETS
                      if env.get("AMAZON_PARTNER_TAG_" + market[-2:].upper())
                      for q in QUERIES[market]]
    if not market_queries:
        return []
    try:
        token = get_token(env)
    except (HTTPError, URLError, TimeoutError, OSError, ValueError, KeyError) as e:
        print("AMAZON DIRECT: token unavailable (" + type(e).__name__ + "), retaining RSS fallback")
        return []
    found = {}
    successful = 0
    attempted = 0
    watched = state.get("direct_watch_asins", {})
    if isinstance(watched, dict) and watched:
        watch_cursor = int(state.get("direct_watch_cursor", 0))
        watch_groups = []
        for market in MARKETS:
            if not env.get("AMAZON_PARTNER_TAG_" + market[-2:].upper()):
                continue
            ids = watched.get(market) or []
            if not isinstance(ids, list):
                continue
            for offset in range(0, len(ids), 10):
                valid = [a for a in ids[offset:offset+10] if
                         isinstance(a, str) and ASIN.fullmatch(a)]
                if valid:
                    watch_groups.append((market, valid))
        try:
            watch_budget = max(0, min(int(env.get("DIRECT_WATCH_BATCHES", "2")), 5))
        except (TypeError, ValueError):
            watch_budget = 2
        for i in range(min(watch_budget, len(watch_groups))):
            market, ids = watch_groups[(watch_cursor+i) % len(watch_groups)]
            try:
                rsp = get_items(token, market,
                    env["AMAZON_PARTNER_TAG_" + market[-2:].upper()], ids)
                for it in ((rsp.get("itemsResult") or {}).get("items") or []):
                    rec = normalize_item(it, market, when)
                    if rec:
                        found[rec["link"]] = rec
                successful += 1
            except (HTTPError, URLError, TimeoutError, OSError, ValueError, KeyError) as e:
                print(f"AMAZON DIRECT WATCH: {market} failed ({type(e).__name__})")
                if isinstance(e, HTTPError) and e.code in (401, 403, 429):
                    break
            time.sleep(sleep_between)
        state["direct_watch_cursor"] = watch_cursor + min(watch_budget, len(watch_groups))
    for i in range(min(limit, len(market_queries))):
        market, query = market_queries[(cursor + i) % len(market_queries)]
        if attempted:
            time.sleep(sleep_between)
        attempted += 1
        try:
            # Three of four searches prioritize actual Amazon-declared deals.
            # Every fourth query is unfiltered so a raw misprice with no
            # advertised discount may still be discovered.
            save = None if (cursor + i) % 4 == 0 else (
                25 if any(word in query.lower() for word in (
                    "design", "bauhaus", "lampe", "mobili", "möbel",
                    "scandinav", "sessel", "tisch", "tapis", "tavolino",
                    "fauteuil", "interior", "poltrona", "escandinavo",
                )) else 40
            )
            response = search_items(token, market,
                     env["AMAZON_PARTNER_TAG_" + market[-2:].upper()], query,
                     saving_min=save)
            items = ((response.get("searchResult") or {}).get("items") or [])
            if not isinstance(items, list):
                continue
            successful += 1
            for it in items:
                rec = normalize_item(it, market, when)
                if rec:
                    found[rec["link"]] = rec
        except (HTTPError, URLError, TimeoutError, OSError, ValueError, KeyError) as error:
            print(f"AMAZON DIRECT: {market} search failed ({type(error).__name__})")
            # Authentication failure / quota limit: don't hammer the provider.
            if isinstance(error, HTTPError) and error.code in (401, 403, 429):
                break
    state["creators_cursor"] = (cursor + attempted) % len(market_queries)
    history = state.setdefault("direct_price_history", {})
    stamp = now.timestamp()
    for rec in found.values():
        rec["direct_observed_drop"] = observe_price(history, rec, stamp)
    # Amazon Creators offer content is time-limited; prune persisted prices.
    for key in list(history):
        history[key] = [r for r in history[key] if
                        isinstance(r, list) and len(r) == 2 and
                        0 <= stamp - r[0] <= 23 * 3600]
        if not history[key]:
            del history[key]
    if len(history) > 4000:
        state["direct_price_history"] = dict(list(history.items())[-3000:])
    print(f"AMAZON DIRECT: {len(found)} first-party new product quotes from {successful}/{attempted} queries")
    return list(found.values())
