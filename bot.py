"""Amazon Diamond Hunter v2 — free RSS discovery; advertised discounts are NOT verified."""
import html
import json
import os
import re
import sys
import time
from zoneinfo import ZoneInfo
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import unquote, urlsplit, urlencode
from urllib.request import HTTPRedirectHandler, build_opener
from urllib.request import Request, urlopen
from html.parser import HTMLParser

FEEDS = [
    "https://www.dealabs.com/rss/hot.xml",
    "https://www.dealabs.com/rss",
    "https://www.dealabs.com/rss/groupe/informatique",
    "https://www.dealabs.com/rss/groupe/consoles-jeux-video",
    "https://www.dealabs.com/rss/groupe/image-son-video",
    "https://www.dealabs.com/rss/groupe/telephonie",
    "https://www.chollometro.com/rss",
    "https://quantospendo.app/offerte/feed.xml",
    "https://www.lesnumeriques.com/rss-gooddeals.xml",
    "https://www.lesnumeriques.com/informatique/rss.xml",
    "https://www.lesnumeriques.com/gaming/rss.xml",
    "https://www.lesnumeriques.com/loisirs/rss.xml",
    "https://www.lesnumeriques.com/electromenager/rss.xml",
]
MAX_DETAIL_PAGES = int(os.getenv("MAX_DETAIL_PAGES", "20"))
MAX_META_DISCOVERY_PAGES = int(os.getenv("MAX_META_DISCOVERY_PAGES", "8"))
DETAIL_FETCH_TIMEOUT = 6
RSS_URL = os.getenv("RSS_URL")  # If specified, override all sources for local debugging.
STATE_FILE = Path(os.getenv("STATE_FILE", "seen.json"))
PREFERENCES_FILE = Path(__file__).with_name("preferences-draft.json")
try:
    PREF = json.loads(PREFERENCES_FILE.read_text(encoding="utf-8"))
except (ValueError, OSError):
    PREF = {}
