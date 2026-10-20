import json
import os
import sys
import unittest
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import parse_qs
from io import BytesIO

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import media
import bot


class Response:
    def __init__(self, body=b'{"ok":true}'):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return self.body


class MediaTests(unittest.TestCase):
    def test_only_three_approved_amazon_countries(self):
        brickset = sorted(url for url in bot.FEEDS
                          if "brickset.com/feed/amazon/" in url)
        self.assertEqual(brickset, [
            "https://brickset.com/feed/amazon/es",
            "https://brickset.com/feed/amazon/fr",
            "https://brickset.com/feed/amazon/it",
        ])
        self.assertFalse(any("/amazon/uk" in url or "/amazon/us" in url
                             for url in bot.FEEDS))
        self.assertTrue(bot.dealabs_link("https://brickset.com/article/123"))

    def test_media_thumbnail_from_only_this_item(self):
        rss = """<rss xmlns:media="http://search.yahoo.com/mrss/">
        <channel><item><title>LEGO Technic -55%</title>
        <link>https://brickset.com/article/123</link>
        <media:thumbnail url="https://img.example.org/products/lego.jpg"/>
        </item></channel></rss>"""
        parsed = bot.parse_feed(rss)
        self.assertEqual(parsed[0]["image"],
                         "https://img.example.org/products/lego.jpg")

    def test_rss_html_img_and_image_enclosure(self):
        rss = """<rss><channel><item>
        <title>Vase Bauhaus -30%</title>
        <link>https://www.dealabs.com/bons-plans/x</link>
        <description><![CDATA[
        <div><img src="https://cdn.example.org/pics/vase.webp"></div>
        ]]></description>
        </item></channel></rss>"""
        parsed = bot.parse_feed(rss)
        self.assertEqual(parsed[0]["image"], "https://cdn.example.org/pics/vase.webp")
        e = ET.fromstring("""<item><enclosure url="https://img.example.org/item.png"
                            type="image/png" /></item>""")
        self.assertEqual(media.rss_item_image(e),
                         "https://img.example.org/item.png")

    def test_page_image_only_open_graph_not_sidebar(self):
        article = """<html><head><meta property="og:image"
            content="/images/lego.jpg" /></head><body>
            <img src="https://example.org/ads/wrong-product.jpg"/>
            </body></html>"""
        self.assertEqual(
            media.page_og_image(article, "https://brickset.com/article/123"),
            "https://brickset.com/images/lego.jpg")
        self.assertIsNone(media.page_og_image(
            "<img src='https://ads.example.com/promo.jpg'>",
            "https://brickset.com/article/123"))

    def test_reject_untrustworthy_urls_and_generic_assets(self):
        invalid = (
            "http://example.com/item.jpg",
            "https://localhost/item.jpg",
            "https://127.0.0.1/item.jpg",
            "https://[::1]/item.png",
            "https://internal.local/item.jpg",
            "https://example.org/logo.png",
            "https://example.org/favicon.ico",
            "https://user:pass@example.org/product.jpg",
            "https://example.org:444/product.jpg",
            "file:///etc/passwd",
        )
        for value in invalid:
            with self.subTest(value=value):
                self.assertIsNone(media.safe_image_url(value))

    def test_telegram_sendphoto_has_caption_and_buttons(self):
        requests = []
        def fake_urlopen(request, timeout=15):
            requests.append(request)
            return Response()
        with patch.dict(os.environ, {
            "TELEGRAM_BOT_TOKEN": "not-a-real-token",
            "TELEGRAM_CHAT_ID": "98765",
        }), patch.object(bot, "urlopen", side_effect=fake_urlopen):
            bot.telegram_send(
                "LEGO Technic -55% | НЕ ПОДТВЕРЖДЕНО",
                buttons={"inline_keyboard": [[{
                    "text": "Проверить находку",
                    "url": "https://brickset.com/article/123",
                }]]},
                photo_url="https://images.example.org/lego.jpg",
            )
        self.assertEqual(len(requests), 1)
        self.assertTrue(requests[0].full_url.endswith("/sendPhoto"))
        fields = parse_qs(requests[0].data.decode())
        self.assertIn("photo", fields)
        self.assertIn("Фото из публикации", fields["caption"][0])
        self.assertEqual(json.loads(fields["reply_markup"][0])
                         ["inline_keyboard"][0][0]["text"], "Проверить находку")

    def test_photo_failed_uses_text_fallback_with_same_buttons(self):
        requests = []
        def fake_urlopen(request, timeout=15):
            requests.append(request)
            if request.full_url.endswith("/sendPhoto"):
                raise HTTPError(request.full_url, 400, "Bad Request", None,
                                BytesIO(b'{"description":"failed photo"}'))
            return Response()
        buttons = {"inline_keyboard": [[{
            "text": "Открыть на Amazon",
            "url": "https://amazon.fr/dp/B0B9C4DKKG",
        }]]}
        with patch.dict(os.environ, {
            "TELEGRAM_BOT_TOKEN": "fake",
            "TELEGRAM_CHAT_ID": "456",
        }), patch.object(bot, "urlopen", side_effect=fake_urlopen):
            bot.telegram_send(
                "Итоговое сообщение с предупреждением",
                buttons=buttons, photo_url="https://cdn.example.org/lego.webp"
            )
        self.assertEqual(len(requests), 2)
        self.assertTrue(requests[0].full_url.endswith("/sendPhoto"))
        self.assertTrue(requests[1].full_url.endswith("/sendMessage"))
        payload = parse_qs(requests[1].data.decode())
        self.assertEqual(payload["text"][0], "Итоговое сообщение с предупреждением")
        self.assertIn("reply_markup", payload)

    def test_real_scan_routes_item_photo_to_telegram(self):
        item = {
            "title": "LEGO Technic -60%",
            "description": "https://www.amazon.fr/dp/B0B9C4DKKG",
            "content": "", "pubDate": "",
            "link": "https://brickset.com/article/testlego",
            "key": "photo-test",
            "image": "https://img.example.org/technic.jpg",
        }
        sent = []
        with tempfile.TemporaryDirectory() as td, \
             patch.dict(os.environ, {
                 "TELEGRAM_BOT_TOKEN": "fake-token",
                 "TELEGRAM_CHAT_ID": "12345",
             }), \
             patch.object(bot, "STATE_FILE", Path(td) / "seen.json"), \
             patch.object(bot, "DRY_RUN", False), \
             patch.object(bot, "REPORT_HOUR", 99), \
             patch.object(bot, "telegram_send",
                          side_effect=lambda msg, buttons=None, photo_url=None:
                          sent.append((msg, buttons, photo_url))), \
             patch.object(bot, "collect", return_value=([item], 1, 1)):
            result = bot.run()
        self.assertEqual(result, 1)
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0][2], "https://img.example.org/technic.jpg")
        self.assertEqual(sent[0][1]["inline_keyboard"][0][0]["text"],
                         "Открыть на Amazon")

    def test_real_scan_uses_original_article_og_image_if_rss_missing(self):
        item = {
            "title": "LEGO Technic -60%",
            "description": "https://www.amazon.es/dp/B0B9C4DKKG",
            "content": "", "pubDate": "",
            "link": "https://brickset.com/article/testlego",
            "key": "photo-test-og",
            "image": None,
        }
        sent = []
        og_page = '<html><head><meta property="og:image" content="https://images.example.org/item.jpg" /></head></html>'
        with tempfile.TemporaryDirectory() as td, \
             patch.dict(os.environ, {
                 "TELEGRAM_BOT_TOKEN": "fake-token",
                 "TELEGRAM_CHAT_ID": "12345",
             }), \
             patch.object(bot, "STATE_FILE", Path(td) / "seen.json"), \
             patch.object(bot, "DRY_RUN", False), \
             patch.object(bot, "REPORT_HOUR", 99), \
             patch.object(bot, "fetch", return_value=og_page), \
             patch.object(bot, "telegram_send",
                          side_effect=lambda msg, buttons=None, photo_url=None:
                          sent.append((msg, buttons, photo_url))), \
             patch.object(bot, "collect", return_value=([item], 1, 1)):
            result = bot.run()
        self.assertEqual(result, 1)
        self.assertEqual(sent[0][2], "https://images.example.org/item.jpg")

    def test_long_caption_falls_back_to_text(self):
        requests = []
        def fake_urlopen(request, timeout=15):
            requests.append(request)
            return Response()
        with patch.dict(os.environ, {
            "TELEGRAM_BOT_TOKEN": "fake",
            "TELEGRAM_CHAT_ID": "456",
        }), patch.object(bot, "urlopen", side_effect=fake_urlopen):
            bot.telegram_send("A" * 1000,
                              photo_url="https://cdn.example.org/item.jpg")
        self.assertEqual(len(requests), 1)
        self.assertTrue(requests[0].full_url.endswith("/sendMessage"))


if __name__ == "__main__":
    unittest.main()
