"""
Swiggy Coupons Telegram Bot
===========================
Finds Swiggy restaurants near you that are running coupon offers
(e.g. "70% OFF UPTO ₹140", "FLAT ₹125 OFF", "ITEMS AT ₹129", ...).

Commands:
  /start            - register & pick a location
  /coupons [arg]    - restaurants with coupon offers. arg can be:
        70          -> only deals ≥ 70% off
        biryani     -> only restaurants matching "biryani" (name/cuisine)
        80 dominos  -> combine both
  /deals            - top 10 best coupon offers at your location
  /whereami         - show current location
  /help             - this message

Location works via:
  * Tapping "Share Location" (live GPS)
  * Typing "lat,lng"            e.g. "12.9716,77.5946"
  * Typing a city/area name     e.g. "Indiranagar Bangalore"

Environment variables:
  TELEGRAM_BOT_TOKEN   (required)
  SWIGGY_SID           (optional) your Swiggy session cookie. When set,
                       offer banners/coupons become visible; without it
                       Swiggy serves anonymous data (may hide banners).
                       How to get it: log into swiggy.com in your browser,
                       DevTools (F12) > Application > Cookies > www.swiggy.com
                       > copy the value of the "sid" cookie.
  SWIGGY_LAT / SWIGGY_LNG  (optional) default location when the bot starts
"""

import os
import re
import html
import json
import logging
import asyncio
import threading
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import httpx
from aiogram import Bot, Dispatcher, Router, F
from aiogram.types import (
    Message,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    ReplyKeyboardMarkup,
    KeyboardButton,
    Location,
)
from aiogram.filters import CommandStart, Command
from aiogram.enums import ParseMode, ChatType
from threading import Thread
from flask import Flask
from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip().strip('"').strip("'")
SWIGGY_SID = os.getenv("SWIGGY_SID", "").strip().strip('"').strip("'")
SWIGGY_USER_ID = os.getenv("SWIGGY_USER_ID", "").strip().strip('"').strip("'")
DEFAULT_LAT = float(os.getenv("SWIGGY_LAT", "0") or 0)
DEFAULT_LNG = float(os.getenv("SWIGGY_LNG", "0") or 0)
MAX_KM = float(os.getenv("SWIGGY_MAX_KM", "7") or 7)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger("swiggy_bot")

# Flask keep-alive for Render
app = Flask(__name__)

@app.route('/')
def home():
    return "Swiggy coupons bot is alive!", 200

@app.route('/health')
def health():
    return {"status": "ok"}, 200

def run_flask():
    port = int(os.environ.get('PORT', 8081))
    app.run(host='0.0.0.0', port=port, threaded=True)

# ---------------------------------------------------------------------------
# Swiggy client
# ---------------------------------------------------------------------------
BASE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "*/*",
    "Accept-Language": "en-IN,en;q=0.9",
    "Referer": "https://www.swiggy.com/",
    "__fetch_req__": "true",
    "platform": "dweb",
    "user-id": "0",
    "content-type": "application/json",
}

@dataclass
class Coupon:
    text: str            # raw banner, e.g. "70% OFF UPTO ₹140"
    kind: str            # percent | flat | itemsat | other
    percent: Optional[int] = None
    cap: Optional[int] = None        # max discount in ₹ (for % deals)
    flat: Optional[int] = None       # ₹ off (flat deals)
    itemsat: Optional[int] = None    # ITEMS AT ₹x
    min_order: Optional[int] = None  # e.g. ₹50 OFF above ₹199

    def sort_key(self) -> float:
        """Rough 'goodness' score used for ranking deals."""
        if self.kind == "percent":
            p = (self.percent or 0) * 10
            cap_bonus = min(self.cap or 0, 500) / 5.0
            return p + cap_bonus
        if self.kind == "flat":
            return self.flat or 0
        if self.kind == "itemsat":
            return 40
        return 0


@dataclass
class Restaurant:
    id: str
    name: str
    area: str
    cuisines: List[str]
    cost_for_two: str
    rating: str
    total_ratings: str
    sla: str
    is_open: bool
    distance_km: Optional[float] = None
    coupons: List[Coupon] = field(default_factory=list)
    link: str = ""

    @property
    def best_coupon(self) -> Optional[Coupon]:
        if not self.coupons:
            return None
        return max(self.coupons, key=lambda c: c.sort_key())


