"""Strict destination and retailer-provenance policy for Diamond Hunter.

Only independently supplied product records of Amazon, Cdiscount and Joybuy
may enter the live storefront or Telegram. Source/forum articles (Dealabs,
Brickset, Mydealz, etc.) are NEVER eligible in retailer-only mode even when
the article contains a retailer link: that is still a recycled forum find.
"""
import os
import re
from urllib.parse import urlsplit

AMAZON = frozenset(("amazon.fr", "amazon.de", "amazon.es", "amazon.it"))
DIRECT_PRICE_SOURCES = frozenset((
    "amazon_creators_featured_new", "keepa_observed_new",
    "brightdata_search_listing",
    "cdiscount_direct_observed", "joybuy_direct_observed",
))
PUBLISHERS = {
    "Amazon Creators API", "Keepa Deals", "Bright Data Amazon Search",
    "Cdiscount Direct", "Joybuy Direct",
}


def enabled(env=None):
    env = os.environ if env is None else env
    return str(env.get("RETAILER_ONLY", "true")).lower() != "false"


def shop(url):
    """Return authorized merchant only for a plausible direct product URL.

    No website/category/search/blog/homepage, referral redirect, unicode host,
    user info, plaintext http, third-party lookalike or generic deal listing.
    """
    try:
        p = urlsplit(str(url or ""))
        host = (p.hostname or "").lower()
        if (p.scheme != "https" or p.username or p.password or
                p.port not in (None, 443) or not host or p.fragment):
            return None
        if host.startswith("www."):
            host = host[4:]
        if host in AMAZON:
            return "Amazon" if re.fullmatch(r"/dp/[A-Za-z0-9]{10}/?", p.path) else None
        if host == "cdiscount.com":
            # Cdiscount individual product pages use /.../f-<id>.html.
            return "Cdiscount" if re.fullmatch(
                r"/[a-z0-9/_-]+/f-[a-z0-9_-]{6,}\.html", p.path, re.I
            ) else None
        if host in ("joybuy.fr", "m.joybuy.fr"):
            # Official product links: /dp/<product-slug>/<numeric-SKU>
            return "Joybuy" if re.fullmatch(
                r"/dp/[^/?#]{2,200}/[0-9]{6,20}/?", p.path
            ) else None
    except (ValueError, TypeError):
        pass
    return None


def direct_post(post):
    if not isinstance(post, dict):
        return False
    provider = post.get("direct_source")
    if provider not in PUBLISHERS:
        return False
    actual_store = shop(post.get("link"))
    if provider in ("Amazon Creators API", "Keepa Deals", "Bright Data Amazon Search"):
        return actual_store == "Amazon"
    if provider == "Cdiscount Direct":
        return actual_store == "Cdiscount"
    return actual_store == "Joybuy"


def indexed_product(row):
    """Remove legacy forum and source-only cards from previously saved SQLite."""
    if not isinstance(row, dict):
        return False
    return (row.get("price_scope") in DIRECT_PRICE_SOURCES and
            shop(row.get("amazon_product_url")) is not None and
            shop(row.get("amazon_product_url")) ==
            shop(row.get("source_url")))
