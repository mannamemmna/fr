import unittest

from core.delisting_monitor import _extract_perp_symbol, _extract_multi_symbols


class DelistingParserTests(unittest.TestCase):
    def test_bybit_style_title(self):
        title = "Bybit will be delisting the AKROUSDT Perpetual Contract at Dec 24, 2024, 10:00AM UTC."
        self.assertEqual(_extract_perp_symbol(title), "AKRO")

    def test_kucoin_style_title(self):
        title = "KuCoin Futures Will Delist the NFPUSDT Perpetual Contract (2026-07-08)"
        self.assertEqual(_extract_perp_symbol(title), "NFP")

    def test_non_delisting_title_returns_none(self):
        title = "New Listing: Arbitrum (ARB) — Deposit, Trade and Stake ARB"
        self.assertIsNone(_extract_perp_symbol(title))

    def test_title_without_perpetual_keyword_returns_none(self):
        title = "Bybit will be delisting AKRO spot trading pair."
        self.assertIsNone(_extract_perp_symbol(title))

    def test_multi_symbol_st_style(self):
        title = (
            "ST: KuCoin Will Delist Certain Projects and Their Associated Trading Pairs"
        )
        desc = (
            "1. LSS, PBUX, CLAY, FREEDOG, GOATS will be delisted at 08:00:00 "
            "on November 19, 2025 (UTC)."
        )
        symbols = _extract_multi_symbols(title, desc)
        self.assertIn("LSS", symbols)
        self.assertIn("PBUX", symbols)
        self.assertNotIn("UTC", symbols)

    def test_stopwords_excluded(self):
        # "UTC" / "USDT" tidak boleh pernah lolos jadi simbol
        title = "Bybit will be delisting the USDT Perpetual Contract at 10AM UTC."
        self.assertIsNone(_extract_perp_symbol(title))


if __name__ == "__main__":
    unittest.main()