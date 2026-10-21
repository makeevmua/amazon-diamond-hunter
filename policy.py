"""Strict product-scope rules shared by discovery, alerts and the storefront.

Match the *product being offered*, not page sidebar or unrelated brand mentions.
Only Xbox is removed from gaming; PlayStation, Nintendo and PC stay eligible.
Samsung is eligible only when the headline clearly describes an SSD.
"""
import re

XBOX = re.compile(
    r"(?i)\b(?:x[\s-]?box|xbox\s*game\s*pass|xbox\s*live|"
    r"series\s*[sx]\s*\(?xbox\)?|xb[\s-]?one)\b"
)
SAMSUNG = re.compile(r"(?i)\bsamsung\b")
GALAXY_PRODUCT = re.compile(
    r"(?i)\b(?:galaxy\s*(?:s\d{1,2}|a\d{1,2}|z\s*(?:flip|fold)|"
    r"tab|buds|watch|book|fit|ring|note\d*)|"
    r"coque\s+(?:pour\s+)?galaxy|case\s+(?:for\s+)?galaxy)\b"
)
SAMSUNG_SSD = re.compile(
    r"(?i)\b(?:ssd|nvme|m\.?2|solid[\s-]?state\s+drive|"
    r"disque\s*(?:dur\s*)?ssd|"
    r"(?:9100|990|980|970|870|860)\s*(?:pro|evo|qvo)|"
    r"t[579]\s*(?:shield|portable|ssd)|p3\s*plus)\b"
)
NON_SSD_SAMSUNG = re.compile(
    r"(?i)\b(?:galaxy|smartphone|t[ée]l[ée]phone|phone|tablet|tablette|"
    r"smartwatch|tv|t[ée]l[ée]viseur|monitor|moniteur|odyssey|"
    r"[ée]couteurs?|buds|earbuds|coque|housse|case|film\s+de\s+protection|"
    r"chargeur\s+(?:pour\s+)?(?:galaxy|samsung)|"
    r"powerbank|batterie\s+externe|micro\s*sd|carte\s+m[ée]moire|"
    r"lave[\s-]?linge|r[ée]frig[ée]rateur|aspirateur)\b"
)



# Hard product-title exclusions. Avoid scanning unrelated descriptions/sidebar.
# These rules intentionally preserve LEGO construction and PC/console games.
APPAREL = re.compile(
    r"(?i)\b(?:surchemise|chemises?|shirt|shirts|t[ -]?shirts?|"
    r"pulls?|pullovers?|sweatshirts?|sweaters?|hoodies?|"
    r"vestes?|manteaux?|blousons?|parkas?|doudounes?|"
    r"robes?|jupes?|pantalons?|jeans|shorts?|"
    r"chaussures?|baskets?|sneakers?|boots?|"
    r"casquettes?|écharpes?|echarpes?|"
    r"jackets?|coats?|trousers?|skirts?|dresses?|"
    r"hemden?|jacken?|hosen?|schuhe|kleider|pullover|"
    r"camisas?|camisetas?|vestidos?|pantalones|zapatos?|"
    r"giacche|magliette|pantaloni|scarpe|"
    r"vêtements?|vetements?|clothing|apparel|mode\s+homme|"
    r"mode\s+femme|fashion|streetwear|"
    r"boxers?|boxer[ -]?shorts?|cale[cç]ons?|culottes?|slips?|"
    r"sous[ -]?v[eê]tements?|underwear|underpants|"
    r"briefs?|trunks?|panties|lingerie|bralettes?|"
    r"soutiens?[ -]?gorges?|knickers|mutande|"
    r"calzoncillos?|ropa\s+interior|unterhosen?|unterw[aä]sche)\b"
)
TABLETOP_GAMES = re.compile(
    r"(?i)\b(?:jeux?\s+de\s+soci[ée]t[ée]|jeux?\s+de\s+plateau|"
    r"board\s*games?|tabletop\s+games?|"
    r"juegos?\s+de\s+mesa|juegos?\s+de\s+tablero|"
    r"gioc(?:o|hi)\s+da\s+tavolo|gesellschaftsspiele?|brettspiele?|"
    r"kartenspiele?|card\s+games?|juegos?\s+de\s+cartas|"
    r"jeux?\s+de\s+cartes|puzzles?)\b"
)


# No hair products or styling tools, regardless of discount or recognizable brand.
# Keep ordinary skin care and other user-approved categories untouched.
HAIR_PRODUCTS = re.compile(
    r"(?i)\b(?:masques?\s+capillaires?|cr[eè]mes?\s+capillaires?|"
    r"soins?\s+(?:pour\s+les\s+)?cheveux|"
    r"shampoings?|apr[eè]s[ -]?shampoings?|"
    r"conditioners?|hair\s*(?:masks?|care|oil|serums?|cream|"
    r"sprays?|treatments?|dryers?|straighteners?|curlers?|brushes?)|"
    r"hairbrush(?:es)?|hairdryer|"
    r"huile\s+(?:pour\s+les\s+)?cheveux|"
    r"coloration\s+capillaire|s[eè]che[ -]?cheveux|"
    r"lisseurs?|boucleurs?|brosses?\s+(?:[aà]\s+)?(?:cheveux|brushing)|"
    r"mascarillas?\s+capilares?|champ[uú]s?|acondicionadores?|"
    r"maschere?\s+(?:per\s+)?capelli|shampoo|"
    r"sp[uü]lungen?\s+f[uü]r\s+haare|haarmasken?)\b"
)

