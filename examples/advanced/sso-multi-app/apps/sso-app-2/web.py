#!/usr/bin/env python3
"""
FLUX v6.5 - Sentinel Prime
==========================
Backend: FastAPI + MongoDB + Finviz + Azure OpenAI + Firecrawl
"""

import asyncio
import json
import logging
import math
import os
import re
from pathlib import Path
from typing import Any

import pandas as pd
from fastapi import Depends, FastAPI, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

# Import shared security utilities
try:
    from shared_security import get_cookie_settings
except ImportError:
    # Fallback if shared_security not available (shouldn't happen in normal usage)
    def get_cookie_settings():
        return {"httponly": True, "samesite": "lax", "secure": False}

# --- External Clients ---
try:
    import httpx

    HTTPX_AVAILABLE = True
except ImportError:
    HTTPX_AVAILABLE = False

try:
    from openai import AzureOpenAI

    OPENAI_AVAILABLE = True
except ImportError:
    OPENAI_AVAILABLE = False

# --- Database Mock/Real ---
try:
    from mdb_engine import MongoDBEngine
    from mdb_engine.dependencies import get_scoped_db
    from mdb_engine.utils import clean_mongo_doc, clean_mongo_docs
except ImportError:
    print("WARNING: mdb_engine not found. Using Mock Engine.")

    class MongoDBEngine:
        def __init__(self, mongo_uri, db_name):
            pass

        def create_app(self, **kwargs):
            return FastAPI(**kwargs)

    async def get_scoped_db():
        class MockDB:
            user_balances = None
            user_positions = None

        return MockDB()

    def clean_mongo_docs(docs):
        return docs

    def clean_mongo_doc(doc):
        return doc


# --- Financial Data ---
try:
    from finvizfinance.quote import finvizfinance
    from finvizfinance.screener.overview import Overview
    from finvizfinance.screener.technical import Technical

    FINVIZ_AVAILABLE = True
except ImportError:
    FINVIZ_AVAILABLE = False
    print("WARNING: finvizfinance not installed.")

# --- Logging & Config ---
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("FLUX")

try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).parent / ".env")
except ImportError:
    pass

# --- Initialize Clients ---
openai_client = None
if OPENAI_AVAILABLE and os.getenv("AZURE_OPENAI_ENDPOINT"):
    try:
        openai_client = AzureOpenAI(
            azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT"),
            api_key=os.getenv("AZURE_OPENAI_API_KEY"),
            api_version=os.getenv("AZURE_OPENAI_API_VERSION", "2024-02-15-preview"),
        )
        logger.info("Azure OpenAI: Connected")
    except (ValueError, KeyError) as e:
        logger.exception(f"Azure OpenAI Connection Failed: {e}")

FIRECRAWL_API_KEY = os.getenv("FIRECRAWL_API_KEY")

# --- App Initialization ---
try:
    engine = MongoDBEngine(
        mongo_uri=os.getenv("MONGODB_URI", "mongodb://localhost:27017"),
        db_name=os.getenv("MONGODB_DB", "flux_trading_db"),
    )
    _app_obj = engine.create_app(
        slug="flux",
        manifest=Path(__file__).parent / "manifest.json",
        title="FLUX Prime",
        version="6.5.0",
    )
    app = _app_obj.app if hasattr(_app_obj, "app") else _app_obj
except (AttributeError, ImportError, ValueError, TypeError):
    app = FastAPI(title="FLUX Fallback")

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


# --- Models ---
class TradeRequest(BaseModel):
    symbol: str
    side: str
    qty: float
    stop_loss: float | None = 0.0
    take_profit: float | None = 0.0


# --- Helper Functions ---


def clean_float(val: Any) -> float | None:
    if val is None or pd.isna(val):
        return None
    if isinstance(val, int | float):
        return None if (math.isnan(val) or math.isinf(val)) else float(val)
    s = str(val).replace(",", "").replace("$", "").replace("%", "").strip()
    if s.lower() in ["-", "nan", "n/a", ""]:
        return None
    try:
        multiplier = 1
        if s.endswith("B"):
            multiplier, s = 1_000_000_000, s[:-1]
        elif s.endswith("M"):
            multiplier, s = 1_000_000, s[:-1]
        elif s.endswith("K"):
            multiplier, s = 1_000, s[:-1]
        return float(s) * multiplier
    except (ValueError, TypeError):
        return None


