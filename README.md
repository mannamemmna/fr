# FR-Bot — Funding Rate Arbitrage Bot

Telegram bot untuk **delta-neutral funding rate arbitrage** antara **Bybit** dan **KuCoin** futures.

Bot melakukan **Short** pada exchange dengan funding rate lebih tinggi, dan **Long** pada exchange dengan funding rate lebih rendah, untuk menangkap selisih funding rate secara netral pasar (hedged).

---

## Fitur

- 📡 **Real-time WebSocket** — Harga & funding rate dari Bybit + KuCoin via WS (tanpa REST polling)
- 🤖 **Auto Engine** — State machine: IDLE → LOOKING → DELAY → LIVE. Cari, delay, entry, monitor, close otomatis
- ⚖️ **Auto Rebalance** — Jaga keseimbangan saldo antar exchange (50:50)
- 🛡️ **Hedge Integrity Guard** — Emergency close jika salah satu leg force-closed / margin call
- 📊 **Telegram Commands** — 16 perintah untuk monitor & kontrol bot
- 💼 **Paper Mode** — Simulasi trading dengan fee realistis (Bybit 0.055% / KuCoin 0.06%)
- 🔁 **Auto-reconnect WS** — Exponential backoff + KuCoin bullet token refresh tiap reconnect
- 💾 **SQLite Database** — Riwayat posisi, PnL, fee tersimpan

---

## Arsitektur

```
ws_pool (BybitWS + KuCoinWS)
    │  real-time price + funding
    ▼
market_cache (PriceCache + FundingCache)
    │
    ▼
spread_engine (compute price spread, funding diff, signal)
    │
    ▼
automation_engine (state machine: IDLE → LOOKING → DELAY → LIVE)
    │
    ▼
paper_engine / live_engine (eksekusi posisi)
```

### State Machine Auto Engine

| State | Deskripsi |
|-------|-----------|
| `IDLE` | Tunggu funding window (anchor ke SHORT exchange payment) |
| `LOOKING` | Cari pair dengan delta terbaik, ranking murni `delta_pct` |
| `DELAY` | Monitor price spread entry, cancel jika funding drop |
| `LIVE` | Posisi terbuka — monitoring, close 2 jalur: interval beda (estimated PnL + max hold) / interval sama (FR decay + spread) |
| `REBALANCING` | Transfer saldo antar exchange |

### Perubahan Penting

- **Entry window anchor**: Menggunakan timestamp funding SHORT exchange (bukan `min(bb_ts, kc_ts)`)
- **Scoring**: Murni `delta_pct` — tidak ada bonus same-interval
- **Fee paper**: Bybit taker `0.055%`, KuCoin taker `0.060%` (per leg)
- **KuCoin WS reconnect**: Bullet token di-refresh tiap reconnect + counter backoff hanya reset jika koneksi stabil ≥10 detik

---

## Telegram Commands

### 📡 Scan & Analisa
| Command | Fungsi |
|---------|--------|
| `/scan` | Scan funding rate Bybit & KuCoin |
| `/top [N]` | Top N pair by Funding Difference (default 10) |
| `/pair SYM` | Detail satu pair: spread, funding, countdown, APR |

### 💼 Trading Manual
| Command | Fungsi |
|---------|--------|
| `/execute SYM [amount] [lev]` | Buka posisi manual |
| `/close ID` | Tutup posisi by ID |
| `/closeall` | Tutup SEMUA posisi (darurat) |

### 📊 Info & Status
| Command | Fungsi |
|---------|--------|
| `/status` | Dashboard mode, saldo, koneksi, auto engine |
| `/portfolio` | Posisi terbuka detail + funding next payment tiap exchange |
| `/pnl` | Ringkasan Untung/Rugi (1D, 7D, 30D, Total) |
| `/mode` | Cek PAPER atau LIVE mode |
| `/health` | Test ping ke Bybit & KuCoin |

### 🤖 Automation
| Command | Fungsi |
|---------|--------|
| `/auto on` | Aktifkan auto trading |
| `/auto off` | Nonaktifkan auto trading |
| `/auto status` | Status mesin automation |

### ⚖️ Rebalance
| Command | Fungsi |
|---------|--------|
| `/rebalance` | Cek keseimbangan saldo exchange |
| `/rebalance on` | Aktifkan auto rebalance |
| `/rebalance off` | Nonaktifkan auto rebalance |

### 🛠️ Dasar
| Command | Fungsi |
|---------|--------|
| `/start` | Sambutan / pengenalan |
| `/help` | Bantuan lengkap semua command |

---

## Setup

### 1. Clone & Install

