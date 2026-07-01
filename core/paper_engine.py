"""Paper Trading Engine — simulated execution for funding rate arbitrage.

Mirrors 100% of the live execution logic but operates on virtual balances
and simulated order fills. All positions, PnL, and execution logs follow
the same schema as live trades.

When PAPER_MODE=false in config, this module is bypassed and the real
executor/portfolio modules are used instead.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from config import DATA_DIR, PAPER_INITIAL_BALANCE, REBALANCE_PAPER_FEE_PCT, REBALANCE_PAPER_DELAY_SEC
from core.scanner import read_opportunities

log = logging.getLogger("paper_engine")

PORTFOLIO_FILE = os.path.join(DATA_DIR, "portfolio.json")
EXEC_LOG_FILE = os.path.join(DATA_DIR, "execution_log.jsonl")
STATE_FILE = os.path.join(DATA_DIR, "paper_state.json")

BYBIT_TAKER_FEE = 0.00055  # 0.055% per leg
KUCOIN_TAKER_FEE = 0.0006  # 0.060% per leg


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _utcnow_ts() -> float:
    return time.time()


# ─── Paper Engine ─────────────────────────────────────────────────────────


class PaperEngine:
    """Simulated trading engine for funding rate arbitrage.

    Maintains virtual USDT balance, tracks paper positions with entry/exit
    prices, and computes PnL identically to the live portfolio module.
    """

    def __init__(self):
        self._lock = threading.RLock()
        self._positions: List[Dict[str, Any]] = []
        self._balance_bybit = PAPER_INITIAL_BALANCE / 2.0
        self._balance_kucoin = PAPER_INITIAL_BALANCE / 2.0
        self._realized_pnl = 0.0
        self._total_fees = 0.0
        self._total_funding_pnl = 0.0
        self._closed_positions: List[Dict[str, Any]] = []
        self._load_state()

    # ─── State Persistence ────────────────────────────────────────────────

    def _load_state(self):
        # Try combined format first (post-fix), then fall back to legacy split files
        if os.path.exists(STATE_FILE):
            try:
                with open(STATE_FILE) as f:
                    state = json.load(f)
                self._balance_bybit = float(state.get("balance_bybit", PAPER_INITIAL_BALANCE / 2))
                self._balance_kucoin = float(state.get("balance_kucoin", PAPER_INITIAL_BALANCE / 2))
                self._realized_pnl = float(state.get("realized_pnl", 0))
                self._total_fees = float(state.get("total_fees", 0))
                self._total_funding_pnl = float(state.get("total_funding_pnl", 0))
                # Combined format: positions stored in same file
                if "positions" in state:
                    self._positions = state.get("positions", [])
                    self._closed_positions = state.get("closed_positions", [])
                    return
            except (json.JSONDecodeError, OSError, ValueError):
                pass

        # Legacy: load portfolio separately
        self._load_portfolio()

    def _save_state(self):
        """Legacy single-file save (balance only). Kept for compatibility."""
        state = {
            "balance_bybit": self._balance_bybit,
            "balance_kucoin": self._balance_kucoin,
            "realized_pnl": self._realized_pnl,
            "total_fees": self._total_fees,
            "total_funding_pnl": self._total_funding_pnl,
            "saved_at": _utcnow_iso(),
        }
        tmp = STATE_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(state, f, indent=2)
        os.replace(tmp, STATE_FILE)

    def _load_portfolio(self):
        if not os.path.exists(PORTFOLIO_FILE):
            self._positions = []
            return
        try:
            with open(PORTFOLIO_FILE) as f:
                data = json.load(f)
            self._positions = data.get("positions", [])
            self._closed_positions = data.get("closed_positions", [])
        except (json.JSONDecodeError, OSError):
            self._positions = []
            self._closed_positions = []

    def _save_portfolio(self):
        with self._lock:
            data = {
                "positions": self._positions,
                "closed_positions": self._closed_positions,
                "saved_at": _utcnow_iso(),
            }
            tmp = PORTFOLIO_FILE + ".tmp"
            with open(tmp, "w") as f:
                json.dump(data, f, indent=2)
            os.replace(tmp, PORTFOLIO_FILE)

    def _save_all(self):
        """Atomic save of both portfolio + state in a single file.

        Prevents desync if the bot crashes between two separate writes.
        """
        with self._lock:
            data = {
                "balance_bybit": self._balance_bybit,
                "balance_kucoin": self._balance_kucoin,
                "realized_pnl": self._realized_pnl,
                "total_fees": self._total_fees,
                "total_funding_pnl": self._total_funding_pnl,
                "positions": self._positions,
                "closed_positions": self._closed_positions,
                "saved_at": _utcnow_iso(),
            }
            tmp = STATE_FILE + ".tmp"
            with open(tmp, "w") as f:
                json.dump(data, f, indent=2)
            os.replace(tmp, STATE_FILE)

    def _log_execution(self, entry: dict):
        with open(EXEC_LOG_FILE, "a") as f:
            f.write(json.dumps(entry, default=str) + "\n")

    # ─── Execution ────────────────────────────────────────────────────────

    def execute_instant(
        self,
        symbol: str,
        amount_usd: float,
        side_bybit: str,
        side_kucoin: str,
        leverage: int = 2,
    ) -> Dict[str, Any]:
        """Execute a paper trade — both legs simultaneously.

        Uses current mark prices from the latest scan to simulate fills.
        Deducts taker fees and records the position.

        leverage: position multiplier (1–20x). Position size = amount_usd × leverage.
        Collateral used = amount_usd (margin), position = amount_usd × leverage.
        """
        task_id = str(uuid.uuid4())
        started_at = _utcnow_iso()
        ts_start = _utcnow_ts()

        leverage = max(1, min(leverage, 20))
        position_size = amount_usd * leverage

        # Validation
        errors: List[str] = []
        side_bybit = side_bybit.lower()
        side_kucoin = side_kucoin.lower()
        if side_bybit not in ("buy", "sell"):
            errors.append(f"invalid side_bybit: {side_bybit}")
        if side_kucoin not in ("buy", "sell"):
            errors.append(f"invalid side_kucoin: {side_kucoin}")
        if amount_usd <= 0:
            errors.append("amount must be positive")

        if amount_usd > self.get_balance():
            errors.append(
                f"insufficient paper balance: need ${amount_usd:.2f} margin, have ${self.get_balance():.2f}"
            )

        # Get current prices from latest scan
        opp = self._get_opportunity(symbol)
        if not opp:
            errors.append(f"symbol '{symbol}' not found in latest scan")

        if errors:
            result = {
                "task_id": task_id,
                "mode": "instant",
                "symbol": symbol,
                "amount_usd": amount_usd,
                "side_bybit": side_bybit,
                "side_kucoin": side_kucoin,
                "status": "failed",
                "errors": errors,
                "started_at": started_at,
                "finished_at": _utcnow_iso(),
                "duration_seconds": round(_utcnow_ts() - ts_start, 3),
            }
            self._log_execution(result)
            return result

        # Simulate fills
        bb_price = opp.get("bybit_mark") or opp.get("price", 0)
        kc_price = opp.get("kucoin_mark") or opp.get("price", 0)
        qty_bybit = position_size / max(bb_price, 0.0001)
        qty_kucoin = position_size / max(kc_price, 0.0001)

        fee_bybit = position_size * BYBIT_TAKER_FEE
        fee_kucoin = position_size * KUCOIN_TAKER_FEE
        fee = fee_bybit + fee_kucoin
        margin_per_ex = amount_usd / 2.0
        self._balance_bybit -= margin_per_ex + fee_bybit
        self._balance_kucoin -= margin_per_ex + fee_kucoin
        self._total_fees += fee

        # Record position
        position = {
            "id": task_id,
            "symbol": symbol,
            "unified_symbol": opp.get("unified_symbol", f"{symbol}/USDT:USDT"),
            "side_bybit": side_bybit,
            "side_kucoin": side_kucoin,
            "amount_usd": amount_usd,
            "position_size": round(position_size, 2),
            "leverage": leverage,
            "qty_bybit": round(qty_bybit, 8),
            "qty_kucoin": round(qty_kucoin, 8),
            "quantity": round(qty_bybit, 8),  # backward compat
            "entry_price_bybit": bb_price,
            "entry_price_kucoin": kc_price,
            "entry_fee_bybit": round(fee_bybit, 6),
            "entry_fee_kucoin": round(fee_kucoin, 6),
            "entry_spread": opp.get("spread_pct"),
            "entry_rate_bybit": opp.get("bybit_rate_pct"),
            "entry_rate_kucoin": opp.get("kucoin_rate_pct"),
            "bybit_interval_h": opp.get("bybit_interval_h"),
            "kucoin_interval_h": opp.get("kucoin_interval_h"),
            "entry_time": started_at,
            "status": "open",
            "legs_status": {"bybit": "open", "kucoin": "open"},
            "paper": True,
        }

        with self._lock:
            self._positions.append(position)
            self._save_all()

        result = {
            "task_id": task_id,
            "mode": "instant",
            "symbol": symbol,
            "amount_usd": amount_usd,
            "side_bybit": side_bybit,
            "side_kucoin": side_kucoin,
            "status": "done",
            "position": position,
            "started_at": started_at,
            "finished_at": _utcnow_iso(),
            "duration_seconds": round(_utcnow_ts() - ts_start, 3),
        }
        self._log_execution(result)
        return result

    def close_position(self, position_id: str) -> Dict[str, Any]:
        """Close a paper position and compute realized PnL."""
        with self._lock:
            pos = next(
                (p for p in self._positions if p.get("id", "").startswith(position_id)),
                None,
            )
            if not pos:
                return {"ok": False, "error": "position not found", "position_id": position_id}
            if pos.get("status") != "open":
                return {
                    "ok": False,
                    "error": f"position is {pos.get('status')}, not open",
                    "position_id": position_id,
                }

            pos["status"] = "closing"
            self._save_portfolio()

        # Get current prices
        opp = self._get_opportunity(pos["symbol"])
        if not opp:
            with self._lock:
                pos["status"] = "open"
                self._save_portfolio()
            return {
                "ok": False,
                "error": f"cannot get current prices for {pos['symbol']}",
                "position_id": position_id,
            }

        exit_price_bb = opp.get("bybit_mark") or opp.get("price", 0)
        exit_price_kc = opp.get("kucoin_mark") or opp.get("price", 0)

        # Compute PnL (mirrors live portfolio formula)
        entry_bb = pos.get("entry_price_bybit", 0)
        entry_kc = pos.get("entry_price_kucoin", 0)
        qty_bb = pos.get("qty_bybit", 0) or pos.get("quantity", 0)
        qty_kc = pos.get("qty_kucoin", 0) or pos.get("quantity", 0)

        # Price PnL per leg — each leg has its OWN quantity
        if pos["side_bybit"] == "buy":
            price_pnl_bb = qty_bb * (exit_price_bb - entry_bb)
        else:
            price_pnl_bb = qty_bb * (entry_bb - exit_price_bb)

        if pos["side_kucoin"] == "buy":
            price_pnl_kc = qty_kc * (exit_price_kc - entry_kc)
        else:
            price_pnl_kc = qty_kc * (entry_kc - exit_price_kc)

        total_price_pnl = price_pnl_bb + price_pnl_kc

        # Exit fees (on position size, not margin)
        position_size = pos.get("position_size", pos.get("amount_usd", 0))
        exit_fee_bybit = position_size * BYBIT_TAKER_FEE
        exit_fee_kucoin = position_size * KUCOIN_TAKER_FEE
        exit_fee = exit_fee_bybit + exit_fee_kucoin
        entry_fee = float(pos.get("entry_fee_bybit", 0) or 0) + float(
            pos.get("entry_fee_kucoin", 0) or 0
        )
        total_fee = entry_fee + exit_fee

        # Funding PnL breakdown — SHORT leg receives FR, LONG leg pays FR
        entry_rate_bb = float(pos.get("entry_rate_bybit", 0) or 0) / 100.0
        entry_rate_kc = float(pos.get("entry_rate_kucoin", 0) or 0) / 100.0
        bb_iv = int(pos.get("bybit_interval_h", 8) or 8)
        kc_iv = int(pos.get("kucoin_interval_h", 8) or 8)
        entry_time_str = pos.get("entry_time", _utcnow_iso())
        try:
            entry_dt = datetime.fromisoformat(entry_time_str.replace("Z", "+00:00"))
            hours_held = max(0, (_utcnow_ts() - entry_dt.timestamp()) / 3600.0)
        except (ValueError, AttributeError):
            hours_held = 0
        # Est: rate_leg * position_size * (hours_held / interval_hours)
        raw_bb = entry_rate_bb * position_size * (hours_held / max(bb_iv, 1))
        raw_kc = entry_rate_kc * position_size * (hours_held / max(kc_iv, 1))
        # Funding PnL breakdown — correct for both positive AND negative FR:
        #   FR positif: SHORT receive, LONG pay
        #   FR negatif: SHORT pay,    LONG receive
        if pos["side_bybit"] == "sell":   # SHORT
            fr_received_bb = max(raw_bb, 0)
            fr_paid_bb     = max(-raw_bb, 0)
        else:                              # LONG
            fr_paid_bb     = max(raw_bb, 0)
            fr_received_bb = max(-raw_bb, 0)
        if pos["side_kucoin"] == "sell":   # SHORT
            fr_received_kc = max(raw_kc, 0)
            fr_paid_kc     = max(-raw_kc, 0)
        else:                              # LONG
            fr_paid_kc     = max(raw_kc, 0)
            fr_received_kc = max(-raw_kc, 0)
        fr_paid = fr_paid_bb + fr_paid_kc
        fr_received = fr_received_bb + fr_received_kc
        funding_pnl = fr_received - fr_paid

        realized_pnl = total_price_pnl + funding_pnl - total_fee

        # Update balance & state
        with self._lock:
            pos["status"] = "closed"
            pos["exit_price_bybit"] = exit_price_bb
            pos["exit_price_kucoin"] = exit_price_kc
            pos["exit_fee_bybit"] = round(exit_fee_bybit, 6)
            pos["exit_fee_kucoin"] = round(exit_fee_kucoin, 6)
            pos["exit_time"] = _utcnow_iso()
            pos["price_pnl_bb"] = round(price_pnl_bb, 8)
            pos["price_pnl_kc"] = round(price_pnl_kc, 8)
            pos["total_price_pnl"] = round(total_price_pnl, 8)
            pos["funding_pnl"] = round(funding_pnl, 8)
            pos["fr_paid"] = round(fr_paid, 8)
            pos["fr_received"] = round(fr_received, 8)
            pos["total_fee"] = round(total_fee, 8)
            pos["realized_pnl"] = round(realized_pnl, 8)

            # Return margin proportionally (50/50 split)
            margin_back = pos["amount_usd"]
            margin_per_ex = margin_back / 2.0
            pnl_per_ex = realized_pnl / 2.0
            self._balance_bybit += margin_per_ex + pnl_per_ex
            self._balance_kucoin += margin_per_ex + pnl_per_ex
            self._realized_pnl += realized_pnl
            self._total_fees += exit_fee
            self._total_funding_pnl += funding_pnl

            # Move to closed
            self._positions = [p for p in self._positions if p["id"] != pos["id"]]
            self._closed_positions.append(pos)

            self._save_all()

        result = {
            "ok": True,
            "position_id": position_id,
            "symbol": pos["symbol"],
            "side_bybit": pos.get("side_bybit", "?"),
            "side_kucoin": pos.get("side_kucoin", "?"),
            "entry_price_bybit": pos.get("entry_price_bybit", 0),
            "entry_price_kucoin": pos.get("entry_price_kucoin", 0),
            "exit_price_bybit": exit_price_bb,
            "exit_price_kucoin": exit_price_kc,
            "entry_fee_bybit": float(pos.get("entry_fee_bybit", 0) or 0),
            "entry_fee_kucoin": float(pos.get("entry_fee_kucoin", 0) or 0),
            "exit_fee_bybit": round(exit_fee_bybit, 6),
            "exit_fee_kucoin": round(exit_fee_kucoin, 6),
            "realized_pnl": round(realized_pnl, 2),
            "price_pnl": round(total_price_pnl, 2),
            "funding_pnl": round(funding_pnl, 2),
            "fr_paid": round(fr_paid, 2),
            "fr_received": round(fr_received, 2),
            "fees": round(total_fee, 2),
            "balance_after": round(self.get_balance(), 2),
            "amount_usd": pos.get("amount_usd", 0),
            "leverage": pos.get("leverage", 1),
            "position_size": pos.get("position_size", pos.get("amount_usd", 0)),
        }
        self._log_execution({"type": "close", **result, "finished_at": _utcnow_iso()})
        return result

    def force_close_leg(self, position_id: str, exchange: str) -> Dict[str, Any]:
        """Simulate one leg being force-closed (margin call / liquidation).
        
        Marks the given leg as 'closed' in legs_status. If both legs are now
        closed, runs full close_position. Returns the position so the caller
        can close the remaining leg.
        """
        with self._lock:
            pos = next(
                (p for p in self._positions if p.get("id", "").startswith(position_id)),
                None,
            )
            if not pos:
                return {"ok": False, "error": "position not found", "position_id": position_id}
            if pos.get("status") != "open":
                return {"ok": False, "error": f"position is {pos.get('status')}, not open"}
            
            legs = pos.setdefault("legs_status", {"bybit": "open", "kucoin": "open"})
            exchange = exchange.lower()
            if exchange not in ("bybit", "kucoin"):
                return {"ok": False, "error": f"invalid exchange: {exchange}"}
            if legs.get(exchange) != "open":
                return {"ok": False, "error": f"{exchange} leg already closed"}
            
            legs[exchange] = "closed"
            self._save_portfolio()
            
        result = {
            "ok": True,
            "position_id": position_id,
            "symbol": pos["symbol"],
            "exchange": exchange,
            "both_legs_closed": legs.get("bybit") == "closed" and legs.get("kucoin") == "closed",
            "legs_status": dict(legs),
            "message": f"{exchange} leg force-closed (simulated margin call)",
        }
        log.warning("FORCE CLOSE: %s leg %s — %s", exchange, position_id[:12], pos["symbol"])
        self._log_execution({"type": "force_close_leg", **result})
        return result

    def close_all_positions(self) -> List[Dict[str, Any]]:
        results = []
        for pos in self.get_open_positions():
            results.append(self.close_position(pos["id"]))
        return results

    # ─── Queries ──────────────────────────────────────────────────────────

    def _get_opportunity(self, symbol: str) -> Optional[dict]:
        """Look up a symbol in the latest scan data."""
        data = read_opportunities()
        symbol_upper = symbol.upper()
        for opp in data.get("opportunities", []):
            if opp["symbol"].upper() == symbol_upper:
                return opp
        return None

    def get_open_positions(self) -> List[Dict[str, Any]]:
        with self._lock:
            return [p for p in self._positions if p.get("status") == "open"]

    def get_closed_positions(self) -> List[Dict[str, Any]]:
        with self._lock:
            return list(self._closed_positions)

    def get_balance(self) -> float:
        with self._lock:
            return self._balance_bybit + self._balance_kucoin

    def get_bybit_balance(self) -> float:
        with self._lock:
            return self._balance_bybit

    def get_kucoin_balance(self) -> float:
        with self._lock:
            return self._balance_kucoin

    def get_summary(self) -> dict:
        """Portfolio summary for /portfolio and /pnl commands."""
        open_positions = self.get_open_positions()
        total_exposure = sum(p.get("position_size", p["amount_usd"]) for p in open_positions)

        # Compute unrealized PnL for open positions
        unrealized_pnl = 0.0
        for pos in open_positions:
            opp = self._get_opportunity(pos["symbol"])
            if opp:
                exit_bb = opp.get("bybit_mark") or opp.get("price", 0)
                exit_kc = opp.get("kucoin_mark") or opp.get("price", 0)
                entry_bb = pos.get("entry_price_bybit", 0)
                entry_kc = pos.get("entry_price_kucoin", 0)
                # Use per-leg qty
                qty_bb = pos.get("qty_bybit", 0) or 0
                qty_kc = pos.get("qty_kucoin", 0) or 0
                if pos["side_bybit"] == "buy":
                    pnl_bb = qty_bb * (exit_bb - entry_bb)
                else:
                    pnl_bb = qty_bb * (entry_bb - exit_bb)
                if pos["side_kucoin"] == "buy":
                    pnl_kc = qty_kc * (exit_kc - entry_kc)
                else:
                    pnl_kc = qty_kc * (entry_kc - exit_kc)
                unrealized_pnl += pnl_bb + pnl_kc

        return {
            "balance": self.get_balance(),
            "bybit_balance": self.get_bybit_balance(),
            "kucoin_balance": self.get_kucoin_balance(),
            "realized_pnl": self._realized_pnl,
            "unrealized_pnl": unrealized_pnl,
            "total_pnl": self._realized_pnl + unrealized_pnl,
            "total_exposure": total_exposure,
            "open_positions": len(open_positions),
            "positions": open_positions,
            "closed": len(self._closed_positions),
            "passed_funding_cycles": sum(
                1 for c in self._closed_positions if abs(c.get("funding_pnl", 0)) > 0
            ),
            "total_fees": round(self._total_fees, 2),
            "total_funding_pnl": round(self._total_funding_pnl, 2),
        }