async def _scrape_firecrawl(url: str, client: httpx.AsyncClient) -> str | None:
    """Scrapes content using Firecrawl v2 API to get full article text."""
    if not FIRECRAWL_API_KEY or not url:
        return None
    try:
        resp = await client.post(
            "https://api.firecrawl.dev/v2/scrape",
            json={"url": url},
            headers={
                "Authorization": f"Bearer {FIRECRAWL_API_KEY}",
                "Content-Type": "application/json",
            },
            timeout=15.0,
        )
        if resp.status_code == 200:
            data = resp.json()
            # Firecrawl v2 response structure: data.success, data.data.markdown or data.data.content
            if data.get("success") and data.get("data"):
                content = data["data"].get("markdown") or data["data"].get("content") or ""
                return content if content else None
            logger.warning(f"Firecrawl v2: Unexpected response structure for {url}")
            return None
        else:
            logger.warning(
                f"Firecrawl v2 failed for {url}: Status {resp.status_code}, {resp.text[:200]}"
            )
            return None
    except (httpx.HTTPError, httpx.RequestError, httpx.TimeoutException) as e:
        logger.warning(f"Firecrawl scrape failed for {url}: {e}")
        return None


async def _get_sentinel_analysis(symbol: str, price: float, techs: dict, news: list[dict]) -> dict:
    """
    Generates Institutional Grade Analysis.
    """
    if not openai_client:
        return {
            "verdict": "NOT BUY",
            "confidence": 0,
            "reasoning": "AI Configuration Missing. Cannot generate analysis without AI service.",
            "risk_level": "UNKNOWN",
        }

    # Construct Context from News
    news_context = ""
    if news:
        for idx, item in enumerate(news):
            # Include more content so AI can extract meaningful highlights
            content_snippet = (item.get("content") or "No content available.")[:1000]
            news_context += (
                f"NEWS ARTICLE {idx+1}:\nTitle: {item['title']}\nContent: {content_snippet}\n\n"
            )
    else:
        news_context = "No recent specific news found. Rely on Technicals."

    prompt = f"""
    You are SENTINEL, a Senior Risk Manager at a top-tier quantitative hedge fund.

    TARGET ASSET: {symbol} (Current Price: ${price})

    TECHNICAL DATA:
    - RSI (14): {techs.get('rsi', 'N/A')} (Over 70=Overbought, Under 30=Oversold)
    - Relative Volume: {techs.get('rel_volume', 'N/A')} (Values > 1.5 indicate high activity)
    - SMA200: {techs.get('sma200', 'N/A')}
    - Beta: {techs.get('beta', 'N/A')}

    INTELLIGENCE DOSSIER (Scraped News):
    {news_context}

    MISSION:
    Synthesize technicals and news to determine if this is a BUY or NOT BUY opportunity.
    Be decisive - there is no neutral. Every stock is either worth buying now or not worth
    buying now.

    CRITICAL: In your reasoning, include specific highlights and key points from the news
    articles provided. Quote or paraphrase the most relevant news details that support your
    BUY or NOT BUY decision. If multiple news sources are provided, reference the most
    impactful ones.

    OUTPUT FORMAT (Strict JSON):
    {{
        "verdict": "BUY" | "NOT BUY",
        "confidence": integer (0-100),
        "reasoning": "A clear, detailed 4-5 sentence explanation that explains WHY you "
                     "recommend BUY or NOT BUY. MUST include specific highlights from the "
                     "news articles (quote key points, mention specific catalysts or "
                     "concerns). Also reference specific technical indicators (RSI, volume, "
                     "price action). Be specific about what makes this a good or bad "
                     "opportunity right now.",
        "risk_level": "LOW" | "MEDIUM" | "HIGH" | "EXTREME",
        "projected_target": float (realistic take profit price based on technical resistance
                                   levels, news catalysts, and risk/reward. For BUY: typically
                                   5-15% above current price. For NOT BUY: set to current price
                                   or slightly below),
        "projected_stop": float (logical stop loss price based on support levels and risk
                                 tolerance. For BUY: typically 3-8% below current price, below
                                 key support. For NOT BUY: set to current price or slightly
                                 above),
        "opportunity_score": integer (0-100),
        "recommendations": ["specific actionable bullet 1", "specific actionable bullet 2",
                           "specific actionable bullet 3"]
    }}

    STOP LOSS & TAKE PROFIT GUIDELINES:
    - Calculate based on current price: ${price}
    - Consider volatility (Beta), support/resistance levels (SMA200), and RSI levels
    - Stop loss should be tight enough to limit losses but wide enough to avoid false breakouts
    - Take profit should be realistic based on technical patterns and news catalysts
    - Risk/Reward ratio should ideally be 1:2 or better (stop loss distance vs take profit
      distance)

    DECISION LOGIC:
    1. BUY if: Strong technical setup (oversold RSI < 30, high volume, bullish pattern)
       AND/OR positive news catalysts. Even if slightly overbought (RSI 70-75), still BUY if
       news is strongly positive.
    2. NOT BUY if: Overbought conditions (RSI > 75) with no positive news, OR negative news
       outweighs technicals, OR both technicals and news are neutral/negative.
    3. If technicals are mixed but news is strongly positive → BUY
    4. If technicals are bullish but news is strongly negative → NOT BUY (news trumps
       technicals)
    5. Never return "HOLD" or "NEUTRAL" - always choose BUY or NOT BUY based on whether there's
       a clear edge right now.
    6. Do not use markdown formatting in the JSON keys/values.
    """

    try:
        response = await run_in_threadpool(
            lambda: openai_client.chat.completions.create(
                model=os.getenv("AZURE_OPENAI_DEPLOYMENT_NAME", "gpt-4o"),
                messages=[
                    {
                        "role": "system",
                        "content": "You are a JSON-only financial API. Output valid JSON.",
                    },
                    {"role": "user", "content": prompt},
                ],
                response_format={"type": "json_object"},
                temperature=0.3,
                max_tokens=800,
            )
        )
        return json.loads(response.choices[0].message.content)
    except (ValueError, KeyError, AttributeError) as e:
        logger.exception(f"AI Error: {e}")
        return {
            "verdict": "NOT BUY",
            "reasoning": "Analysis computation failed. Unable to generate recommendation.",
            "confidence": 0,
        }


