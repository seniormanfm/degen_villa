# app.py
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel
import os
from dotenv import load_dotenv
from fastapi.middleware.cors import CORSMiddleware
import functools
import asyncio
import pandas as pd
from joblib import dump, load
import json
import time
from datetime import datetime
import httpx
from pathlib import Path
import logging
import traceback

# ========================
# LOAD ENV
# ========================
load_dotenv()
MORALIS_API_KEY = os.getenv("MORALIS_API_KEY")
BIRDEYE_API_KEY = os.getenv("BIRDEYE_API_KEY") or os.getenv("Birdeye_Api_key")
if not MORALIS_API_KEY or not BIRDEYE_API_KEY:
    raise ValueError("API keys not found in .env")

# ========================
# CONFIG
# ========================
CHAIN = "mainnet"
LIMIT = 25
DB_FOLDER = Path(r"C:\Users\daddy brian\Desktop\projects\degenvilla\database")
DB_FOLDER.mkdir(parents=True, exist_ok=True)

# setup simple file logging for diagnostics
LOG_FILE = DB_FOLDER / "app.log"
logging.basicConfig(
    filename=str(LOG_FILE),
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)


app = FastAPI(title="DegenVilla Solana API", version="1.0")

# Configure CORS
_origins_env = os.getenv("FRONTEND_ORIGINS", "")
if _origins_env:
    try:
        ALLOWED_ORIGINS = [o.strip() for o in _origins_env.split(",") if o.strip()]
    except Exception:
        ALLOWED_ORIGINS = ["*"]
        logging.warning("Failed to parse FRONTEND_ORIGINS, falling back to allow-all")
else:
    ALLOWED_ORIGINS = ["*"]  # default allow all for development; set FRONTEND_ORIGINS in production

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ========================
# ROOT ENDPOINT
# ========================
@app.get("/")
async def root():
    return {
        "message": "Welcome to DegenVilla Solana API!",
        "endpoints": [
            "/new-token-listings",
            "/top-holders",
            "/earliest-buyer",
            "/daily-flow",
            "/wallet-portfolio",
            "/wallet-profit",
            "/docs"
        ],
        "docs_url": "/docs"
    }

# ========================
# MODELS
# ========================
class TokenRequest(BaseModel):
    token_mint: str

# ========================
# UTILS
# ========================
async def async_get_json(url, headers=None, params=None):
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.get(url, headers=headers, params=params)
            r.raise_for_status()
            return r.json()
    except httpx.HTTPStatusError as e:
        logging.error("HTTP error fetching %s: %s", url, str(e))
        logging.error(traceback.format_exc())
        raise HTTPException(status_code=502, detail=f"Upstream HTTP error: {e}")
    except Exception as e:
        logging.error("Error fetching %s: %s", url, str(e))
        logging.error(traceback.format_exc())
        raise HTTPException(status_code=502, detail=f"Upstream request failed: {e}")


# Convenience wrappers to reduce repetition when calling upstream APIs
MORALIS_BASE = "https://solana-gateway.moralis.io"
BIRDEYE_BASE = "https://public-api.birdeye.so"


async def moralis_get(path: str, params: dict | None = None):
    url = f"{MORALIS_BASE}/{path.lstrip('/') }"
    headers = {"Accept": "application/json", "X-API-Key": MORALIS_API_KEY}
    return await async_get_json(url, headers=headers, params=params)


async def birdeye_get(path: str, params: dict | None = None, extra_headers: dict | None = None):
    url = f"{BIRDEYE_BASE}/{path.lstrip('/') }"
    headers = {"accept": "application/json", "x-api-key": BIRDEYE_API_KEY}
    if extra_headers:
        headers.update(extra_headers)
    return await async_get_json(url, headers=headers, params=params)


