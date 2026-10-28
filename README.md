# MatrixPanel CLI Lookup Telegram Bot

A Telegram bot that performs CLI lookups on MatrixPanel.io and hunts for CLIs from announcements.

## Features
- Login to MatrixPanel automatically
- Lookup CLI names via `/lookup <CLI>` command
- Direct lookup by just sending the CLI name
- **Hunt CLIs** from announcements with `/hunt`
- **Auto-monitor** announcements for new CLIs every 30 seconds
- Fast lookups using MatrixPanel's API

## Setup

1. Install dependencies:
```bash
pip install -r requirements.txt
```

2. Set environment variables:
```bash
export TELEGRAM_BOT_TOKEN="your_telegram_bot_token"
export MATRIX_USERNAME="your_matrix_username"
export MATRIX_PASSWORD="your_matrix_password"
```

Or create a `.env` file:
```env
TELEGRAM_BOT_TOKEN=your_telegram_bot_token
MATRIX_USERNAME=LOSER
MATRIX_PASSWORD=AMAN2810
```

3. Run the bot:
```bash
python matrix_cli_bot.py
```

## Usage
- Start: `/start` - Enables auto-monitoring
- Lookup: `/lookup AMAZON` or just send `AMAZON`
- Hunt: `/hunt` - Scan latest announcements for CLIs
- Monitor: `/monitor` - Toggle auto-alerts on/off

## Notes
- The bot extracts CLIs from "NEW LAMIX APP: AppName" patterns in announcements
- Auto-monitoring checks every 30 seconds for new announcements
- Uses MatrixPanel's API endpoints for lookups and announcements
- Credentials are configured in the script or via environment variables

---

# Swiggy Coupons Telegram Bot

A second bot (`swiggy_bot.py`) that finds Swiggy food-delivery restaurants near you that run coupon offers — "70% OFF UPTO ₹140", "FLAT ₹125 OFF", "ITEMS AT ₹129", etc.

## Features
- Location via **share-GPS**, typed `lat,lng`, or a city/area name
- `/coupons` — all nearby restaurants with coupon offers
- `/coupons 70` — only deals ≥ 70% off; `/coupons pizza` — only pizza places
- `/deals` — top 10 best offers, ranked (percent+cap first, then flat)
- Paginated results with inline Next/Prev buttons and direct Swiggy links

## Setup
1. `pip install -r requirements.txt` (same deps as the Matrix bot — no extra packages)
2. Create a second bot in [@BotFather](https://t.me/BotFather) to get its own token
3. Run:
```bash
python swiggy_bot.py                      # uses TELEGRAM_BOT_TOKEN from .env
set TELEGRAM_BOT_TOKEN=your_token && python swiggy_bot.py   # Windows CMD
```

## Important: seeing coupon banners
Swiggy's API **hides coupon/offer banners for anonymous requests** (verified live: listing/search endpoints return them empty without a session, while authenticated calls return populated `aggregatedDiscountInfoV2`). The bot works anonymously for restaurants/ratings; to see actual coupons set your own logged-in session:

```
SWIGGY_SID=<the "sid" cookie value — or a full "sid=...; aws-waf-token=..." Cookie header>
SWIGGY_USER_ID=<your numeric Swiggy user id>   # optional; shows on your Swiggy profile page URL/API
```

Get `sid` by logging into [swiggy.com](https://www.swiggy.com/) in a browser → F12 → Application → Cookies → `www.swiggy.com` → copy the `sid` value. This is your own account used read-only for listing data; the bot never orders anything.

When authenticated, the bot enriches every nearby restaurant with one menu-API call (the verified endpoint that returns active offers, including percent/cap deals like "70% OFF UPTO ₹140").

## Distance filter
Results are filtered to restaurants within **7 km** by default (`SWIGGY_MAX_KM` env), using Swiggy's own last-mile distance data (`sla.lastMileTravel`). `/deals70` ranks places within that radius offering ≥70% off.

## Notes
- Data comes from Swiggy's public web API (`dapi`), used read-only, once per command — no spam.
- Deals change constantly; what the bot shows reflects what Swiggy serves at query time.
- Respect Swiggy's Terms of Service — this is for personal deal-checking, not scraping at scale.
