"""Optional, budget-controlled direct Amazon product checks via Bright Data.

Official Bright Data Amazon product dataset endpoint. Candidate discovery is
independent (Keepa/Creators). The scraper checks the product's current
displayed EUR price but does not validate checkout, delivery or guarantees.
Credential absence is a deliberate no-op, never a request to Amazon itself.
"""
import json
import math
import os
import re
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from urllib.parse import urlencode, urlsplit

import pricechecks

DATASET_ID = "gd_l7q7dkf244hwjntr0"
URL = "https://api.brightdata.com/datasets/v3/scrape"
ASIN = re.compile(r"^[A-Z0-9]{10}$", re.I)
ALLOWED_MONTHLY = 3200
ALLOWED_DAILY = 80


def amount(row):
    if not isinstance(row, dict):
        return None
    currency = str(row.get("currency") or row.get("currency_code") or "").upper()
    if currency not in ("EUR", "€"):
        return None
    # The provider returns several possible price types. Only actual current
    # product price is eligible; do not substitute list/initial price.
    for key in ("final_price", "price", "current_price", "buybox_price"):
        price = row.get(key)
        if isinstance(price, dict):
            price = price.get("value", price.get("amount"))
        if isinstance(price, str):
            price = price.replace("€", "").replace("\u00a0", "").strip().replace(",", ".")
        try:
            value = float(price)
        except (ValueError, TypeError):
            continue
        if math.isfinite(value) and 0 < value <= 100000:
            return round(value, 2)
    return None


def exact_item(row, canonical):
    """Avoid accidentally validating another ASIN or marketplace variant."""
    identity = pricechecks.identify(canonical)
    if not identity or not isinstance(row, dict):
        return False
    market, asin = identity
    returned = row.get("url") or row.get("origin_url") or row.get("product_url")
    if isinstance(returned, str):
        try:
            parsed = urlsplit(returned)
            if (parsed.scheme != "https" or
                parsed.hostname not in (market, "www." + market)):
                return False
            path = parsed.path
            if not re.search(r"/(?:dp|gp/product)/" + re.escape(asin) + r"(?:/|$)", path, re.I):
                return False
        except ValueError:
            return False
    else:
        return False
    other = row.get("asin")
    return not other or (isinstance(other, str) and other.upper() == asin)


def _reserve_one(state, now, max_daily=ALLOWED_DAILY, max_monthly=ALLOWED_MONTHLY):
    """Advance quota BEFORE network request, so failures do not create loops."""
    key_month = now.strftime("%Y-%m")
    key_day = now.strftime("%Y-%m-%d")
    usage = state.setdefault("brightdata_usage", {})
    if usage.get("month") != key_month:
        usage.clear()
        usage.update(month=key_month, monthly=0, day=key_day, daily=0)
    if usage.get("day") != key_day:
        usage["day"], usage["daily"] = key_day, 0
    if usage["monthly"] >= min(max_monthly, ALLOWED_MONTHLY) or usage["daily"] >= min(max_daily, ALLOWED_DAILY):
        print("BRIGHT DATA: self-enforced free quota exhausted")
        return False
    usage["monthly"] += 1
    usage["daily"] += 1
    return True


def _request_product(canonical, key, timeout=70):
    api = URL + "?" + urlencode({"dataset_id": DATASET_ID, "format": "json"})
    req = Request(api,
                  data=json.dumps({"input":[{"url":canonical}]}).encode("utf-8"),
                  headers={"Authorization":"Bearer " + key,
                           "Content-Type":"application/json"},
                  method="POST")
    with urlopen(req, timeout=timeout) as res:
        doc = json.loads(res.read(1_500_000))
        if res.status == 202:
            # Bright Data may defer the snapshot. Don't invent a real-time
            # quote or repeatedly issue paid queries to poll it.
            print("BRIGHT DATA: snapshot pending, no instant verification")
            return None
    if isinstance(doc, list):
        return doc[0] if len(doc) == 1 else None
    if isinstance(doc, dict) and isinstance(doc.get("data"), list):
        records = doc["data"]
        return records[0] if len(records) == 1 else None
    return doc if isinstance(doc, dict) else None


def verify(items, state, env=None, now=None):
    """Verify at most two candidate ASINs per scan; no paid call without key."""
    env = env if env is not None else os.environ
    key = env.get("BRIGHTDATA_API_TOKEN")
    if not key:
        return {}
    now = now or datetime.now(timezone.utc)
    max_per_scan = max(0, min(2, int(env.get("BRIGHTDATA_PER_SCAN", "1"))))
    checks = {}
    recent = state.setdefault("brightdata_checked", {})
    for item in items:
        if len(checks) >= max_per_scan:
            break
        product = item.get("amazon")
        ident = pricechecks.identify(product)
        if not ident or product in checks:
            continue
        last = recent.get(product)
        try:
            if last and (now.timestamp() - float(last)) < 3600 * 4:
                continue
        except (ValueError, TypeError):
            pass
        if not _reserve_one(state, now):
            break
        recent[product] = int(now.timestamp())
        try:
            row = _request_product(product, key)
        except (HTTPError, URLError, OSError, ValueError, TimeoutError, TypeError) as error:
            print(f"BRIGHT DATA: {ident[0]} request failed: {type(error).__name__}")
            continue
        if not exact_item(row, product):
            print(f"BRIGHT DATA: source identity mismatch or pending for {ident[0]}")
            continue
        actual = amount(row)
        if actual is None:
            print(f"BRIGHT DATA: no verified EUR price for {ident[0]}")
            continue
        checks[product] = {
            "price_eur": actual, "source": "Bright Data Amazon PDP",
            "observed_at": now.isoformat(timespec="seconds"),
            "seller_unverified": True, "delivery_unverified": True,
        }
    if len(recent) > 1500:
        state["brightdata_checked"] = dict(list(recent.items())[-1000:])
    print(f"BRIGHT DATA: verified {len(checks)} exact product price values")
    return checks