async def _fetch_quote_data(symbol: str) -> dict:
    if not FINVIZ_AVAILABLE:
        return {"error": "Market Data Module Missing"}

    try:
        # 1. Fetch Basic Data (Blocking)
        stock = await run_in_threadpool(lambda: finvizfinance(symbol))

        # 2. Parallel Fetch: Fundamentals + News Lists
        fund_task = run_in_threadpool(stock.ticker_fundament)
        news_list_task = run_in_threadpool(stock.ticker_news)

        results = await asyncio.gather(fund_task, news_list_task, return_exceptions=True)

        # Handle Fundamentals
        fund = results[0]
        if isinstance(fund, Exception):
            return {"error": "Ticker not found"}

        def get(k):
            return clean_float(fund.get(k))

        price = get("Price") or get("Current Price")
        if not price:
            return {"error": "Price unavailable"}

        technicals = {
            "rsi": get("RSI (14)"),
            "rel_volume": get("Rel Volume"),
            "sma200": get("SMA200"),
            "beta": get("Beta"),
        }

        # Handle News & Scraping
        raw_news = results[1]
        news_items = []

        if isinstance(raw_news, Exception):
            logger.warning(f"News fetch error for {symbol}: {raw_news}")
        elif hasattr(raw_news, "head") and not raw_news.empty:
            top_news = raw_news.head(3)  # Top 3 articles
            logger.info(
                f"Found {len(top_news)} news articles for {symbol}. "
                f"Columns: {list(top_news.columns)}"
            )

            async with httpx.AsyncClient() as client:
                scrape_tasks = []

                for idx, row in top_news.iterrows():
                    # Try multiple possible column name variations
                    link = (
                        row.get("Link")
                        or row.get("link")
                        or row.get("URL")
                        or row.get("url")
                        or row.get("href")
                        or row.get("Href")
                    )
                    title = (
                        row.get("Title")
                        or row.get("title")
                        or row.get("Headline")
                        or row.get("headline")
                        or f"News Article {idx + 1}"
                    )

                    # Clean title - remove non-ASCII characters
                    title = re.sub(r"[^\x00-\x7F]+", "", str(title)).strip()

                    # Always add news item with URL and title, even if scraping fails
                    if link:
                        # Ensure link is a valid URL
                        if not link.startswith(("http://", "https://")):
                            if link.startswith("//"):
                                link = "https:" + link
                            elif link.startswith("/"):
                                link = "https://finviz.com" + link

                        item = {"title": title, "url": link, "content": None}
                        news_items.append(item)
                        logger.debug(f"Added news item: {title[:50]}... -> {link[:50]}...")

                        # Attempt to scrape content if API key available
                        if FIRECRAWL_API_KEY:
                            scrape_tasks.append(_scrape_firecrawl(link, client))
                        else:
                            scrape_tasks.append(asyncio.sleep(0))  # No-op task
                    else:
                        logger.warning(
                            f"Skipping news item {idx} - missing link. Row data: {dict(row)}"
                        )

                # Execute Scrapes and populate content
                if scrape_tasks and len(scrape_tasks) == len(news_items):
                    contents = await asyncio.gather(*scrape_tasks, return_exceptions=True)
                    for i, c in enumerate(contents):
                        if isinstance(c, str) and c:
                            news_items[i]["content"] = c
                            logger.debug(
                                f"Scraped content for {news_items[i].get('url')}: {len(c)} chars"
                            )
                        elif isinstance(c, Exception):
                            logger.debug(f"Scraping failed for {news_items[i].get('url')}: {c}")
        else:
            logger.info(f"No news data available for {symbol} (raw_news type: {type(raw_news)})")

        # 3. AI Analysis
        analysis = await _get_sentinel_analysis(symbol, price, technicals, news_items)

        logger.info(f"Quote data for {symbol}: {len(news_items)} news items found")
        if news_items:
            logger.info(f"News items: {[item.get('title', 'No title') for item in news_items]}")

        return {
            "symbol": symbol,
            "price": price,
            "technicals": technicals,
            "sentinel_analysis": analysis,
            "news_items": news_items if news_items else [],  # Always return list, even if empty
        }
    except Exception as e:
        logger.exception(f"Quote fetch fatal error: {e}")
        return {"error": str(e)}