MARKETS = tuple(PREF.get("markets", ["amazon.fr", "amazon.es", "amazon.it"]))
LOCAL_TZ = ZoneInfo("Europe/Paris")
REPORT_HOUR = int(PREF.get("notifications", {}).get("daily_report", {}).get("local_time", "21:00").split(":")[0]) if PREF.get("notifications", {}).get("daily_report", {}).get("local_time") else 21
PREFERRED_BRANDS = re.compile(
    r"apple|iphone|ipad|airpods|macbook|sony|playstation|samsung|lg|asus|msi|"
    r"logitech|razer|corsair|nintendo|meta|oculus|quest|valve|steam deck|"
    r"crucial|western digital|wd black|kingston|jbl|bose|dyson|roborock|"
    r"philips|de.longhi|"
    r"amd|ryzen|intel|nvidia|geforce|hyperx|steelseries|lenovo|dell|"
    r"seagate|anker|xiaomi|aoc|gigabyte|acer|benq|tcl|hisense|electrolux|"
    r"bosch|karcher|kärcher|gopro|garmin|hp|thrustmaster|ecovacs", re.I
)
OBVIOUS_NO_NAME = re.compile(
    r"(?:\bsans\s+marque\b|\bno[ -]?name\b|\bgeneric\b|\bmarque\s+inconnue\b)", re.I
)
HOME_RE = re.compile(
    r"aspirateur|robot\s+aspirateur|airfryer|friteuse|cafetière|"
    r"machine\s+à\s+café|lave[- ]linge|lave[- ]vaisselle|"
    r"réfrigérateur|frigo|congélateur|four|micro[- ]ondes|"
    r"ventilateur|purificateur|blender|mixeur|machine\s+à\s+glace|"
    r"balai\s+vapeur|sèche[- ]linge|air\s+conditionné|"
    r"aspirapolvere|friggitrice|lavatrice|lavastoviglie|"
    r"macchina\s+del\s+caffè|robot\s+da\s+cucina|cafetera|"
    r"freidora|lavadora|lavavajillas", re.I
)
# New interest filters are intentionally multilingual (FR/ES/IT/EN).
# They classify *titles and descriptions*, not pictures or Amazon catalog data.
INTERIOR_RE = re.compile(
    r"\b(chair|chairs|desk|desk chair|wall art|art mural|objet déco|"
    r"furniture|furnishing|sofa|scandinavian style chair|"
    r"bauhaus chair|bauhaus lampe|sculptural vase|"
    r"memphis furniture|space.age furniture|designer furniture|"
    r"ikea furniture|mobilier|ameublement|décoration|decoration|"
    r"décoratif|decoracion|decoración|arredamento|"
    r"chaise|fauteuil|tabouret|canapé|sofa|silla|sillón|"
    r"tavolo|sedia|poltrona|sedie|stool|armchair|"
    r"table|mesa|coffee table|side table|"
    r"étagère|étagères|estantería|mensola|scaffale|"
    r"lampes?|lampadas?|lámparas?|lamps?|luminaires?|abat.jour|"
    r"miroir|espejo|specchio|mirror|"
    r"vases?|vasos?|bougies?|velas?|candelas?|candles?|"
    r"céramique|ceramica|cerámica|ceramic|"
    r"assiettes?|vaisselle|stoviglie|vajilla|"
    r"verres?|glasses|tasse|taza|tazza|mug|"
    r"tapis|alfombras?|tappeti|tappeto|rugs?|carpets?|"
    r"coussins?|cojines|cojín|cuscini|cuscino|pillows?|"
    r"rideaux?|cortinas?|tende|tenda|curtains?|"
    r"plaid|textile|organiseur|organizer|"
    r"burger chair|burger table|food.shaped)\b", re.I,
)
INTERIOR_STYLE_RE = re.compile(
    r"bauhaus|scandinave|scandinavian|scandinavo|escandinavo|"
    r"nordic|nordique|nordico|nórdico|modernist|modernismo|"
    r"mid.century|ikea|memphis|space.age|"
    r"sculptur|geometric|géométrique|geométric|"
    r"design|statement|pop.art|playful|"
    r"burger|food.shaped|forme originale|formas? original|"
    r"forma originale|unusual|original design", re.I,
)
LOW_PRIORITY_INTERIOR_RE = re.compile(
    r"\b(rustic|rustique|rustico|rústico|boho|"
    r"baroque|barocco|barroco|rococo|farmhouse)\b", re.I,
)
LEGO_RE = re.compile(
    r"\b(lego|duplo|technic|minifig|"
    r"briques? de construction|jouets? de construction|"
    r"bloques? de construcción|mattoncini|"
    r"building blocks?|construction sets?|"
    r"playmobil|nanoblock|cobi)\b", re.I,
)
COLLECTIBLES_RE = re.compile(
    r"\b(funko|pop!|figurine|figura|figure|"
    r"statue|statuette|collectible|collector|"
    r"collection|coleccionable|collezionabile|"
    r"edition limitée|édition limitée|limited edition|"
    r"edición limitada|edizione limitata|"
    r"art toy|designer toy|nendoroid|"
    r"maquette|modèle réduit|modelo a escala|"
    r"model kit|gunpla|blind box)\b", re.I,
)
PERSONAL_CARE_RE = re.compile(
    r"\b(soin visage|soin corps|soins de beauté|"
    r"rasoir|tondeuse|épilateur|brosse à dents|"
    r"sèche.cheveux|lisseur|boucleur|"
    r"skincare|skin care|hair care|"
    r"hair dryer|shaver|trimmer|"
    r"depiladora|afeitadora|secador de pelo|"
    r"cepillo dental|cuidado personal|"
    r"cura della persona|asciugacapelli|"
    r"rasoio|spazzolino elettrico|"
    r"bellezza|massaggiatore|massager|"
    r"beauty device|personal care)\b", re.I,
)

MIN_DISCOUNT = int(os.getenv("MIN_DISCOUNT", str(PREF.get("min_discount_percent", 50))))
INTERIOR_DISCOUNT = int(PREF.get("interior_design_profile", {}).get("min_claimed_discount_percent", 25))
SURPRISE_DISCOUNT = int(PREF.get("unexpected_diamond", {}).get("min_claimed_discount_percent", 80))
MAX_NOTIFICATIONS = min(8, int(os.getenv("MAX_NOTIFICATIONS", "5")))
DRY_RUN = os.getenv("DRY_RUN", "false").lower() == "true"
REPORT_STATUS = os.getenv("REPORT_STATUS", "false").lower() == "true"

