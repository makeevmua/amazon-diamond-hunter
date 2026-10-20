import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bot

RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><item>
<title>Samsung SSD 990 PRO 2 To -70%</title>
<link>https://www.dealabs.com/bons-plans/ssd</link>
<guid>deal-100</guid>
<description><![CDATA[SSD NVMe -70%. https://www.amazon.fr/dp/B0B9C4DKKG?tag=demo]]></description>
</item></channel></rss>
"""


def post(title, link, description, key="test-1", pub_date=""):
    return {
        "title": title, "link": link, "description": description,
        "content": "", "key": key, "pubDate": pub_date,
    }


class DiamondTests(unittest.TestCase):
    def test_feed_and_discount(self):
        item = bot.parse_feed(RSS)[0]
        self.assertEqual(item["key"], "deal-100")
        self.assertEqual(bot.percent(item["title"], item["description"]), 70)
        self.assertTrue(bot.relevant(item["title"], item["description"]))

    def test_direct_amazon_all_three_marketplaces(self):
        for suffix in ("fr", "es", "it"):
            source = "https://www.amazon." + suffix + "/dp/B0B9C4DKKG?tag=demo"
            self.assertEqual(bot.find_amazon_link(source),
                             "https://amazon." + suffix + "/dp/B0B9C4DKKG")
        self.assertIsNone(bot.find_amazon_link("https://www.amazon.de/dp/B0B9C4DKKG"))
        self.assertIsNone(bot.find_amazon_link("https://evilamazon.fr/dp/B0B9C4DKKG"))
        self.assertIsNone(bot.find_amazon_link("https://amazon.fr.evil.com/dp/B0B9C4DKKG"))

    def test_brand_priority_and_categories(self):
        self.assertTrue(bot.PREFERRED_BRANDS.search("Apple iPad"))
        self.assertTrue(bot.relevant("Dyson aspirateur -65%", "aspirateur robot"))
        self.assertTrue(bot.relevant("Dyson aspirapolvere -65%", "casa"))
        self.assertTrue(bot.relevant("Sony auriculares -65%", "audio"))
        self.assertTrue(bot.relevant("Nintendo videogiochi -65%", "giochi"))
        self.assertFalse(bot.relevant("Robe pas cher -70%", "fashion"))

    def test_excludes_used_in_three_languages(self):
        for name in ("reconditionné", "reacondicionado", "segunda mano",
                     "ricondizionato", "usato", "occasion"):
            self.assertFalse(bot.relevant("Samsung SSD -70%", name))

    def test_only_trusted_source_pages(self):
        for url in ("https://www.dealabs.com/bons-plans/foo",
                    "https://www.chollometro.com/ofertas/foo",
                    "https://quantospendo.app/offerte/foo"):
            self.assertTrue(bot.dealabs_link(url), url)
        self.assertFalse(bot.dealabs_link("https://evil-chollometro.com/ofertas/foo"))
        self.assertFalse(bot.dealabs_link("https://www.dealabs.com.evil.com/foo"))

    def test_percentage_below_threshold(self):
        self.assertEqual(bot.percent("Samsung -72%", ""), 72)
        self.assertEqual(bot.percent("Samsung -40%", ""), 40)
        self.assertIsNone(bot.percent("Samsung -20%", ""))
        self.assertIsNone(bot.percent("Samsung jusqu'à -70%", ""))

    def test_interior_discount_from_25_others_still_50(self):
        interior = post("Lampe Bauhaus -30%",
                        "https://www.dealabs.com/bons-plans/lampe",
                        "https://www.amazon.fr/dp/B0B9C4DKKG",
                        key="int-30")
        tech = post("Samsung SSD -30%",
                    "https://www.dealabs.com/bons-plans/ssd-low",
                    "https://www.amazon.fr/dp/B0B9C4DKKH",
                    key="tech-30")
        interior_below = post("Vase Bauhaus -24%",
                              "https://www.dealabs.com/bons-plans/vase",
                              "https://www.amazon.fr/dp/B0B9C4DKKJ",
                              key="int-24")
        with patch.object(bot, "fetch", side_effect=AssertionError("unexpected fetch")):
            found = bot.candidates([interior, tech, interior_below])
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["discount"], 30)
        self.assertIn("design & interior", found[0]["category"])

    def test_multilingual_discount_and_price_pair(self):
        self.assertEqual(bot.percent("Lámpara descuento del 29%", ""), 29)
        self.assertEqual(bot.percent("Lampada sconto del 28%", ""), 28)
        self.assertEqual(bot.percent("Nordic lampe 25% off", ""), 25)
        self.assertEqual(bot.price_pair("75 € au lieu de 100 €"),
                         (25, 75.0, 100.0))

    def test_encoded_amazon_links_and_short_link_resolution(self):
        encoded = "https://www.dealabs.com/goto?u=https%3A%2F%2Fwww.amazon.it%2Fdp%2FB0B9C4DKKG"
        self.assertEqual(bot.find_amazon_link(encoded),
                         "https://amazon.it/dp/B0B9C4DKKG")
        with patch.object(bot, "resolve_shortlink", return_value="https://amazon.es/dp/B0B9C4DKKG") as resolve:
            self.assertEqual(bot.find_amazon_link("https://amzn.eu/d/testabc"),
                             "https://amazon.es/dp/B0B9C4DKKG")
            resolve.assert_called_once()
        self.assertFalse(bot.allowed_redirect_url("https://localhost/private"))
        self.assertFalse(bot.allowed_redirect_url("https://amazon.fr.evil.com/dp/B0B9C4DKKG"))
        self.assertTrue(bot.allowed_redirect_url("https://amzn.eu/d/x"))

    def test_rss_attribute_and_atom_links(self):
        rss = """<rss><channel><item><title>Bauhaus lamp -30%</title>
        <link>https://www.dealabs.com/bons-plans/house</link>
        <enclosure url="https://www.amazon.fr/dp/B0B9C4DKKG" />
        </item></channel></rss>"""
        feed = bot.parse_feed(rss)
        self.assertIn("https://www.amazon.fr/dp/B0B9C4DKKG", feed[0]["content"])
        atom = """<feed xmlns="http://www.w3.org/2005/Atom"><entry>
        <title>Bauhaus lamp -30%</title><link href="https://www.dealabs.com/bons-plans/x" />
        <id>example-1</id></entry></feed>"""
        data = bot.parse_feed(atom)
        self.assertEqual(data[0]["key"], "example-1")
        self.assertEqual(data[0]["link"], "https://www.dealabs.com/bons-plans/x")

    def test_editorial_source_trusted(self):
        self.assertTrue(bot.dealabs_link("https://www.lesnumeriques.com/soldes/bon-plan.html"))

    def test_fabric_percentages_are_not_deals(self):
        self.assertIsNone(bot.percent(
            "Pull col rond Bonobo Instinct - 70% coton", ""
        ))
        self.assertIsNone(bot.percent("Chemise - 60% polyester", ""))
        self.assertIsNone(bot.percent("Camicia - 55% cotone", ""))
        self.assertEqual(bot.percent("SSD Samsung -55%", ""), 55)

    def test_metadata_only_extracts_article_header(self):
        page = """<html><head><title>Promo lampe Nordic</title>
        <meta property="og:description"
        content="Lampe Nordic -30% chez Amazon"/></head>
        <body><aside>SSD -99% discount
        https://www.amazon.fr/dp/B0B9C4DKKJ</aside></body></html>"""
        metadata = bot.article_metadata(page)
        self.assertEqual(bot.percent(metadata, ""), 30)
        self.assertNotIn("99%", metadata)

    def test_ambiguous_article_does_not_choose_wrong_amazon(self):
        multiple = """<main>
            href="https://www.amazon.fr/dp/B0B9C4DKKG"
            href="https://www.amazon.fr/dp/B0B9C4DKKH"
        </main>"""
        self.assertIsNone(bot.find_article_amazon_link(multiple))
        single = """<main>
            href="https://www.amazon.es/dp/B0B9C4DKKG"
            href="https://www.amazon.es/dp/B0B9C4DKKG?tag=ads"
        </main>"""
        self.assertEqual(bot.find_article_amazon_link(single),
                         "https://amazon.es/dp/B0B9C4DKKG")

    def test_clothes_do_not_become_interior_via_description(self):
        item = post(
            "Pull col rond Bonobo Instinct - 70% coton",
            "https://www.dealabs.com/bons-plans/bonobo",
            "Promo -70% pour la table de salon; lampe design",
        )
        with patch.object(bot, "fetch", side_effect=AssertionError("unexpected fetch")):
            self.assertEqual(bot.candidates([item]), [])


        item = post("Lampade Nordic promo", "https://wrong-site.example/spam",
                    "Descuento! 10€")
        with patch.object(bot, "fetch", side_effect=AssertionError("untrusted fetch")) as f:
            self.assertEqual(bot.candidates([item]), [])
            f.assert_not_called()


        item = post("Samsung smartphone -70%",
                    "https://www.chollometro.com/ofertas/samsung",
                    "https://www.amazon.es/dp/B0B9C4DKKG")
        with patch.object(bot, "fetch", side_effect=AssertionError("unexpected fetch")):
            candidates = bot.candidates([item])
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["market"], "amazon.es")
        self.assertTrue(candidates[0]["amazon"].startswith("https://amazon.es/dp/"))

    def test_italian_home_appliances(self):
        item = post("Dyson aspirapolvere -70%",
                    "https://quantospendo.app/offerte/offerta1",
                    "https://www.amazon.it/dp/B0B9C4DKKG")
        with patch.object(bot, "fetch", side_effect=AssertionError("unexpected fetch")):
            found = bot.candidates([item])
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["market"], "amazon.it")

    def test_unexpected_diamond_requires_80_percent_and_recognized_brand(self):
        surprise = post("Bosch mystery product -80%",
                        "https://www.dealabs.com/bons-plans/odd",
                        "https://amazon.fr/dp/B0B9C4DKKG")
        plain = post("Unknown mystery product -80%",
                     "https://www.dealabs.com/bons-plans/odd2",
                     "https://amazon.fr/dp/B0B9C4DKKG")
        too_low = post("Bosch mystery product -75%",
                       "https://www.dealabs.com/bons-plans/odd3",
                       "https://amazon.fr/dp/B0B9C4DKKG")
        with patch.object(bot, "fetch", side_effect=AssertionError("unexpected fetch")):
            found = bot.candidates([surprise, plain, too_low])
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["category"], "Unexpected Diamond")

    def test_bauhaus_and_nordic_interior_categories(self):
        cases = [
            ("Bauhaus lamp -65%", "modernist Nordic style"),
            ("Fauteuil design sculptural -60%", "forme originale"),
            ("Mesa estilo IKEA años 90 -55%", "mesa de salón"),
            ("Lampada design Memphis -70%", "arredamento"),
            ("Chaise en forme de burger -60%", "meuble original"),
            ("Tapis scandinave -55%", "nordique"),
            ("Vase céramique moderniste -60%", "décoration"),
            ("Assiettes design -55%", "vaisselle"),
            ("Coussins modernes -55%", "textile"),
            ("Bougies sculpturales -55%", "déco"),
        ]
        for title, desc in cases:
            with self.subTest(title=title):
                self.assertIn("design & interior", bot.category_matches(title + " " + desc))
                self.assertTrue(bot.relevant(title, desc))

    def test_interior_style_boost_and_rustic_deprioritization(self):
        styled = post("Fauteuil Bauhaus -60%",
                      "https://www.dealabs.com/bons-plans/styled",
                      "https://amazon.fr/dp/B0B9C4DKKG")
        rustic = post("Fauteuil rustique -60%",
                      "https://www.dealabs.com/bons-plans/rustic",
                      "https://amazon.fr/dp/B0B9C4DKKH")
        with patch.object(bot, "fetch", side_effect=AssertionError("unexpected fetch")):
            found = bot.candidates([rustic, styled])
        self.assertEqual(len(found), 2)
        self.assertEqual(found[0]["title"], styled["title"])

    def test_four_added_category_filters(self):
        self.assertTrue(bot.relevant("LEGO Technic -50%", "set"))
        self.assertTrue(bot.relevant("Funko Pop figurine -65%", "collector"))
        self.assertTrue(bot.relevant("Philips rasoir -65%", "soin"))
        self.assertTrue(bot.relevant("Nordic vase -50%", "design"))
        self.assertFalse(bot.relevant("Bauhaus vase occasion -90%", "déjà utilisé"))

    def test_dry_run_deduplicates_and_tracks_state(self):
        with tempfile.TemporaryDirectory() as temp:
            with patch.object(bot, "STATE_FILE", Path(temp) / "seen.json"), \
                 patch.object(bot, "fetch", return_value=RSS), \
                 patch.object(bot, "DRY_RUN", True):
                self.assertEqual(bot.run(), 1)
                self.assertEqual(bot.run(), 0)
                seen = bot.get_seen()
                self.assertIn("__metadata__", seen)
                self.assertEqual(seen["__metadata__"]["daily_stats"][
                    next(iter(seen["__metadata__"]["daily_stats"]))]["scans"], 2)

    def test_source_only_candidate_requires_reachable_approved_source(self):
        item = post("Roborock Q7 L5+ -54%",
                    "https://www.lesnumeriques.com/aspirateur-robot/offer.html",
                    "Robot aspirateur laveur en promotion")
        with patch.object(bot, "fetch", return_value="<html><title>Offer</title></html>"):
            found = bot.candidates([item])
        self.assertEqual(len(found), 1)
        self.assertTrue(found[0]["source_only"])
        self.assertIsNone(found[0]["amazon"])
        self.assertEqual(found[0]["discount"], 54)
        self.assertTrue(found[0]["item"]["link"].startswith("https://www.lesnumeriques.com/"))

    def test_source_only_rejects_failed_or_unapproved_source(self):
        approved = post("Dyson aspirateur -55%",
                        "https://www.dealabs.com/bons-plans/test",
                        "Dyson aspirateur -55%")
        foreign = post("Dyson aspirateur -55%",
                       "https://www.chollometro.com/ofertas/offer",
                       "Dyson aspirateur -55%")
        with patch.object(bot, "fetch", side_effect=OSError("unavailable")):
            self.assertEqual(bot.candidates([approved]), [])
        with patch.object(bot, "fetch", return_value="<html><title>Offer</title></html>"):
            self.assertEqual(bot.candidates([foreign]), [])

    def test_source_only_alert_is_distinct_from_amazon_product_alert(self):
        item = post("Vase Bauhaus -30%",
                    "https://www.dealabs.com/bons-plans/vase-source",
                    "Vase design -30%", key="source123")
        direct = dict(item, description=item["description"] +
                      " https://www.amazon.fr/dp/B0B9C4DKKG")
        with tempfile.TemporaryDirectory() as temp, \
             patch.object(bot, "STATE_FILE", Path(temp) / "seen.json"), \
             patch.object(bot, "DRY_RUN", True), \
             patch.object(bot, "fetch", return_value="<html><title>Deal</title></html>"), \
             patch.object(bot, "collect", side_effect=[
                 ([item], 1, 1), ([item], 1, 1), ([direct], 1, 1),
                 ([direct], 1, 1)
             ]):
            self.assertEqual(bot.run(), 1)
            self.assertEqual(bot.run(), 0)
            self.assertEqual(bot.run(), 1)  # link upgrade allowed
            self.assertEqual(bot.run(), 0)

    def test_direct_alerts_first_and_source_only_per_scan_cap(self):
        items = [post(
            f"LEGO Technic set {n} -60%",
            f"https://www.dealabs.com/bons-plans/lego{n}",
            "LEGO construction toy -60%", key=f"unique{n}"
        ) for n in range(5)]
        direct = post("Samsung SSD -60%",
                      "https://www.dealabs.com/bons-plans/ssd-high",
                      "https://www.amazon.fr/dp/B0B9C4DKKG",
                      key="high")
        with tempfile.TemporaryDirectory() as temp, \
             patch.object(bot, "STATE_FILE", Path(temp) / "seen.json"), \
             patch.object(bot, "DRY_RUN", True), \
             patch.object(bot, "MAX_NOTIFICATIONS", 8), \
             patch.object(bot, "fetch", return_value="<html><title>Deal</title></html>"), \
             patch.object(bot, "collect", return_value=(items + [direct], 1, 1)):
            self.assertEqual(bot.run(), 4)
            saved = bot.get_seen()
            source_keys = [x for x in saved if x.startswith("source:")]
            self.assertEqual(len(source_keys), 3)
            self.assertIn("https://amazon.fr/dp/B0B9C4DKKG", saved)

    def test_configuration(self):
        self.assertEqual(tuple(bot.MARKETS),
                         ("amazon.fr", "amazon.es", "amazon.it"))
        self.assertEqual(bot.REPORT_HOUR, 21)


if __name__ == "__main__":
    unittest.main()
