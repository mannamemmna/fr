"""/simulate — Force-close one leg of an open PAPER position.

Testing/QA tool for the Hedge Integrity Guard (see core/automation_engine.py
HEDGE_EMERGENCY_OPEN logic and core/live_engine.py's partial-liquidation
detection). Wires up PaperEngine.force_close_leg(), which existed with a
full implementation and test coverage but had no command pointing to it.

Paper mode only — there is deliberately no equivalent for live mode. This
is a testing tool; a testing tool must never be able to force a REAL
exchange position closed. If you need to verify the live-mode hedge guard,
do it against a real (or exchange testnet) partial/full liquidation event,
not through this command.
"""

from __future__ import annotations

from telegram import Update
from telegram.ext import ContextTypes

from config import PAPER_MODE
from core.tg_format import b, i, code, esc
import handlers.state as state


async def cmd_simulate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not PAPER_MODE:
        await update.message.reply_text(
            f"⚠️ {b('/simulate hanya tersedia di Paper Mode')}\n\n"
            f"Ini tool testing untuk memicu Hedge Integrity Guard secara manual "
            f"(simulasi satu leg force-closed / margin call). Di Live Mode, tool ini "
            f"sengaja tidak tersedia — command testing tidak boleh bisa memaksa "
            f"posisi exchange sungguhan tertutup.",
            parse_mode="HTML",
        )
        return

    if not state.paper_engine:
        await update.message.reply_text("⚠️ Engine belum siap.")
        return

    if not context.args or len(context.args) < 2:
        await update.message.reply_text(
            f"📋 {b('Usage:')} <code>/simulate &lt;id_posisi&gt; &lt;bybit|kucoin&gt;</code>\n\n"
            f"Simulasikan satu leg force-closed (margin call / likuidasi) pada posisi "
            f"paper yang terbuka — untuk testing Hedge Integrity Guard tanpa harus "
            f"menunggu kejadian sungguhan.\n\n"
            f"Contoh: <code>/simulate a1b2c3d4 bybit</code>\n"
            f"Lihat ID posisi di /portfolio.",
            parse_mode="HTML",
        )
        return

    position_id = context.args[0]
    exchange = context.args[1].lower()

    result = state.paper_engine.force_close_leg(position_id, exchange)

    if result.get("ok"):
        legs = result["legs_status"]
        both_str = ("✅ Ya — hedge guard akan trigger pada tick berikutnya"
                    if result["both_legs_closed"]
                    else "❌ Belum — masih 1 leg terbuka")
        await update.message.reply_text(
            f"🚨 {b('LEG FORCE-CLOSED (SIMULASI)')}\n\n"
            f"Simbol: {b(esc(result['symbol']))}\n"
            f"Exchange yang di-force-close: {code(exchange)}\n"
            f"Status leg sekarang: Bybit={code(legs['bybit'])} | KuCoin={code(legs['kucoin'])}\n"
            f"Kedua leg tertutup: {both_str}\n\n"
            f"{i('Jika auto-mode aktif dan HEDGE_EMERGENCY_OPEN=true (default), hedge guard akan menutup leg satunya dalam <= HEDGE_CHECK_INTERVAL_SEC detik.')}",
            parse_mode="HTML",
        )
    else:
        await update.message.reply_text(f"❌ {esc(result.get('error', 'error tidak diketahui'))}")