"""Offline checks for the isolated, not-yet-published Alice Pro landing page."""

from html.parser import HTMLParser
from pathlib import Path
import unittest
from urllib.parse import urlsplit
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / "website"


class Document(HTMLParser):
    def __init__(self, text):
        super().__init__(convert_charrefs=True)
        self.elements = []
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        self.elements.append((tag, dict(attrs)))


class WebsiteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = (SITE / "index.html").read_text(encoding="utf-8")
        cls.elements = Document(cls.text).elements

    def test_semantics_and_accessible_navigation(self):
        for tag in ("html", "title", "h1", "main", "header", "footer", "nav"):
            self.assertEqual(sum(t == tag for t, _ in self.elements), 1, tag)
        self.assertIn(("html", {"lang": "ru"}), self.elements)
        ids = [a["id"] for _, a in self.elements if "id" in a]
        self.assertEqual(len(ids), len(set(ids)))
        for _, attrs in self.elements:
            for reference in attrs.get("aria-labelledby", "").split():
                self.assertIn(reference, ids)
            if attrs.get("href", "").startswith("#"):
                self.assertIn(attrs["href"][1:], ids)
        self.assertTrue(
            any(a.get("id") == "english" and a.get("lang") == "en" for _, a in self.elements)
        )
        self.assertTrue(
            any(
                a.get("class") == "skip-link" and a.get("href") == "#main" for _, a in self.elements
            )
        )

    def test_local_assets_and_small_static_payload(self):
        self.assertFalse(any(t in ("script", "iframe", "form") for t, _ in self.elements))
        for tag, attrs in self.elements:
            if tag == "link":
                href = attrs["href"]
                self.assertTrue(href.startswith("./"), href)
                self.assertTrue((SITE / href).is_file(), href)
        self.assertLess(sum(p.stat().st_size for p in SITE.iterdir() if p.is_file()), 40_000)
        self.assertTrue((SITE / ".nojekyll").is_file())

    def test_review_draft_cannot_claim_a_publication_url(self):
        meta = {a.get("name"): a.get("content") for t, a in self.elements if t == "meta"}
        self.assertIn("noindex", meta["robots"])
        self.assertTrue(meta["description"])
        self.assertIn("width=device-width", meta["viewport"])
        self.assertFalse(any(a.get("rel") == "canonical" for _, a in self.elements))
        self.assertFalse((SITE / "CNAME").exists())
        self.assertFalse((SITE / "sitemap.xml").exists())
        for unapproved in ("maxxxpavlov.ru", "maxxxpavlov.online", "example.invalid"):
            self.assertNotIn(unapproved, self.text)
        robots = (SITE / "robots.txt").read_text()
        self.assertIn("Allow: /", robots)
        self.assertFalse(any(line.startswith("Sitemap:") for line in robots.splitlines()))
        template = ET.parse(ROOT / "docs/deployment/alice-pro-sitemap.xml.example")
        loc = template.find(".//{http://www.sitemaps.org/schemas/sitemap/0.9}loc")
        self.assertEqual(loc.text, "https://example.invalid/")

    def test_external_links_point_to_the_real_repository(self):
        links = [a["href"] for t, a in self.elements if t == "a" and not a["href"].startswith("#")]
        self.assertIn("https://github.com/maksimp6/Chat", links)
        for link in links:
            parsed = urlsplit(link)
            self.assertEqual(parsed.scheme, "https")
            self.assertEqual(parsed.netloc, "github.com")
            self.assertTrue(
                parsed.path == "/maksimp6/Chat" or parsed.path.startswith("/maksimp6/Chat/")
            )


if __name__ == "__main__":
    unittest.main()