def _to_km(sla: Optional[dict]) -> Optional[float]:
    """Extract distance in km from the sla block (lastMileTravel is in km)."""
    if not isinstance(sla, dict):
        return None
    val = sla.get("lastMileTravel")
    try:
        return float(val) if val is not None else None
    except (TypeError, ValueError):
        return None


def parse_offer_text(text: str) -> Coupon:
    """Parse an offer banner string into a structured Coupon."""
    t = (text or "").strip()
    low = t.lower()

    # "50% OFF above ₹199"
    m = re.search(r"(\d{1,2})\s*%\s*off.*?(?:above|for orders?\s*above)\s*[₹]?\s*(\d+)", low)
    if m:
        return Coupon(text=t, kind="percent", percent=int(m.group(1)), min_order=int(m.group(2)))

    # "70% OFF UPTO ₹140" / "20% OFF up to ₹50"
    m = re.search(r"(\d{1,2})\s*%\s*off.*?(?:upto|up to)\s*[₹]?\s*(\d+)", low)
    if m:
        return Coupon(text=t, kind="percent", percent=int(m.group(1)), cap=int(m.group(2)))

    # "50% OFF" (no cap)
    m = re.search(r"(\d{1,2})\s*%\s*off", low)
    if m:
        return Coupon(text=t, kind="percent", percent=int(m.group(1)))

    # "₹125 OFF above ₹299"
    m = re.search(r"[₹]\s*(\d+)\s*off.*?(?:above|upto)\s*[₹]?\s*(\d+)", low)
    if m:
        return Coupon(text=t, kind="flat", flat=int(m.group(1)), min_order=int(m.group(2)))

    # "FLAT ₹125 OFF"
    m = re.search(r"flat\s*[₹]\s*(\d+)\s*off", low)
    if m:
        return Coupon(text=t, kind="flat", flat=int(m.group(1)))

    # "ITEMS AT ₹129"
    m = re.search(r"items?\s*at\s*[₹]\s*(\d+)", low)
    if m:
        return Coupon(text=t, kind="itemsat", itemsat=int(m.group(1)))

    return Coupon(text=t, kind="other")


def extract_coupons(info: dict) -> List[Coupon]:
    coupons: List[Coupon] = []
    seen = set()

    v2 = info.get("aggregatedDiscountInfoV2") or {}
    header = (v2.get("header") or "").strip()
    if header:
        c = parse_offer_text(header)
        desc = (v2.get("shortDescription") or "").strip()
        if desc:
            c.text += f" ({desc})"
        coupons.append(c)
        seen.add(header)

    pres = info.get("restaurantOfferPresentationInfo") or {}
    offer_name = (pres.get("offerName") or "").strip()
    if offer_name and offer_name not in seen:
        coupons.append(parse_offer_text(offer_name))

    agg = info.get("aggregatedDiscountInfo") or {}
    agg_header = (agg.get("header") or "").strip()
    if agg_header and agg_header not in seen:
        coupons.append(parse_offer_text(agg_header))

    return [c for c in coupons if c.text]