def sanitize_token_mint(token_mint: str) -> str:
    """Basic sanitization and validation for Solana token mint strings.

    Accepts raw mint strings or query fragments (e.g. "token_mint=..."),
    strips whitespace and basic validation (base58 chars, reasonable length).
    Returns an empty string on invalid input.
    """
    if not token_mint:
        return ""

    token_mint = str(token_mint).strip()

    # if passed a query fragment or url, extract the value
    if "token_mint=" in token_mint:
        token_mint = token_mint.split("token_mint=")[-1].split("&")[0]

    token_mint = token_mint.strip("/ \t\n")

    import re
    # Base58 chars (no 0,O,I,l) and reasonable length for solana pubkeys
    if not re.fullmatch(r"[1-9A-HJ-NP-Za-km-z]{30,60}", token_mint):
        return ""

    return token_mint

# ========================
# ENDPOINTS
# ========================


# Simple in-memory async cache decorator (avoids external dependency)
_simple_cache: dict = {}
_simple_cache_lock = asyncio.Lock()


def cache(expire: int = 300):
    def decorator(fn):
        @functools.wraps(fn)
        async def wrapper(*args, **kwargs):
            key = (fn.__name__, str(args), str(kwargs))
            now = time.time()
            async with _simple_cache_lock:
                entry = _simple_cache.get(key)
                if entry and entry[0] > now:
                    return entry[1]
            result = await fn(*args, **kwargs)
            async with _simple_cache_lock:
                _simple_cache[key] = (now + expire, result)
            return result

        return wrapper

    return decorator


# Helper: Fetch new token listings (Birdeye) with retry/backoff
async def fetch_new_tokens(limit=20, offset=0, retries=3):
    url = f"{BIRDEYE_BASE}/defi/v2/tokens/new_listing"
    headers = {"accept": "application/json", "x-chain": "solana", "x-api-key": BIRDEYE_API_KEY}
    params = {
        "limit": limit,
        "offset": offset,
        "meme_platform_enabled": 0,
        "sort_by": "created_at",
        "sort_order": "desc",
        "created_at_from": int(time.time()) - 86400,  # last 24h
    }

    for attempt in range(1, retries + 1):
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(url, headers=headers, params=params)
            if response.status_code == 429:  # Too Many Requests
                retry_after = int(response.headers.get("Retry-After", 2))
                await asyncio.sleep(retry_after * attempt)  # exponential backoff
                continue
            response.raise_for_status()
            return response.json().get("data", {}).get("items", [])

    raise HTTPException(status_code=429, detail="Rate limit exceeded, please try again later.")


@app.get("/new-token-listings")
@cache(expire=300)  # cache for 5 minutes
async def new_token_listings(limit: int = 20, offset: int = 0):
    tokens = await fetch_new_tokens(limit=limit, offset=offset)
    if not tokens:
        raise HTTPException(status_code=404, detail="No new token listings found")

    # Save to joblib database
    df = pd.DataFrame(tokens)
    save_path = DB_FOLDER / "birdeye_new_listings.joblib"
    if not df.empty:
        dump(df, str(save_path))

    return {"totalFetched": len(tokens), "tokens": tokens, "savedTo": str(save_path)}



@app.get("/top-holders")
async def get_top_holders(token_mint: str = Query(..., description="Solana token mint address")):
    db_path = DB_FOLDER / f"{token_mint}_top_holders.pkl"
    params = {"limit": LIMIT}
    data = await moralis_get(f"token/{CHAIN}/{token_mint}/top-holders", params=params)
    result = data.get("result", [])
    if not result:
        raise HTTPException(status_code=404, detail=f"No top holders found for {token_mint}")
    df = pd.DataFrame(result)
    if "balance" in df.columns and "total_supply" in df.columns:
        df["percent_of_supply"] = (df["balance"] / df["total_supply"]) * 100
    dump(df, db_path)
    return {"top_holders": df.head(LIMIT).to_dict(orient="records")}