CATEGORY_RE = re.compile(
    r"\b(ssd|nvme|gpu|geforce|radeon|rtx|cpu|ryzen|ddr[45]|ram|laptop|"
    r"ordinateur|pc|monitor|oled|qled|wifi|bluetooth|gaming|gamer|xbox|"
    r"nintendo|switch|playstation|ps[345]|logitech|razer|steelseries|"
    r"corsair|hyperx|samsung|asus|msi|ipad|iphone|pixel|xiaomi|"
    r"smartphone|webcam|chromebook)\b|carte graphique|disque dur|"
    r"processeur|mémoire|ecran|écran|moniteur|clavier|souris|"
    r"casque|écouteurs?|microphone|manettes?|jeu[x]? vidéo|"
    r"tablette|imprimante 3d|téléviseur|routeur|projecteur|"
    r"ordenador|tarjeta\s+gráfica|auriculares|procesador|"
    r"consola|videojuegos|teléfono|telefono|portátil|portatil|"
    r"scheda\s+video|scheda\s+grafica|cellulare|cuffie|"
    r"videogiochi|smartwatch|televisore|tastiera|"
    r"carte mère|alimentation pc|console de jeu", re.I,
)
EXCLUDE_RE = re.compile(
    r"occasion|seconde main|reconditionn|refurbished|\bused\b|"
    r"warehouse|abonnement|location|rental|rupture de stock|"
    r"reacondicionado|segunda mano|ricondizionato|\busato\b|\busata\b|"
    r"expiré|fin du deal|écoulé|épuisé", re.I,
)
PERCENT_RE = re.compile(
    r"(?:(?:-|−|–)\s*|remise\s+(?:de\s*)?|"
    r"r[eé]duction\s+(?:de\s*)?|économie\s+de\s*|"
    r"discount\s+(?:de\s*)?|descuento\s+(?:del?\s*)?|"
    r"sconto\s+(?:del?\s*)?)"
    r"(\d{1,3})\s*%", re.I,
)
PERCENT_SUFFIX_RE = re.compile(
    r"\b(\d{1,3})\s*%\s*(?:off|de\s+descuento|di\s+sconto|"
    r"de\s+réduction|de\s+remise)", re.I,
)
PAIR_RE = re.compile(
    r"(?P<now>\d{1,5}(?:[.,]\d{1,2})?)\s*€\s*"
    r"(?:au\s+lieu\s+de|contre|plut[oô]t\s+que|"
    r"antes\s+(?:de|por)|invece\s+di|anziché|anziche|"
    r"precio\s+anterior|prima\s+di)\s*"
    r"(?P<before>\d{1,5}(?:[.,]\d{1,2})?)\s*€", re.I,
)
URL_RE = re.compile(r"https?://[^\s\"'<>]+", re.I)
ASIN_RE = re.compile(r"/(?:dp|gp/product|gp/aw/d|gp/offer-listing|exec/obidos/ASIN)/([A-Z0-9]{10})(?:[/?#]|$)", re.I)
AMAZON_EMBEDDED_RE = re.compile(
    r"https?://(?:www\.|smile\.)?amazon\.(?:fr|es|it)/[^\s\"'<>]+", re.I
)
SHORTLINK_HOSTS = {"amzn.eu", "amzn.to", "a.co"}
PROMO_HINT_RE = re.compile(
    r"bon\s+plan|promo|soldes|discount|"
    r"offre|oferta|offerta|deal|réduction|sconto|"
    r"descuento|economis|économis", re.I
)
SCAN_STATS = {}


BRAND_RE = re.compile(
    r"samsung|sony|lg|asus|msi|logitech|corsair|razer|"
    r"steelseries|hyperx|western digital|wd black|crucial|"
    r"nintendo|playstation|xbox|amd|intel|nvidia|"
    r"apple|kingston|seagate|lenovo|dell|hp|bose|"
    r"jbl|anker|thrustmaster|elgato", re.I
)
ERROR_RE = re.compile(r"erreur\s+de\s+prix|bug\s+de\s+prix|price\s+error", re.I)