class SwiggyClient:
    """Async client for Swiggy's public web API (dapi)."""

    LIST_URL = "https://www.swiggy.com/dapi/restaurants/list/v5"
    MENU_URL = "https://www.swiggy.com/dapi/menu/pl"
    GEOCODE_URL = "https://nominatim.openstreetmap.org/search"

    def __init__(self, sid: Optional[str] = None, user_id: Optional[str] = None, timeout: float = 20.0):
        headers = dict(BASE_HEADERS)
        if sid:
            # Accept either a bare sid value or a full Cookie header string
            headers["cookie"] = sid if "=" in sid else f"sid={sid}"
        if user_id:
            headers["user-id"] = user_id
        self._client = httpx.AsyncClient(headers=headers, timeout=timeout, follow_redirects=True)

    async def aclose(self):
        await self._client.aclose()

    # ------------------------------------------------------------------
    async def fetch_list_page(self, lat: float, lng: float, offset: str = "") -> dict:
        params: Dict[str, str] = {"lat": str(lat), "lng": str(lng), "page_type": "DESKTOP_WEB_LISTING"}
        if offset:
            params["offset"] = offset
        res = await self._client.get(self.LIST_URL, params=params)
        res.raise_for_status()
        try:
            data = res.json()
        except Exception as e:
            raise RuntimeError(f"Swiggy returned non-JSON response (HTTP {res.status_code}): {res.text[:120]}") from e
        return data

    async def fetch_menu(self, restaurant_id: str, lat: float, lng: float) -> Optional[dict]:
        """Authenticated menu call — this endpoint returns populated offer banners."""
        res = await self._client.get(
            self.MENU_URL,
            params={
                "page-type": "REGULAR_MENU",
                "complete-menu": "true",
                "lat": str(lat),
                "lng": str(lng),
                "restaurantId": str(restaurant_id),
                "submitAction": "ENTER",
            },
        )
        if res.status_code != 200:
            return None
        try:
            j = res.json()
        except Exception:
            return None
        info = next((c["card"]["card"]["info"] for c in j.get("data", {}).get("cards", []) if c.get("card", {}).get("card", {}).get("info")), None)
        return info

    async def get_restaurants(self, lat: float, lng: float, max_pages: int = 6) -> List[Restaurant]:
        """Paginate the nearby-restaurant listing, dedup by id."""
        by_id: Dict[str, dict] = {}
        order: List[str] = []
        offset = ""
        for _ in range(max_pages):
            page = await self.fetch_list_page(lat, lng, offset)
            data = page.get("data") or {}
            cards = data.get("cards") or []
            got = 0
            for c in cards:
                grid = (c.get("card") or {}).get("card") or {}
                restos = (grid.get("gridElements") or {}).get("infoWithStyle", {}).get("restaurants") or []
                for r in restos:
                    info = r.get("info") or {}
                    rid = str(info.get("id") or "")
                    if not rid or rid in by_id:
                        continue
                    by_id[rid] = r
                    order.append(rid)
                    got += 1
            page_offset = data.get("pageOffset") or {}
            new_offset = page_offset.get("nextOffset") or ""
            if not got or not new_offset or new_offset == offset:
                break
            offset = new_offset

        restaurants = []
        for rid in order:
            r = by_id[rid]
            info = r.get("info") or {}
            restaurants.append(
                Restaurant(
                    id=rid,
                    name=info.get("name") or "Unknown",
                    area=info.get("areaName") or info.get("locality") or "",
                    cuisines=info.get("cuisines") or [],
                    cost_for_two=info.get("costForTwo") or "",
                    rating=info.get("avgRatingString") or "--",
                    total_ratings=info.get("totalRatingsString") or "",
                    sla=(info.get("sla") or {}).get("slaString") or "",
                    is_open=bool(info.get("isOpen")),
                    distance_km=_to_km(info.get("sla")),
                    coupons=extract_coupons(info),
                    link=r.get("cta", {}).get("link") or "",
                )
            )
        return restaurants

    # ------------------------------------------------------------------
    async def get_offers(self, lat: float, lng: float, max_km: float = 7.0, min_pct: int = 0, max_rest: int = 30, concurrency: int = 6) -> List[Restaurant]:
        """Fetch nearby restaurants (deduped), then enrich each with its authenticated
        menu call (the verified endpoint that returns active coupon banners)."""
        base = await self.get_restaurants(lat, lng, max_pages=3)
        in_range = [r for r in base if r.distance_km is not None and r.distance_km <= max_km][:max_rest]
        sem = asyncio.Semaphore(concurrency)

        async def enrich(r: Restaurant):
            async with sem:
                info = await self.fetch_menu(r.id, lat, lng)
                if not info:
                    return
                coupons = extract_coupons(info)
                if coupons:
                    r.coupons = coupons
                sla_menu = info.get("sla") or {}
                km = _to_km(sla_menu)
                if km is not None:
                    r.distance_km = km

        await asyncio.gather(*(enrich(r) for r in in_range))

        hits = [r for r in in_range if r.coupons]
        if min_pct > 0:
            hits = [r for r in hits if any(cp.kind == "percent" and (cp.percent or 0) >= min_pct for cp in r.coupons)]
        hits.sort(key=lambda r: max((c.sort_key() for c in r.coupons), default=0), reverse=True)
        return hits

    # ------------------------------------------------------------------
    async def geocode(self, query: str) -> Optional[tuple]:
        res = await self._client.get(
            self.GEOCODE_URL,
            params={"q": query, "format": "json", "limit": 1},
            headers={"User-Agent": BASE_HEADERS["User-Agent"]},
        )
        res.raise_for_status()
        arr = res.json() or []
        if not arr:
            return None
        return float(arr[0]["lat"]), float(arr[0]["lon"])

# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------
MAX_MSG_LEN = 3900
PER_PAGE = 6


