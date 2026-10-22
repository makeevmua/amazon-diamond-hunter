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
SNAPSHOT_ID = re.compile(r"(?:s|sd)_[A-Za-z0-9_]{8,80}\Z")
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


def _read_search(key, market, phrase, timeout=18):
    """Trigger exactly one official Bright Data search job (billed provider-side).

    Bright Data documents this discovery dataset through /trigger with a
    TOP-LEVEL ARRAY, not the PDP-specific /scrape {"input": [...]} envelope.
    """
    url = "https://api.brightdata.com/datasets/v3/trigger?" + urlencode({
        "dataset_id": DATASET_ID, "format": "json",
        "uncompressed_webhook": "true"
    })
    payload = [{"keyword":phrase, "url":"https://www."+market,
                "pages_to_search":"1"}]
    req = Request(url, data=json.dumps(payload).encode("utf-8"),
                  headers={"Authorization":"Bearer "+key,
                           "Content-Type":"application/json"}, method="POST")
    with urlopen(req, timeout=timeout) as res:
        response = json.loads(res.read(1_000_000))
    if isinstance(response, dict):
        identifier = response.get("snapshot_id")
        if isinstance(identifier, str) and SNAPSHOT_ID.fullmatch(identifier):
            return {"snapshot_id": identifier}
    # Do not echo provider payload (may include sensitive account metadata).
    fields = ",".join(sorted(str(k)[:40] for k in response)[:8]) if isinstance(response, dict) else type(response).__name__
    raise ValueError(f"Bright Data trigger response lacks valid snapshot_id (fields: {fields})")


def _poll_snapshot(key, identifier, timeout=12):
    """Download only ready results; never initiate another billable collection."""
    if not SNAPSHOT_ID.fullmatch(str(identifier)):
        raise ValueError("Malformed snapshot ID")
    headers = {"Authorization": "Bearer "+key, "Accept":"application/json"}
    progress_url = "https://api.brightdata.com/datasets/v3/progress/"+identifier
    with urlopen(Request(progress_url, headers=headers), timeout=timeout) as res:
        status = json.loads(res.read(200_000)).get("status")
    if status in ("starting", "running"):
        return None
    if status == "failed":
        raise ValueError("Bright Data snapshot failed")
    if status != "ready":
        raise ValueError("Unexpected Bright Data snapshot status")
    download = ("https://api.brightdata.com/datasets/v3/snapshot/"+identifier+
                "?format=json")
    with urlopen(Request(download, headers=headers), timeout=timeout) as res:
        raw = json.loads(res.read(7_000_000))
    if isinstance(raw, list):
        return raw[:120]
    if isinstance(raw, dict) and isinstance(raw.get("data"), list):
        return raw["data"][:120]
    raise ValueError("Bright Data snapshot not a product list")


def _normalized(raw, now):
    posts = {}
    for row in raw:
        offer = normalize(row, now)
        if offer:
            posts[offer["link"]] = offer
    return list(posts.values())


def discover(state, env=None, now=None):
    env = env if env is not None else os.environ
    key = env.get("BRIGHTDATA_API_TOKEN")
    if not key or env.get("BRIGHTDATA_SEARCH_ENABLED", "true").lower() != "true":
        return []
    now = now or datetime.now(timezone.utc)
    # Retrieve any in-flight snapshot *before* touching daily request quota.
    # Each scan can poll an old job for free without launching another job.
    pending = state.get("brightdata_search_pending")
    if isinstance(pending, dict):
        ident = pending.get("id")
        try:
            started = datetime.fromisoformat(pending.get("started", ""))
            if started.tzinfo is None:
                started = started.replace(tzinfo=timezone.utc)
            if (now - started).total_seconds() > 48*3600:
                state.pop("brightdata_search_pending", None)
                print("BRIGHT SEARCH: stale pending snapshot discarded")
                return []
            rows = _poll_snapshot(key, ident)
        except (HTTPError, URLError, ValueError, OSError, TimeoutError) as error:
            state["brightdata_search_last_error"] = (
                "snapshot HTTP " + str(error.code)
                if isinstance(error, HTTPError) else
                "snapshot " + type(error).__name__)
            if isinstance(error, HTTPError):
                print(f"BRIGHT SEARCH: snapshot HTTP {error.code}, no product data")
            else:
                print(f"BRIGHT SEARCH: snapshot {type(error).__name__}, will retry")
            pending["attempts"] = int(pending.get("attempts", 0))+1
            if pending["attempts"] >= 5:
                state.pop("brightdata_search_pending", None)
            return []
        if rows is None:
            print("BRIGHT SEARCH: snapshot still running, checking next scan")
            return []
        state.pop("brightdata_search_pending", None)
        state.pop("brightdata_search_last_error", None)
        products = _normalized(rows, now)
        print(f"BRIGHT SEARCH: snapshot ready; {len(products)} eligible "
              f"Amazon products out of {len(rows)} records")
        return products
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
        result = _read_search(key, market, phrase)
    except (HTTPError, URLError, ValueError, OSError, TimeoutError) as error:
        state["brightdata_search_last_error"] = (
            f"{market} HTTP {error.code}" if isinstance(error, HTTPError)
            else f"{market} {type(error).__name__}")
        if isinstance(error, HTTPError):
            print(f"BRIGHT SEARCH: {market} HTTP {error.code}; check dataset permission/quota")
        else:
            print(f"BRIGHT SEARCH: {market} {type(error).__name__}: "
                  f"{str(error)[:180]}; request not confirmed")
        return []
    state.pop("brightdata_search_last_error", None)
    if isinstance(result, dict) and result.get("snapshot_id"):
        state["brightdata_search_pending"] = {
            "id": result["snapshot_id"], "market": market, "query": phrase,
            "started": now.isoformat(timespec="seconds"), "attempts": 0,
        }
        print(f"BRIGHT SEARCH: {market} query submitted, awaiting snapshot")
        return []
    # Legacy in-memory test adapters may return synchronous normalized rows.
    if isinstance(result, list):
        return _normalized(result, now)
    return []