def fetch(url, timeout=15):
    request = Request(url, headers={
        "User-Agent": "Mozilla/5.0 (compatible; RSS Reader 1.0)",
        "Accept": "application/rss+xml,application/xml,text/xml,text/html,*/*",
    })
    with urlopen(request, timeout=timeout) as response:
        return response.read(2_000_000).decode("utf-8", "replace")


def amazon_link(candidate):
    try:
        candidate = html.unescape(unquote(candidate.replace("\\/", "/"))).rstrip(".,;)\\]")
        parsed = urlsplit(candidate)
        if parsed.scheme not in ("http", "https"):
            return None
        if (parsed.hostname or "").lower() not in tuple(
            host for market in MARKETS
            for host in (market, "www." + market, "smile." + market)
        ):
            return None
        match = ASIN_RE.search(parsed.path)
        return ("https://" + (parsed.hostname or "").lower().removeprefix("www.").removeprefix("smile.") + "/dp/" + match.group(1).upper()) if match else None
    except ValueError:
        return None


def allowed_redirect_url(url):
    """Allow only HTTPS Amazon marketplaces and official Amazon shorteners."""
    try:
        p = urlsplit(url)
        host = (p.hostname or "").lower()
        allowed_amazon = any(host in (market, "www." + market, "smile." + market)
                             for market in MARKETS)
        return (p.scheme == "https" and p.username is None and
                p.password is None and p.port in (None, 443) and
                (allowed_amazon or host in SHORTLINK_HOSTS))
    except ValueError:
        return False


class RestrictedAmazonRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not allowed_redirect_url(newurl):
            raise ValueError("Redirect to unapproved destination")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def resolve_shortlink(url):
    """Follow official short links only; never fetch arbitrary redirect hosts."""
    try:
        parsed = urlsplit(url)
        if parsed.scheme != "https" or (parsed.hostname or "").lower() not in SHORTLINK_HOSTS:
            return None
        opener = build_opener(RestrictedAmazonRedirect())
        request = Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with opener.open(request, timeout=DETAIL_FETCH_TIMEOUT) as response:
            return amazon_link(response.geturl()) if allowed_redirect_url(response.geturl()) else None
    except (HTTPError, URLError, ValueError, TimeoutError, OSError):
        return None


def find_amazon_link(text):
    """Extract canonical direct links from escaped HTML, JSON or URL parameters."""
    cleaned = str(text)
    for _ in range(3):
        cleaned = html.unescape(unquote(cleaned)).replace("\\/", "/")
        cleaned = re.sub(
            r"\\u(0026|002f|003a|003d|003f)", 
            lambda m: chr(int(m.group(1), 16)), cleaned, flags=re.I
        )
    # Embedded destination parameters may contain an entire Amazon URL.
    for match in AMAZON_EMBEDDED_RE.finditer(cleaned):
        direct = amazon_link(match.group(0))
        if direct:
            return direct
    for url in URL_RE.findall(cleaned):
        direct = amazon_link(url)
        if direct:
            return direct
    # Resolve a bounded number of Amazon-owned shortened addresses.
    tried = set()
    for url in URL_RE.findall(cleaned):
        try:
            host = (urlsplit(url).hostname or "").lower()
        except ValueError:
            continue
        if host in SHORTLINK_HOSTS and url not in tried:
            tried.add(url)
            result = resolve_shortlink(url.rstrip(".,;)\\]"))
            if result:
                return result
            if len(tried) >= 2:
                break
    return None


def find_article_amazon_link(markup):
    """Avoid attributing a related product in a sidebar to the advertised offer.

    An article with multiple distinct Amazon ASIN URLs is too ambiguous to
    send as a single direct deal. Never synthesize an ASIN from product names.
    """
    cleaned = str(markup)
    for _ in range(3):
        cleaned = html.unescape(unquote(cleaned)).replace("\\/", "/")
    direct_links = set()
    for match in AMAZON_EMBEDDED_RE.finditer(cleaned):
        direct = amazon_link(match.group(0))
        if direct:
            direct_links.add(direct)
            if len(direct_links) > 1:
                return None
    if len(direct_links) == 1:
        return next(iter(direct_links))
    # If only a short URL is present, require just one distinct target.
    short_links = []
    for url in URL_RE.findall(cleaned):
        try:
            if urlsplit(url).hostname in SHORTLINK_HOSTS:
                short_links.append(url)
        except ValueError:
            pass
    if len(set(short_links)) == 1:
        return resolve_shortlink(short_links[0].rstrip(".,;)\\]"))
    return None


