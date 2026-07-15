"""Rate limiter tests — usage warning, refill correctness, lock-not-held-during-sleep."""

import threading
import time
import unittest
from unittest.mock import patch

from core.rate_limiter import RateLimiter, get_limiter


class RateLimiterUsageWarningTests(unittest.TestCase):
    """Bug A1: usage warning was inverted (warned when usage LOW, silent when HIGH)."""

    def test_warns_when_usage_genuinely_high(self):
        with patch.object(RateLimiter, "_refill", lambda self: None):
            rl = RateLimiter("test", rate=10, warn_pct=0.8)
            # Drain 9 of 10 tokens → 90% usage
            for _ in range(9):
                rl.acquire(block=False)
            with self.assertLogs("fr-bot.rate_limiter", level="WARNING") as cm:
                rl.acquire(block=False)  # 10th token → 100% usage
            self.assertTrue(any("usage at 100%" in msg for msg in cm.output))

    def test_silent_when_usage_genuinely_low(self):
        with patch.object(RateLimiter, "_refill", lambda self: None):
            rl = RateLimiter("test", rate=10, warn_pct=0.8)
            rl.acquire(block=False)  # 10% usage
            # Should NOT log a warning at 10% usage
            logger = rl.name
            import logging
            logging.getLogger("fr-bot.rate_limiter").setLevel(logging.WARNING)
            # Use assertNoLogs (Python 3.10+)
            import sys
            if sys.version_info >= (3, 10):
                with self.assertNoLogs("fr-bot.rate_limiter", level="WARNING"):
                    rl.acquire(block=False)  # 20% usage — still below 80%

    def test_usage_formula_is_consumed_not_remaining(self):
        """Directly test the formula: usage = (rate - tokens) / rate."""
        with patch.object(RateLimiter, "_refill", lambda self: None):
            rl = RateLimiter("test", rate=10, warn_pct=0.5)
            # Drain 6 tokens → 60% consumed, 40% remaining
            for _ in range(6):
                rl.acquire(block=False)
            # Now tokens=4, usage should be 0.6 >= 0.5 → should warn
            with self.assertLogs("fr-bot.rate_limiter", level="WARNING") as cm:
                rl.acquire(block=False)  # 7th → 70% usage
            self.assertTrue(any("usage at 70%" in msg for msg in cm.output))


class RateLimiterRefillTests(unittest.TestCase):
    """Bug A2: blocking wait reset to FULL capacity instead of refilling correctly."""

    def test_blocking_acquire_does_not_jump_to_full_capacity(self):
        """A blocking acquire on a drained limiter should NOT reset to full."""
        rl = RateLimiter("test", rate=5, warn_pct=0.99)
        # Drain all tokens
        for _ in range(5):
            rl.acquire(block=False)
        self.assertLess(rl._tokens, 0.01)

        # Block for one token. With the bug, it would reset to rate=5 then
        # subtract 1 → 4 tokens. With the fix, it should sleep and earn back
        # ~1 token (or slightly more due to elapsed time), but NOT 4.
        # Patch sleep to be instant so we don't slow the test down
        with patch("core.rate_limiter.time.sleep"):
            rl.acquire(block=True)

        # After the fix: tokens should be well below 4 (the buggy value).
        # With instant sleep, _refill sees ~0 elapsed time → tokens ≈ 0.
        # But since sleep is patched to 0, we loop back and _refill computes
        # based on real monotonic time (which did pass slightly). The key
        # assertion is: NOT >= 4 (which would indicate the over-refill bug).
        self.assertLess(rl._tokens, 4.0)

    def test_blocking_acquire_eventually_succeeds_with_real_time(self):
        """With real time passing, a blocking acquire should succeed."""
        rl = RateLimiter("test", rate=20, warn_pct=0.99)  # 20 tokens/sec
        # Drain
        for _ in range(20):
            rl.acquire(block=False)
        self.assertFalse(rl.acquire(block=False))

        # Block — should succeed after ~50ms (1/20)
        result = rl.acquire(block=True)
        self.assertTrue(result)


class RateLimiterLockTests(unittest.TestCase):
    """Bug A3: time.sleep() ran while holding the lock, blocking all other threads."""

    def test_lock_not_held_during_sleep(self):
        """Thread A blocks on a drained limiter; Thread B (sharing the same
        limiter) should be able to acquire the lock and get a prompt answer
        rather than stalling for Thread A's full wait."""
        rl = RateLimiter("test_lock", rate=2, warn_pct=0.99)
        # Drain
        for _ in range(2):
            rl.acquire(block=False)

        # Thread A will try to acquire(block=True) and should sleep ~0.5s
        # We patch sleep to make it a controllable barrier
        sleep_event = threading.Event()

        def fake_sleep(seconds):
            sleep_event.set()
            sleep_event.wait(timeout=2)

        results = {}

        def thread_a():
            with patch("core.rate_limiter.time.sleep", fake_sleep):
                results["a"] = rl.acquire(block=True)

        def thread_b():
            # Wait until thread A is sleeping (lock released)
            sleep_event.wait(timeout=2)
            # Now try to acquire — should NOT stall for A's full wait
            # With the bug, this would block for A's entire sleep duration
            start = time.monotonic()
            results["b"] = rl.acquire(block=False)
            results["b_time"] = time.monotonic() - start

        t_a = threading.Thread(target=thread_a)
        t_b = threading.Thread(target=thread_b)
        t_a.start()
        t_b.start()
        t_b.join(timeout=3)
        # Release thread A
        sleep_event.clear()
        t_a.join(timeout=2)

        self.assertTrue(results.get("a", False))
        # Thread B should have gotten a fast answer (False = no tokens),
        # but crucially it should NOT have waited ~0.5s
        self.assertIn("b_time", results)
        self.assertLess(results["b_time"], 0.3)  # way less than 0.5s


class RateLimiterRegressionTests(unittest.TestCase):
    """Regression guards for ordinary (non-throttled) behavior."""

    def test_immediate_success_when_tokens_available(self):
        rl = RateLimiter("test", rate=10)
        self.assertTrue(rl.acquire(block=False))

    def test_non_blocking_failure_when_drained(self):
        rl = RateLimiter("test", rate=2)
        rl.acquire(block=False)
        rl.acquire(block=False)
        self.assertFalse(rl.acquire(block=False))

    def test_context_manager_usage(self):
        rl = RateLimiter("test", rate=10)
        with rl:
            self.assertTrue(True)  # just shouldn't raise

    def test_stats_shape(self):
        rl = RateLimiter("test", rate=5)
        rl.acquire(block=False)
        s = rl.stats
        self.assertEqual(s["name"], "test")
        self.assertEqual(s["rate"], 5)
        self.assertEqual(s["total_calls"], 1)


if __name__ == "__main__":
    unittest.main()