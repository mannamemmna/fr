# FR Bot — Bybit × KuCoin Funding Rate Arbitrage

Telegram bot untuk scan funding-rate arbitrage Bybit vs KuCoin, paper trading, automation 30 menit sebelum funding, dan **live account mode** dengan guard `LIVE_CONFIRM=true`.

> ⚠️ Live mode memakai dana real. Test paper mode dulu. Jangan aktifkan live kalau belum paham risiko partial fill, liquidation, API permission, dan network failure.

## Fitur

- Scan funding rate Bybit + KuCoin futures
- Sort peluang by absolute delta: `|FR_Bybit - FR_KuCoin|`
- Support dua arah:
  - Bybit FR > KuCoin FR → SHORT Bybit / LONG KuCoin
  - KuCoin FR > Bybit FR → SHORT KuCoin / LONG Bybit
- Telegram bot commands
- Paper mode simulasi
- Live mode real orders dengan guard
- Auto engine: IDLE → LOOKING → DELAY → LIVE → CLOSE
- Notifikasi Telegram via raw Bot API
- `/status`, `/portfolio`, `/pnl`, `/health`

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

# Waktu pencarian & delay
AUTO_ENTRY_WINDOW_MIN=30
AUTO_DELAY_CHECKS=10
AUTO_MONITOR_INTERVAL=0.5

# Threshold Entry & Exit (Automation Rules)
AUTO_DELTA_THRESHOLD=0.3
AUTO_DELAY_CANCEL_PRICE_SPREAD=0.05
AUTO_DELAY_CANCEL_FUNDING_DIFF=0.2
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

## Exchange API permissions

### Bybit

Butuh permission futures/derivatives:

- Read balance
- Read positions/orders
- Trade derivatives

Endpoint yang dipakai:

- `GET /v5/account/wallet-balance`
- `GET /v5/market/tickers`
- `POST /v5/position/set-leverage`
- `POST /v5/order/create`

### KuCoin Futures

Butuh KuCoin Futures API key:

- Futures read
- Futures trade
- API passphrase wajib

Endpoint yang dipakai:

- `GET /api/v1/account-overview`
- `GET /api/v1/ticker`
- `POST /api/v1/orders`

## Commands

| Command | Fungsi |
|---|---|
| `/start` | Intro bot + perintah utama |
| `/help` | Semua commands |
| `/status` | Status ringkas: mode, balance, exchange, auto engine, next funding |
| `/scan` | Scan funding rate terbaru |
| `/top [N]` | Top N peluang by delta |
| `/execute SYMBOL [amount] [lev]` | Manual entry pakai arah dari scan terbaru |
| `/portfolio` | Balance + posisi terbuka |
| `/close ID` | Tutup satu posisi |
| `/closeall` | Tutup semua posisi |
| `/pnl` | PnL 1D/7D/30D + closed trades |
| `/health` | Ping Bybit + KuCoin |
| `/auto on` | Nyalakan automation |
| `/auto off` | Matikan automation |
| `/auto status` | Status automation |

## Automation strategy

State machine:

```text
IDLE → LOOKING → DELAY → LIVE → CLOSE → IDLE
```

### IDLE

Menunggu funding window.

### LOOKING

Cari pair terbaik berdasarkan delta funding:

```text
delta = abs(FR_Bybit - FR_KuCoin)
```

Direction:

```text
FR_Bybit > FR_KuCoin  → SHORT Bybit / LONG KuCoin
FR_KuCoin > FR_Bybit  → SHORT KuCoin / LONG Bybit
```

### DELAY

Sebelum entry, bot monitor price spread mark price:

```text
price_spread = (Bybit_mark - KuCoin_mark) / KuCoin_mark × 100
```

Kalau price spread stabil selama `AUTO_DELAY_CHECKS`, bot execute.

### LIVE

Setelah posisi terbuka, bot monitor reversal:

- funding spread flip
- delta collapse
- price spread flip

### CLOSE

Tutup dua leg dan kirim trade summary.

## Live mode limitations

Live support sudah ada, tapi tetap ada batasan penting:

1. KuCoin futures contract sizing memakai pendekatan basic contract size. Untuk produksi serius, validasi `multiplier`, `lotSize`, `tickSize` per symbol perlu ditambahkan.
2. PnL live masih berdasarkan tracked orders lokal + balance, belum full sync realized PnL exchange.
3. Kalau salah satu leg berhasil dan leg kedua gagal, result `failed_partial` + `critical=true`. User wajib cek exchange manual dan hedge/close segera.
4. Bot belum auto-reconcile posisi real exchange yang dibuka manual di luar bot.
5. Gunakan amount kecil dulu untuk live test.

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

## Safety

- Jangan pakai live mode dengan saldo besar sebelum test kecil.
- Jangan aktifkan withdraw permission API.
- Pakai API key khusus bot.
- Pantau `/portfolio`, `/status`, dan exchange dashboard.
- Kalau `failed_partial`, segera cek exchange manual.
