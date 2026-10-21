"""Optional Keepa-backed, source-labelled Amazon price-history observations.

No access keys are bundled or logged. Keepa needs a paid API subscription.
This module NEVER equates a public RSS headline price with the Amazon offer,
and never claims that France delivery, seller or checkout price was validated.
"""
import gzip
import json
import os
import re
from datetime import datetime, timezone
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

MARKETS = {"amazon.fr": 4, "amazon.it": 8, "amazon.es": 9}
ASIN = re.compile(r"^/dp/([A-Z0-9]{10})$", re.I)
MAX_CHECKS = 6
MAX_AGE_MINUTES = 48 * 60


def identify(product_url):
    """Return exact (marketplace, ASIN) from canonical product URLs only."""
    try:
        parsed = urlsplit(product_url or "")
        host = (parsed.hostname or "").lower()
        match = ASIN.fullmatch(parsed.path)
        if (parsed.scheme == "https" and host in MARKETS and match
                and not parsed.username and not parsed.password
                and parsed.port in (None, 443)):
            return host, match.group(1).upper()
    except ValueError:
        pass
    return None


def _amount(cents):
    if type(cents) is not int or cents <= 0 or cents > 100_000_00:
        return None
    return round(cents / 100, 2)


def _keepa_date(keepa_minute):
    return datetime.fromtimestamp((keepa_minute + 21564000) * 60, timezone.utc)


def parse_product(product, market, asin, now=None):
    """Only compare one matched ASIN and the same price type over time.

    Amazon price (index 0) is preferred. The fallback NEW (index 1) is
    explicitly labelled marketplace-new and never treated as Amazon retail.
    """
    now = now or datetime.now(timezone.utc)
    if not isinstance(product, dict):
        return None
    if product.get("asin") != asin or product.get("domainId") != MARKETS[market]:
        return None
    if product.get("productType") not in (None, 0):
        return None
    last_update = product.get("lastUpdate")
    if type(last_update) is not int or last_update <= 0:
        return None
    observed_at = _keepa_date(last_update)
    age = (now - observed_at).total_seconds() / 60
    if age < -10 or age > MAX_AGE_MINUTES:
        return None
    histories = product.get("csv") or []
    if not isinstance(histories, list):
        return None
    selected = None
    for index, label in ((0, "amazon"), (1, "marketplace_new")):
        if index >= len(histories):
            continue
        sequence = histories[index]
        if not isinstance(sequence, list) or len(sequence) < 2 or len(sequence) % 2:
            continue
        pairs = [(sequence[i], sequence[i + 1]) for i in range(0, len(sequence), 2)]
        if any(type(t) is not int or type(p) is not int for t, p in pairs):
            continue
        pairs.sort(key=lambda p: p[0])
        current = _amount(pairs[-1][1])
        if current is not None:
            selected = (pairs, current, label)
            break
    if not selected:
        return None
    pairs, current, label = selected
    # Duration-weighted reference, not an average of price change *events*.
    # Price segments are clamped to the preceding 90 days; invalid prices
    # have zero weight, and at least 30d of valid historical coverage is needed.
    end = now.timestamp()
    beginning = end - 90 * 86400
    weighted = []
    for i, (t, p) in enumerate(pairs):
        start_sec = max(_keepa_date(t).timestamp(), beginning)
        end_sec = min(_keepa_date(pairs[i+1][0]).timestamp() if i+1 < len(pairs) else end, end)
        value = _amount(p)
        if value is not None and end_sec > start_sec:
            weighted.append((value, end_sec - start_sec))
    coverage = sum(duration for _, duration in weighted)
    reference = round(sum(v * sec for v, sec in weighted) / coverage, 2) if coverage >= 30 * 86400 else None
    discount = (round(100 * (1-current/reference), 1)
                if reference is not None and current < reference else None)
    return {
        "market": market, "asin": asin, "current_eur": current,
        "reference_90d_eur": reference, "drop_90d_percent": discount,
        "source": "Keepa", "price_type": label,
        "observed_at": observed_at.isoformat(timespec="seconds"),
        "shipping_verified": False, "seller_verified": False,
    }


def fetch_observation(product_url, key, now=None, timeout=8):
    identity = identify(product_url)
    if not identity or not key:
        return None
    market, asin = identity
    url = "https://api.keepa.com/product?" + urlencode({
        "key": key, "domain": MARKETS[market], "asin": asin
    })
    try:
        with urlopen(Request(url, headers={"Accept-Encoding": "gzip"}),
                     timeout=timeout) as response:
            data = response.read(1_800_000)
            if response.headers.get("Content-Encoding", "").lower() == "gzip":
                data = gzip.decompress(data)
        doc = json.loads(data)
        products = doc.get("products", [])
        if not isinstance(products, list):
            return None
        for product in products:
            parsed = parse_product(product, market, asin, now=now)
            if parsed:
                return parsed
    except (HTTPError, URLError, TimeoutError, OSError, ValueError, TypeError):
        return None
    return None


def check_catalog(rows, key=None, max_checks=MAX_CHECKS):
    """Prioritize candidate ASINs, dedupe cross-post links and bound API cost."""
    key = key if key is not None else os.getenv("KEEPA_API_KEY", "")
    if not key:
        return []
    candidates = sorted(rows, key=lambda row: (
        -(row.get("claimed_discount") or 0),
        not bool(row.get("advertised_price_eur")),
    ))
    results = []
    visited = set()
    for row in candidates:
        url = row.get("amazon_product_url")
        identity = identify(url)
        if not identity or identity in visited:
            continue
        visited.add(identity)
        result = fetch_observation(url, key)
        if result:
            results.append(result)
        if len(visited) >= max(0, min(20, max_checks)):
            break
    return results
