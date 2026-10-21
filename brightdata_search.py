"""Search Amazon itself through Bright Data's published Amazon Search dataset.

Independent of Amazon Associates and deal-sharing websites. Free-tier budget
is intentionally conservative. A search index snapshot is NOT a real-time
checkout quote and an "initial price" is NOT independently verified history.
"""
import json
import os
import re
from datetime import datetime, timezone
from email.utils import format_datetime
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen

import policy
import brightdata_verify

DATASET_ID = "gd_lwdb4vjm1ehb499uxs"
API = "https://api.brightdata.com/datasets/v3/scrape"
ASIN_PATH = re.compile(r"/(?:dp|gp/product)/([A-Z0-9]{10})(?:/|$)", re.I)
MARKETS = ("amazon.fr", "amazon.de", "amazon.es", "amazon.it")
QUERIES = [
    ("amazon.fr", "SSD NVMe"),
    ("amazon.de", "SSD NVMe"),
    ("amazon.es", "mando PS5"),
    ("amazon.it", "controller PS5"),
    ("amazon.fr", "lampe Bauhaus"),
    ("amazon.de", "Design Möbel"),
    ("amazon.es", "LEGO Technic"),
    ("amazon.it", "lampada design"),
    ("amazon.fr", "PC RTX"),
    ("amazon.de", "PlayStation 5 Spiele"),
    ("amazon.es", "monitor OLED"),
    ("amazon.it", "Nintendo Switch"),
    ("amazon.fr", "tapis design"),
    ("amazon.de", "Ryzen CPU"),
    ("amazon.es", "lampara escandinava"),
    ("amazon.it", "SSD Samsung"),
]
MAX_SEARCH_MONTHLY = 24
ASSUMED_RECORDS_PER_SEARCH = 75  # pessimistic reservation; provider limits are authoritative.


def _price(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, str):
        value = value.replace("€", "").replace("\u00a0", "").strip().replace(",", ".")
    try:
        n = float(value)
    except (ValueError, TypeError):
        return None
    return round(n, 2) if 0 < n <= 100000 else None


def normalize(row, now=None):
    now = now or datetime.now(timezone.utc)
    if not isinstance(row, dict) or str(row.get("currency") or "").upper() != "EUR":
        return None
    original = row.get("url")
    if not isinstance(original, str):
        return None
    try:
        p = urlsplit(original)
        market = (p.hostname or "").lower().removeprefix("www.")
        asin_path = ASIN_PATH.search(p.path)
        if p.scheme != "https" or market not in MARKETS or not asin_path:
            return None
        asin = asin_path.group(1).upper()
        other = row.get("asin")
        if isinstance(other, str) and other.upper() != asin:
            return None
    except ValueError:
        return None
    title = str(row.get("name") or row.get("title") or "").strip()
    if not title or not policy.is_allowed(title):
        return None
    timestamp = row.get("timestamp")
    if not isinstance(timestamp, str):
        return None
    try:
        published = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        if published.tzinfo is None:
            published = published.replace(tzinfo=timezone.utc)
        age = (now - published).total_seconds()
        if age < -3600 or age > 48 * 3600:
            return None
    except (ValueError, OverflowError):
        return None
    current = _price(row.get("final_price"))
    if current is None:
        current = _price(row.get("initial_price"))
    if current is None:
        return None
    reference = _price(row.get("initial_price"))
    # Comparing to a scraped initial/list price is NOT market history.
    claimed = (round(100 * (reference - current) / reference)
               if reference and reference > current else 0)
    link = f"https://{market}/dp/{asin}"
    image = str(row.get("image") or row.get("image_url") or "")[:1200]
    if not image.startswith("https://"):
        image = ""
    return {
        "title": title, "description": "", "content": "",
        "link": link, "key": link,
        "pubDate": format_datetime(now, usegmt=True), "image": image,
        "direct_source": "Bright Data Amazon Search",
        "direct_asin": asin, "direct_current_eur": current,
        "direct_list_price_eur": reference,
        "direct_claimed_percent": max(0, min(99, claimed)),
        "direct_observed_drop": None,
        "direct_offer_type": "SEARCH_RESULT",
        "direct_merchant": str(row.get("seller_name") or "не указан")[:120],
        "direct_affiliate_url": link,
        "search_snapshot_at": timestamp,
    }


def _read_search(key, market, phrase, timeout=75):
    url = API + "?" + urlencode({"dataset_id": DATASET_ID, "format": "json"})
    payload = {"input":[{"keyword":phrase,"url":"https://www."+market,
                         "pages_to_search":1}]}
    req = Request(url, data=json.dumps(payload).encode("utf-8"),
                  headers={"Authorization":"Bearer "+key,
                           "Content-Type":"application/json"}, method="POST")
    with urlopen(req, timeout=timeout) as res:
        response = json.loads(res.read(6_000_000))
        if res.status == 202:
            print("BRIGHT SEARCH: search snapshot pending; no fresh results asserted.")
            return []
    if isinstance(response, list):
        return response[:120]
    if isinstance(response, dict) and isinstance(response.get("data"), list):
        return response["data"][:120]
    return []


def discover(state, env=None, now=None):
    env = env if env is not None else os.environ
    key = env.get("BRIGHTDATA_API_TOKEN")
    if not key or env.get("BRIGHTDATA_SEARCH_ENABLED", "true").lower() != "true":
        return []
    now = now or datetime.now(timezone.utc)
    cursor = int(state.get("brightdata_search_cursor", 0)) % len(QUERIES)
    month = now.strftime("%Y-%m")
    day = now.date().isoformat()
    usage = state.setdefault("brightdata_search_usage", {})
    if usage.get("month") != month:
        usage.clear()
        usage.update(month=month, count=0, day=day, today=0)
    if usage.get("day") != day:
        usage["day"] = day
        usage["today"] = 0
    try:
        max_day = max(0, min(4, int(env.get("BRIGHTDATA_SEARCH_PER_DAY", "1"))))
    except (ValueError, TypeError):
        max_day = 1
    max_month = min(MAX_SEARCH_MONTHLY, 3200 // ASSUMED_RECORDS_PER_SEARCH)
    if usage["today"] >= max_day or usage["count"] >= max_month:
        return []
    # Provider-side billing alerts/limits remain essential: record count can
    # vary, and this is a request cap, not an actual provider billing lock.
    market, phrase = QUERIES[cursor]
    usage["count"] += 1
    usage["today"] += 1
    state["brightdata_search_cursor"] = (cursor + 1) % len(QUERIES)
    try:
        raw = _read_search(key, market, phrase)
    except (HTTPError, URLError, ValueError, OSError, TimeoutError) as error:
        print(f"BRIGHT SEARCH: {market} request failed: {type(error).__name__}")
        return []
    posts = {}
    for row in raw:
        offer = normalize(row, now)
        if offer:
            posts[offer["link"]] = offer
    print(f"BRIGHT SEARCH: {len(posts)} dated EUR Amazon listings, "
          f"market {market}, query='{phrase}'; not checkout-verified")
    return list(posts.values())
