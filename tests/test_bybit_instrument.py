import unittest
from unittest.mock import patch, MagicMock

from exchanges.bybit_live import BybitLiveClient


class BybitInstrumentStepTests(unittest.TestCase):
    def test_uses_actual_qty_step_from_exchange(self):
        client = BybitLiveClient("key", "secret")
        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = {
            "result": {"list": [{"lotSizeFilter": {"qtyStep": "10", "minOrderQty": "10"}}]}
        }
        with patch.object(client.session, "request", return_value=mock_response):
            step, min_qty = client._get_instrument_step("SOMEALT")
        self.assertEqual(step, 10.0)
        self.assertEqual(min_qty, 10.0)

    def test_falls_back_to_default_on_lookup_failure(self):
        client = BybitLiveClient("key", "secret")
        with patch.object(client.session, "request", side_effect=RuntimeError("network")):
            step, min_qty = client._get_instrument_step("SOMEALT")
        self.assertEqual(step, 0.001)
        self.assertEqual(min_qty, 0.001)

    def test_step_is_cached_after_first_lookup(self):
        client = BybitLiveClient("key", "secret")
        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = {
            "result": {"list": [{"lotSizeFilter": {"qtyStep": "1", "minOrderQty": "1"}}]}
        }
        with patch.object(client.session, "request", return_value=mock_response) as mock_req:
            client._get_instrument_step("BTC")
            client._get_instrument_step("BTC")
        mock_req.assert_called_once()


if __name__ == "__main__":
    unittest.main()
