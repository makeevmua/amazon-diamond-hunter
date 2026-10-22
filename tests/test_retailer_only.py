"""Full regression: no clothes, board games, forum reposts or off-site links."""
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import alerts
import bot
import catalog
import policy
import retail_scope


def native(title, link="https://amazon.fr/dp/B0B9C4DKKG"):
    return {
        "title":title, "description":"", "content":"", "link":link,
        "key":link, "pubDate":"", "image":"",
        "direct_source":"Bright Data Amazon Search",
        "direct_current_eur":59.99, "direct_claimed_percent":70,
    }


class RetailerOnlyTests(unittest.TestCase):
    def test_excluded_product_types_from_all_sources(self):
        bad = [
            "Surchemise Boutonnée Homme Bonobo Instinct - Marron Foncé et Gris Foncé",
            "Levi's jeans homme -80%", "Chaussures Nike -60%",
            "[Vente privée] Jeu de société - 1902 : Méliès",
            "Board Game Catan", "Gioco da tavolo", "Gesellschaftsspiel",
            "Samsung Galaxy S26 Ultra", "Xbox Series X",
            "CALVIN KLEIN Icon Boxers coton stretch",
            "FINO Masque capillaire Premium Touch 230 g",
            "JOYFURNOS Grand Panier à Linge en Bambou",
        ]
        for title in bad:
            with self.subTest(title=title):
                self.assertFalse(policy.is_allowed(title))
        for title in [
            "LEGO Ninjago Creator 50% - online",
            "Marvel Spider-Man 2 Jeu PS5",
            "PC Gamer RTX 5080", "Samsung SSD 990 PRO 2 TB",
            "Nintendo Switch 2 console", "Lampe Bauhaus design",
        ]:
            with self.subTest(title=title):
                self.assertTrue(policy.is_allowed(title))

    def test_reported_joybuy_products_never_become_alerts_or_catalog_entries(self):
        import direct_pipeline
        bad = (
            "CALVIN KLEIN Icon Boxers coton stretch",
            "JOYFURNOS Grand Panier à Linge en Bambou",
            "FINO Masque capillaire Premium Touch 230 g",
        )
        for title in bad:
            with self.subTest(title=title):
                offer = native(title, "https://www.joybuy.fr/dp/sample-deal/102762790")
                offer["direct_source"] = "Joybuy Direct"
                offer["direct_claimed_percent"] = 70
                self.assertIsNone(
                    direct_pipeline.to_candidate(offer, bot.category_matches))
                with patch.dict(os.environ, {"RETAILER_ONLY": "true"}):
                    rows = catalog.collect_records(
                        [offer], [], bot.category_matches, bot.percent,
                        bot.price_pair, lambda _: 10,
                        lambda _: False, lambda _: True,
                        bot.find_amazon_link)
                self.assertEqual(rows, [])

    def test_only_exact_product_pages_on_approved_shops(self):
        good = {
            "https://amazon.fr/dp/B0B9C4DKKG":"Amazon",
            "https://www.amazon.de/dp/B0B9C4DKKG?th=1":"Amazon",
            "https://www.cdiscount.com/informatique/disques-durs/f-1070301-ssdpromo.html":"Cdiscount",
            "https://m.joybuy.fr/dp/marvels-wolverine-edition-standard/102816564":"Joybuy",
            "https://www.joybuy.fr/dp/manette-ps5/102762790?requestIdentity=productDetail":"Joybuy",
        }
        for url, expected in good.items():
            with self.subTest(url=url):
                self.assertEqual(retail_scope.shop(url),expected)
        invalid = [
            "https://dealabs.com/bons-plans/ssd", "https://www.bonoboplanet.com/product/bonobo",
            "https://cdiscount.com/jeux-pc-video-console/ps5/l-1035002.html",
            "https://www.joybuy.fr/explore/consoles/mPT6DnWB",
            "https://www.joybuy.fr/blog/ps5-docking/abc123",
            "https://amazon.fr.evil.example/dp/B0B9C4DKKG",
            "http://amazon.fr/dp/B0B9C4DKKG",
            "https://www.amazon.fr/s?k=ssd", "https://joybuy.fr/",
        ]
        for url in invalid:
            with self.subTest(url=url):
                self.assertIsNone(retail_scope.shop(url))

    def test_approved_headphones_and_funko_under_25_percent_rule(self):
        import direct_pipeline
        for name in (
            "Écouteurs Bluetooth 5.4 sport avec crochets anti-chute",
            "Écouteurs Bluetooth sport ENC 4 micros",
            "Funko Pop Rides Max Verstappen with RB20",
        ):
            with self.subTest(name=name):
                item = native(name, "https://amazon.fr/dp/B0DGB8L5XY")
                item["direct_claimed_percent"] = 25
                self.assertIsNotNone(
                    direct_pipeline.to_candidate(item,bot.category_matches))
                item["direct_claimed_percent"] = 24
                self.assertIsNone(
                    direct_pipeline.to_candidate(item,bot.category_matches))

    def test_direct_source_required_for_retail_products(self):
        self.assertTrue(retail_scope.direct_post(native("Samsung SSD 990 PRO")))
        fake=native("SSD Samsung 990", "https://www.dealabs.com/bons-plans/ssd")
        self.assertFalse(retail_scope.direct_post(fake))
        no_provenance=native("Samsung SSD 990 PRO")
        no_provenance.pop("direct_source")
        self.assertFalse(retail_scope.direct_post(no_provenance))
        joy=native("SSD Lexar", "https://www.joybuy.fr/dp/ssd-lexar/102762790")
        joy["direct_source"]="Joybuy Direct"
        self.assertTrue(retail_scope.direct_post(joy))
        cdiscount=native("Manette PS5", "https://www.cdiscount.com/jeux-pc-video-console/f-10350-ps5manette.html")
        cdiscount["direct_source"]="Cdiscount Direct"
        self.assertTrue(retail_scope.direct_post(cdiscount))

    def test_legacy_sqlite_purged_and_static_site_only_shop_links(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/"catalog.sqlite"
            target=Path(temp)/"web"
            fake_c={}
            raw={
                "source_url":"https://www.dealabs.com/bons-plans/bonobo",
                "source":"www.dealabs.com",
                "title":"Chaise Bauhaus design",
                "category":"collectibles", "claimed_discount":60,
                "advertised_price_eur":23.99,
                "amazon_product_url":None, "price_scope":"unverified_public_offer",
                "state":"watchlist", "image_url":"",
            }
            raw2={**raw, "title":"LEGO Ninjago Online",
                  "source_url":"https://www.dealabs.com/bons-plans/lego",
                  "amazon_product_url":"https://amazon.fr/dp/B0B9C4DKKG"}
            with patch.dict(os.environ, {"RETAILER_ONLY":"false"}):
                catalog.save_records([raw,raw2],path)
            self.assertEqual(sqlite3.connect(path).execute("SELECT count(*) FROM offers").fetchone()[0],2)
            with patch.dict(os.environ, {"RETAILER_ONLY":"true"}):
                product=native("Samsung SSD 990 PRO NVMe")
                rows=catalog.collect_records(
                    [product, {"title":"Jeu de société Catan","link":"https://www.dealabs.com/bons-plans/catan",
                               "key":"https://www.dealabs.com/bons-plans/catan","description":"","content":"","pubDate":"","image":""}],
                    [],bot.category_matches,bot.percent,bot.price_pair,lambda title:None,
                    lambda title:False,lambda date:True,bot.find_amazon_link
                )
                self.assertEqual(len(rows),1)
                catalog.save_records(rows,path)
                count=sqlite3.connect(path).execute("SELECT count(*) FROM offers").fetchone()[0]
                self.assertEqual(count,1)
                exported=catalog.export(path,target)
                self.assertEqual(exported["exported"],1)
                html=(target/"index.html").read_text("utf8")
                self.assertIn("Открыть на Amazon",html)
                self.assertNotIn("Объявление ↗",html)
                self.assertNotIn("Chaise Bauhaus",html)
                offers=json.loads((target/"finds.json").read_text("utf8"))["offers"]
                self.assertEqual([x["title"] for x in offers],["Samsung SSD 990 PRO NVMe"])

    def test_telegram_no_forum_button_in_strict_mode(self):
        with patch.dict(os.environ, {"RETAILER_ONLY":"true"}):
            buttons=alerts.deal_buttons(
                "https://amazon.fr/dp/B0B9C4DKKG",
                "https://dealabs.com/bons-plans/ssd"
            )
        self.assertEqual(len(buttons["inline_keyboard"]),1)
        self.assertEqual(buttons["inline_keyboard"][0][0]["text"],"Открыть на Amazon")


if __name__=="__main__":
    unittest.main()
