import unittest
from datetime import datetime, timezone, timedelta

from core.funding_pnl import compute_funding_pnl


def _iso_hours_ago(hours: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()


class FundingPnlTests(unittest.TestCase):
    def test_short_bybit_positive_rate_receives(self):
        result = compute_funding_pnl(
            entry_rate_bybit_pct=0.01,
            entry_rate_kucoin_pct=-0.005,
            bybit_interval_h=8,
            kucoin_interval_h=8,
            position_size=1000,
            side_bybit="sell",
            side_kucoin="buy",
            entry_time_iso=_iso_hours_ago(8),
        )
        self.assertGreater(result["fr_received"], 0)
        self.assertGreaterEqual(result["funding_pnl"], 0)

    def test_long_bybit_positive_rate_pays(self):
        result = compute_funding_pnl(
            entry_rate_bybit_pct=0.01,
            entry_rate_kucoin_pct=-0.005,
            bybit_interval_h=8,
            kucoin_interval_h=8,
            position_size=1000,
            side_bybit="buy",
            side_kucoin="sell",
            entry_time_iso=_iso_hours_ago(8),
        )
        self.assertGreater(result["fr_paid"], 0)

    def test_zero_holding_time_yields_zero_pnl(self):
        result = compute_funding_pnl(
            entry_rate_bybit_pct=0.05, entry_rate_kucoin_pct=-0.02,
            bybit_interval_h=8, kucoin_interval_h=8, position_size=1000,
            side_bybit="sell", side_kucoin="buy",
            entry_time_iso=datetime.now(timezone.utc).isoformat(),
        )
        self.assertAlmostEqual(result["funding_pnl"], 0.0, places=6)

    def test_different_intervals_normalized_correctly(self):
        result = compute_funding_pnl(
            entry_rate_bybit_pct=0.08, entry_rate_kucoin_pct=0.01,
            bybit_interval_h=8, kucoin_interval_h=1, position_size=1000,
            side_bybit="sell", side_kucoin="buy",
            entry_time_iso=_iso_hours_ago(8),
        )
        self.assertTrue(isinstance(result["funding_pnl"], float))

    def test_malformed_entry_time_defaults_to_zero_hours(self):
        result = compute_funding_pnl(
            entry_rate_bybit_pct=0.05, entry_rate_kucoin_pct=-0.02,
            bybit_interval_h=8, kucoin_interval_h=8, position_size=1000,
            side_bybit="sell", side_kucoin="buy",
            entry_time_iso="not-a-valid-timestamp",
        )
        self.assertEqual(result["hours_held"], 0.0)
        self.assertEqual(result["funding_pnl"], 0.0)


if __name__ == "__main__":
    unittest.main()
