"""Discover changed Amazon offers directly from Keepa Browsing Deals.

Keepa refresh is not a live checkout observation. This adapter never labels
Keepa's 90-day average as a reference/list price supplied by Amazon.
Providers are contacted only with an explicitly supplied private API key.
"""
import json
import math
import os
import re
from datetime import datetime, timezone
from urllib.request import Request, urlopen
from urllib.parse import urlencode
from urllib.error import HTTPError, URLError

import policy

DOMAINS = {"amazon.fr": 4, "amazon.de": 3, "amazon.es": 9, "amazon.it": 8}
ASIN = re.compile(r"^[A-Z0-9]{10}$", re.I)
MAX_AGE_SECONDS = 90 * 60
MAX_CHANGE_AGE_SECONDS = 4 * 3600
PRICE_TYPES = (0, 1)  # Amazon-owned price, then cheapest marketplace NEW.


def when_keepa(value):
    if type(value) is not int or value <= 0:
        return None
    try:
        return datetime.fromtimestamp((value + 21564000) * 60, tz=timezone.utc)
    except (ValueError, OSError, OverflowError):
        return None


def euros(cents):
    if type(cents) is not int or not (0 < cents <= 10_000_000):
        return None
    return round(cents / 100, 2)


def _index_value(data, index):
    return data[index] if isinstance(data, list) and len(data) > index else None


def normalize_deal(row, market, price_type, now=None):
    now = now or datetime.now(timezone.utc)
    if not isinstance(row, dict) or market not in DOMAINS or price_type not in PRICE_TYPES:
        return None
    asin = row.get("asin")
    title = str(row.get("title") or "").strip()
    if not isinstance(asin, str) or not ASIN.fullmatch(asin) or not title:
        return None
    if not policy.is_allowed(title):
        return None
    updated = when_keepa(row.get("lastUpdate"))
    changed = when_keepa(row.get("creationDate"))
    if not updated or not changed:
        return None
    age = (now - updated).total_seconds()
    change_age = (now - changed).total_seconds()
    if not (-120 <= age <= MAX_AGE_SECONDS and
            -120 <= change_age <= MAX_CHANGE_AGE_SECONDS):
        return None
    current = euros(_index_value(row.get("current"), price_type))
    averages = row.get("avg")
    avg90 = euros(_index_value(_index_value(averages, 3), price_type))
    if current is None or avg90 is None or avg90 <= current:
        return None
    percent = round((avg90 - current) / avg90 * 100)
    # Avoid tiny/noisy fluctuations, leave category thresholds to the pipeline.
    if percent < 15:
        return None
    link = f"https://{market}/dp/{asin.upper()}"
    img = ""
    raw = row.get("image")
    if isinstance(raw, list) and len(raw) <= 150 and all(
        type(x) is int and 32 <= x <= 126 for x in raw
    ):
        filename = "".join(map(chr, raw))
        if re.fullmatch(r"[A-Za-z0-9_.-]+\.(?:jpg|jpeg|png|webp)", filename, re.I):
            img = "https://images-na.ssl-images-amazon.com/images/I/" + filename
    return {
        "title": title, "description": "", "content": "", "link": link,
        "key": link, "pubDate": updated.strftime("%a, %d %b %Y %H:%M:%S GMT"),
        "image": img,
        "direct_source": "Keepa Deals",
        "direct_asin": asin.upper(),
        "direct_current_eur": current,
        "direct_list_price_eur": None,
        "direct_claimed_percent": 0,
        "direct_observed_drop": None,
        "keepa_drop90_percent": percent,
        "keepa_reference90_eur": avg90,
        "keepa_price_type": "amazon" if price_type == 0 else "marketplace_new",
        "keepa_updated_at": updated.isoformat(timespec="seconds"),
        "keepa_changed_at": changed.isoformat(timespec="seconds"),
        "direct_offer_type": "KEEPa_OBSERVED",
        "direct_merchant": "Amazon" if price_type == 0 else "продавец маркетплейса не подтверждён",
        "direct_affiliate_url": link,
    }


