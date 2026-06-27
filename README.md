# FR Bot — Bybit × KuCoin Funding Rate Arbitrage

Bot Telegram otomatis (Delta Neutral Arbitrage) yang mencari selisih *Funding Rate* antara Bybit dan KuCoin via **WebSocket real-time feed**. Bot akan melakukan **Short** pada pair yang funding rate-nya tinggi, dan **Long** pada pair yang funding rate-nya rendah. Tujuannya menangkap profit dari pembayaran funding sambil meminimalkan kerugian dari selisih harga (Price Spread).

Bot mendukung **Paper Trading Mode** (simulasi) dan **Live Account Mode** (dengan guard berlapis).

> ⚠️ Live mode memakai dana real. Test paper mode dulu. Jangan aktifkan live kalau belum paham risiko partial fill, liquidation, API permission, dan network failure.

---

## Arsitektur — Event-Driven WebSocket

```
Exchange Bybit ──[WebSocket push]──→  WSPool ──→  PriceCache + FundingCache
Exchange KuCoin──[WebSocket push]──→  WSPool ──┘         │
                                                          ▼
                                                   SpreadEngine
                                              (event-driven compute)
                                                          │
                                                          ▼
                                              AutomationEngine
                                              (state machine)
```

**Perubahan utama dari arsitektur lama (REST polling):**

| Aspek | Lama (REST) | Baru (WebSocket) |
|-------|-------------|------------------|
| Data source | Polling REST setiap 60s / 0.5s | WebSocket push (<5ms latency) |
| Price/funding | Read file `opportunities.json` | In-memory `PriceCache` + `FundingCache` |
| Rate limit | Tidak ada guard | `RateLimiter` dengan token bucket |
| Error recovery | Reconnect manual | Auto-reconnect exponential backoff |
| Event log | stdout / Telegram saja | SQLite `event_log` + `trade_log` |
| Spread calc | Duplikasi 3x di automation | `SpreadEngine()` — single source of truth |

### Components

| Module | Path | Fungsi |
|--------|------|--------|
| WebSocket Pool | `core/ws_pool.py` | Koneksi WS Bybit + KuCoin, auto-reconnect, heartbeat |
| Price Cache | `core/market_cache.py` | In-memory cache harga mark price kedua exchange |
| Funding Cache | `core/market_cache.py` | In-memory cache funding rate + next payment |
| Spread Engine | `core/spread_engine.py` | Computes price spread + funding diff, emits signals |
| Rate Limiter | `core/rate_limiter.py` | Token bucket untuk REST API, warning jika mendekati limit |
| Local DB | `core/db.py` | SQLite (WAL mode) untuk trade log, event log, funding snapshots |
| Automation Engine | `core/automation_engine.py` | State machine IDLE→LOOKING→DELAY→LIVE→CLOSE |

---

## Fitur

- **Real-time data** via WebSocket — tidak perlu polling REST
- Auto-reconnect dengan exponential backoff
- Rate limit protection (REST calls)
- SQLite lokal untuk log trade + event
- Scan funding rate Bybit + KuCoin futures
- Sort peluang by absolute delta: `|FR_Bybit - FR_KuCoin|`
- Support dua arah:
  - Bybit FR > KuCoin FR → SHORT Bybit / LONG KuCoin
  - KuCoin FR > Bybit FR → SHORT KuCoin / LONG Bybit
- Telegram bot commands
- Paper mode simulasi (identik dengan live)
- Live mode real orders dengan guard
- Auto engine: IDLE → LOOKING → DELAY → LIVE → CLOSE
- Notifikasi Telegram via raw Bot API
- `/status`, `/portfolio`, `/pnl`, `/health`, `/pair`

---

## Setup VPS

```bash
git clone https://github.com/mannamemmna/fr.git
cd fr
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
nano .env
python bot.py
```

## `.env` minimum paper mode

