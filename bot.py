"""Amazon Diamond Hunter v2 — free RSS discovery; advertised discounts are NOT verified."""
import html
import json
import os
import re
import sys
import time
from zoneinfo import ZoneInfo
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import unquote, urlsplit, urlencode
from urllib.request import HTTPRedirectHandler, build_opener
from urllib.request import Request, urlopen
from html.parser import HTMLParser
import signals
import alerts
import media
import catalog
import policy
import pepper

FEEDS = [
    "https://www.dealabs.com/rss/hot.xml",
    "https://www.dealabs.com/rss",
    "https://www.dealabs.com/rss/groupe/informatique",
    "https://www.dealabs.com/rss/groupe/consoles-jeux-video",
    "https://www.dealabs.com/rss/groupe/image-son-video",
    "https://www.dealabs.com/rss/groupe/telephonie",
    # Additional thematic feeds documented by public Dealabs RSS integrations.
    "https://www.dealabs.com/rss/groupe/jardin-bricolage",
    "https://www.dealabs.com/rss/groupe/jeux-jouets",
    "https://www.dealabs.com/rss/groupe/sante-et-cosmetiques",
    "https://www.dealabs.com/rss/groupe/accessoires-gadgets",
    "https://www.dealabs.com/rss/groupe/animaux",
    "https://www.dealabs.com/rss/groupe/culture-divertissement",
    "https://www.chollometro.com/rss",
    "https://quantospendo.app/offerte/feed.xml",
    "https://www.lesnumeriques.com/rss-gooddeals.xml",
    "https://www.lesnumeriques.com/informatique/rss.xml",
    "https://www.lesnumeriques.com/gaming/rss.xml",
    "https://www.lesnumeriques.com/loisirs/rss.xml",
    "https://www.lesnumeriques.com/electromenager/rss.xml",
    # Only French, Spanish and Italian Amazon-specific bargain feeds.
    "https://brickset.com/feed/amazon/fr",
    "https://brickset.com/feed/amazon/es",
    "https://brickset.com/feed/amazon/it",
]
MAX_DETAIL_PAGES = int(os.getenv("MAX_DETAIL_PAGES", "20"))
MAX_META_DISCOVERY_PAGES = int(os.getenv("MAX_META_DISCOVERY_PAGES", "8"))
MAX_PHOTO_LOOKUPS = max(0, min(5, int(os.getenv("MAX_PHOTO_LOOKUPS", "3"))))
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
    r"\b(?:apple|iphone|ipad|airpods|macbook|sony|playstation|"
    r"samsung|lg|asus|msi|logitech|razer|corsair|nintendo|"
    r"meta|oculus|quest|valve|steam\s*deck|crucial|"
    r"western\s*digital|wd\s*black|kingston|jbl|bose|"
    r"dyson|roborock|philips|de['. -]?longhi|"
    r"amd|ryzen|intel|nvidia|geforce|hyperx|steelseries|"
    r"lenovo|dell|seagate|anker|xiaomi|aoc|gigabyte|"
    r"acer|benq|tcl|hisense|electrolux|bosch|"
    r"k[aä]rcher|gopro|garmin|hp|thrustmaster|ecovacs)\b", re.I
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
SOURCE_ALERT_SETTINGS = PREF.get("notifications", {}).get("source_only_promising_alerts", {})
SOURCE_ALERTS_ENABLED = bool(SOURCE_ALERT_SETTINGS.get("enabled", False))
MAX_SOURCE_ALERTS = max(0, min(3, int(SOURCE_ALERT_SETTINGS.get("maximum_notifications_per_scan", 3))))
NEW_DISCOVERY = PREF.get("early_discovery", {})
ENABLE_EARLY_DISCOVERY = bool(NEW_DISCOVERY.get("enabled", False))
MAX_PRICE_WATCH = max(0, min(2, int(NEW_DISCOVERY.get("max_price_watch_alerts_per_scan", 2))))
MAX_URGENT = max(0, min(2, int(NEW_DISCOVERY.get("max_urgent_alerts_per_scan", 2))))
MAX_PRICE_WATCH_DAILY = max(0, min(24, int(NEW_DISCOVERY.get("max_price_watch_alerts_per_day", 6))))
MAX_URGENT_DAILY = max(0, min(24, int(NEW_DISCOVERY.get("max_urgent_alerts_per_day", 12))))
MAX_NOTIFICATIONS = min(8, int(os.getenv("MAX_NOTIFICATIONS", "5")))
DRY_RUN = os.getenv("DRY_RUN", "false").lower() == "true"
REPORT_STATUS = os.getenv("REPORT_STATUS", "false").lower() == "true"
CATALOG_ENABLED = os.getenv("CATALOG_ENABLED", "false").lower() == "true"
CATALOG_PATH = Path(os.getenv("CATALOG_PATH", "catalog.sqlite"))
CATALOG_EXPORT_DIR = Path(os.getenv("CATALOG_EXPORT_DIR", "catalog_export"))

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
BRICKSET_ROUNDUP_RE = re.compile(
    r"^(?:discounts?|deals?|offers?|promotions?)\s+(?:at|on|from)\s+amazon\.(?:fr|es|it)\b",
    re.I,
)
FASHION_TITLE_RE = re.compile(
    r"\b(pull|sweatshirt|robe|chemise|t.shirt|pantalon|"
    r"jupe|veste|jeans|chaussures?|baskets?|sneakers?|"
    r"vestido|camiseta|pantal[oó]n|zapatos?|"
    r"maglietta|vestiti|giacca|scarpe|cotone|coton)\b", re.I
)
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
        item_photo = media.rss_item_image(entry, fields.get("link", ""))
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
            "pubDate": fields.get("pubDate", "") or fields.get("published", "") or fields.get("updated", ""),
            "image": item_photo,
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
        suffix = source[match.end():match.end() + 24].lower()
        not_real_discount = bool(re.match(
            r"\s*(?:coton|cotton|polyester|laine|wool|"
            r"lin|linen|élasthanne|elastane|viscose|"
            r"acrylique|acrylic|soie|silk|"
            r"algod[oó]n|cotone|lana|poliéster|poliestere)\b", suffix
        ))
        if 25 <= value <= 95 and not not_real_discount and not re.search(
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
    return bool(policy.is_allowed(title, desc) and category_matches(source) and
                not EXCLUDE_RE.search(source) and
                not OBVIOUS_NO_NAME.search(source))


def dealabs_link(link):
    try:
        p = urlsplit(link)
        return p.scheme == "https" and (p.hostname or "").lower() in (
            "www.dealabs.com", "dealabs.com",
            "www.chollometro.com", "chollometro.com",
            "quantospendo.app", "www.quantospendo.app",
            "lesnumeriques.com", "www.lesnumeriques.com",
            "brickset.com", "www.brickset.com"
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
    # Atomic replacement avoids half-written state on interrupted runs.
    temp_file = STATE_FILE.with_name(STATE_FILE.name + ".tmp")
    temp_file.write_text(json.dumps(subset, ensure_ascii=False), encoding="utf-8")
    temp_file.replace(STATE_FILE)


def telegram_send(message, buttons=None, photo_url=None):
    """Send photo and caption when possible, otherwise deliver full text.

    Images are taken from the original deal post; they are NOT verified
    Amazon product imagery. An inaccessible or unsupported image must
    never prevent the deal text being delivered.
    """
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        raise RuntimeError("Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID (Actions Secrets)")
    photo = media.safe_image_url(photo_url)
    caption_suffix = "\nФото из публикации, соответствие товару не подтверждено."
    # Use a safety margin under Telegram's 1024-character caption limit.
    with_photo = bool(photo and len(message) + len(caption_suffix) <= 900)
    for use_photo in ([True, False] if with_photo else [False]):
        payload = {"chat_id": chat_id}
        if use_photo:
            payload.update({"photo": photo, "caption": message + caption_suffix})
            method = "sendPhoto"
        else:
            payload.update({"text": message, "disable_web_page_preview": "true"})
            method = "sendMessage"
        if buttons:
            payload["reply_markup"] = json.dumps(buttons, ensure_ascii=False)
        request = Request(
            "https://api.telegram.org/bot" + token + "/" + method,
            data=urlencode(payload).encode(), method="POST"
        )
        try:
            with urlopen(request, timeout=15) as response:
                data = json.loads(response.read().decode("utf-8", "replace"))
                if not data.get("ok"):
                    raise RuntimeError("Telegram rejected " + method)
                return
        except HTTPError as error:
            if use_photo:
                print("PHOTO FALLBACK: Telegram refused image; sending original text.")
                continue
            try:
                details = json.loads(error.read(2048).decode("utf-8", "replace"))
                reason = str(details.get("description", "unknown error"))
            except ValueError:
                reason = "unknown error"
            raise RuntimeError(
                f"Telegram API HTTP {error.code}: "
                + reason.replace(token, "[REDACTED]")
            ) from None
        except (URLError, TimeoutError, OSError) as error:
            if use_photo:
                print("PHOTO FALLBACK: image send failed; retrying as text.")
                continue
            raise
        except (ValueError, RuntimeError) as error:
            if use_photo:
                print("PHOTO FALLBACK: photo response unavailable; retrying as text.")
                continue
            raise



# Additional public RSS feeds may be supplied via EXTRA_RSS_FEEDS, separated
# by commas, semicolons or newlines. URLs are restricted to the known FR/ES/IT
# publishers; the scanner never accepts arbitrary URLs or UK/US sources.
ALLOWED_FEED_DOMAINS = {
    "dealabs.com", "chollometro.com", "quantospendo.app",
    "lesnumeriques.com", "brickset.com",
}


def configured_feeds():
    if RSS_URL:  # Explicit local/debug override remains unchanged.
        return [RSS_URL]
    urls = list(FEEDS)
    for raw in re.split(r"[,;\n]+", os.getenv("EXTRA_RSS_FEEDS", "")):
        url = raw.strip()
        if not url or url in urls:
            continue
        try:
            parsed = urlsplit(url)
            host = (parsed.hostname or "").lower()
            trusted = host.removeprefix("www.") in ALLOWED_FEED_DOMAINS
            if (parsed.scheme != "https" or not trusted or parsed.username
                    or parsed.password or parsed.port not in (None, 443)):
                print(f"Extra feed rejected (unsupported publisher): {host}")
                continue
        except ValueError:
            continue
        urls.append(url)
        if len(urls) >= len(FEEDS) + 24:
            break
    return urls


def _read_one_feed(url):
    try:
        return url, parse_feed(fetch(url)), None
    except (HTTPError, URLError, TimeoutError, ET.ParseError, ValueError, OSError) as error:
        return url, [], type(error).__name__


def collect():
    feed_urls = configured_feeds()
    posts = {}
    ok_feeds = 0
    # Parallel I/O prevents one broken RSS host from blocking the whole
    # 15-minute scan. executor.map retains stable priority/order for duplicates.
    with ThreadPoolExecutor(max_workers=min(6, len(feed_urls))) as executor:
        results = executor.map(_read_one_feed, feed_urls)
        for url, items, error in results:
            if error:
                print(f"FEED unavailable: {url}: {error}")
                continue
            ok_feeds += 1
            print(f"FEED OK: {url}: {len(items)} posts")
            for item in items:
                unique = item["link"] or item["key"]
                if unique and unique not in posts:
                    posts[unique] = item
    if os.getenv("PEPPER_GRAPHQL_ENABLED", "false").lower() == "true" and not RSS_URL:
        # Anonymous best-effort secondary channel. Failure must never break RSS.
        for item in pepper.fetch_public_threads():
            key = item["link"]
            if key in posts:
                existing = posts[key]
                if existing.get("advertised_price_eur") is None:
                    existing["advertised_price_eur"] = item.get("advertised_price_eur")
            else:
                posts[key] = item
    if not ok_feeds and not posts:
        raise RuntimeError("All available deal discovery sources failed.")
    return list(posts.values()), ok_feeds, len(feed_urls)


def candidates(posts, public_history=None):
    public_history = public_history if isinstance(public_history, dict) else {}
    result = []
    detail_requests = 0
    metadata_requests = 0
    rejected_without_direct_link = 0
    counters = {
        "stale": 0, "used_or_excluded": 0, "no_category": 0,
        "below_threshold": 0, "no_amazon_product_link": 0,
        "eligible": 0, "editorial_metadata_found": 0,
        "price_only": 0, "suspected_price_error": 0, "historical_claim": 0,
        "filtered_product": 0,
    }
    unresolved_examples = []
    for item in posts:
        title = html.unescape(item["title"])
        desc = item["description"]
        if not policy.is_allowed(title, desc):
            counters["filtered_product"] += 1
            continue
        # Brickset's "Discounts at Amazon.es" RSS entries are multi-product
        # roundups, not an individual deal. Percentages and ASINs can belong
        # to DIFFERENT items. Never send the roundup as one discounted product.
        if "brickset.com" in item["link"].lower() and BRICKSET_ROUNDUP_RE.search(title):
            counters.setdefault("multi_product_roundups_skipped", 0)
            counters["multi_product_roundups_skipped"] += 1
            continue
        if not fresh_enough(item["pubDate"]):
            counters["stale"] += 1
            continue
        if not dealabs_link(item["link"]):
            counters["no_category"] += 1
            continue
        # Les Numériques articles contain multi-merchant price comparison
        # tables. The headline's LOWEST price may belong to Joybuy, Fnac,
        # etc., even if the page also contains an Amazon ASIN. Neither the
        # price nor the advertised percent can be attributed to Amazon.
        editorial_comparison = (urlsplit(item["link"]).hostname or "").lower() in (
            "lesnumeriques.com", "www.lesnumeriques.com"
        )
        claimed_percent = percent(title, desc)
        pair = price_pair(title + " " + desc)
        reduction = max(claimed_percent or 0, pair[0] if pair else 0)
        # Strip HTML tags before category matching; otherwise <table>
        # markup was being misread as "table furniture".
        plain_description = html.unescape(re.sub(r"<[^>]*>", " ", desc))
        source_text = title + " " + plain_description[:700]
        if EXCLUDE_RE.search(source_text) or OBVIOUS_NO_NAME.search(source_text):
            counters["used_or_excluded"] += 1
            continue
        # Never let generic RSS descriptions turn clothing into a furniture deal.
        # Clothing wasn't selected as a category; the exceptional branded
        # 80%+ rule is handled separately after category matching.
        fashion_only_title = (
            bool(FASHION_TITLE_RE.search(title))
            and not category_matches(title)
        )
        matches = [] if fashion_only_title else category_matches(source_text)
        interior = "design & interior" in matches
        minimum = INTERIOR_DISCOUNT if interior else MIN_DISCOUNT
        # Recognized-brand surprise deals require 80%+.
        unexpected = (
            not matches
            and PREF.get("unexpected_diamond", {}).get("enabled", False)
            and reduction >= SURPRISE_DISCOUNT
            and bool(PREFERRED_BRANDS.search(source_text))
        )
        headline_eur = signals.headline_price(title) if ENABLE_EARLY_DISCOVERY else None
        explicit_error = bool(signals.ERROR_RE.search(title)) if ENABLE_EARLY_DISCOVERY else False
        price_watch = bool(
            ENABLE_EARLY_DISCOVERY and not editorial_comparison
            and matches and headline_eur is not None
            and signals.PROMO_RE.search(title)
            and (PREFERRED_BRANDS.search(title) or
                 (interior and INTERIOR_STYLE_RE.search(title)))
        )
        potential_urgent = bool(
            explicit_error and headline_eur is not None
            and (matches or PREFERRED_BRANDS.search(title))
        )
        # Known-product history can surface offers even without promotional
        # language. Historical comparison uses distinct RSS posts, not Amazon.
        rss_text = item["link"] + " " + item["title"] + " " + desc + " " + item["content"]
        history_direct = (
            find_amazon_link(rss_text)
            if not editorial_comparison and headline_eur is not None
            and "amazon." in rss_text.lower() else None
        )
        history_comparison = (
            signals.reference_drop(
                public_history.get(history_direct, []), headline_eur,
                item["key"], signals.publication_seconds(item["pubDate"])
            ) if history_direct and headline_eur is not None else None
        )
        # When the feed omits an explicit percent, inspect only article
        # title/description metadata, never the full page's unrelated sidebar.
        if reduction < minimum and (
            matches or (PREFERRED_BRANDS.search(source_text) and
                        PREF.get("unexpected_diamond", {}).get("enabled", False))
        ) and not (price_watch or potential_urgent or history_comparison) and PROMO_HINT_RE.search(source_text) and metadata_requests < MAX_META_DISCOVERY_PAGES:
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
        if reduction < minimum and not unexpected and not (
            potential_urgent or price_watch or history_comparison is not None
        ):
            counters["below_threshold"] += 1
            continue
        # Only trusted editorial pages and exact Amazon FR/ES/IT product URLs.
        direct = None if editorial_comparison else (
            history_direct or find_amazon_link(rss_text)
        )
        # Read trusted article pages only when a direct product URL is absent.
        # A fetch succeeding does not imply the product is sold on Amazon.
        article_available = False
        article_image = None
        if not direct and detail_requests < MAX_DETAIL_PAGES:
            detail_requests += 1
            try:
                markup = fetch(item["link"], timeout=DETAIL_FETCH_TIMEOUT)
                article_available = bool(markup.strip())
                if article_available and not item.get("image"):
                    article_image = media.page_og_image(markup, item["link"])
                if not editorial_comparison:
                    direct = find_article_amazon_link(markup) if article_available else None
            except (HTTPError, URLError, TimeoutError, ValueError, OSError):
                pass
        if not direct and reduction < minimum and not unexpected and not potential_urgent:
            counters["no_amazon_product_link"] += 1
            continue
        if not direct:
            rejected_without_direct_link += 1
            counters["no_amazon_product_link"] += 1
            if len(unresolved_examples) < 3:
                unresolved_examples.append((title[:110], item["link"][:180]))
            # The fallback links to the actual editorial article, not Amazon.
            # Only use two approved source domains for informational alerts.
            source_host = (urlsplit(item["link"]).hostname or "").lower()
            fallback_source = source_host in (
                "dealabs.com", "www.dealabs.com",
                "lesnumeriques.com", "www.lesnumeriques.com",
                "brickset.com", "www.brickset.com",
            )
            if not (SOURCE_ALERTS_ENABLED and article_available and fallback_source):
                continue
        baseline = history_comparison
        if direct and headline_eur is not None and not baseline:
            baseline = signals.reference_drop(
                public_history.get(direct, []), headline_eur, item["key"],
                signals.publication_seconds(item["pubDate"])
            )
        # A new price-only candidate without an independently observed drop
        # is strictly a WATCH, never a claimed verified discount.
        signal = ("suspected_error" if potential_urgent else
                  "historical_offer_drop" if baseline is not None and reduction < minimum else
                  "price_watch" if reduction < minimum and not unexpected else
                  "claimed_discount")
        if signal == "price_watch" and not direct:
            continue
        if signal == "suspected_error":
            counters["suspected_price_error"] += 1
        elif signal == "price_watch":
            counters["price_only"] += 1
        elif signal == "historical_offer_drop":
            counters["historical_claim"] += 1
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
            "priority": priority, "market": urlsplit(direct).hostname if direct else None,
            "category": ", ".join(matches) if matches else "Unexpected Diamond",
            "source_only": direct is None,
            "merchant_comparison": editorial_comparison,
            "signal": signal, "headline_price": headline_eur,
            "public_baseline": baseline,
            "image": item.get("image") or article_image,
        })
    print(
        f"Article lookups: {detail_requests} link checks, "
        f"{metadata_requests} metadata checks. "
        f"Rejected {rejected_without_direct_link} qualifying offers without a direct Amazon URL."
    )
    print("SCAN FUNNEL:", json.dumps(counters, ensure_ascii=False))
    for candidate_title, candidate_url in unresolved_examples:
        print("UNRESOLVED:", repr(candidate_title), candidate_url)
    # Urgent explicit error claims first, direct products before source-only.
    return sorted(result, key=lambda d: (
        d["signal"] == "suspected_error",
        not d["source_only"], d["priority"], d["discount"]
    ), reverse=True)


