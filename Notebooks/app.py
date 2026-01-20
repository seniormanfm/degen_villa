# app.py
from fastapi import FastAPI, HTTPException, Query
import os
from dotenv import load_dotenv
import pandas as pd
from joblib import dump
from datetime import datetime
import httpx
from pathlib import Path
import logging

# load env
load_dotenv()
MORALIS_API_KEY = os.getenv("MORALIS_API_KEY")
BIRDEYE_API_KEY = os.getenv("BIRDEYE_API_KEY") or os.getenv("Birdeye_Api_key")
if not MORALIS_API_KEY or not BIRDEYE_API_KEY:
    raise ValueError("API keys not found in .env")

# config
CHAIN = "mainnet"
LIMIT = 25
DB_FOLDER = Path(r"C:\Users\daddy brian\Desktop\projects\degenvilla\database")
DB_FOLDER.mkdir(parents=True, exist_ok=True)

logging.basicConfig(level=logging.INFO)
app = FastAPI(title="DegenVilla Solana API", version="1.0")

# utils
async def async_get_json(url, headers=None, params=None):
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.get(url, headers=headers, params=params)
        try:
            r.raise_for_status()
        except httpx.HTTPStatusError as exc:
            detail = exc.response.text if exc.response is not None else str(exc)
            logging.error("HTTP error %s %s", exc, detail)
            raise HTTPException(status_code=exc.response.status_code if exc.response is not None else 500, detail=detail)
        try:
            return r.json()
        except ValueError:
            return {"_raw_text": r.text}

def sanitize_token_mint(mint: str) -> str:
    if not isinstance(mint, str):
        return ""
    return mint.strip().replace("\n", "").replace("\r", "")

# endpoints

@app.get("/top-holders")
async def get_top_holders(token_mint: str = Query(..., description="Solana token mint address")):
    token_mint = sanitize_token_mint(token_mint)
    if not token_mint:
        raise HTTPException(status_code=400, detail="Invalid or empty token_mint")
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
    except HTTPException:
        raise
    except Exception as e:
        logging.exception("top-holders error")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/earliest-buyer")
async def get_earliest_buyer(token_mint: str = Query(...)):
    token_mint = sanitize_token_mint(token_mint)
    if not token_mint:
        raise HTTPException(status_code=400, detail="Invalid or empty token_mint")
    try:
        save_file = DB_FOLDER / f"{token_mint}_earliest_buyer.pkl"
        url = f"https://solana-gateway.moralis.io/token/mainnet/{token_mint}/swaps"
        headers = {"accept": "application/json", "X-API-Key": MORALIS_API_KEY}
        params = {"limit": 500, "order": "ASC"}
        data = await async_get_json(url, headers=headers, params=params)
        results = data.get("result", [])
        buys = [tx for tx in results if (tx.get("transactionType") or "").lower() == "buy"]
        if not buys:
            sample_types = list(dict.fromkeys(tx.get("transactionType") for tx in results))[:10]
            raise HTTPException(
                status_code=404,
                detail=f"No buy transactions found for {token_mint} (swaps returned={len(results)}; sample transactionTypes={sample_types})"
            )
        earliest_tx = buys[0]
        earliest_buyer = {
            "walletAddress": earliest_tx.get("walletAddress"),
            "blockTimestamp": earliest_tx.get("blockTimestamp"),
            "transactionHash": earliest_tx.get("transactionHash"),
            "totalValueUsd": earliest_tx.get("totalValueUsd")
        }
        dump(earliest_buyer, save_file)
        return earliest_buyer
    except HTTPException:
        raise
    except Exception as e:
        logging.exception("earliest-buyer error")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/daily-flow")
async def get_daily_flow(token_mint: str = Query(...)):
    token_mint = sanitize_token_mint(token_mint)
    if not token_mint:
        raise HTTPException(status_code=400, detail="Invalid or empty token_mint")
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
    except HTTPException:
        raise
    except Exception as e:
        logging.exception("daily-flow error")
        raise HTTPException(status_code=500, detail=str(e))
    


@app.get("/wallet-portfolio")
async def wallet_portfolio(wallet_address: str = Query(..., description="Solana wallet address")):
    """
    Fetch the portfolio of a given Solana wallet dynamically.
    Returns tokens, NFTs, and total USD value.
    """
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
    except HTTPException as e:
        raise e
    except Exception as e:
        logging.exception("wallet-portfolio error")
        raise HTTPException(status_code=500, detail=str(e))

    return {
        "wallet": wallet_address,
        "portfolio": data
    }


@app.get("/wallet-profit")
async def wallet_profit(token_mint: str = Query(...)):
    token_mint = sanitize_token_mint(token_mint)
    if not token_mint:
        raise HTTPException(status_code=400, detail="Invalid or empty token_mint")
    try:
        db_path = DB_FOLDER / f"{token_mint}_wallets.pkl"
        url = f"https://solana-gateway.moralis.io/token/{CHAIN}/{token_mint}/swaps"
        headers = {"accept": "application/json", "X-API-Key": MORALIS_API_KEY}
        params = {"limit": 500, "order": "ASC"}
        data = await async_get_json(url, headers=headers, params=params)
        rows = []
        for tx in data.get("result", []):
            if (tx.get("transactionType") or "").lower() == "buy":
                wallet = tx.get("walletAddress")
                bought = tx.get("bought")
                amount = 0.0
                if isinstance(bought, dict) and bought.get("address") == token_mint:
                    try:
                        amount = float(bought.get("amount") or 0)
                    except (TypeError, ValueError):
                        amount = 0.0
                elif isinstance(bought, (int, float, str)):
                    try:
                        amount = float(bought)
                    except (TypeError, ValueError):
                        amount = 0.0
                try:
                    usd_spent = float(tx.get("totalValueUsd", 0) or 0)
                except (TypeError, ValueError):
                    usd_spent = 0.0
                if wallet and amount > 0:
                    rows.append({"wallet": wallet, "bought": amount, "spent_usd": usd_spent})
        df = pd.DataFrame(rows)
        if df.empty:
            raise HTTPException(status_code=404, detail=f"No buy transactions found for {token_mint} (rows=0)")
        wallet_buys = df.groupby("wallet", as_index=False).agg({"bought": "sum", "spent_usd": "sum"})
        # price fetch (best-effort)
        price_url = "https://public-api.birdeye.so/defi/price"
        price_headers = {"accept": "application/json", "X-API-KEY": BIRDEYE_API_KEY, "x-chain": "solana"}
        price_params = {"address": token_mint}
        try:
            price_data = await async_get_json(price_url, headers=price_headers, params=price_params)
            current_price = price_data.get("data", {}).get("value", 0) or 0
        except HTTPException:
            current_price = 0
        wallet_buys["current_value_usd"] = wallet_buys["bought"] * current_price
        wallet_buys["profit_usd"] = wallet_buys["current_value_usd"] - wallet_buys["spent_usd"]
        wallet_buys["profit_percent"] = wallet_buys.apply(
            lambda r: (r["profit_usd"] / r["spent_usd"] * 100) if r["spent_usd"] else None,
            axis=1
        )
        wallet_buys = wallet_buys.sort_values("profit_percent", ascending=False, na_position="last")
        dump(wallet_buys, db_path)
        return wallet_buys.head(20).to_dict(orient="records")
    except HTTPException:
        raise
    except Exception as e:
        logging.exception("wallet-profit error")
        raise HTTPException(status_code=500, detail=str(e))