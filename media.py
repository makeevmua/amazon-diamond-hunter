"""Images from the SAME deal publication only (RSS item or page OG metadata).

Never infer a product photo from a title/ASIN. An editorial image is not a
verified Amazon listing photo. Source URLs are untrusted, so restrict schemes
and private/internal hosts before passing a URL to Telegram.
"""
import html
import ipaddress
import re
from html.parser import HTMLParser
from urllib.parse import urlsplit, urljoin

IMAGE_EXT = re.compile(r"\.(?:jpe?g|png|webp)(?:$|[?])", re.I)
BLOCKED_EXT = re.compile(r"\.(?:svg|gif|ico|xml|html?|pdf)(?:$|[?])", re.I)
BAD_NAME = re.compile(r"(?:pixel|tracking|spacer|favicon|avatar|logo|banner|placeholder)", re.I)
IMAGE_URL_RE = re.compile(
    r"https://[^\s\"'<>]+\.(?:jpe?g|png|webp)(?:\?[^\s\"'<>]*)?", re.I
)


def safe_image_url(value, base_url=None):
    if not isinstance(value, str) or not value.strip():
        return None
    url = html.unescape(value.strip()).replace("\\/", "/")
    if base_url:
        url = urljoin(base_url, url)
    try:
        parsed = urlsplit(url)
        hostname = (parsed.hostname or "").lower().rstrip(".")
        if (parsed.scheme != "https" or not hostname or parsed.username or
                parsed.password or parsed.port not in (None, 443) or
                hostname == "localhost" or hostname.endswith((
                    ".localhost", ".local", ".internal", ".test", ".invalid"
                )) or BLOCKED_EXT.search(parsed.path) or BAD_NAME.search(parsed.path)):
            return None
        if hostname.replace(".", "").isdigit():
            return None
        try:
            # Reject ALL IP literals, public or private, for image fetching.
            ipaddress.ip_address(hostname)
            return None
        except ValueError:
            pass
        if len(url) > 1500:
            return None
    except ValueError:
        return None
    return url


class ItemImageParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.urls = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() not in ("img", "source"):
            return
        d = dict(attrs)
        for key in ("src", "data-src", "data-original"):
            if d.get(key):
                self.urls.append(d[key])
                break


class OGImageParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.urls = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() != "meta":
            return
        d = dict(attrs)
        field = (d.get("property") or d.get("name") or "").lower()
        if field in ("og:image", "og:image:secure_url", "twitter:image"):
            self.urls.append(d.get("content", ""))


def rss_item_image(entry, base_url=None):
    """Find product-associated image in a SINGLE RSS/Atom entry."""
    for el in entry.iter():
        tag = el.tag.split("}")[-1].lower()
        attrs = {k.split("}")[-1].lower(): v for k, v in el.attrib.items()}
        if tag in ("thumbnail", "image", "content", "enclosure"):
            # media:content can be video, audio: require image type or extension.
            url = attrs.get("url") or attrs.get("href") or attrs.get("src")
            mime = attrs.get("type", "").lower()
            if url and (mime.startswith("image/") or IMAGE_EXT.search(urlsplit(url).path) or
                        tag in ("thumbnail", "image")):
                good = safe_image_url(url, base_url)
                if good:
                    return good
    for el in entry:
        tag = el.tag.split("}")[-1].lower()
        if tag in ("description", "encoded", "content", "summary"):
            parser = ItemImageParser()
            parser.feed(el.text or "")
            for url in parser.urls:
                good = safe_image_url(url, base_url)
                if good and not BAD_NAME.search(url):
                    return good
    return None


def page_og_image(markup, page_url):
    """Only the article's OG/Twitter image, NOT arbitrary page sidebar images."""
    parser = OGImageParser()
    parser.feed(markup[:250_000])
    for value in parser.urls:
        found = safe_image_url(value, page_url)
        if found:
            return found
    return None