def run():
    if not DRY_RUN and (
        not os.getenv("TELEGRAM_BOT_TOKEN") or
        not os.getenv("TELEGRAM_CHAT_ID")
    ):
        raise RuntimeError("Create Telegram bot and set both Actions secrets first.")
    posts, available, requested = collect()
    before_scope_filter = len(posts)
    posts = [item for item in posts if policy.is_allowed(item.get("title"), item.get("description"))]
    print(f"PRODUCT POLICY: {before_scope_filter - len(posts)} unwanted Xbox/Samsung listings excluded.")
    seen = get_seen()
    meta = seen.setdefault("__metadata__", {})
    history = meta.setdefault("public_offer_history", {})
    notified_prices = meta.setdefault("notified_offer_prices", {})
    found = candidates(posts, history)
    if CATALOG_ENABLED:
        catalog_records = catalog.collect_records(
            posts, found, category_matches, percent, price_pair,
            signals.headline_price,
            lambda text: bool(EXCLUDE_RE.search(text) or OBVIOUS_NO_NAME.search(text)),
            fresh_enough, find_amazon_link,
        )
        saved_count = catalog.save_records(catalog_records, CATALOG_PATH)
        exported = catalog.export(CATALOG_PATH, CATALOG_EXPORT_DIR)
        print(
            f"FINDINGS DATABASE: {saved_count} unique source publications; "
            f"{len(catalog_records)} relevant listings in this scan, "
            f"{exported['exported']} exported. Claims unverified."
        )
    if DRY_RUN:
        for deal in found[:40]:
            print("SCAN CANDIDATE:", json.dumps({
                "title": deal["title"][:160],
                "category": deal["category"],
                "claimed_discount": deal["discount"],
                "market": deal["market"],
                "amazon": deal["amazon"],
                "source": deal["item"]["link"],
                "unverified": True,
            }, ensure_ascii=False))
    sent = 0
    sent_source_only = 0
    sent_watch = 0
    sent_urgent = 0
    photo_lookups = 0
    sent_photos = 0
    today = datetime.now(LOCAL_TZ).date().isoformat()
    stats = meta.setdefault("daily_stats", {})
    counts = stats.setdefault(today, {
        "scans": 0, "posts_checked": 0, "candidates": 0,
        "alerts_sent": 0, "markets": {}
    })
    counts.setdefault("price_watch_sent", 0)
    counts.setdefault("urgent_sent", 0)
    counts["scans"] += 1
    counts["posts_checked"] += len(posts)
    counts["candidates"] += len(found)
    for candidate in found:
        market = candidate["market"] or "editorial-link-only"
        counts["markets"][market] = counts["markets"].get(market, 0) + 1
    for deal in found:
        item = deal["item"]
        source_only = deal["source_only"]
        if source_only and sent_source_only >= MAX_SOURCE_ALERTS:
            continue
        if deal["signal"] == "price_watch" and (
            sent_watch >= MAX_PRICE_WATCH or
            counts["price_watch_sent"] >= MAX_PRICE_WATCH_DAILY
        ):
            continue
        if deal["signal"] == "suspected_error" and (
            sent_urgent >= MAX_URGENT or
            counts["urgent_sent"] >= MAX_URGENT_DAILY
        ):
            continue
        # Separate keys allow a later verified direct Amazon URL for the same
        # editorial post to be delivered even after a source-only alert.
        dedupe_key = ("source:" + item["key"]) if source_only else deal["amazon"]
        previous = seen.get(dedupe_key)
        current_headline_price = deal["headline_price"]
        if not alerts.meaningful_change(
            previous, deal["discount"],
            notified_prices.get(dedupe_key), current_headline_price
        ):
            continue
        # Preserve an upgrade from source-only to direct Amazon URL.
        # For the same direct-link article, don't re-alert on unchanged claims.
        item_key = "product:" + item["key"]
        if (not source_only and deal["discount"] > 0 and item_key in seen and
                deal["discount"] < int(seen[item_key]) + 10):
            continue
        pair = deal["pair"]
        prices = (
            f"\nВ объявлении: {pair[1]:.2f} € вместо {pair[2]:.2f} €"
            if pair else ""
        )
        level = (
            "⚠️ СРОЧНО: ВОЗМОЖНАЯ ОШИБКА ЦЕНЫ"
            if deal["signal"] == "suspected_error" else
            "📉 ПАДЕНИЕ ЦЕНЫ ПО ПУБЛИКАЦИЯМ — НЕ ПРОВЕРЕНО"
            if deal["signal"] == "historical_offer_drop" else
            "👀 НАБЛЮДЕНИЕ ЗА ЦЕНОЙ"
            if deal["signal"] == "price_watch" else
            "💎 КАНДИДАТ НА СКИДКУ"
        )
        observed_amount = (f"Цена в заголовке публикации: {deal['headline_price']:.2f} €\n"
                           if deal["headline_price"] is not None else "")
        historical = deal["public_baseline"]
        reference_text = (
            f"Медиана {historical['previous_posts']} прошлых объявлений: "
            f"{historical['reference']:.2f} €, отличие {historical['decrease_percent']}%.\n"
            "Это НЕ история фактических цен Amazon.\n"
            if historical else ""
        )
        market = deal["market"] or "Amazon"
        if source_only:
            claim_line = (f"Заявленная скидка: −{deal['discount']}%\n"
                          if deal["discount"] > 0 else "Скидка в процентах не указана.\n")
            message = (
                ("⚠️ СРОЧНО: ЗАЯВЛЕНА ВОЗМОЖНАЯ ОШИБКА ЦЕНЫ\n"
                 if deal["signal"] == "suspected_error" else
                 "🔎 ПЕРСПЕКТИВНАЯ НАХОДКА — НЕПРОВЕРЕНО\n")
                + deal["title"][:170] + "\n\n"
                + f"Категория: {deal['category']}\n"
                + claim_line + observed_amount + reference_text + prices + "\n\n"
                + ("ЦЕНА В ПУБЛИКАЦИИ МОЖЕТ ОТНОСИТЬСЯ К ДРУГОМУ МАГАЗИНУ.\n"
                   "На странице сравниваются разные продавцы; цена Amazon отдельно НЕ проверена.\n"
                   if deal.get("merchant_comparison") else
                   "ПРЯМОЙ ССЫЛКИ AMAZON НЕ ПОДТВЕРЖДЕНО.\n")
                + "Неизвестно, предлагается ли эта цена на Amazon. "
                + "Цена, продавец, доставка во Францию и история цен НЕ проверены.\n"
                + f"Публикация для ручной проверки: {item['link']}"
            )
        else:
            shipping = (
            "Доставка во Францию: НЕ ПРОВЕРЕНА; уточни доступность и итоговую цену в корзине.\n"
            if market in ("amazon.es", "amazon.it") else
            "Доставка и наличие: НЕ ПРОВЕРЕНЫ; уточни цену и условия.\n"
            )
            message = (
                f"{level} · НЕ ПОДТВЕРЖДЕНО\n"
                + deal["title"][:170] + "\n\n"
                + f"Категория: {deal['category']}\n"
                + (f"Заявленная скидка: −{deal['discount']}%\n"
                   if deal["discount"] > 0 else "Процент скидки НЕ заявлен.\n")
                + observed_amount + reference_text + prices + "\n"
                + "История цен и текущая цена Amazon: НЕ проверены\n"
                + shipping
                + f"{market}: {deal['amazon']}\n"
                + f"Первоисточник: {item['link']}\n\n"
                + "Проверь цену, доставку в FR, продавца, состояние товара и график Keepa."
            )
        buttons = alerts.deal_buttons(deal["amazon"], item["link"])
        photo = media.safe_image_url(deal.get("image"))
        # Fetch only the ORIGINAL deal article for OG image, never Google Images
        # or guessed Amazon image/ASIN URLs. Bounded per scan.
        if not photo and not DRY_RUN and photo_lookups < MAX_PHOTO_LOOKUPS:
            photo_lookups += 1
            if dealabs_link(item["link"]):
                try:
                    page = fetch(item["link"], timeout=DETAIL_FETCH_TIMEOUT)
                    photo = media.page_og_image(page, item["link"])
                except (HTTPError, URLError, TimeoutError, ValueError, OSError):
                    pass
        if DRY_RUN:
            print(message + "\n---")
        else:
            telegram_send(message, buttons=buttons, photo_url=photo)
            if photo and len(message) <= 875:
                sent_photos += 1  # attempted via Telegram, photo fallback is supported
            time.sleep(0.4)
        seen[dedupe_key] = deal["discount"]
        if current_headline_price is not None:
            notified_prices[dedupe_key] = current_headline_price
        if source_only:
            sent_source_only += 1
        else:
            seen["product:" + item["key"]] = deal["discount"]
        if deal["signal"] == "price_watch":
            sent_watch += 1
            counts["price_watch_sent"] += 1
        if deal["signal"] == "suspected_error":
            sent_urgent += 1
            counts["urgent_sent"] += 1
        sent += 1
        counts["alerts_sent"] += 1
        if sent >= MAX_NOTIFICATIONS:
            break
    # Bounded dedupe price state (not verified live Amazon prices).
    if len(notified_prices) > 3000:
        meta["notified_offer_prices"] = dict(list(notified_prices.items())[-2000:])
    print(
        f"Alert types this run: {sent - sent_source_only} direct Amazon; "
        f"{sent_source_only} editorial source-only; {sent_urgent} suspected errors; "
        f"{sent_watch} price watches; {sent_photos} photo delivery attempts."
    )
    # Track prices advertised in independently dated PUBLIC RSS announcements.
    # These are NOT observations of current Amazon prices.
    if ENABLE_EARLY_DISCOVERY:
        for item in posts:
            title = html.unescape(item["title"])
            price = signals.headline_price(title)
            if price is None or not fresh_enough(item["pubDate"]):
                continue
            if not dealabs_link(item["link"]) or not category_matches(title):
                continue
            if EXCLUDE_RE.search(title) or OBVIOUS_NO_NAME.search(title):
                continue
            # Avoid spending network calls on unrelated entries or short URLs.
            combined = item["link"] + " " + item["description"] + " " + item["content"]
            if "amazon." not in combined.lower():
                continue
            # Never record aggregator prices as if they were Amazon offers.
            if (urlsplit(item["link"]).hostname or "").lower() in (
                "lesnumeriques.com", "www.lesnumeriques.com"
            ):
                continue
            direct = find_amazon_link(combined)
            if direct:
                signals.record_observation(
                    history, direct, item["key"], price,
                    signals.publication_seconds(item["pubDate"])
                )
        signals.prune_history(history)
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
            f"Магазины: {markets}\n"
            + (f"База находок (CSV, JSON, HTML): "
               f"https://github.com/makeevmua/amazon-diamond-hunter/actions/runs/"
               f"{os.getenv('GITHUB_RUN_ID')} (раздел Artifacts)\n"
               if CATALOG_ENABLED and os.getenv("GITHUB_RUN_ID") else "")
            + "\n"
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