```bash
git clone https://github.com/mannamemmna/fr.git
cd fr-bot
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 2. Konfigurasi

```bash
cp .env.example .env
# Isi BOT_TOKEN, NOTIFY_CHAT_ID, dan opsional API keys
```

### 3. Jalankan

```bash
python bot.py
```

### Environment Variables

| Variable | Default | Deskripsi |
|----------|---------|-----------|
| `BOT_TOKEN` | — | Token dari @BotFather |
| `NOTIFY_CHAT_ID` | — | Chat ID untuk notifikasi |
| `PAPER_MODE` | `true` | Simulasi (`true`) / Live (`false`) |
| `LIVE_CONFIRM` | `false` | Guard live order (butuh `true` untuk live) |
| `PAPER_INITIAL_BALANCE` | `10000` | Saldo awal paper mode |
| `BYBIT_API_KEY` | — | API Key Bybit (wajib untuk LIVE) |
| `BYBIT_API_SECRET` | — | API Secret Bybit |
| `KUCOIN_API_KEY` | — | API Key KuCoin (wajib untuk LIVE) |
| `KUCOIN_API_SECRET` | — | API Secret KuCoin |
| `KUCOIN_API_PASSPHRASE` | — | Passphrase KuCoin |
| `AUTO_SCAN_INTERVAL` | `60` | Interval scan ulang funding rate (detik) |
| `WS_HEARTBEAT_SEC` | `20` | Ping interval WebSocket |
| `REST_RATE_LIMIT_PER_SEC` | `10` | Max REST call per detik |
| `DEFAULT_LEVERAGE` | `2` | Default leverage untuk `/execute` |
| `DB_PATH` | `fr-bot.db` | Path database SQLite |
| `DEFAULT_TOP_N` | `10` | Default jumlah pair di `/top` |
| `AUTO_MODE` | `false` | Aktifkan auto engine saat start |
| `AUTO_BALANCE_PER_LEG` | `1000` | Margin per leg ($) saat auto entry |
| `AUTO_LEVERAGE` | `3` | Leverage auto entry |
| `AUTO_MAX_POSITIONS` | `1` | Max posisi bersamaan |
| `AUTO_ENTRY_WINDOW_MIN` | `30` | Window entry sebelum funding dominant (menit) |
| `AUTO_MONITOR_INTERVAL` | `0.5` | Interval loop auto engine (detik) |
| `AUTO_DELTA_THRESHOLD` | `0.4` | Min Diff FR untuk masuk LOOKING (%) |
| `AUTO_DELAY_CANCEL_FUNDING_DIFF` | `0.2` | Cancel entry jika Diff FR drop ke ≤ (%) |
| `AUTO_DELAY_ENTRY_PRICE_SPREAD` | `0.0` | Entry jika price spread ≤ threshold (%) |
| `AUTO_LIVE_CLOSE_FUNDING_DIFF` | `0.05` | Tahap 1 close: Diff FR ≤ (%) |
| `AUTO_LIVE_CLOSE_PRICE_SPREAD` | `0.0` | Tahap 2 close: price spread ≥ (%) |
| `AUTO_CLOSE_ON_RESTART` | `true` | Tutup posisi paper saat restart |
| `REBALANCE_THRESHOLD` | `0.40` | Min ratio exchange kecil / total |
| `REBALANCE_PAPER_FEE_PCT` | `0.001` | Fee simulasi transfer paper (0.1%) |
| `REBALANCE_PAPER_DELAY_SEC` | `5` | Delay simulasi transfer paper (detik) |
| `REBALANCE_CHECK_INTERVAL_SEC` | `60` | Interval polling cek saldo (detik) |
| `REBALANCE_AUTO_TRANSFER` | `false` | Auto transfer via withdrawal API |
| `HEDGE_EMERGENCY_OPEN` | `true` | Emergency close jika satu leg hilang |
| `HEDGE_CHECK_INTERVAL_SEC` | `30` | Interval cek hedge guard (detik) |
| `HEDGE_BALANCE_DROP_THRESHOLD` | `0.95` | Threshold balance drop (cadangan) |
| `LIVE_ORDER_PLACEMENT_MAX_RETRIES` | `3` | Retry order saat gagal (network/rate limit) |
| `LIVE_ORDER_PLACEMENT_RETRY_BASE_SEC` | `1.0` | Backoff awal retry (detik) |
| `LIVE_FILL_POLL_INTERVAL_SEC` | `0.5` | Interval polling status fill (detik) |
| `LIVE_FILL_POLL_TIMEOUT_SEC` | `10` | Timeout polling fill (detik) |
| `LIVE_PARTIAL_FILL_TOLERANCE_PCT` | `0.02` | Toleransi mismatch fill antar leg (2%) |
| `LIVE_PARTIAL_FILL_TOPUP_MAX_ATTEMPTS` | `2` | Max percobaan top-up partial fill |
| `LIVE_UNREALIZED_PNL_ENABLED` | `true` | Hitung floating PnL dari mark price |
| `LIVE_DIFF_HOLD_MAX_MINUTES` | `40` | Max hold interval beda (force exit) |

---

## Teknologi

- Python 3.11+
- `python-telegram-bot` — Telegram Bot API
- `websocket-client` — Real-time WS ke exchange
- `requests` — REST API exchange
- `SQLite` — Riwayat posisi
- `Bybit V5 API` — Perpetual futures
- `KuCoin Futures API` — Perpetual futures

---

## Lisensi

MIT