@app.get("/earliest-buyer")
async def get_first_20_buyers(token_mint: str = Query(..., description="Solana token mint address")):
    token_mint = sanitize_token_mint(token_mint)
    if not token_mint:
        raise HTTPException(status_code=400, detail="Invalid or empty token_mint")
    save_file = DB_FOLDER / f"{token_mint}_first_20_buyers.pkl"
    params = {"limit": 50, "order": "ASC"}
    data = await moralis_get(f"token/mainnet/{token_mint}/swaps", params=params)
    results = data.get("result", [])

    if not results:
        raise HTTPException(status_code=404, detail="No swap data found")

    seen_wallets = set()
    buyers = []

    for tx in results:
        if (tx.get("transactionType") or "").lower() != "buy":
            continue

        wallet = tx.get("walletAddress")
        if not wallet or wallet in seen_wallets:
            continue

        buyers.append({
            "walletAddress": wallet,
            "blockTimestamp": tx.get("blockTimestamp"),
            "transactionHash": tx.get("transactionHash"),
            "totalValueUsd": tx.get("totalValueUsd"),
        })

        seen_wallets.add(wallet)

        if len(buyers) == 20:
            break

    if not buyers:
        raise HTTPException(status_code=404, detail="No buy transactions found")

    df = pd.DataFrame(buyers)
    dump(df, save_file)

    return {"token_mint": token_mint, "count": len(buyers), "buyers": buyers}

@app.get("/daily-flow")
async def get_daily_flow(token_mint: str = Query(...)):
    params = {"address": token_mint, "ui_amount_mode": "scaled"}
    extra = {"x-chain": "solana"}
    data = await birdeye_get("defi/v3/token/trade-data/single", params=params, extra_headers=extra)
    today = datetime.utcnow().date()
    daily_flow = {
        "date": today,
        "token": token_mint,
        "inflow": data.get("volume_buy_24h", 0),
        "outflow": data.get("volume_sell_24h", 0),
        "inflow_usd": data.get("volume_buy_24h_usd", 0),
        "outflow_usd": data.get("volume_sell_24h_usd", 0),
    }
    daily_flow["net_flow"] = daily_flow["inflow"] - daily_flow["outflow"]
    daily_flow["net_flow_usd"] = daily_flow["inflow_usd"] - daily_flow["outflow_usd"]
    return daily_flow
    
# add this before the wallet-profit endpoint

@app.get("/wallet-portfolio")
async def wallet_portfolio(wallet_address: str = Query(..., description="Solana wallet address")):
    wallet_address = wallet_address.strip()
    if not wallet_address:
        raise HTTPException(status_code=400, detail="Invalid or empty wallet_address")
    params = {"nftMetadata": "false", "mediaItems": "false", "excludeSpam": "true"}
    data = await moralis_get(f"account/mainnet/{wallet_address}/portfolio", params=params)
    return {"wallet": wallet_address, "portfolio": data}


@app.get("/wallet-profit")
async def wallet_profit(token_mint: str = Query(...)):
    db_path = DB_FOLDER / f"{token_mint}_wallets.pkl"
    params = {"limit": 100, "order": "ASC"}
    data = await moralis_get(f"token/{CHAIN}/{token_mint}/swaps", params=params)
    rows = []
    for tx in data.get("result", []):
        if tx.get("transactionType") == "buy":
            wallet = tx.get("walletAddress")
            bought = tx.get("bought")
            amount = 0
            if isinstance(bought, dict) and bought.get("address") == token_mint:
                amount = float(bought.get("amount", 0) or 0)
            usd_spent = float(tx.get("totalValueUsd", 0) or 0)
            if wallet and amount > 0:
                rows.append({"wallet": wallet, "bought": amount, "spent_usd": usd_spent})

    df = pd.DataFrame(rows)
    if df.empty:
        raise HTTPException(status_code=404, detail="No buy transactions found")

    wallet_buys = df.groupby("wallet", as_index=False).agg({"bought": "sum", "spent_usd": "sum"})
    price_params = {"address": token_mint}
    price_data = await birdeye_get("defi/price", params=price_params, extra_headers={"x-chain": "solana"})
    current_price = price_data.get("data", {}).get("value", 0)
    wallet_buys["current_value_usd"] = wallet_buys["bought"] * current_price
    wallet_buys["profit_usd"] = wallet_buys["current_value_usd"] - wallet_buys["spent_usd"]
    wallet_buys["profit_percent"] = (wallet_buys["profit_usd"] / wallet_buys["spent_usd"]) * 100
    wallet_buys = wallet_buys.sort_values("profit_percent", ascending=False)
    dump(wallet_buys, db_path)
    return wallet_buys.head(20).to_dict(orient="records")