def fmt_coupon(c: Coupon) -> str:
    return html.escape(c.text)


def fmt_restaurant(r: Restaurant, idx: int) -> str:
    open_tag = "🟢" if r.is_open else "🔴 (closed)"
    name_esc = html.escape(r.name)
    area_esc = html.escape(r.area)
    cuisine_esc = html.escape(" • ".join(r.cuisines[:3]))
    dist = f"{r.distance_km:.1f} km" if r.distance_km is not None else ""
    extra = html.escape(" • ".join(x for x in (r.cost_for_two, dist) if x))
    lines = [f"{idx}. <b>{name_esc}</b> {open_tag}"]
    if area_esc:
        lines.append(f"   📍 {area_esc}")
    if cuisine_esc:
        lines.append(f"   🍽 {cuisine_esc}")
    lines.append(f"   ⭐ {r.rating} ({r.total_ratings})")
    if extra:
        lines.append(f"   ℹ️ {extra}")
    lines.append("   🎟 <i>Offers:</i>")
    for c in r.coupons:
        lines.append(f"   → {fmt_coupon(c)}")
    if r.link:
        lines.append(f'   🔗 <a href="{html.escape(r.link, quote=True)}">Open on Swiggy</a>')
    return "\n".join(lines)


def render_page(restaurants: List[Restaurant], start: int, per_page: int = PER_PAGE) -> Optional[str]:
    chunk = restaurants[start:start + per_page]
    if not chunk:
        return None
    parts = [fmt_restaurant(r, start + i + 1) for i, r in enumerate(chunk)]
    return "\n\n".join(parts)


def chunk_messages(text: str, limit: int = MAX_MSG_LEN) -> List[str]:
    if len(text) <= limit:
        return [text]
    parts, buf = [], ""
    for para in text.split("\n\n"):
        if len(buf) + len(para) + 2 > limit:
            if buf:
                parts.append(buf)
            buf = para
            if len(buf) > limit:
                parts.append(buf[:limit])
                buf = ""
        else:
            buf = f"{buf}\n\n{para}" if buf else para
    if buf:
        parts.append(buf)
    return parts


def keyboard_for(restaurants: List[Restaurant], start: int, per_page: int = PER_PAGE) -> InlineKeyboardMarkup:
    total = len(restaurants)
    has_next = start + per_page < total
    has_prev = start > 0
    row1 = []
    if has_prev:
        row1.append(InlineKeyboardButton(text="⬅️ Prev", callback_data=f"pg:{start - per_page}"))
    row1.append(InlineKeyboardButton(text=f"Page {start // per_page + 1}/{max(1, -(-total // per_page))}", callback_data="noop"))
    if has_next:
        row1.append(InlineKeyboardButton(text="Next ➡️", callback_data=f"pg:{start + per_page}"))
    return InlineKeyboardMarkup(inline_keyboard=[row1] if row1 else [])

# ---------------------------------------------------------------------------
# In-memory state
# ---------------------------------------------------------------------------
USER_LOC: Dict[int, tuple] = {}
RESULTS: Dict[int, List[Restaurant]] = {}
CLIENT: Optional[SwiggyClient] = None

router = Router()

# Location picker keyboard (with the native Telegram "Share Location" button)
def location_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📍 Share my location", request_location=True)],
        ],
        resize_keyboard=True,
        one_time_keyboard=True,
        input_field_placeholder="Share location, type 'lat,lng', or a city name",
    )

# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------
@router.message(CommandStart())
async def cmd_start(message: Message):
    uid = message.from_user.id
    if uid not in USER_LOC and DEFAULT_LAT and DEFAULT_LNG:
        USER_LOC[uid] = (DEFAULT_LAT, DEFAULT_LNG)
    greeting = (
        "🍔 <b>Swiggy Coupons Bot</b>\n\n"
        "I find Swiggy restaurants near you that are running coupon offers — "
        'things like "70% OFF UPTO ₹140", "FLAT ₹125 OFF", "ITEMS AT ₹129".\n\n'
        "First, tell me where you are. Tap <b>Share my location</b> below, "
        "type coordinates as <code>lat,lng</code> (e.g. <code>12.9716,77.5946</code>), "
        "or just send a city/area name (e.g. <code>Koramangala Bangalore</code>).\n\n"
        "Then use:\n"
        "🎟 <code>/coupons</code> — all restaurants with coupons\n"
        "🎟 <code>/coupons 70</code> — only ≥70% off\n"
        "🎟 <code>/coupons pizza</code> — pizza places with coupons\n"
        "🔥 <code>/deals</code> — top 10 best offers\n\n"
        "🎫 70% off hunters: use <code>/deals70</code> — ranked places within 7 km (change: <code>SWIGGY_MAX_KM</code> env)\n"
        "Also, retrieval is for personal price-checking — deal in your own account only."
    )
    await message.answer(greeting, reply_markup=location_kb(), parse_mode=ParseMode.HTML)