def parse_feed(text):
    root = ET.fromstring(text)
    items = []
    for entry in (root.findall(".//item") or root.findall(".//{*}entry")):
        fields = {}
        for child in entry:
            key = child.tag.split("}")[-1]
            fields[key] = (
                (child.get("href") or child.get("url") or child.text or "").strip()
            )
        other_urls = " ".join(
            value for sub in entry.iter()
            for key, value in sub.attrib.items()
            if key.lower().endswith(("url", "href", "src")) and value.startswith("https://")
        )
        items.append({
            "title": fields.get("title", ""),
            "description": fields.get("description", ""),
            "content": " ".join(filter(None, (
                fields.get("encoded", ""), fields.get("content", ""), other_urls
            ))),
            "link": fields.get("link", ""),
            "key": fields.get("guid") or fields.get("id") or fields.get("link", ""),
            "pubDate": fields.get("pubDate", ""),
        })
    return items


class ArticleMetadata(HTMLParser):
    """Only extract first-party article title and description, not sidebar promos."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.texts = []
        self.in_title = False

    def handle_starttag(self, tag, attrs):
        meta = dict(attrs)
        if tag == "title":
            self.in_title = True
        if tag == "meta" and (
            meta.get("name", "").lower() in ("description", "twitter:description")
            or meta.get("property", "").lower() in (
                "og:title", "og:description", "twitter:title")
        ):
            self.texts.append(meta.get("content", "")[:700])

    def handle_endtag(self, tag):
        if tag == "title":
            self.in_title = False

    def handle_data(self, data):
        if self.in_title:
            self.texts.append(data[:350])


def article_metadata(markup):
    parser = ArticleMetadata()
    parser.feed(markup[:400_000])
    return " ".join(parser.texts)[:1800]


def percent(title, desc):
    source = html.unescape(title + " " + re.sub(r"<[^>]*>", " ", desc[:1000]))
    claims = []
    for match in PERCENT_RE.finditer(source):
        value = int(match.group(1))
        prefix = source[max(0, match.start() - 19):match.start()].lower()
        if 25 <= value <= 95 and not re.search(
            r"(?:jusqu['’]?\s*[àa]|hasta|fino\s+a|up\s+to)\s*$", prefix
        ):
            claims.append(value)
    for match in PERCENT_SUFFIX_RE.finditer(source):
        value = int(match.group(1))
        prefix = source[max(0, match.start() - 25):match.start()].lower()
        if 25 <= value <= 95 and not re.search(
            r"(?:jusqu['’]?\s*[àa]|hasta|fino\s+a|up\s+to)\s*$", prefix
        ):
            claims.append(value)
    return max(claims) if claims else None


def price_pair(text):
    plain = html.unescape(re.sub(r"<[^>]*>", " ", text[:1200]))
    for match in PAIR_RE.finditer(plain):
        current = float(match.group("now").replace(",", "."))
        previous = float(match.group("before").replace(",", "."))
        if 5 <= current < previous <= 50000:
            claimed = round(100 * (1 - current / previous))
            if 25 <= claimed <= 95:
                return claimed, current, previous
    return None


def category_matches(source):
    """Classify searchable RSS text without claiming to analyze product images."""
    groups = []
    if CATEGORY_RE.search(source):
        groups.append("electronics/gaming")
    if HOME_RE.search(source):
        groups.append("home appliances")
    for pattern, name in (
        (INTERIOR_RE, "design & interior"),
        (LEGO_RE, "LEGO & construction"),
        (COLLECTIBLES_RE, "collectibles"),
        (PERSONAL_CARE_RE, "personal care"),
    ):
        if pattern.search(source):
            groups.append(name)
    return groups


def relevant(title, desc):
    source = html.unescape(title + " " + re.sub(r"<[^>]*>", " ", desc[:600]))
    return bool(category_matches(source) and
                not EXCLUDE_RE.search(source) and
                not OBVIOUS_NO_NAME.search(source))


def dealabs_link(link):
    try:
        p = urlsplit(link)
        return p.scheme == "https" and (p.hostname or "").lower() in (
            "www.dealabs.com", "dealabs.com",
            "www.chollometro.com", "chollometro.com",
            "quantospendo.app", "www.quantospendo.app",
            "lesnumeriques.com", "www.lesnumeriques.com"
        )
    except ValueError:
        return False


def fresh_enough(pubdate):
    if not pubdate:
        return True
    try:
        dt = parsedate_to_datetime(pubdate)
        if not dt.tzinfo:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt >= datetime.now(timezone.utc) - timedelta(hours=48)
    except (ValueError, TypeError):
        return True


def get_seen():
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
        if isinstance(data, list):
            return {k: 100 for k in data if isinstance(k, str)}
    except (ValueError, OSError, TypeError):
        pass
    return {}


def save_seen(seen):
    # Values store the highest notified *claimed* discount for each deal or ASIN.
    # No Amazon price history is inferred from these values.
    metadata = seen.get("__metadata__", {})
    subset = {k: v for k, v in list(seen.items())[-5000:] if k != "__metadata__"}
    subset["__metadata__"] = metadata
    STATE_FILE.write_text(json.dumps(subset, ensure_ascii=False), encoding="utf-8")


def telegram_send(message):
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        raise RuntimeError("Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID (Actions Secrets)")
    body = urlencode({
        "chat_id": chat_id, "text": message,
        "disable_web_page_preview": "true"
    }).encode()
    request = Request(
        "https://api.telegram.org/bot" + token + "/sendMessage",
        data=body, method="POST"
    )
    try:
        with urlopen(request, timeout=15) as response:
            result = json.loads(response.read().decode())
            if not result.get("ok"):
                raise RuntimeError("Telegram rejected message")
    except HTTPError as error:
        try:
            data = json.loads(error.read(2048).decode("utf-8", "replace"))
            reason = str(data.get("description", "unknown error"))
        except ValueError:
            reason = "unknown error"
        raise RuntimeError(
            f"Telegram API HTTP {error.code}: " + reason.replace(token, "[REDACTED]")
        ) from None


def collect():
    feed_urls = [RSS_URL] if RSS_URL else FEEDS
    posts = {}
    ok_feeds = 0
    for url in feed_urls:
        try:
            xml_text = fetch(url)
            items = parse_feed(xml_text)
            ok_feeds += 1
            print(f"FEED OK: {url}: {len(items)} posts")
            for item in items:
                unique = item["key"] or item["link"]
                if unique and unique not in posts:
                    posts[unique] = item
        except (HTTPError, URLError, TimeoutError, ET.ParseError, ValueError) as error:
            print(f"FEED unavailable: {url}: {type(error).__name__}")
    if not ok_feeds:
        raise RuntimeError("All RSS sources failed; check Dealabs feed accessibility.")
    return list(posts.values()), ok_feeds, len(feed_urls)


def candidates(posts):
    result = []
    detail_requests = 0
    metadata_requests = 0
    rejected_without_direct_link = 0
    counters = {
        "stale": 0, "used_or_excluded": 0, "no_category": 0,
        "below_threshold": 0, "no_amazon_product_link": 0,
        "eligible": 0, "editorial_metadata_found": 0,
    }
    unresolved_examples = []
    for item in posts:
        title = html.unescape(item["title"])
        desc = item["description"]
        if not fresh_enough(item["pubDate"]):
            counters["stale"] += 1
            continue
        if not dealabs_link(item["link"]):
            counters["no_category"] += 1
            continue
        claimed_percent = percent(title, desc)
        pair = price_pair(title + " " + desc)
        reduction = max(claimed_percent or 0, pair[0] if pair else 0)
        source_text = title + " " + desc[:700]
        if EXCLUDE_RE.search(source_text) or OBVIOUS_NO_NAME.search(source_text):
            counters["used_or_excluded"] += 1
            continue
        matches = category_matches(source_text)
        interior = "design & interior" in matches
        minimum = INTERIOR_DISCOUNT if interior else MIN_DISCOUNT
        # Recognized-brand surprise deals require 80%+.
        unexpected = (
            not matches
            and PREF.get("unexpected_diamond", {}).get("enabled", False)
            and reduction >= SURPRISE_DISCOUNT
            and bool(PREFERRED_BRANDS.search(source_text))
        )
        # When the feed omits an explicit percent, inspect only article
        # title/description metadata, never the full page's unrelated sidebar.
        if reduction < minimum and (
            matches or (PREFERRED_BRANDS.search(source_text) and
                        PREF.get("unexpected_diamond", {}).get("enabled", False))
        ) and PROMO_HINT_RE.search(source_text) and metadata_requests < MAX_META_DISCOVERY_PAGES:
            metadata_requests += 1
            try:
                snippet = article_metadata(fetch(item["link"], timeout=DETAIL_FETCH_TIMEOUT))
                claimed_meta = percent(snippet, "")
                pair_meta = price_pair(snippet)
                improved = max(claimed_meta or 0, pair_meta[0] if pair_meta else 0)
                if improved > reduction:
                    reduction = improved
                    pair = pair_meta if pair_meta and improved == pair_meta[0] else pair
                    counters["editorial_metadata_found"] += 1
            except (HTTPError, URLError, TimeoutError, ValueError, OSError):
                pass
        unexpected = (
            not matches
            and PREF.get("unexpected_diamond", {}).get("enabled", False)
            and reduction >= SURPRISE_DISCOUNT
            and bool(PREFERRED_BRANDS.search(source_text))
        )
        if not matches and not unexpected:
            counters["no_category"] += 1
            continue
        if reduction < minimum and not unexpected:
            counters["below_threshold"] += 1
            continue
        # Only trusted editorial pages and exact Amazon FR/ES/IT product URLs.
        direct = find_amazon_link(
            item["link"] + " " + item["title"] + " " + desc + " " + item["content"]
        )
        # A known editorial deal page may expose a real merchant URL which
        # is not present in the feed. Bound work to keep GitHub runs affordable.
        if not direct and detail_requests < MAX_DETAIL_PAGES:
            detail_requests += 1
            try:
                direct = find_article_amazon_link(
                    fetch(item["link"], timeout=DETAIL_FETCH_TIMEOUT)
                )
            except (HTTPError, URLError, TimeoutError, ValueError, OSError):
                pass
        if not direct:
            rejected_without_direct_link += 1
            counters["no_amazon_product_link"] += 1
            if len(unresolved_examples) < 3:
                unresolved_examples.append((title[:110], item["link"][:180]))
            continue
        style_bonus = 15 if interior and INTERIOR_STYLE_RE.search(source_text) else 0
        style_penalty = 10 if interior and LOW_PRIORITY_INTERIOR_RE.search(source_text) else 0
        priority = (
            reduction + 8 * bool(ERROR_RE.search(title))
            + 12 * bool(PREFERRED_BRANDS.search(title))
            + style_bonus - style_penalty
        )
        counters["eligible"] += 1
        result.append({
            "item": item, "title": title, "amazon": direct,
            "discount": reduction, "pair": pair,
            "priority": priority, "market": urlsplit(direct).hostname,
            "category": ", ".join(matches) if matches else "Unexpected Diamond",
        })
    print(
        f"Article lookups: {detail_requests} link checks, "
        f"{metadata_requests} metadata checks. "
        f"Rejected {rejected_without_direct_link} qualifying offers without a direct Amazon URL."
    )
    print("SCAN FUNNEL:", json.dumps(counters, ensure_ascii=False))
    for candidate_title, candidate_url in unresolved_examples:
        print("UNRESOLVED:", repr(candidate_title), candidate_url)
    return sorted(result, key=lambda d: (d["priority"], d["discount"]), reverse=True)


def run():
    if not DRY_RUN and (
        not os.getenv("TELEGRAM_BOT_TOKEN") or
        not os.getenv("TELEGRAM_CHAT_ID")
    ):
        raise RuntimeError("Create Telegram bot and set both Actions secrets first.")
    posts, available, requested = collect()
    seen = get_seen()
    found = candidates(posts)
    sent = 0
    today = datetime.now(LOCAL_TZ).date().isoformat()
    meta = seen.setdefault("__metadata__", {})
    stats = meta.setdefault("daily_stats", {})
    counts = stats.setdefault(today, {
        "scans": 0, "posts_checked": 0, "candidates": 0,
        "alerts_sent": 0, "markets": {}
    })
    counts["scans"] += 1
    counts["posts_checked"] += len(posts)
    counts["candidates"] += len(found)
    for candidate in found:
        market = candidate["market"] or "unknown"
        counts["markets"][market] = counts["markets"].get(market, 0) + 1
    for deal in found:
        item = deal["item"]
        asin_key = deal["amazon"]
        # Notify again only if *claimed* discount grows at least 10 percentage points.
        if int(seen.get(asin_key, 0)) + (10 if asin_key in seen else 0) > deal["discount"]:
            continue
        if item["key"] in seen and int(seen[item["key"]]) >= deal["discount"]:
            continue
        pair = deal["pair"]
        prices = (
            f"\nВ объявлении: {pair[1]:.2f} € вместо {pair[2]:.2f} €"
            if pair else ""
        )
        level = "⚡ ВОЗМОЖНАЯ ЦЕНОВАЯ ОШИБКА" if ERROR_RE.search(deal["title"]) else "💎 КАНДИДАТ НА СКИДКУ"
        market = deal["market"] or "Amazon"
        shipping = (
            "Доставка во Францию: НЕ ПРОВЕРЕНА; уточни доступность и итоговую цену в корзине.\n"
            if market in ("amazon.es", "amazon.it") else
            "Доставка и наличие: НЕ ПРОВЕРЕНЫ; уточни цену и условия.\n"
        )
        message = (
            f"{level} · НЕ ПОДТВЕРЖДЕНО\n"
            + deal["title"][:170] + "\n\n"
            + f"Категория: {deal['category']}\n"
            + f"Заявленная скидка: −{deal['discount']}%" + prices + "\n"
            + "История цен и цена Amazon: НЕ проверены\n"
            + shipping
            + f"{market}: {deal['amazon']}\n"
            + f"Первоисточник: {item['link']}\n\n"
            + "Проверь цену, доставку в FR, продавца, состояние товара и график Keepa."
        )
        if DRY_RUN:
            print(message + "\n---")
        else:
            telegram_send(message)
            time.sleep(0.4)
        seen[asin_key] = deal["discount"]
        seen[item["key"]] = deal["discount"]
        sent += 1
        counts["alerts_sent"] += 1
        if sent >= MAX_NOTIFICATIONS:
            break
    # Send once per Paris-local day, only during the requested 21:00 hour.
    # Scheduled 19:00 and 20:00 UTC triggers cover summer and winter DST.
    local_time = datetime.now(LOCAL_TZ)
    if (not DRY_RUN and local_time.hour == REPORT_HOUR and
            meta.get("last_report_date") != today):
        markets = ", ".join(f"{k}: {v}" for k, v in counts["markets"].items()) or "пока нет кандидатов"
        daily_message = (
            f"📊 Amazon Diamond Hunter — отчёт за {today} (Париж)\n"
            f"Запусков: {counts['scans']}\n"
            f"Проверок публикаций: {counts['posts_checked']} "
            "(включая повторные между сканированиями)\n"
            f"Кандидатов: {counts['candidates']} (интерьер от 25%, остальные от 50%) "
            "(суммарно по проверкам)\n"
            f"Отправлено уведомлений: {counts['alerts_sent']}\n"
            f"Магазины: {markets}\n\n"
            "Это мониторинг публичных объявлений, не подтверждение цен Amazon. "
            "Доставка во Францию, продавец и история цен требуют проверки."
        )
        telegram_send(daily_message)
        meta["last_report_date"] = today
    # Keep only a few local-day report summaries in cached state.
    meta["daily_stats"] = {
        k: v for k, v in sorted(stats.items())[-4:]
    }
    save_seen(seen)
    summary = (
        f"💎 Diamond Hunter: проверено {len(posts)} публикаций из "
        f"{available}/{requested} лент. Кандидатов по порогам 25% интерьер / 50% остальные: {len(found)}, "
        f"новых уведомлений: {sent}."
    )
    print(summary)
    if REPORT_STATUS and not DRY_RUN:
        telegram_send(summary + "\nБез подтверждённой истории цен предложения не считаются доказанными скидками.")
    return sent


if __name__ == "__main__":
    try:
        run()
    except Exception as error:
        print("Diamond Hunter:", type(error).__name__, str(error), file=sys.stderr)
        sys.exit(1)