```env
BOT_TOKEN=your_telegram_bot_token
NOTIFY_CHAT_ID=5500339980

PAPER_MODE=true
LIVE_CONFIRM=false
PAPER_INITIAL_BALANCE=10000

AUTO_MODE=false
AUTO_SCAN_INTERVAL=60
AUTO_LEVERAGE=3
AUTO_BALANCE_PER_LEG=1000

# WebSocket
WS_HEARTBEAT_SEC=20
REST_RATE_LIMIT_PER_SEC=10

# Waktu pencarian & delay
AUTO_ENTRY_WINDOW_MIN=30
AUTO_MONITOR_INTERVAL=0.5

# Threshold
AUTO_DELTA_THRESHOLD=0.4
AUTO_DELAY_CANCEL_FUNDING_DIFF=0.2
AUTO_DELAY_ENTRY_PRICE_SPREAD=0.0
AUTO_LIVE_CLOSE_FUNDING_DIFF=0.05
AUTO_LIVE_CLOSE_PRICE_SPREAD=0.0
```

Paper mode tidak butuh API key exchange.

## `.env` live mode

Live order hanya aktif kalau **dua kunci** ini benar:

```env
PAPER_MODE=false
LIVE_CONFIRM=true
```

Lalu isi credentials:

```env
BYBIT_API_KEY=...
BYBIT_API_SECRET=...
KUCOIN_API_KEY=...
KUCOIN_API_SECRET=...
KUCOIN_API_PASSPHRASE=...
```

Jika `PAPER_MODE=false` tapi `LIVE_CONFIRM=false`, bot akan menolak start live engine:

```text
LIVE MODE LOCKED: set LIVE_CONFIRM=true to allow real exchange orders
```

---

## Commands

| Command | Fungsi |
|---------|--------|
| `/start` | Intro bot + perintah utama |
| `/help` | Semua commands |
| `/status` | Status ringkas: mode, balance, exchange, auto engine, next funding |
| `/scan` | Scan funding rate terbaru (REST fallback) |
| `/top [N]` | Top N peluang by delta |
| `/pair SYMBOL` | Detail satu pair — price, funding, spread, interval, APR |
| `/execute SYMBOL [amount] [lev]` | Manual entry pakai arah dari scan terbaru |
| `/portfolio` | Balance + posisi terbuka |
| `/close ID` | Tutup satu posisi |
| `/closeall` | Tutup semua posisi |
| `/pnl` | PnL 1D/7D/30D + closed trades |
| `/health` | Ping Bybit + KuCoin |
| `/auto on` | Nyalakan automation |
| `/auto off` | Matikan automation |
| `/auto status` | Status automation |

---

## Automation strategy

State machine:

```text
IDLE → LOOKING → DELAY → LIVE → CLOSE → IDLE
```

### IDLE

Menunggu funding window. Memanfaatkan data real-time dari WebSocket — tidak ada polling.

### LOOKING

Cari pair terbaik berdasarkan delta funding:

```text
delta = abs(FR_Bybit - FR_KuCoin)  (dinormalisasi ke max interval)
```

Direction:

```text
FR_Bybit > FR_KuCoin  → SHORT Bybit / LONG KuCoin
FR_KuCoin > FR_Bybit  → SHORT KuCoin / LONG Bybit
```

### DELAY

Sebelum entry, bot monitor Price Spread dari real-time WebSocket data:

```text
price_spread = ((P_Long - P_Short) / P_Short) × 100
```

Notifikasi delay dikirim **sekali saja** saat masuk queue.

### LIVE

Setelah posisi terbuka, bot monitor reversal 2 tahap:

1. **Tahap 1:** Tunggu Diff FR turun ke threshold
2. **Tahap 2:** Tutup saat Price Spread kembali positif

### CLOSE

Tutup dua leg dan kirim trade summary + catat ke SQLite.

---

## Local Database (SQLite)

Bot menyimpan data ke `data/fr-bot.db`:

