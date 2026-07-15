# FR-Bot — Funding Rate Arbitrage Bot

Telegram bot untuk **delta-neutral funding rate arbitrage** antara **Bybit** dan **KuCoin** futures.

Bot melakukan **Short** pada exchange dengan funding rate lebih tinggi, dan **Long** pada exchange dengan funding rate lebih rendah, untuk menangkap selisih funding rate secara netral pasar (hedged).

---

## 🆕 WHATS NEW? (Juli 2026)

**5 bug fixes besar — test suite 106/106 ✅**

| Fix | Judul | Masalah | Solusi |
|-----|-------|---------|--------|
| #1 | `entry_spread` LiveEngine | LiveEngine tidak menyimpan `entry_spread` ke posisi dict → `/portfolio` selalu tampil `—` dan `_estimate_exit_pnl()` underestimate price PnL untuk posisi live | Tambah `entry_spread` dari scan snapshot ke posisi dict LiveEngine (seperti PaperEngine) |
| #2 | Hedge Guard Partial Leg | Hedge guard hanya deteksi leg yang **fully closed** (qty=0). Partial liquidation (leg tereduksi ke 40% tapi masih `open`) tidak terdeteksi → naked exposure | `HEDGE_BALANCE_DROP_THRESHOLD` sekarang enforced: cek drift ratio sisa ukuran leg via `_hedge_leg_drift()`. `close_position()` defensive clamp ke live size sebelum order |
| #3 | Live Restart Resume | Bot restart → automation engine mulai dari IDLE, `_live_position_id=None`. Hedge guard, delisting guard, exit logic **hilang** untuk posisi yang masih terbuka | `resume_live_position()` ricostruksi `DelayOrder` dari field yang dipersist. `bot.py` auto-resume saat restart (live mode). 4 field baru di posisi dict (`entry_delta`, `entry_raw_fr_diff`, `bybit_next_ts`, `kucoin_next_ts`) |
| #4 | `/scan` WS Subscription Cap | `/scan` subscribe semua common symbols ke WebSocket tanpa batas → bisa trigger disconnect loop kalau >100 pairs | `MAX_WS_SUBSCRIPTIONS=100` (env-overridable) diterapkan seragam di `bot.py`, `bg_scanner.py`, dan `handlers/scan.py` |
| #5 | Multi-Position Live Tracking | Automation engine hanya bisa track **1 posisi** (`_live_order`/`_live_position_id` singular). Entry kedua overwrite yang pertama. `State.LIVE` blokir state machine dari cari kandidat baru | `LiveTrackedPosition` dataclass per posisi. `_live_positions: Dict[str, ...]` ganti 6 singular fields. `State.LIVE` dihapus — live monitoring & entry-side dispatcher jalan independen tiap tick. `AUTO_MAX_POSITIONS` benar-benar berfungsi sekarang |

**Detail teknis:** lihat file `fix-01` s/d `fix-05` di repo atau commit history.

---

## Overview

FR-Bot adalah bot trading otomatis yang mengeksploitasi selisih *funding rate* (Diff FR) antara dua exchange perpetual futures: Bybit dan KuCoin. Strateginya *delta-neutral*: posisi Long dan Short dibuka bersamaan dengan ukuran sama, sehingga paparan arah harga (directional risk) di-hedge. Profit bersih berasal dari akumulasi pembayaran funding rate, bukan dari spekulasi harga.

Fitur utama:
- **Real-time WebSocket** — harga & funding rate langsung dari Bybit & KuCoin (tanpa REST polling berulang)
- **Auto Engine** — state machine lengkap: `IDLE → LOOKING → DELAY → LIVE` dengan manajemen entry, monitoring, dan exit otomatis
- **Auto Rebalance** — menjaga keseimbangan saldo 50:50 antar exchange, mendukung simulasi (paper) dan live withdrawal on-chain
- **Hedge Integrity Guard** — emergency close otomatis jika salah satu leg hilang (margin call / force close)
- **Paper Mode** — simulasi penuh dengan fee realistis (Bybit 0.055% / KuCoin 0.060% taker per leg)
- **SQLite persistence** — riwayat posisi, PnL, fee, event log tersimpan lokal

---

## Installation / Setup

### 1. Clone & Install

