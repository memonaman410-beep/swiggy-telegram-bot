# Swiggy Coupons Telegram Bot 🍔🎟

Telegram bot that finds Swiggy food-delivery restaurants near you that run coupon offers —
**"70% OFF UPTO ₹140"**, **"FLAT ₹125 OFF"**, **"ITEMS AT ₹129"** and other banners.

## Features
- 📍 Location via **share-GPS**, typed `lat,lng`, or a city/area name (auto-geocoded)
- 🎟 `/coupons` — all nearby restaurants with coupons
- 🎟 `/coupons 70` — only deals ≥ 70% off
- 🎟 `/coupons pizza` — only places matching name/cuisine
- 🥇 `/deals` — top 10 best offers, ranked (percent+cap first, then flat)
- 🎫 `/deals70` — places within `SWIGGY_MAX_KM` (default **7 km**) offering ≥ 70% off
- `/whereami` — show current saved location
- Paginated inline Next/Prev buttons + direct Swiggy links

## Commands
| Command | What it shows |
|---|---|
| `/start` | Intro + location picker |
| `/coupons` | All places with coupons within radius |
| `/coupons 70` | Only ≥ 70% off deals |
| `/coupons 80 dominos` | ≥ 80% off + keyword filter |
| `/deals` | Best 10 deals |
| `/deals70` | ≥ 70% off within 7 km |
| `/whereami` | Current location |
| `/help` | Command list |

## Quick start (local)

```bash
git clone <this-repo-url>
cd swiggy_telegram_bot
pip install -r requirements.txt
copy .env.example .env      # Windows  (Linux/macOS: cp .env.example .env)
# edit .env: paste YOUR TELEGRAM_BOT_TOKEN and (optional) SWIGGY_SID
python swiggy_bot.py
```

In Telegram, open your bot → `/start` → share location → `/deals70`.

## Deploy free (Render.com)

1. **Push this repo to GitHub** — keep `.env` ignored; render.yaml stores nothing secret (`sync: false`).
2. On [dashboard.render.com](https://dashboard.render.com) → **New → Blueprint** → pick the repo → the `render.yaml` service is created automatically.
3. Paste the env vars when prompted (`TELEGRAM_BOT_TOKEN`, optionally `SWIGGY_SID`, `SWIGGY_USER_ID`).
4. **Deploy** — the free plan runs a long-polling bot on one free web service; the Flask side listens on `$PORT`.
5. The first deploy will show "Bot is alive!" at the Service URL. Test `/start` in Telegram.

**Limit:** one free web service per Render account (.Option: for a second Telegram service, use Telegram webhooks or Railway/Fly free tier.)

## Seeing real coupon banners (important)

Swiggy strips coupon/offer banners from responses for **anonymous access** — verified live across the listing, search and menu endpoints. Anonymous mode shows restaurants/ratings only. To see actual coupons, use your own logged-in session (read-only listing data; the bot never orders anything):

```
SWIGGY_SID=<the "sid" cookie value — or the full "sid=...; aws-waf-token=..." Cookie header>
SWIGGY_USER_ID=<your numeric Swiggy user id>   # optional
```

Get `sid` by logging into [swiggy.com](https://www.swiggy.com/) in a browser →
**F12 → Application → Cookies → `www.swiggy.com` → copy the `sid` value.**

If you get `HTTP 202` responses, copy the full `cookie` request header from any dapi
request (F12 → Network → copy → copy value) into `SWIGGY_SID`.

## Distance filter
Results are filtered by Swiggy's own last-mile distance (`sla.lastMileTravel`). Change the radius with `SWIGGY_MAX_KM`.

## Notes
- Data comes from Swiggy's public web API (`dapi`), used read-only, one scoped command — no spam, no login of random users.
- Deals change constantly; what the bot shows reflects what Swiggy serves at query time.
- Respect Swiggy's Terms of Service — for personal deal-checking, not scraping at scale.
