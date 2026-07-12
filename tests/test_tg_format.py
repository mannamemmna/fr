import unittest

from core.tg_format import esc, b, i, code, pre, link, spoiler, kv, status_icon


class TgFormatTests(unittest.TestCase):
    def test_esc_escapes_angle_brackets_and_ampersand(self):
        self.assertEqual(esc("<script>"), "&lt;script&gt;")
        self.assertEqual(esc("A & B"), "A &amp; B")

    def test_esc_handles_non_string_input(self):
        self.assertEqual(esc(42), "42")
        self.assertEqual(esc(None), "None")
        self.assertEqual(esc(-12.3456), "-12.3456")

    def test_esc_leaves_markdown_style_chars_untouched(self):
        # The whole point of HTML mode: these never needed escaping in the
        # first place, unlike MarkdownV2.
        text = "Price: $19.99 (+2.5%) - great deal!"
        self.assertEqual(esc(text), text)

    def test_realistic_exchange_announcement_title_is_safe(self):
        risky = "Bybit will delist X<Y & Z tokens (rate < 5%)"
        result = esc(risky)
        self.assertNotIn("<Y", result)
        self.assertIn("&lt;Y", result)
        self.assertIn("&amp;", result)

    def test_b_wraps_and_escapes(self):
        self.assertEqual(b("BTC & ETH"), "<b>BTC &amp; ETH</b>")

    def test_code_wraps_and_escapes(self):
        self.assertEqual(code("<id>"), "<code>&lt;id&gt;</code>")

    def test_i_wraps_and_escapes(self):
        self.assertEqual(i("waiting..."), "<i>waiting...</i>")

    def test_link_escapes_both_text_and_url(self):
        result = link("click & go", "https://example.com/?a=1&b=2")
        self.assertIn("&amp;", result)
        self.assertTrue(result.startswith('<a href="'))

    def test_spoiler_wraps(self):
        self.assertEqual(spoiler("secret"), "<tg-spoiler>secret</tg-spoiler>")

    def test_kv_mono_default(self):
        self.assertEqual(kv("Balance", "$100.00"), "Balance: <code>$100.00</code>")

    def test_kv_non_mono(self):
        self.assertEqual(kv("Note", "hello & world", mono=False), "Note: hello &amp; world")

    def test_status_icon(self):
        self.assertEqual(status_icon(True), "🟢")
        self.assertEqual(status_icon(False), "🔴")


if __name__ == "__main__":
    unittest.main()
