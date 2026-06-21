#!/usr/bin/env python3
"""Daily PnL summary — called by cron at 00:00 WIB."""
import json, os, sys

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")

def load_state():
    path = os.path.join(DATA_DIR, "paper_state.json")
    if not os.path.exists(path):
        return None
    with open(path) as f:
        return json.load(f)

def load_portfolio():
    path = os.path.join(DATA_DIR, "paper_portfolio.json")
    if not os.path.exists(path):
        return []
    try:
        with open(path) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return []

def main():
    state = load_state()
    if not state:
        print("📊 *DAILY SUMMARY*\n\n⏳ No data yet. Run /start on the bot to initialize.")
        return

    balance = state.get("balance", 0)
    realized_pnl = state.get("realized_pnl", 0)
    fees = state.get("total_fees", 0)
    funding = state.get("total_funding_pnl", 0)
    saved_at = state.get("saved_at", "?")[:19]

    closed = load_portfolio()
    today_trades = [p for p in closed if p.get("closed_at")]

    lines = [
        "📊 *DAILY PnL SUMMARY*",
        f"_{saved_at} UTC_",
        "",
        f"💰 Balance: `${balance:,.2f} USD`",
        f"📊 Realized PnL: `{realized_pnl:+,.2f}`",
        f"💸 Fees paid: `{fees:,.2f}`",
        f"⚡ Funding earned: `{funding:,.2f}`",
    ]

    net = realized_pnl
    if abs(net) > 0.01:
        sign = "🟢" if net > 0 else "🔴"
        lines.append(f"")
        lines.append(f"Net: {sign} `{net:+,.2f} USD`")

    # Recent trades
    if closed:
        recent = closed[-3:]
        lines.append("")
        lines.append("*Recent trades:*")
        for p in recent:
            sym = p.get("symbol", "?")
            pnl = p.get("realized_pnl", 0)
            emoji = "✅" if pnl >= 0 else "❌"
            lines.append(f"  {emoji} *{sym}* PnL: `{pnl:+.2f}`")

    print("\n".join(lines))

if __name__ == "__main__":
    main()
