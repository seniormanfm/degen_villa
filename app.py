# app.py
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel
import os
from dotenv import load_dotenv
from fastapi.middleware.cors import CORSMiddleware
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

@app.get("/new-token-listings")
async def get_new_token_listings(
    hours: int = Query(24, description="Lookback window in hours"),
    limit: int = Query(100, le=200, description="Max tokens to fetch"),
):
    try:
        save_file = DB_FOLDER / "birdeye_new_listings.pkl"

        url = "https://public-api.birdeye.so/defi/v2/tokens/new_listing"

        headers = {
            "accept": "application/json",
            "x-chain": "solana",
            "x-api-key": BIRDEYE_API_KEY,
        }

        now = int(time.time())
        created_at_from = now - (hours * 3600)

        batch_size = 20
        all_tokens = []

        for offset in range(0, limit, batch_size):
            params = {
                "limit": batch_size,
                "offset": offset,
                "meme_platform_enabled": 0,
                "sort_by": "created_at",
                "sort_order": "desc",
                "created_at_from": created_at_from,
            }

            data = await async_get_json(url, headers=headers, params=params)
            items = data.get("data", {}).get("items", [])

            if not items:
                break

            all_tokens.extend(items)

        if not all_tokens:
            raise HTTPException(status_code=404, detail="No new token listings found")

        df = pd.DataFrame(all_tokens)

        dump(df, save_file)

        return {
            "count": len(df),
            "hours": hours,
            "new_tokens": df.to_dict(orient="records"),
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))



@app.get("/top-holders")
async def get_top_holders(token_mint: str = Query(..., description="Solana token mint address")):
    try:
        db_path = DB_FOLDER / f"{token_mint}_top_holders.pkl"
        url = f"https://solana-gateway.moralis.io/token/{CHAIN}/{token_mint}/top-holders"
        headers = {"Accept": "application/json", "X-API-Key": MORALIS_API_KEY}
        params = {"limit": LIMIT}
        data = await async_get_json(url, headers=headers, params=params)
        result = data.get("result", [])
        if not result:
            raise HTTPException(status_code=404, detail=f"No top holders found for {token_mint}")
        df = pd.DataFrame(result)
        if "balance" in df.columns and "total_supply" in df.columns:
            df["percent_of_supply"] = (df["balance"] / df["total_supply"]) * 100
        dump(df, db_path)
        return {"top_holders": df.head(LIMIT).to_dict(orient="records")}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))



@app.get("/earliest-buyer")
async def get_first_20_buyers(token_mint: str = Query(..., description="Solana token mint address")):
    token_mint = sanitize_token_mint(token_mint)
    if not token_mint:
        raise HTTPException(status_code=400, detail="Invalid or empty token_mint")

    try:
        save_file = DB_FOLDER / f"{token_mint}_first_20_buyers.pkl"

        url = f"https://solana-gateway.moralis.io/token/mainnet/{token_mint}/swaps"
        headers = {
            "accept": "application/json",
            "X-API-Key": MORALIS_API_KEY
        }
        params = {
            "limit": 50,   # scan enough swaps
            "order": "ASC"  # earliest first
        }

        data = await async_get_json(url, headers=headers, params=params)
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

        return {
            "token_mint": token_mint,
            "count": len(buyers),
            "buyers": buyers
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/daily-flow")
async def get_daily_flow(token_mint: str = Query(...)):
    try:
        url = "https://public-api.birdeye.so/defi/v3/token/trade-data/single"
        headers = {"accept": "application/json", "x-chain": "solana", "x-api-key": BIRDEYE_API_KEY}
        params = {"address": token_mint, "ui_amount_mode": "scaled"}
        data = await async_get_json(url, headers=headers, params=params)
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
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    
# add this before the wallet-profit endpoint
@app.get("/wallet-portfolio")
async def wallet_portfolio(
    wallet_address: str = Query(..., description="Solana wallet address")
):
    wallet_address = wallet_address.strip()
    if not wallet_address:
        raise HTTPException(status_code=400, detail="Invalid or empty wallet_address")

    url = f"https://solana-gateway.moralis.io/account/mainnet/{wallet_address}/portfolio"
    headers = {
        "Accept": "application/json",
        "X-API-Key": MORALIS_API_KEY
    }
    params = {
        "nftMetadata": "false",
        "mediaItems": "false",
        "excludeSpam": "true"
    }

    try:
        data = await async_get_json(url, headers=headers, params=params)

        tokens = data.get("tokens", [])
        enriched_tokens = []

        for token in tokens:
            balance = float(token.get("balance", 0))
            decimals = int(token.get("decimals", 0))
            price_usd = float(token.get("priceUsd", 0) or 0)

            normalized_balance = balance / (10 ** decimals) if decimals else balance
            token_value_usd = normalized_balance * price_usd

            enriched_tokens.append({
                "mint": token.get("mint"),
                "symbol": token.get("symbol"),
                "name": token.get("name"),
                "balance": normalized_balance,
                "decimals": decimals,
                "priceUsd": price_usd,
                "tokenValueUsd": round(token_value_usd, 6)
            })

        total_wallet_value_usd = round(
            sum(t["tokenValueUsd"] for t in enriched_tokens),
            6
        )

        return {
            "wallet": wallet_address,
            "totalValueUsd": total_wallet_value_usd,
            "tokens": enriched_tokens
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/wallet-profit")
async def wallet_profit(token_mint: str = Query(...)):
    try:
        db_path = DB_FOLDER / f"{token_mint}_wallets.pkl"
        url = f"https://solana-gateway.moralis.io/token/{CHAIN}/{token_mint}/swaps"
        headers = {"accept": "application/json", "X-API-Key": MORALIS_API_KEY}
        params = {"limit": 100, "order": "ASC"}
        data = await async_get_json(url, headers=headers, params=params)
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
                    rows.append({
                        "wallet": wallet,
                        "bought": amount,
                        "spent_usd": usd_spent
                    })
        df = pd.DataFrame(rows)
        if df.empty:
            raise HTTPException(status_code=404, detail="No buy transactions found")
        wallet_buys = df.groupby("wallet", as_index=False).agg({
            "bought": "sum",
            "spent_usd": "sum"
        })
        price_url = "https://public-api.birdeye.so/defi/price"
        price_headers = {"accept": "application/json", "X-API-KEY": BIRDEYE_API_KEY, "x-chain": "solana"}
        price_params = {"address": token_mint}
        price_data = await async_get_json(price_url, headers=price_headers, params=price_params)
        current_price = price_data.get("data", {}).get("value", 0)
        wallet_buys["current_value_usd"] = wallet_buys["bought"] * current_price
        wallet_buys["profit_usd"] = wallet_buys["current_value_usd"] - wallet_buys["spent_usd"]
        wallet_buys["profit_percent"] = (wallet_buys["profit_usd"] / wallet_buys["spent_usd"]) * 100
        wallet_buys = wallet_buys.sort_values("profit_percent", ascending=False)
        dump(wallet_buys, db_path)
        return wallet_buys.head(20).to_dict(orient="records")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