```bash
git clone https://github.com/mannamemmna/fr.git
cd fr-bot
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 2. Konfigurasi Environment

```bash
cp .env.example .env
# Edit .env — isi minimal: BOT_TOKEN, NOTIFY_CHAT_ID
```

**Wajib diisi:**
| Variable | Deskripsi |
|----------|-----------|
| `BOT_TOKEN` | Token bot dari @BotFather |
| `NOTIFY_CHAT_ID` | Chat ID Telegram (dapat dari @userinfobot) — semua notifikasi otomatis dikirim ke sini |

**Untuk Live Trading (opsional, hanya jika `PAPER_MODE=false`):**
| Variable | Deskripsi |
|----------|-----------|
| `BYBIT_API_KEY`, `BYBIT_API_SECRET` | API Key Bybit (perlu permission Futures trading + Withdrawal untuk auto-rebalance) |
| `KUCOIN_API_KEY`, `KUCOIN_API_SECRET`, `KUCOIN_API_PASSPHRASE` | API Key KuCoin Futures |
| `LIVE_CONFIRM` | Harus `true` untuk mengaktifkan live order |

### 3. Jalankan

```bash
python bot.py
```

---

## Arsitektur (Bot)

```
┌─────────────────┐
│  WebSocket Pool │  ← Bybit V5 WS (tickers) + KuCoin WS (bullet token + tickers)
│  (ws_pool.py)   │     Auto-reconnect exponential backoff, heartbeat 20s
└────────┬────────┘
         │ real-time price + funding updates
         ▼
┌──────────────────────┐
│   Market Cache       │  ← PriceCache + FundingCache (thread-safe, in-memory)
│  (market_cache.py)   │
└────────┬─────────────┘
         │ on_funding_update callback
         ▼
┌──────────────────────┐
│   Spread Engine      │  ← Event-driven spread & funding diff computation
│  (spread_engine.py)  │     Single source of truth untuk sinyal
└────────┬─────────────┘
         │ query / compute_signal()
         ▼
┌──────────────────────────┐
│   Automation Engine      │  ← State machine (IDLE/LOOKING/DELAY/LIVE/REBALANCING)
│ (automation_engine.py)   │     Background thread, 0.5s tick interval
└────────┬─────────────────┘
         │ execute / close
         ▼