# Interior is a curated *aesthetic*, not a blanket home-goods category.
# We cannot infer visual style from merchant photos reliably: when the title
# lacks an explicit agreed-on style cue, exclude the generic home item.
INTERIOR_PRODUCTS = re.compile(
    r"(?i)\b(?:"
    r"paniers?|corbeilles?|cestas?|cestos?|laundry\s+(?:baskets?|hampers?)|"
    r"baskets?|hampers?|storage\s+bins?|"
    r"chaises?|fauteuils?|canap[eé]s?|tabourets?|"
    r"chairs?|armchairs?|sofas?|stools?|"
    r"sillas?|sillones?|sedie|sedia|poltrone?|"
    r"tables?|tavoli?|mesa|mesas|tische?|st[uü]hle?|sessel|"
    r"lampes?|luminaires?|abat[ -]?jour|lamps?|lampadas?|l[aá]mparas?|"
    r"leuchten?|tischlampen?|stehlampen?|"
    r"vases?|bougies?|bougeoirs?|candles?|candleholders?|"
    r"miroirs?|mirrors?|espejos?|specchi|"
    r"tapis|rugs?|carpets?|alfombras?|tappeti|"
    r"[eé]tag[eè]res?|shelves|shelving|biblioth[eè]ques?|"
    r"coussins?|cushions?|pillows?|rideaux?|curtains?|"
    r"assiettes?|vaisselle|tableware|ceramics?|c[eé]ramiques?|"
    r"meubles?|furniture|mobilier|home\s+decor|d[eé]coration"
    r")\b"
)
INTERIOR_DESIGN_CUES = re.compile(
    r"(?i)\b(?:bauhaus|memphis|nordic|nordique|nordico|n[oó]rdico|"
    r"scandinave|scandinavian|scandinavo|escandinavo|"
    r"modernist(?:e)?|modernismo|mid[ -]?century|"
    r"space[ -]?age|postmodern(?:e|ist)?|"
    r"sculptur\w*|g[eé]om[eé]triqu\w*|geometric\w*|geom[eé]tric\w*|"
    r"minimalis\w*|asym[eé]triqu\w*|organic\s+shape|"
    r"r[eé]tro|retro|seventies|eighties|"
    r"(?:19)?(?:60|70|80|90)s|(?:60|70|80|90)er|"
    r"pop[ -]?art|color[ -]?block|colour[ -]?block|"
    r"playful|funky|wavy|ondul[eé]s?|"
    r"tulipe|tulip|champignon|mushroom|"
    r"forme\s+(?:originale|insolite|g[eé]om[eé]trique)|"
    r"food[ -]?shaped|burger[ -]?(?:chair|table)|"
    r"vitra|kartell|verner\s+panton|panthella|panton|"
    r"flos|artemide|fermob|muuto|hay\s+design|"
    r"ikea|alessi|fatboy|usm\s+haller)\b"
)
# A gaming mouse mat is not a living-room rug.
TECHNICAL_MATS = re.compile(r"(?i)\b(?:tapis\s+(?:de\s+)?souris|mouse\s*pads?)\b")


def matches_interior_aesthetic(title):
    """Fail closed for generic furnishings; preserve explicit design styles."""
    title = re.sub(r"<[^>]*>", " ", str(title or ""))
    if TECHNICAL_MATS.search(title):
        return True
    if not INTERIOR_PRODUCTS.search(title):
        return True
    return bool(INTERIOR_DESIGN_CUES.search(title))


def is_allowed(title, description=""):
    """Conservatively classify the offer headline, not a whole merchant page.

    Use description only to catch explicit compatibility for ambiguous generic
    titles; avoid rejecting a PS5 deal just because comparisons mention Xbox.
    """
    title = re.sub(r"<[^>]*>", " ", str(title or ""))
    description = re.sub(r"<[^>]*>", " ", str(description or ""))
    if (APPAREL.search(title) or TABLETOP_GAMES.search(title)
            or HAIR_PRODUCTS.search(title)):
        return False
    if not matches_interior_aesthetic(title):
        return False
    if XBOX.search(title):
        return False
    if GALAXY_PRODUCT.search(title):
        return False
    if SAMSUNG.search(title):
        return bool(SAMSUNG_SSD.search(title) and not NON_SSD_SAMSUNG.search(title))
    # Generic accessory listing with explicit Xbox-only compatibility: filter.
    if XBOX.search(description) and re.search(
        r"(?i)\b(?:manette|controller|contr[oô]leur|headset|"
        r"accessoire|accessory|station\s+de\s+charge|"
        r"support\s+pour|cable\s+pour)\b", title
    ) and not re.search(r"(?i)\b(?:ps[345]|playstation|pc|ordinateur|switch)\b", title):
        return False
    return True