# --- Routes ---


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    user = getattr(request.state, "user", {"email": "demo@flux.com"})
    return templates.TemplateResponse("index.html", {"request": request, "user": user})


@app.get("/api/portfolio")
async def get_portfolio(request: Request, db=Depends(get_scoped_db)):
    user = getattr(request.state, "user", {"email": "demo@flux.com"})
    if not hasattr(db, "user_balances"):
        return {"cash": 50000.0, "equity": 50000.0, "positions_count": 0}

    bal = await db.user_balances.find_one({"user_email": user["email"]})
    if not bal:
        bal = {"user_email": user["email"], "cash": 50000.0}
        await db.user_balances.insert_one(bal)

    positions = await db.user_positions.find({"user_email": user["email"]}).to_list(None)
    equity = bal["cash"]
    for p in positions:
        equity += p["qty"] * p.get("last_price", 0)

    return {"cash": bal["cash"], "equity": equity, "positions_count": len(positions)}


@app.get("/api/positions")
async def get_positions(request: Request, db=Depends(get_scoped_db)):
    user = getattr(request.state, "user", {"email": "demo@flux.com"})
    if not hasattr(db, "user_positions"):
        return []

    positions = await db.user_positions.find({"user_email": user["email"]}).to_list(None)
    enhanced = []
    for p in positions:
        # Get current price - fetch fresh quote if needed
        avg_price = p.get("avg_price", 0)
        last_price = p.get("last_price", avg_price)

        # Try to get fresh price (async, but we'll use cached if available)
        # For now, use last_price or avg_price
        curr = last_price if last_price else avg_price

        # Calculate P&L
        pnl = (curr - avg_price) * p["qty"]
        pct = ((curr - avg_price) / avg_price) * 100 if avg_price > 0 else 0

        # Ensure stop_loss and take_profit are included (with defaults if missing)
        stop_loss = p.get("stop_loss") or (avg_price * 0.95)
        take_profit = p.get("take_profit") or (avg_price * 1.10)

        p.update(
            {
                "pnl": pnl,
                "pnl_pct": pct,
                "last_price": curr,
                "stop_loss": stop_loss,
                "take_profit": take_profit,
            }
        )
        enhanced.append(p)
    return JSONResponse(clean_mongo_docs(enhanced))


