"""Optional read-only Pepper/Dealabs GraphQL scout.

Inspired by the MIT-licensed FaserF/ha-pepper client. The endpoint is
undocumented and may fail or disappear without notice. Never authenticate,
solve challenges, crawl through proxies, or treat community prices as
verified Amazon prices.
"""
from datetime import datetime, timezone
from email.utils import format_datetime
from http.cookiejar import CookieJar
from urllib.error import HTTPError, URLError
from urllib.parse import unquote, urlsplit
from urllib.request import HTTPCookieProcessor, Request, build_opener
import json
import math
import re


BASE = "https://www.dealabs.com"
QUERY = """query getThreads($filter: ThreadFilter!, $limit: Int) {
  threads(filter: $filter, limit: $limit) {
    threadId title url price publishedAt description isExpired
  }
}"""


def source_url(raw):
    """Accept only original Dealabs deal pages, never untrusted redirects."""
    if not isinstance(raw, str):
        return None
    if raw.startswith("/bons-plans/"):
        raw = BASE + raw
    try:
        parts = urlsplit(raw)
        if (parts.scheme != "https" or parts.hostname not in
                ("dealabs.com", "www.dealabs.com") or parts.username or
                parts.password or parts.port not in (None, 443) or
                not parts.path.startswith("/bons-plans/")):
            return None
        return parts._replace(query="", fragment="").geturl()
    except ValueError:
        return None


def offered_eur(value):
    """A *reported* Dealabs price in EUR; never a live store price."""
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    if isinstance(value, str):
        value = value.strip().replace("\u00a0", "").replace(" ", "").replace(",", ".")
    try:
        n = float(value)
        return round(n, 2) if math.isfinite(n) and 0 <= n <= 100000 else None
    except (ValueError, TypeError):
        return None


def normalize(threads):
    """Convert Dealabs threads to the existing RSS-like post interface."""
    result = []
    if not isinstance(threads, list):
        return result
    for t in threads[:75]:
        if not isinstance(t, dict) or t.get("isExpired"):
            continue
        link = source_url(t.get("url"))
        title = str(t.get("title") or "").strip()
        if not link or not title or len(title) > 500:
            continue
        description = re.sub(r"<[^>]+>", " ", str(t.get("description") or ""))[:1500]
        published = ""
        try:
            dt = datetime.fromisoformat(str(t.get("publishedAt") or "").replace("Z", "+00:00"))
            if dt.tzinfo:
                published = format_datetime(dt.astimezone(timezone.utc), usegmt=True)
        except (ValueError, TypeError):
            pass
        result.append({
            "title": title, "description": description, "content": "",
            "link": link, "key": link, "pubDate": published,
            "image": "", "advertised_price_eur": offered_eur(t.get("price")),
        })
    return result


def fetch_public_threads(timeout=9):
    """One anonymous page request + one bounded GraphQL POST per scan."""
    try:
        jar = CookieJar()
        opener = build_opener(HTTPCookieProcessor(jar))
        headers = {
            "User-Agent": "DiamondHunter/1.0 (+public-deals-RSS-research)",
            "Accept": "text/html,application/xhtml+xml",
        }
        with opener.open(Request(BASE + "/", headers=headers), timeout=timeout) as response:
            response.read(300000)
        raw_token = next((cookie.value for cookie in jar if cookie.name == "xsrf_t"), "")
        token = unquote(raw_token).strip('"')
        if not token:
            print("PEPPER unavailable: anonymous XSRF cookie not provided")
            return []
        payload = json.dumps({
            "query": QUERY, "variables": {"filter": {}, "limit": 60},
        }).encode("utf-8")
        req = Request(BASE + "/graphql", data=payload, headers={
            "User-Agent": headers["User-Agent"],
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Origin": BASE,
            "Referer": BASE + "/",
            "X-Xsrf-Token": token,
            "X-Requested-With": "XMLHttpRequest",
        }, method="POST")
        with opener.open(req, timeout=timeout) as response:
            doc = json.loads(response.read(1_200_000).decode("utf-8"))
        if not isinstance(doc, dict) or doc.get("errors"):
            print("PEPPER unavailable: query unsupported or returned GraphQL errors")
            return []
        posts = normalize((doc.get("data") or {}).get("threads"))
        print(f"PEPPER: {len(posts)} Dealabs articles received (unverified prices)")
        return posts
    except (HTTPError, URLError, TimeoutError, OSError, ValueError,
            json.JSONDecodeError) as error:
        print(f"PEPPER unavailable: {type(error).__name__}; RSS fallback active")
        return []
