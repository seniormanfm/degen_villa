# app.py
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel
import os
from dotenv import load_dotenv
import pandas as pd
from joblib import dump, load
import json
import time
from datetime import datetime
import httpx
from pathlib import Path

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


app = FastAPI(title="DegenVilla Solana API", version="1.0")

# ========================
# ROOT ENDPOINT
# ========================
@app.get("/")
async def root():
    return {
        "message": "Welcome to DegenVilla Solana API!",
        "endpoints": [
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
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.get(url, headers=headers, params=params)
        r.raise_for_status()
        return r.json()

# ========================
# ENDPOINTS
# ========================

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
async def get_earliest_buyer(token_mint: str = Query(...)):
    try:
        save_file = DB_FOLDER / f"{token_mint}_earliest_buyer.pkl"
        url = f"https://solana-gateway.moralis.io/token/mainnet/{token_mint}/swaps"
        headers = {"accept": "application/json", "X-API-Key": MORALIS_API_KEY}
        params = {"limit": 50, "order": "ASC", "transactionTypes": "buy"}
        data = await async_get_json(url, headers=headers, params=params)
        results = data.get("result", [])
        if not results:
            raise HTTPException(status_code=404, detail=f"No buy transactions found for {token_mint}")
        earliest_tx = results[0]
        earliest_buyer = {
            "walletAddress": earliest_tx.get("walletAddress"),
            "blockTimestamp": earliest_tx.get("blockTimestamp"),
            "transactionHash": earliest_tx.get("transactionHash"),
            "totalValueUsd": earliest_tx.get("totalValueUsd")
        }
        dump(earliest_buyer, save_file)
        return earliest_buyer
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
async def wallet_portfolio(wallet_address: str = Query(..., description="Solana wallet address")):
    wallet_address = wallet_address.strip()
    if not wallet_address:
        raise HTTPException(status_code=400, detail="Invalid or empty wallet_address")

    url = f"https://solana-gateway.moralis.io/account/mainnet/{wallet_address}/portfolio"
    headers = {"Accept": "application/json", "X-API-Key": MORALIS_API_KEY}
    params = {"nftMetadata": "false", "mediaItems": "false", "excludeSpam": "true"}

    try:
        data = await async_get_json(url, headers=headers, params=params)
        return {"wallet": wallet_address, "portfolio": data}
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