@app.get("/api/quote/{symbol}")
async def get_quote(symbol: str):
    data = await _fetch_quote_data(symbol.upper())
    if "error" in data:
        return JSONResponse(data, 404)
    return data


@app.get("/api/scan")
async def scan_market(filter_type: str = "oversold"):
    filters = {
        "oversold": {"RSI (14)": "Oversold (30)", "Relative Volume": "Over 1.5"},
        "breakout": {"Pattern": "Wedge Up", "Relative Volume": "Over 2"},
        "squeeze": {"Pattern": "Wedge", "Float Short": "Over 10%"},
    }
    sel_filter = filters.get(filter_type, filters["oversold"])

    if not FINVIZ_AVAILABLE:
        return []
    try:
        # Use Technical screener for the filter (has RSI filters)
        screener = Technical()
        screener.set_filter(filters_dict=sel_filter)
        df = await run_in_threadpool(screener.screener_view)
        results = []
        if df is not None and not df.empty:
            # Get Overview screener data which has Relative Volume and other complete metrics
            overview_screener = Overview()
            overview_screener.set_filter(filters_dict=sel_filter)
            overview_df = await run_in_threadpool(overview_screener.screener_view)

            # Create a lookup dict from overview for relative volume and other metrics
            overview_lookup = {}
            if overview_df is not None and not overview_df.empty:
                logger.debug(f"Overview screener columns: {list(overview_df.columns)}")
                for _, row in overview_df.iterrows():
                    ticker = row.get("Ticker")
                    if ticker:
                        overview_lookup[ticker] = row

            # Helper function to extract values with fallbacks
            def safe_get(row_dict, *keys):
                for key in keys:
                    val = row_dict.get(key)
                    if val is not None and str(val).strip() not in ["-", "N/A", ""]:
                        return clean_float(val)
                return None

            # Helper to get from overview lookup
            def get_from_overview(ticker, *keys):
                if ticker not in overview_lookup:
                    return None
                overview_row = overview_lookup[ticker]
                for key in keys:
                    val = overview_row.get(key)
                    if val is not None and str(val).strip() not in ["-", "N/A", ""]:
                        return clean_float(val)
                return None

            for _, row in df.head(15).iterrows():
                ticker = row.get("Ticker")
                if not ticker:
                    continue

                # Get relative volume - prioritize overview screener, then technical,
                # then individual fetch
                rel_vol = get_from_overview(
                    ticker, "Rel Volume", "Relative Volume", "Rel Vol", "Rel. Volume"
                )
                if rel_vol is None:
                    rel_vol = safe_get(
                        row, "Rel Volume", "Relative Volume", "Rel Vol", "Rel. Volume"
                    )

                # If still not found, fetch individual ticker (only for first few to avoid
                # rate limits)
                if rel_vol is None and len([r for r in results if r.get("rel_volume") is None]) < 3:
                    try:
                        stock = await run_in_threadpool(lambda t=ticker: finvizfinance(t))
                        fund = await run_in_threadpool(stock.ticker_fundament)
                        if fund:
                            rel_vol = clean_float(fund.get("Rel Volume")) or clean_float(
                                fund.get("Relative Volume")
                            )
                            if rel_vol:
                                logger.info(f"Fetched rel volume for {ticker}: {rel_vol}")
                    except (ValueError, KeyError, AttributeError) as e:
                        logger.debug(f"Could not fetch rel volume for {ticker}: {e}")

                results.append(
                    {
                        "symbol": ticker,
                        "price": safe_get(row, "Price") or get_from_overview(ticker, "Price"),
                        "signal": filter_type.upper(),
                        "rsi": safe_get(row, "RSI (14)", "RSI", "RSI(14)")
                        or get_from_overview(ticker, "RSI (14)", "RSI"),
                        "rel_volume": rel_vol,
                        "sma200": safe_get(row, "SMA200", "SMA 200")
                        or get_from_overview(ticker, "SMA200", "SMA 200"),
                        "beta": safe_get(row, "Beta") or get_from_overview(ticker, "Beta"),
                        "change": safe_get(row, "Change", "Change %", "Change%")
                        or get_from_overview(ticker, "Change", "Change %"),
                        "volume": safe_get(row, "Volume") or get_from_overview(ticker, "Volume"),
                        "sma50": safe_get(row, "SMA50", "SMA 50")
                        or get_from_overview(ticker, "SMA50", "SMA 50"),
                        "price_sma200": safe_get(row, "Price", "Current Price"),
                    }
                )
        return results
    except Exception as e:
        logger.exception(f"Scanner error: {e}")
        return []


