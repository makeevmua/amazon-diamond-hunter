"""Direct Cdiscount and Joybuy storefront discovery, without forum middlemen.

This is a polite, bounded HTML/JSON-LD adapter. It only follows exact product
URLs discovered on those shops' OWN public offer pages. A page that blocks
automation produces zero offers; no bypass, proxies, fabricated price or
cross-store redirects. Listing RRP/saving claims are NOT independent history.
"""
import html
import json
import os
import re
from datetime import datetime, timezone
from email.utils import format_datetime
from html.parser import HTMLParser
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlsplit
from urllib.request import Request, urlopen

import policy
import retail_scope

PAGES = {
    "Cdiscount": ("https://www.cdiscount.com/",),
    "Joybuy": ("https://www.joybuy.fr/marketing/lightning-offers?renderStyle=bottom_flow",
               "https://www.joybuy.fr/"),
}
USER_AGENT = "DiamondHunter/3.0 (public-price-observation)"
SALE = re.compile(r"(?:-|−)\s*(\d{2})\s*%|(\d{2})\s*%\s*(?:off|de réduction|réduction)", re.I)
EURO_SUFFIX = re.compile(r"(?<!\d)(\d{1,6}(?:[ .\u00a0]\d{3})*(?:[.,]\d{1,2})?)\s*€\s*(\d{2})?(?!\d)", re.I)
EURO_PREFIX = re.compile(r"€\s*(\d{1,6}(?:[ .\u00a0]\d{3})*(?:[.,]\d{1,2})?)", re.I)
LD_TYPE = {"Product", "https://schema.org/Product", "http://schema.org/Product"}


def _float(v):
    if type(v) is bool:
        return None
    if isinstance(v, dict):
        v = v.get("value", v.get("amount"))
    if isinstance(v, str):
        v = v.strip().replace("\u00a0", "").replace(" ", "").replace(",", ".").replace("€", "")
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    if 0 < v <= 100000 and v < float("inf"):
        return round(v, 2)
    return None


class ProductLinks(HTMLParser):
    def __init__(self, base_url):
        super().__init__(convert_charrefs=True)
        self.base = base_url
        self.active = None
        self.links = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() == "a":
            if self.active:
                self._finish()
            raw = dict(attrs).get("href") or ""
            dest = urljoin(self.base, raw)
            self.active = {"url": dest, "text": []} if retail_scope.shop(dest) else None
        elif self.active and tag.lower() == "img":
            attrs = dict(attrs)
            alt = attrs.get("alt")
            if alt:
                self.active["text"].append(alt)

    def handle_endtag(self, tag):
        if tag.lower() == "a" and self.active:
            self._finish()

    def _finish(self):
        text = re.sub(r"\s+", " ", " ".join(self.active["text"])).strip()
        if text:
            self.links.append((self.active["url"], text[:700]))
        self.active = None

    def handle_data(self, data):
        if self.active:
            self.active["text"].append(data)


