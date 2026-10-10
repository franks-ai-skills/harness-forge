"""Forge's own source fetch: public HTTPS only, checked on every hop, no network in tests."""

import unittest

from harness_forge.fetch import FetchError, Response, fetch_text, page_text


class FakeWeb:
    """Resolves names from a table and answers requests by (host, path)."""

    def __init__(self, addresses, pages):
        self.addresses, self.pages, self.requests = addresses, pages, []

    def resolve(self, host):
        if host not in self.addresses:
            raise FetchError(f"cannot resolve {host}")
        return self.addresses[host]

    def get(self, host, address, path, timeout, limit):
        self.requests.append((host, address, path))
        return self.pages[(host, path)]


def page(body="<p>Platform support. The reproducer is supported only on Linux.</p>", status=200,
         content_type="text/html; charset=utf-8", location=None):
    headers = {"content-type": content_type}
    if location:
        headers["location"] = location
    return Response(status=status, headers=headers, body=body.encode("utf-8"))


class FetchTest(unittest.TestCase):
    def fetch(self, url, web):
        return fetch_text(url, resolve=web.resolve, get=web.get)

    def test_a_public_page_becomes_plain_text(self):
        web = FakeWeb({"docs.example.com": ["93.184.215.14"]},
                      {("docs.example.com", "/repro"): page("<html><script>steal()</script><h1>Platform support.</h1>"
                                                            "<p>The reproducer is <b>supported</b> only on Linux.</p>")})
        text = self.fetch("https://docs.example.com/repro", web)
        self.assertIn("Platform support. The reproducer is supported only on Linux.", text)
        self.assertNotIn("steal", text)
        self.assertEqual(web.requests, [("docs.example.com", "93.184.215.14", "/repro")])

    def test_only_https_urls_are_fetched(self):
        for url in ("http://docs.example.com/", "file:///etc/passwd", "ftp://docs.example.com/x",
                    "https://user:pw@docs.example.com/"):
            with self.subTest(url=url), self.assertRaises(FetchError):
                self.fetch(url, FakeWeb({}, {}))

    def test_private_and_special_destinations_are_refused(self):
        for address in ("127.0.0.1", "10.0.0.5", "172.16.0.1", "192.168.1.1", "169.254.169.254", "::1",
                        "fd00::1", "fe80::1", "0.0.0.0", "100.64.0.1", "224.0.0.1"):
            web = FakeWeb({"docs.example.com": [address]}, {("docs.example.com", "/"): page()})
            with self.subTest(address=address), self.assertRaises(FetchError):
                self.fetch("https://docs.example.com/", web)
            self.assertEqual(web.requests, [])

    def test_a_name_with_any_private_address_is_refused(self):
        web = FakeWeb({"docs.example.com": ["93.184.215.14", "10.0.0.5"]}, {("docs.example.com", "/"): page()})
        with self.assertRaises(FetchError):
            self.fetch("https://docs.example.com/", web)

    def test_every_redirect_is_checked_again(self):
        web = FakeWeb({"docs.example.com": ["93.184.215.14"], "internal.example.com": ["10.0.0.5"]},
                      {("docs.example.com", "/a"): page(status=302, location="https://internal.example.com/secret")})
        with self.assertRaises(FetchError):
            self.fetch("https://docs.example.com/a", web)
        self.assertEqual(len(web.requests), 1)

    def test_redirects_are_followed_a_few_times_only(self):
        pages = {("docs.example.com", f"/{n}"): page(status=301, location=f"/{n + 1}") for n in range(10)}
        web = FakeWeb({"docs.example.com": ["93.184.215.14"]}, pages)
        with self.assertRaises(FetchError):
            self.fetch("https://docs.example.com/0", web)
        self.assertLessEqual(len(web.requests), 4)

    def test_errors_and_other_content_types_are_refused(self):
        for response in (page(status=404), page(content_type="application/octet-stream")):
            web = FakeWeb({"docs.example.com": ["93.184.215.14"]}, {("docs.example.com", "/"): response})
            with self.subTest(status=response.status), self.assertRaises(FetchError):
                self.fetch("https://docs.example.com/", web)

    def test_page_text_keeps_quotes_findable(self):
        text = page_text("<div>Platform&nbsp;support.</div>\n<p>The   reproducer is\nsupported only on Linux.</p>")
        self.assertIn("Platform support. The reproducer is supported only on Linux.", text)


if __name__ == "__main__":
    unittest.main()