┌──────────────────────────┐
│ PaperEngine / LiveEngine │  ← Execution layer (position mgmt, PnL, fees)
│ (paper_engine.py,        │     LiveEngine: partial-fill protection, fill verification,
│  live_engine.py)         │     funding PnL via compute_funding_pnl()
└──────────────────────────┘
```

**Key Modules:**
| File | Responsibility |
|------|----------------|
| `bot.py` | Thin entry point: init WS, engines, handlers, PTB Application |
| `core/ws_pool.py` | WebSocket connection pool (Bybit + KuCoin) dengan auto-reconnect |
| `core/market_cache.py` | PriceCache + FundingCache (in-memory, thread-safe) |
| `core/spread_engine.py` | Event-driven spread & funding diff calculation |
| `core/automation_engine.py` | State machine auto-trading (entry/exit logic, hedge guard, rebalance trigger) |
| `core/rebalance_engine.py` | Multi-phase rebalance: withdraw → deposit poll → internal transfer (Funding↔Unified / Main↔Futures) |
| `core/paper_engine.py` | Simulated trading: virtual balance, positions, fees, funding PnL |
| `core/live_engine.py` | Real trading: idempotent orders, fill polling, reconciliation, accurate fee/funding |
| `core/db.py` | SQLite (WAL) — trade_log, event_log, funding_snapshot, delisting_blacklist, rebalance_transfers.jsonl |
| `handlers/*.py` | 16 Telegram commands (`/status`, `/scan`, `/auto`, `/rebalance`, dll.) |

---

## Arsitektur Strategi

### Delta-Neutral Funding Rate Arbitrage

```
Posisi:  Short Exchange A (FR tinggi)   +   Long Exchange B (FR rendah)
         ─────────────────────────────       ────────────────────────────
         Terima funding (jika FR > 0)        Bayar funding (jika FR > 0)
         Bayar funding (jika FR < 0)         Terima funding (jika FR < 0)
```

**Net funding per interval = \|FR_A - FR_B\| × Position Size**

Profit bersih = funding diterima − funding dibayar − fee (entry + exit × 2 leg).

### Penentuan Arah (Direction)

```python
raw_fr_diff = bybit_fr - kucoin_fr
if raw_fr_diff > 0:
    # Bybit FR lebih tinggi → SHORT Bybit / LONG KuCoin
    direction = "SHORT Bybit / LONG KuCoin"
elif raw_fr_diff < 0:
    # KuCoin FR lebih tinggi → SHORT KuCoin / LONG Bybit
    direction = "SHORT KuCoin / LONG Bybit"
```

**Penting:** Komparasi menggunakan `raw_fr_diff` (nilai numerik mentah), **bukan** `abs(FR)`. 

Contoh kasus FR negatif (TAIKO):
- Bybit FR = -2.5%, KuCoin FR = -2.0%
- `raw_fr_diff = -0.5% < 0` → **SHORT KuCoin / LONG Bybit** ✅
- Logika: LONG di Bybit (FR -2.5%) *menerima* 2.5%, SHORT di KuCoin (FR -2.0%) *bayar* 2.0% → net +0.5%

### Price Spread

```
Price Spread = ((P_Long - P_Short) / P_Short) × 100%
```

Spread **negatif** = ideal untuk entry (beli murah di Long, jual mahal di Short).

### Funding Diff Normalization

| Interval Sama (8h vs 8h / 1h vs 1h) | Interval Beda (8h vs 1h) |
|-------------------------------------|---------------------------|
| `diff = \|FR_A - FR_B\|` (raw)      | Normalisasi per-jam: `FR_A/8h` vs `FR_B/1h` |
| `daily = diff × (24/interval)`      | `daily = \|norm_A - norm_B\| × 24` |
| `APR = daily × 365`                 | `APR = daily × 365` |

### Entry Window Anchor

Entry window 30 menit (default `AUTO_ENTRY_WINDOW_MIN`) **di-anchor ke exchange dominan** (abs(FR) terbesar), **bukan** SHORT exchange. 

Alasan: saat FR negatif, exchange dominan = LONG exchange (tempat kita *menerima* funding terbesar). Entry tepat sebelum payment dominan memaksimalkan receive, meminimalkan pay.

### Exit Logic (LIVE State)

**Jalur A — Interval Beda (8h vs 1h):**
- Trigger utama: **Estimated PnL > 0** setelah payment dominan lewat + margin 5 menit
- Safety: `LIVE_DIFF_HOLD_MAX_MINUTES` (default 40 menit) force exit sebelum bleeding FR berikutnya
- Spread positif (`AUTO_LIVE_CLOSE_PRICE_SPREAD`) hanya sebagai early exit sebelum payment

**Jalur B — Interval Sama (8h vs 8h / 1h vs 1h):**
- **Tahap 1:** Diff FR turun ≤ `AUTO_LIVE_CLOSE_FUNDING_DIFF` (0.05%) **ATAU** arah FR flip (raw_fr_diff tanda berubah)
- **Tahap 2:** Price Spread ≥ `AUTO_LIVE_CLOSE_PRICE_SPREAD` (default 0.0%) → close

---

## Konfigurasi Env / Environment Variables

### Bot & Mode
| Variable | Default | Deskripsi |
|----------|---------|-----------|
| `BOT_TOKEN` | — | **Wajib.** Token dari @BotFather |
| `NOTIFY_CHAT_ID` | — | **Wajib.** Chat ID untuk notifikasi (auto events, health, daily summary) |
| `PAPER_MODE` | `true` | `true` = simulasi, `false` = live (butuh LIVE_CONFIRM=true) |
| `LIVE_CONFIRM` | `false` | Guard live order. Harus `true` kalau `PAPER_MODE=false` |
| `PAPER_INITIAL_BALANCE` | `10000` | Saldo awal paper mode (USDT) |
| `AUTO_CLOSE_ON_RESTART` | `true` | Auto-close posisi paper saat bot restart |

### Exchange API (Live Only)
| Variable | Deskripsi |
|----------|-----------|
| `BYBIT_API_KEY`, `BYBIT_API_SECRET` | Bybit V5 API (perlu Futures Trading + Withdrawal) |
| `KUCOIN_API_KEY`, `KUCOIN_API_SECRET`, `KUCOIN_API_PASSPHRASE` | KuCoin Futures API |

### WebSocket & Rate Limit
| Variable | Default | Deskripsi |
|----------|---------|-----------|
| `WS_HEARTBEAT_SEC` | `20` | Ping interval WS |
| `REST_RATE_LIMIT_PER_SEC` | `10` | Token bucket REST API |
| `AUTO_SCAN_INTERVAL` | `60` | Background scanner interval (detik) |

### Auto Engine — Automation
| Variable | Default | Deskripsi |
|----------|---------|-----------|
| `AUTO_MODE` | `false` | Auto-enable saat start |
| `AUTO_LEVERAGE` | `3` | Leverage auto entry |
| `AUTO_BALANCE_PER_LEG` | `1000` | Margin per leg (USDT) |
| `AUTO_MAX_POSITIONS` | `1` | Max posisi simultan |
| `AUTO_ENTRY_WINDOW_MIN` | `30` | Menit sebelum funding dominan untuk entry |
| `AUTO_MONITOR_INTERVAL` | `0.5` | Tick interval auto engine (detik) |
| `AUTO_DELTA_THRESHOLD` | `0.4` | Min Diff FR untuk kandidat LOOKING (%) |
| `AUTO_DELAY_CANCEL_FUNDING_DIFF` | `0.2` | Cancel DELAY jika Diff FR drop ≤ (%) |
| `AUTO_DELAY_ENTRY_PRICE_SPREAD` | `0.0` | Entry DELAY jika spread ≤ threshold (%) |
| `AUTO_LIVE_CLOSE_FUNDING_DIFF` | `0.05` | Tahap 1: Diff FR ≤ (%) |
| `AUTO_LIVE_CLOSE_PRICE_SPREAD` | `0.0` | Tahap 2: Spread ≥ (%) |

### Auto Rebalance
| Variable | Default | Deskripsi |
|----------|---------|-----------|
| `REBALANCE_THRESHOLD` | `0.40` | Min ratio exchange kecil/total (40%) |
| `REBALANCE_PAPER_FEE_PCT` | `0.001` | Fee simulasi transfer (0.1%) |
| `REBALANCE_PAPER_DELAY_SEC` | `5` | Delay simulasi transfer (detik) |
| `REBALANCE_CHECK_INTERVAL_SEC` | `60` | Polling cek saldo live (detik) |
| `REBALANCE_AUTO_TRANSFER` | `false` | **(DEPRECATED)** Gunakan `REBALANCE_LIVE_TRANSFER_ENABLED` |

### Live CEX→CEX Withdrawal (Real Money — Hati-hati!)
| Variable | Default | Deskripsi |
|----------|---------|-----------|
| `REBALANCE_LIVE_TRANSFER_ENABLED` | `false` | Aktifkan auto withdrawal antar exchange |
| `REBALANCE_LIVE_DRY_RUN` | `true` | `true` = log only, tidak kirim dana. **Test dulu!** |
| `REBALANCE_TOKEN` | `USDT` | Token untuk transfer |
| `REBALANCE_NETWORK` | `TRON` | Network: `TRON` \| `BSC` \| `BASE` \| `ARBITRUM` |
| `REBALANCE_BYBIT_DEPOSIT_ADDRESS` | — | **Wajib.** Deposit address Bybit (UTA) untuk network di atas |
| `REBALANCE_KUCOIN_DEPOSIT_ADDRESS` | — | **Wajib.** Deposit address KuCoin (Main/Futures) untuk network di atas |
| `REBALANCE_BYBIT_DEPOSIT_MEMO` | — | Memo/tag (jika diperlukan network) |
| `REBALANCE_KUCOIN_DEPOSIT_MEMO` | — | Memo/tag (jika diperlukan network) |
| `REBALANCE_MIN_TRANSFER_USD` | `20` | Hard floor amount per transfer |
| `REBALANCE_MAX_TRANSFER_USD` | `500` | Hard cap amount per transfer |
| `REBALANCE_WITHDRAW_POLL_INTERVAL_SEC` | `15` | Polling status withdrawal |
| `REBALANCE_WITHDRAW_POLL_TIMEOUT_SEC` | `1800` | Timeout withdrawal (30 menit) |

### Internal Transfer After Deposit
| Variable | Default | Deskripsi |
|----------|---------|-----------|
| `REBALANCE_DEPOSIT_POLL_INTERVAL_SEC` | `10` | Cek on-chain deposit |
| `REBALANCE_DEPOSIT_POLL_TIMEOUT_SEC` | `1800` | Timeout deposit detection (30 menit) |
| `REBALANCE_INTERNAL_TRANSFER_POLL_INTERVAL_SEC` | `5` | Polling internal transfer (Bybit async) |
| `REBALANCE_INTERNAL_TRANSFER_POLL_TIMEOUT_SEC` | `600` | Timeout internal transfer (10 menit) |

> **Catatan:** KuCoin `transfer_main_to_futures` sinkron (POST = success). Bybit `transfer_funding_to_unified` async (perlu polling).

### Hedge Integrity Guard
| Variable | Default | Deskripsi |
|----------|---------|-----------|
| `HEDGE_EMERGENCY_OPEN` | `true` | Aktifkan emergency close jika 1 leg hilang |
| `HEDGE_CHECK_INTERVAL_SEC` | `30` | Interval cek posisi (detik) |
| `HEDGE_BALANCE_DROP_THRESHOLD` | `0.95` | Threshold drop balance (cadangan) |

### Live Engine — Order Fill & Partial Fill Protection
| Variable | Default | Deskripsi |
|----------|---------|-----------|
| `LIVE_ORDER_PLACEMENT_MAX_RETRIES` | `3` | Retry placement order |
| `LIVE_ORDER_PLACEMENT_RETRY_BASE_SEC` | `1.0` | Backoff base (1s, 2s, 4s) |
| `LIVE_FILL_POLL_INTERVAL_SEC` | `0.5` | Poll fill status |
| `LIVE_FILL_POLL_TIMEOUT_SEC` | `10` | Timeout polling fill |
| `LIVE_PARTIAL_FILL_TOLERANCE_PCT` | `0.02` | Toleransi mismatch qty antar leg (2%) |
| `LIVE_PARTIAL_FILL_TOPUP_MAX_ATTEMPTS` | `2` | Max top-up attempt |
| `LIVE_UNREALIZED_PNL_ENABLED` | `true` | Hitung floating PnL dari mark price |

### Diff Interval Exit Strategy
| Variable | Default | Deskripsi |
|----------|---------|-----------|
| `LIVE_DIFF_HOLD_MAX_MINUTES` | `40` | Max hold interval beda (force exit sebelum bleeding) |

### Delisting Protection
| Variable | Default | Deskripsi |
|----------|---------|-----------|
| `DELISTING_MONITOR_ENABLED` | `true` | Scan pengumuman delisting Bybit/KuCoin |
| `DELISTING_CHECK_INTERVAL_SEC` | `3600` | Interval cek announcement (1 jam) |
| `AUTO_CLOSE_ON_DELISTING_DETECTED` | `false` | Auto-close posisi kalau delisting (berisiko false positive) |
| `DELISTING_BLACKLIST_CACHE_TTL_SEC` | `30` | Cache blacklist di automation loop |

### Display
| Variable | Default | Deskripsi |
|----------|---------|-----------|
| `DB_PATH` | `fr-bot.db` | Path SQLite (relative to DATA_DIR) |
| `DEFAULT_TOP_N` | `10` | Default `/top N` |
| `DEFAULT_LEVERAGE` | `2` | Default leverage `/execute` |

---

## Telegram Command

### 📡 Scan & Analisa
| Command | Deskripsi |
|---------|-----------|
| `/scan` | Full scan REST → update WS subscription. Tampilkan Top 5 by Diff FR & Top 5 by APR |
| `/top [N]` | Daftar Top N pair by Diff FR (default 10, max 30) |
| `/pair SYM` | Detail pair: price, spread, FR per exchange, countdown, APR, direction, raw diff |

### 💼 Trading Manual
| Command | Deskripsi |
|---------|-----------|
| `/execute SYM [amount] [lev]` | Buka posisi manual. Contoh: `/execute TAIKO 100 3` |
| `/close ID` | Tutup 1 posisi (partial ID OK, prefix 8 char) |
| `/closeall` | Emergency close SEMUA posisi |

### 📊 Info & Status
| Command | Deskripsi |
|---------|-----------|
| `/status` | Dashboard: mode, saldo, koneksi, auto engine, posisi terbuka, next funding |
| `/portfolio` | Posisi terbuka detail: entry price, liq price, next payment per exchange, uPnL, funding PnL |
| `/pnl` | Ringkasan PnL 1D/7D/30D/Total + 5 trade terakhir dengan breakdown fee & funding |
| `/mode` | Cek mode: Paper (simulasi) vs Live (real) |
| `/health` | Ping latency ke Bybit & KuCoin REST API |

### 🤖 Automation (Auto-Trade)
| Command | Deskripsi |
|---------|-----------|
| `/auto on` | Aktifkan auto engine (scan + entry + exit otomatis) |
| `/auto off` | Matikan auto engine (posisi berjalan aman) |
| `/auto status` | Status state machine: IDLE/LOOKING/DELAY/LIVE/REBALANCING |

### ⚖️ Rebalance
| Command | Deskripsi |
|---------|-----------|
| `/rebalance` | Cek saldo Bybit vs KuCoin, status keseimbangan, amount transfer needed |
| `/rebalance on` | Aktifkan auto rebalance |
| `/rebalance off` | Nonaktifkan auto rebalance |
| `/rebalance transfers` | Lihat 5 transfer rebalance terakhir (status, amount, network) |

### 🛠️ Dasar
| Command | Deskripsi |
|---------|-----------|
| `/start` | Sambutan + mode + link ke `/help` |
| `/help` | Daftar semua command |
| `/help glossary` | **Glosarium** istilah: delta-neutral, funding rate, diff FR, price spread, APR, leverage, margin, partial fill, rebalance, blacklist, paper/live mode |

---

## Risk Disclaimer

**PERINGATAN: INI ADALAH BOT TRADING DENGAN RISIKO KEUANGAN NYATA.**

1. **Live Mode = Uang Asli.** Set `PAPER_MODE=false` + `LIVE_CONFIRM=true` akan mengeksekusi order real di Bybit & KuCoin. Kerugian tidak terhindarkan bisa terjadi.
2. **Strategi Delta-Neutral ≠ Risk-Free.** Risiko tersisa:
   - **Basis Risk / Price Spread Blowout:** Spread melebar drastis saat entry/exit → kerugian price > funding profit.
   - **Funding Rate Flip:** FR tiba-tiba berubah arah saat posisi terbuka → net funding jadi negatif.
   - **Liquidation:** Leverage 3x default → move ~33% tegen posisi = likuidasi. Gunakan margin yang cukup.
   - **Execution Risk:** Partial fill, latency, exchange downtime → satu leg terbuka tanpa hedge (naked).
   - **Hedge Integrity Guard** memitigasi tapi tidak menghilangkan risiko sepenuhnya (bergantung pada kecepatan deteksi API exchange).
   - **Smart Contract / Chain Risk:** Auto-rebalance live menggunakan on-chain withdrawal. Jaringan congestion, wrong address/network = **kerugian permanen dana**.
3. **Auto-Rebalance Live Withdrawal** (`REBALANCE_LIVE_TRANSFER_ENABLED=true`) memindahkan dana antar exchange **tanpa konfirmasi manual per transaksi**. Hanya aktifkan setelah:
   - Test `REBALANCE_LIVE_DRY_RUN=true` beberapa siklus penuh
   - Verifikasi address deposit **benar-benar milik exchange tujuan** untuk network yang dipilih
   - Memahami `REBALANCE_MIN/MAX_TRANSFER_USD` caps
4. **Paper Mode ≠ Live Performance.** Simulasi fee, fill, latency ideal. Live slippage, partial fill, rate limit, API error akan menurunkan performa.
5. **Tidak Ada Jaminan Profit.** Funding rate berfluktuasi. APR yang ditampilkan adalah proyeksi *jika kondisi saat ini bertahan terus*, bukan janji return.
6. **Tanggung Jawab Penuh di Pengguna.** Penulis/tidak bertanggung jawab atas kerugian apapun. Gunakan dengan modal yang sanggup hilang.

---

## Lisensi

MIT License — bebas digunakan, dimodifikasi, didistribusikan. Lihat file `LICENSE` untuk detail lengkap.