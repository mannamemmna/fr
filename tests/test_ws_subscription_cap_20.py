import unittest
from config import MAX_WS_SUBSCRIPTIONS


class WsSubscriptionCap20Tests(unittest.TestCase):
    def test_default_is_20(self):
        self.assertEqual(MAX_WS_SUBSCRIPTIONS, 20)

    def test_env_override_changes_value(self):
        import os
        os.environ["MAX_WS_SUBSCRIPTIONS"] = "50"
        import importlib
        import config
        importlib.reload(config)
        self.assertEqual(config.MAX_WS_SUBSCRIPTIONS, 50)
        del os.environ["MAX_WS_SUBSCRIPTIONS"]
        importlib.reload(config)


if __name__ == "__main__":
    unittest.main()