@app.post("/api/trade")
async def execute_trade(trade: TradeRequest, request: Request, db=Depends(get_scoped_db)):
    user = getattr(request.state, "user", {"email": "demo@flux.com"})

    # Log for debugging
    logger.info(
        f"Trade request: {trade.symbol} {trade.side} {trade.qty} by {user.get('email', 'unknown')}"
    )

    if not hasattr(db, "user_balances"):
        logger.error("DB Unavailable - user_balances collection missing")
        return JSONResponse({"success": False, "error": "DB Unavailable"}, 500)

    quote = await _fetch_quote_data(trade.symbol)
    if "error" in quote:
        return JSONResponse({"success": False, "error": "Market data error"}, 400)

    price = quote["price"]
    cost = price * trade.qty

    bal = await db.user_balances.find_one({"user_email": user["email"]})
    if not bal:
        bal = {"user_email": user["email"], "cash": 50000.0}

    if trade.side.lower() == "buy":
        if bal["cash"] < cost:
            return JSONResponse({"success": False, "error": "Insufficient Funds"}, 400)
        await db.user_balances.update_one({"user_email": user["email"]}, {"$inc": {"cash": -cost}})

        existing = await db.user_positions.find_one(
            {"user_email": user["email"], "symbol": trade.symbol}
        )
        if existing:
            new_qty = existing["qty"] + trade.qty
            new_avg = ((existing["avg_price"] * existing["qty"]) + cost) / new_qty
            await db.user_positions.update_one(
                {"_id": existing["_id"]},
                {"$set": {"qty": new_qty, "avg_price": new_avg, "last_price": price}},
            )
        else:
            await db.user_positions.insert_one(
                {
                    "user_email": user["email"],
                    "symbol": trade.symbol,
                    "qty": trade.qty,
                    "avg_price": price,
                    "last_price": price,
                    "stop_loss": trade.stop_loss,
                    "take_profit": trade.take_profit,
                }
            )

    elif trade.side.lower() == "sell":
        existing = await db.user_positions.find_one(
            {"user_email": user["email"], "symbol": trade.symbol}
        )
        if not existing or existing["qty"] < trade.qty:
            return JSONResponse({"success": False, "error": "Insufficient Holdings"}, 400)

        proceeds = price * trade.qty
        await db.user_balances.update_one(
            {"user_email": user["email"]}, {"$inc": {"cash": proceeds}}
        )

        if abs(existing["qty"] - trade.qty) < 0.0001:
            await db.user_positions.delete_one({"_id": existing["_id"]})
        else:
            await db.user_positions.update_one(
                {"_id": existing["_id"]}, {"$inc": {"qty": -trade.qty}}
            )

    return {"success": True, "price": price}


@app.post("/logout")
async def logout(request: Request):
    """Logout and revoke token."""
    from mdb_engine.auth.shared_users import SharedUserPool

    pool: SharedUserPool = getattr(app.state, "user_pool", None)

    # Get token from cookie
    token = request.cookies.get("mdb_auth_token")

    # Revoke token if we have pool and token
    if pool and token:
        try:
            await pool.revoke_token(token, reason="logout")
        except (AttributeError, TypeError, ValueError, KeyError) as e:
            logger.warning(f"Failed to revoke token: {e}")

    # Create response redirecting to auth hub
    auth_hub_url = os.getenv("AUTH_HUB_URL", "http://localhost:8000")
    response = RedirectResponse(url=f"{auth_hub_url}/login", status_code=302)

    # Delete cookie
    cookie_settings = get_cookie_settings()
    response.delete_cookie(
        "mdb_auth_token",
        path="/",
        domain=None,  # Let browser handle domain
        secure=cookie_settings["secure"],
        samesite=cookie_settings["samesite"],
    )

    return response


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 8000)))
