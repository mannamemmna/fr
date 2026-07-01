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
| `LIVE` | Posisi terbuka — monitoring reversal, close 2 tahap |
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
| `WS_HEARTBEAT_SEC` | `20` | Ping interval WebSocket |
| `AUTO_MODE` | `false` | Aktifkan auto engine saat start |
| `AUTO_MAX_POSITIONS` | `1` | Max posisi bersamaan |
| `AUTO_BALANCE_PER_LEG` | `1000` | Margin per leg ($) |
| `AUTO_LEVERAGE` | `3` | Leverage |
| `AUTO_ENTRY_WINDOW_MIN` | `30` | Window entry sebelum funding (menit) |
| `AUTO_DELTA_THRESHOLD` | `0.4` | Min Diff FR untuk masuk LOOKING (%) |
| `AUTO_DELAY_CANCEL_FUNDING_DIFF` | `0.2` | Cancel entry jika Diff FR drop ke ≤ threshold (%) |
| `AUTO_DELAY_ENTRY_PRICE_SPREAD` | `0.0` | Entry jika price spread ≤ threshold (%) |
| `AUTO_LIVE_CLOSE_FUNDING_DIFF` | `0.05` | Tahap 1 close: Diff FR turun ke ≤ threshold (%) |
| `AUTO_LIVE_CLOSE_PRICE_SPREAD` | `0.0` | Tahap 2 close: price spread ≥ threshold (%) |
| `AUTO_CLOSE_ON_RESTART` | `true` | Tutup semua posisi paper saat restart |
| `REBALANCE_THRESHOLD` | `0.40` | Min ratio exchange kecil / total |
| `REBALANCE_AUTO_TRANSFER` | `false` | Auto transfer via withdrawal API |
| `HEDGE_EMERGENCY_OPEN` | `true` | Emergency close jika satu leg hilang |

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