@router.message(Command("help"))
async def cmd_help(message: Message):
    await message.answer(
        "🎟 <code>/coupons</code> <i>[min%] [keyword]</i> — restaurants with coupons\n"
        "🔥 <code>/deals</code> — top 10 deals\n"
        "🔥 <code>/deals70</code> — places within <i>SWIGGY_MAX_KM</i> (default 7 km) with ≥70% off\n"
        "📍 <code>/whereami</code> — current saved location\n"
        "🔄 <code>/start</code> — set a new location\n\n"
        "Filter examples: <code>/coupons 70</code>, <code>/coupons biryani</code>, <code>/coupons 80 dominos</code>",
        parse_mode=ParseMode.HTML,
    )


@router.message(Command("whereami"))
async def cmd_whereami(message: Message):
    loc = USER_LOC.get(message.from_user.id)
    if not loc:
        await message.answer("No location yet. Send 📍 location, 'lat,lng', or a city name.")
        return
    await message.answer(f"Location: <b>{loc[0]:.4f}, {loc[1]:.4f}</b>", parse_mode=ParseMode.HTML)


@router.message(Command("deals"))
async def cmd_deals(message: Message, bot: Bot):
    await fetch_and_send(message, bot, min_percent=None, query="", limit=10)


@router.message(Command("deals70"))
async def cmd_deals70(message: Message, bot: Bot):
    """Restaurants within SWIGGY_MAX_KM (default 7 km) offering an offer ≥70% off."""
    await fetch_and_send(message, bot, min_percent=70, query="")


@router.message(Command("coupons"))
async def cmd_coupons(message: Message, bot: Bot):
    # parse args: /coupons [min_percent] [filter words...]
    text = (message.text or "").strip()
    parts = text.split(maxsplit=1)
    args = parts[1] if len(parts) > 1 else ""
    min_percent = None
    words = []
    for tok in args.split():
        if re.fullmatch(r"\d{1,3}", tok):
            n = int(tok)
            if 1 <= n <= 100:
                min_percent = n
                continue
        words.append(tok)
    query = " ".join(words).strip().lower()
    if min_percent is None and query.isdigit():
        min_percent = int(query)
        query = ""
    await fetch_and_send(message, bot, min_percent=min_percent, query=query)


async def fetch_and_send(message: Message, bot: Bot, min_percent: Optional[int], query: str, limit: Optional[int] = None):
    uid = message.from_user.id
    loc = USER_LOC.get(uid)
    if not loc:
        await message.answer("📍 No location yet — send your location first (tap the button or type 'lat,lng').")
        return

    wait = await message.answer("⏳ Searching Swiggy...")
    try:
        if SWIGGY_SID:
            # Authenticated mode: enrich each in-range restaurant via its menu call
            restaurants = await CLIENT.get_offers(*loc, max_km=MAX_KM, min_pct=min_percent or 0)
        else:
            restaurants = await CLIENT.get_restaurants(*loc)
    except Exception as e:
        logger.exception("get_restaurants failed")
        await wait.edit_text(f"❌ Couldn't reach Swiggy: <code>{html.escape(str(e))[:200]}</code>", parse_mode=ParseMode.HTML)
        return

    def matches(r: Restaurant) -> bool:
        # distance filter: keep restaurants within SWIGGY_MAX_KM (default 7 km)
        if r.distance_km is not None and r.distance_km > MAX_KM:
            return False
        if query and not (
            query in r.name.lower() or any(query in c.lower() for c in r.cuisines)
        ):
            return False
        if min_percent is not None and not any(
            c.kind == "percent" and (c.percent or 0) >= min_percent for c in r.coupons
        ):
            return False
        return True

    hits = [r for r in restaurants if r.coupons and matches(r)]
    hits.sort(key=lambda r: max((c.sort_key() for c in r.coupons), default=0), reverse=True)
    if limit:
        hits = hits[:limit]

    RESULTS[uid] = hits
    await wait.delete()
    if not hits:
        tip = ""
        if not SWIGGY_SID:
            tip = ("\n\n💡 <b>Tip:</b> Swiggy hides coupon banners for anonymous access. "
                   "Set the <code>SWIGGY_SID</code> env var with your own Swiggy session "
                   'cookie ("sid") to see them — see the README.')
        wait_msg = (
            f"Found {len(restaurants)} delivery restaurants within {MAX_KM:.0f} km of your location, "
            f"but none matched coupon offers"
            + (f" ≥ {min_percent}% off" if min_percent else "")
            + (f" for “{html.escape(query)}”" if query else "") + "." + tip
        )
        await message.answer(wait_msg, parse_mode=ParseMode.HTML)
        return

    page_text = render_page(hits, 0)
    header = f"🎟 <b>{len(hits)} restaurants with coupon offers</b>\n\n"
    await message.answer(header + page_text, reply_markup=keyboard_for(hits, 0), parse_mode=ParseMode.HTML)