| Table | Isi |
|-------|-----|
| `trade_log` | Semua entry, close, cancel — PnL, fees, balance |
| `event_log` | INFO/WARN/ERROR dengan timestamp |
| `funding_snapshot` | Snapshot funding rate per symbol per exchange |

Command SQL:

```bash
sqlite3 data/fr-bot.db "SELECT * FROM event_log WHERE level='ERROR' ORDER BY ts DESC LIMIT 10;"
```

---

## Error Handling

| Skenario | Action |
|----------|--------|
| WS disconnect | Auto-reconnect dengan exponential backoff (1s → 2s → 4s → ... → 60s max) |
| WS reconnect fail | Tunggu backoff, retry terus sampai sukses |
| Order partial fill (1 leg sukses, 1 gagal) | Immediate market-close leg yang berhasil, alert Telegram |
| Saldo tidak cukup | Skip signal, log warning |
| HTTP 5xx (REST) | Retry 3x dengan delay, alert jika semua gagal |
| Rate limit mendekati batas | Log warning + kirim Telegram |

---

## Exchange API permissions

### Bybit

Butuh permission futures/derivatives:
- Read balance
- Read positions/orders
- Trade derivatives

### KuCoin Futures

Butuh KuCoin Futures API key:
- Futures read
- Futures trade
- API passphrase wajib

---

## Live mode limitations

1. KuCoin futures contract sizing memakai pendekatan basic contract size. Untuk produksi serius, validasi `multiplier`, `lotSize`, `tickSize` per symbol perlu ditambahkan.
2. PnL live masih berdasarkan tracked orders lokal + balance, belum full sync realized PnL exchange.
3. Kalau salah satu leg berhasil dan leg kedua gagal, result `failed_partial` + `critical=true`. User wajib cek exchange manual dan hedge/close segera.
4. Bot belum auto-reconcile posisi real exchange yang dibuka manual di luar bot.
5. Gunakan amount kecil dulu untuk live test.

---

## Recommended live rollout

1. Jalankan paper mode 1–2 funding cycle.
2. Pastikan `/status`, `/health`, `/portfolio`, notif Telegram jalan.
3. Set API key read-only dulu, verify startup.
4. Baru enable trade permission.
5. Live test manual kecil:

```text
/scan
/top 5
/execute BTC 5 1
/portfolio
/close <id>
```

6. Setelah yakin, baru enable:

```text
/auto on
```

---

## Systemd service

```ini
[Unit]
Description=FR Bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=/app/fr
ExecStart=/app/fr/.venv/bin/python bot.py
Restart=always
RestartSec=10
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
```

Install:

```bash
sudo nano /etc/systemd/system/fr-bot.service
sudo systemctl daemon-reload
sudo systemctl enable fr-bot
sudo systemctl start fr-bot
sudo journalctl -u fr-bot -f
```

---

## Troubleshooting

### `ModuleNotFoundError: No module named 'telegram'`

```bash
source .venv/bin/activate
pip install -r requirements.txt
```

### Telegram `409 Conflict`

Ada dua instance bot jalan:

```bash
pkill -9 -f "python.*bot.py"
python bot.py
```

### Notifikasi tidak masuk

Cek:

```bash
grep NOTIFY_CHAT_ID .env
```

Pastikan user sudah pernah DM bot `/start`.

### Live mode refused

Cek:

```bash
grep -E "PAPER_MODE|LIVE_CONFIRM|API" .env
```

Harus:

```env
PAPER_MODE=false
LIVE_CONFIRM=true
```

### WebSocket tidak connect

Cek log:

```bash
grep -i "ws" nohup.out
```

Pastikan firewall tidak block outbound ke port WebSocket (443, 8443).

---

## Safety

- Jangan pakai live mode dengan saldo besar sebelum test kecil.
- Jangan aktifkan withdraw permission API.
- Pakai API key khusus bot.
- Pantau `/portfolio`, `/status`, dan exchange dashboard.
- Kalau `failed_partial`, segera cek exchange manual.