class ProductJSON(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.script = False
        self.raw = []
        self.docs = []

    def handle_starttag(self, tag, attrs):
        if tag == "script" and dict(attrs).get("type", "").lower() == "application/ld+json":
            self.script = True
            self.raw = []

    def handle_data(self, data):
        if self.script:
            self.raw.append(data)

    def handle_endtag(self, tag):
        if tag == "script" and self.script:
            self.script = False
            try:
                self.docs.append(json.loads("".join(self.raw)))
            except (json.JSONDecodeError, ValueError, TypeError):
                pass


def _products(tree):
    if isinstance(tree, list):
        for obj in tree:
            yield from _products(obj)
    elif isinstance(tree, dict):
        kinds = tree.get("@type")
        kinds = kinds if isinstance(kinds, list) else [kinds]
        if any(t in LD_TYPE for t in kinds):
            yield tree
        for key in ("@graph", "mainEntity"):
            if key in tree:
                yield from _products(tree[key])


def _retailer_product(url, markup, expected_shop, now, teaser=""):
    if retail_scope.shop(url) != expected_shop or len(markup) > 3_000_000:
        return None
    tree = ProductJSON()
    tree.feed(markup)
    product = None
    for root in tree.docs:
        for p in _products(root):
            product = p
            break
        if product:
            break
    if not product:
        return None
    name = str(product.get("name") or "").strip()
    if not name or not policy.is_allowed(name):
        return None
    offers = product.get("offers")
    if isinstance(offers, list):
        offers = offers[0] if offers else None
    if not isinstance(offers, dict):
        return None
    currency = str(offers.get("priceCurrency", "")).upper()
    if currency != "EUR":
        return None
    availability = str(offers.get("availability") or "").lower()
    if "outofstock" in availability or "soldout" in availability:
        return None
    current = _float(offers.get("price") or offers.get("lowPrice"))
    if not current:
        specs = offers.get("priceSpecification") or {}
        if isinstance(specs, dict):
            current = _float(specs.get("price"))
    if not current:
        return None
    # Only a same-product explicit crossed/reference amount from listing
    # qualifies. It is a CLAIM, not verified pre-sale pricing.
    nums = [_float((whole.replace(" ", "") + "." + cents) if cents else whole) for whole, cents in EURO_SUFFIX.findall(teaser)]
    nums.extend(_float(x) for x in EURO_PREFIX.findall(teaser))
    reference = max((n for n in nums if n and n > current * 1.25), default=None)
    hinted = SALE.search(teaser)
    pct_claim = max([int(x) for x in hinted.groups() if x] or [0]) if hinted else 0
    claimed = round(100 * (reference - current) / reference) if reference else 0
    # Avoid percentages based on unrelated content (empty/generic tiles).
    if not reference or claimed < 25 or (pct_claim and abs(pct_claim - claimed) > 6):
        return None
    store = "Cdiscount Direct" if expected_shop == "Cdiscount" else "Joybuy Direct"
    image = product.get("image")
    if isinstance(image, list):
        image = image[0] if image else ""
    if isinstance(image, dict):
        image = image.get("url")
    image = str(image or "")
    if not image.startswith("https://") or len(image) > 1200:
        image = ""
    return {
        "title": name[:260], "description": "", "content": "",
        "link": url.split("?")[0], "key": url.split("?")[0],
        "pubDate": format_datetime(now, usegmt=True), "image": image,
        "direct_source": store,
        "direct_current_eur": current,
        "direct_list_price_eur": reference,
        "direct_claimed_percent": claimed,
        "direct_observed_drop": None,
        "direct_merchant": expected_shop,
        "direct_affiliate_url": url.split("?")[0],
        "direct_offer_type": "RETAILER_PAGE_RRP",
    }


def _fetch_page(url, expected_shop):
    """Never follow redirects to other shops, auth pages or deal forums."""
    req = Request(url, headers={"User-Agent": USER_AGENT,
                                "Accept": "text/html,application/xhtml+xml"})
    with urlopen(req, timeout=9) as res:
        final = res.geturl()
        # Discovery URLs are category pages; only compare the exact merchant.
        dest = (urlsplit(final).hostname or "").lower().removeprefix("www.")
        expected = "cdiscount.com" if expected_shop == "Cdiscount" else "joybuy.fr"
        if dest not in (expected, "m.joybuy.fr" if expected_shop == "Joybuy" else expected):
            raise ValueError("Cross-merchant redirect rejected")
        content_type = res.headers.get("Content-Type", "").lower()
        if "text/html" not in content_type:
            raise ValueError("Non-HTML retailer response")
        raw = res.read(2_800_000)
    return raw.decode("utf-8", errors="replace")


def discover(state, env=None, now=None):
    env = os.environ if env is None else env
    if str(env.get("RETAIL_DIRECT_ENABLED", "true")).lower() == "false":
        return []
    now = now or datetime.now(timezone.utc)
    try:
        per_shop = max(0, min(5, int(env.get("RETAIL_PRODUCT_CHECKS_PER_SHOP", "2"))))
    except (TypeError, ValueError):
        per_shop = 2
    if not per_shop:
        return []
    out = {}
    metrics = state.setdefault("retailer_direct_health", {})
    for shop in ("Cdiscount", "Joybuy"):
        page_list = PAGES[shop]
        cursor_key = "retailer_" + shop.lower() + "_cursor"
        cursor = int(state.get(cursor_key, 0))
        url = page_list[cursor % len(page_list)]
        state[cursor_key] = cursor + 1
        try:
            markup = _fetch_page(url, shop)
            parser = ProductLinks(url)
            parser.feed(markup)
            links = list(dict.fromkeys(parser.links))
        except (HTTPError, URLError, ValueError, OSError, TimeoutError) as exc:
            print(f"RETAIL DIRECT: {shop} listing unavailable ({type(exc).__name__})")
            metrics[shop] = {"status": "unavailable", "offers": 0,
                             "checked_at": now.isoformat(timespec="seconds")}
            continue
        # Deterministic rotation across candidate tiles without extra listing calls.
        index_key = "retailer_" + shop.lower() + "_item_cursor"
        start = int(state.get(index_key, 0))
        checked = 0
        for k in range(len(links)):
            if checked >= per_shop:
                break
            dest, text = links[(start + k) % len(links)]
            if retail_scope.shop(dest) != shop:
                continue
            checked += 1
            try:
                details = _fetch_page(dest, shop)
                item = _retailer_product(dest, details, shop, now, teaser=text)
            except (HTTPError, URLError, ValueError, OSError, TimeoutError) as exc:
                print(f"RETAIL DIRECT: {shop} product unavailable ({type(exc).__name__})")
                continue
            if item:
                out[item["link"]] = item
        state[index_key] = start + max(1, checked)
        metrics[shop] = {"status": "scanned", "cards_on_listing": len(links),
                         "offers": sum(1 for x in out.values() if
                                       x["direct_source"].startswith(shop)),
                         "checked_at": now.isoformat(timespec="seconds")}
        print(f"RETAIL DIRECT: {shop} {metrics[shop]['offers']} qualified pages "
              f"from {checked} product checks / {len(links)} links")
    return list(out.values())