@router.callback_query(F.data.startswith("pg:"))
async def cb_paginate(callback):
    uid = callback.from_user.id
    start = int(callback.data.split(":")[1])
    hits = RESULTS.get(uid) or []
    if not hits:
        await callback.answer("Results expired — run /coupons again", show_alert=True)
        return
    page_text = render_page(hits, start)
    if page_text is None:
        await callback.answer("No more results", show_alert=False)
        return
    header = f"🎟 <b>{len(hits)} restaurants with coupon offers</b>\n\n"
    await callback.message.edit_text(header + page_text, reply_markup=keyboard_for(hits, start), parse_mode=ParseMode.HTML)
    await callback.answer()


@router.callback_query(F.data == "noop")
async def cb_noop(callback):
    await callback.answer()


# Fallback: treat any text like "lat,lng", city/area or cached result reuse
@router.message(F.text & F.chat.type.in_({ChatType.PRIVATE}))
async def fallback_text(message: Message, bot: Bot):
    text = (message.text or "").strip()
    m = re.fullmatch(r"(-?\d{1,2}(?:\.\d+)?),\s*(-?\d{1,3}(?:\.\d+)?)", text)
    if m:
        USER_LOC[message.from_user.id] = (float(m.group(1)), float(m.group(2)))
        await message.answer(f"📍 Location set: <b>{m.group(1)}, {m.group(2)}</b>\nNow try /coupons", parse_mode=ParseMode.HTML)
        return

    non_geo = await CLIENT.geocode(text)
    if non_geo:
        USER_LOC[message.from_user.id] = non_geo
        await message.answer(
            f"📍 Location set: <b>{html.escape(text)}</b> → {non_geo[0]:.4f}, {non_geo[1]:.4f}\nNow try /coupons",
            parse_mode=ParseMode.HTML,
        )
        return

    await message.answer(
        "🤷 Couldn't understand that. Location looks like: pasting <code>12.9716,77.5946</code>, "
        "sharing 📍 location, or a place name (e.g. <code>Koramangala Bangalore</code>). Or use /coupons"
    )

# Telegram Location message
@router.message(F.location)
async def loc_message(message: Message):
    loc: Location = message.location
    USER_LOC[message.from_user.id] = (loc.latitude, loc.longitude)
    await message.answer(
        f"📍 Location set: <b>{loc.latitude:.4f}, {loc.longitude:.4f}</b>\nNow try /coupons or /deals",
        parse_mode=ParseMode.HTML,
    )

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    global CLIENT
    if not TELEGRAM_BOT_TOKEN:
        raise SystemExit("TELEGRAM_BOT_TOKEN env var is required. See README.")

    Thread(target=run_flask, daemon=True).start()

    CLIENT = SwiggyClient(sid=SWIGGY_SID or None, user_id=SWIGGY_USER_ID or None)
    if SWIGGY_SID:
        logger.info("Running with your Swiggy session (sid) — coupon banners enabled")
    else:
        logger.info("Running anonymously — set SWIGGY_SID env var to see coupon banners")

    async def _run():
        bot = Bot(token=TELEGRAM_BOT_TOKEN)
        dp = Dispatcher()
        dp.include_router(router)
        try:
            await dp.start_polling(bot, close_bot_session=True)
        finally:
            await CLIENT.aclose()

    try:
        logger.info("Bot starting...")
        asyncio.run(_run())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot stopped")

if __name__ == "__main__":
    main()