def deals_query(market, price_type=0, page=0, min_percent=20):
    return {
        "page": int(page), "domainId": DOMAINS[market],
        "priceTypes": [int(price_type)], "dateRange": 0,
        "isRangeEnabled": True, "isFilterEnabled": False,
        "deltaPercentRange": [max(1, min(99, min_percent)), 100],
        "currentRange": [100, 10000000],
        "sortType": 1, "filterErotic": True, "singleVariation": True,
    }


def request_deals(api_key, query, timeout=11):
    endpoint = "https://api.keepa.com/deal?" + urlencode({"key": api_key})
    body = json.dumps(query, separators=(",", ":")).encode("utf-8")
    req = Request(endpoint, method="POST", data=body,
                  headers={"Content-Type": "application/json",
                           "User-Agent": "DiamondHunter/2.0"})
    with urlopen(req, timeout=timeout) as response:
        obj = json.loads(response.read(3_000_000).decode("utf-8"))
    if not isinstance(obj, dict):
        raise ValueError("Keepa returned non-object")
    if obj.get("error"):
        raise ValueError("Keepa request rejected")
    found = (obj.get("deals") or {}).get("dr")
    if not isinstance(found, list):
        raise ValueError("Keepa response lacks deals.dr")
    return found


def discover(state, env=None, now=None, requests_per_scan=None):
    env = env if env is not None else os.environ
    key = env.get("KEEPA_API_KEY")
    if not key:
        return []
    now = now or datetime.now(timezone.utc)
    # Rotating one to four queries keeps token use and four-market coverage
    # bounded; duplicate ASINs across price-types favor Amazon-owned price.
    try:
        limit = max(1, min(4, int(requests_per_scan if requests_per_scan is not None
                                    else env.get("KEEPA_DEAL_QUERIES", 2))))
    except (TypeError, ValueError):
        limit = 2
    sequence = [(m, p) for p in PRICE_TYPES for m in DOMAINS]
    cursor = int(state.get("keepa_deals_cursor", 0)) % len(sequence)
    discovered = {}
    used = state.setdefault("keepa_deals_usage", {})
    day = now.date().isoformat()
    if used.get("day") != day:
        used.clear()
        used.update(day=day, calls=0)
    try:
        daily_limit = max(0, min(200, int(env.get("KEEPA_MAX_DEAL_QUERIES_PER_DAY", "64"))))
    except (ValueError, TypeError):
        daily_limit = 64
    limit = min(limit, max(0, daily_limit - used["calls"]))
    # Spread the finite token budget throughout the full day. Without this,
    # five-minute polls consume all paid tokens before lunchtime.
    if daily_limit:
        floor_seconds = max(60, (24 * 3600) // daily_limit)
        last_scan = state.get("keepa_deals_last_checked")
        if type(last_scan) in (float, int) and (
                now.timestamp() - last_scan < floor_seconds):
            return []
    if limit:
        state["keepa_deals_last_checked"] = now.timestamp()
    for n in range(limit):
        used["calls"] += 1 # A failed HTTP request may still consume tokens.

        market, price_type = sequence[(cursor + n) % len(sequence)]
        try:
            rows = request_deals(key, deals_query(market, price_type))
        except (HTTPError, URLError, OSError, TimeoutError, ValueError, TypeError) as error:
            print(f"KEEPA DEALS: {market} type={price_type} unavailable: {type(error).__name__}")
            if isinstance(error, HTTPError) and error.code in (402, 403, 429):
                break
            continue
        for row in rows[:150]:
            offer = normalize_deal(row, market, price_type, now)
            if offer:
                k = offer["link"]
                if k not in discovered or offer["keepa_price_type"] == "amazon":
                    discovered[k] = offer
    state["keepa_deals_cursor"] = (cursor + limit) % len(sequence)
    print(f"KEEPA DEALS: {len(discovered)} recent price drops in {limit} bounded scans")
    return list(discovered.values())
