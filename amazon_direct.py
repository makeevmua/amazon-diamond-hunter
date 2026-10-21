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
import statistics
import time
from datetime import datetime, timezone
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

import policy

MARKETS = ("amazon.fr", "amazon.de", "amazon.es", "amazon.it")
ASIN = re.compile(r"^[A-Z0-9]{10}$", re.I)
AUTH_URL = "https://api.amazon.co.uk/auth/o2/token"   # LwA EU v3.2
API_URL = "https://creatorsapi.amazon/catalog/v1/searchItems"
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
    doc = _post_json(AUTH_URL, {
        "grant_type": "client_credentials",
        "client_id": env["AMAZON_CREATORS_CLIENT_ID"],
        "client_secret": env["AMAZON_CREATORS_CLIENT_SECRET"],
        "scope": "creatorsapi::default",
    })
    token = doc.get("access_token")
    if not isinstance(token, str) or not token:
        raise ValueError("Amazon Creators did not issue an access token")
    return token


def search_items(token, market, partner_tag, keywords):
    locale = "www." + market
    return _post_json(API_URL, {
        "keywords": keywords, "searchIndex": "All",
        "itemCount": 10, "itemPage": 1, "condition": "New",
        "partnerTag": partner_tag, "marketplace": locale,
        "resources": RESOURCES,
    }, headers={
        "Authorization": "Bearer " + token,
        "x-marketplace": locale,
    })


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
    base = _positive_euro(((selected.get("price") or {}).get("savingBasis") or {}).get("money"))
    claimed = round(100 * (base - current) / base) if base and base > current else 0
    img = (((item.get("images") or {}).get("primary") or {}).get("medium") or {}).get("url") or ""
    link = f"https://{market}/dp/{asin.upper()}"
    return {
        "title": title, "description": "", "content": "", "link": link, "key": link,
        "pubDate": observed_at, "image": img,
        "direct_source": "Amazon Creators API",
        "direct_asin": asin.upper(), "direct_current_eur": current,
        "direct_list_price_eur": base, "direct_claimed_percent": max(0, min(99, claimed)),
        "direct_offer_type": selected.get("type") or "REGULAR",
        "direct_merchant": ((selected.get("merchantInfo") or {}).get("name") or ""),
    }


def observe_price(history, item, now_epoch):
    """Compare authentic first-party observations, never a list price.

    Require two earlier observations spanning >=5min, at least one from
    >=15min ago, and an aggregate median baseline higher than current.
    """
    key = item["link"]
    current = item["direct_current_eur"]
    previous = history.setdefault(key, [])
    good = [r for r in previous if isinstance(r, list) and len(r) == 2
            and type(r[0]) in (int, float) and type(r[1]) in (int, float)
            and 0 < now_epoch - r[0] <= 2 * 86400 and r[1] > 0]
    old = [r for r in good if now_epoch - r[0] >= 15 * 60]
    decline = None
    if len(good) >= 2 and old and max(r[0] for r in good) - min(r[0] for r in good) >= 5 * 60:
        baseline = statistics.median(r[1] for r in good)
        if baseline > current:
            decline = round(100 * (baseline - current) / baseline)
    # Don't flood the price history with identical five-minute observations.
    if not good or abs(good[-1][1] - current) > .009 or now_epoch - good[-1][0] >= 1200:
        good.append([int(now_epoch), current])
    history[key] = good[-32:]
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
    limit = max(1, min(int(env.get("DIRECT_MAX_QUERIES", "8")), 16))
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
    for i in range(min(limit, len(market_queries))):
        market, query = market_queries[(cursor + i) % len(market_queries)]
        if attempted:
            time.sleep(sleep_between)
        attempted += 1
        try:
            response = search_items(token, market,
                     env["AMAZON_PARTNER_TAG_" + market[-2:].upper()], query)
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
    if len(history) > 5000:
        state["direct_price_history"] = dict(list(history.items())[-4000:])
    print(f"AMAZON DIRECT: {len(found)} first-party new product quotes from {successful}/{attempted} queries")
    return list(found.values())
