"""Tests for RebalanceEngine."""

import math
import time
from unittest.mock import MagicMock

# ─── Helpers ────────────────────────────────────────────────────────

class FakePriceCache:
    def get_price(self, exchange, sym):
        if exchange == "bybit":
            return 0.033
        return 0.036

def make_position(sym="ESPOINTS", qty_bb=9093, qty_kc=9093,
                  entry_bb=0.033, entry_kc=0.033,
                  amount_usd=100, lev=3, pid="test123"):
    return {
        "id": pid,
        "symbol": sym,
        "qty_bybit": qty_bb,
        "qty_kucoin": qty_kc,
        "entry_price_bybit": entry_bb,
        "entry_price_kucoin": entry_kc,
        "amount_usd": amount_usd,
        "leverage": lev,
        "side_bybit": "short",
        "side_kucoin": "long",
    }

class FakePaper:
    def partial_close_leg(self, pid, exchange, close_qty, price, fee, **kw):
        return {"ok": True, "close_qty": close_qty, "exchange": exchange, "fee_paid": fee}
    def close_all_positions(self):
        return [{"realized_pnl": -5.0}]
    def get_balance(self): return 9500.0
    def get_bybit_balance(self): return 5000.0
    def get_kucoin_balance(self): return 5000.0

def dummy_notify(tp, msg): pass

# ─── Import ─────────────────────────────────────────────────────────

from core.rebalance_engine import RebalanceEngine

# === TEST 1: Delta drift trim ===
print("─── Test 1: Delta drift trigger trim ───")
eng = RebalanceEngine(FakePriceCache())
pos = make_position(qty_bb=9093, qty_kc=9093, entry_bb=0.033, entry_kc=0.033)
pos["entry_price_bybit"] = 0.033  # price only moved on kucoin (long)
pos["entry_price_kucoin"] = 0.036  # price rose 10% on Kucoin only -> notional mismatch
pos["qty_bybit"] = 9093
pos["qty_kucoin"] = 9093

# Force drift : KC notional = 9093*0.036=327.35, BB notional = 9093*0.033=300.07
# avg = 313.7, drift = 27.28/313.7*100 = 8.7%
drift_info = eng._compute_drift(pos, 0.033, 0.036)
print(f"  delta_pct = {drift_info['delta_pct']:.1f}%")
assert drift_info["delta_pct"] > 5.0, f"Expected delta > 5%, got {drift_info['delta_pct']}"
print(f"  larger_leg = {'bybit' if drift_info['bb_notional'] > drift_info['kc_notional'] else 'kucoin'}")
assert drift_info["bb_notional"] < drift_info["kc_notional"], "KC should be larger leg"
print("  ✅ PASS")

# === TEST 2: No drift ===
print("─── Test 2: No drift (balanced) ───")
pos2 = make_position()
drift2 = eng._compute_drift(pos2, 0.033, 0.033)
assert drift2["delta_pct"] < 0.01
print("  ✅ PASS")

# === TEST 3: Cooldown mechanism ===
print("─── Test 3: Cooldown blocks rebalance ───")
eng._last_rebalance_ts = time.time()  # just now
result = eng.check_and_rebalance(pos, FakePaper(), dummy_notify)
assert result is None, f"Expected None (cooldown), got {result}"
print("  ✅ PASS")

# === TEST 4: Emergency margin ===
print("─── Test 4: Emergency margin trigger ───")
eng._last_rebalance_ts = 0
pos3 = make_position(qty_bb=1000, qty_kc=1000, entry_bb=100, entry_kc=100, amount_usd=10)
# margin_ratio = 10/100000 = 0.01% -> below 5% emergency
result3 = eng.check_and_rebalance(pos3, FakePaper(), dummy_notify)
assert result3 is not None, "Should trigger emergency"
assert result3["action"] == "emergency_close", f"Expected emergency_close, got {result3.get('action')}"
print("  ✅ PASS")

# === TEST 5: get_status / get_last_rebalance_ts ===
print("─── Test 5: Status methods ───")
status = eng.get_status()
assert isinstance(status, dict)
assert "enabled" in status
assert "cooldown_remaining" in status
print(f"  enabled={status['enabled']}")
print("  ✅ PASS")

# === TEST 6: enable/disable ===
print("─── Test 6: Toggle ───")
eng.disable()
assert not eng.enabled
eng.enable()
assert eng.enabled
print("  ✅ PASS")

print("\n✅ ALL TESTS PASSED")