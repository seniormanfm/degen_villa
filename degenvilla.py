# degenvilla.py
"""
Converted from degen.ipynb
All notebook cells are now Python script blocks.
"""

# ========================
# fetch the top holder of a solana wallet
# ========================
import os
from dotenv import load_dotenv
import requests
import pandas as pd
from joblib import dump, load
import json
import time
from datetime import datetime

# ========================
# LOAD ENV
# ========================
load_dotenv()
MORALIS_API_KEY = os.getenv("MORALIS_API_KEY")
BIRDEYE_API_KEY = os.getenv("BIRDEYE_API_KEY") or os.getenv("Birdeye_Api_key")
if not MORALIS_API_KEY:
    raise ValueError("MORALIS_API_KEY not set in .env")
if not BIRDEYE_API_KEY:
    raise ValueError("BIRDEYE_API_KEY not set in .env")

# ========================
# CONFIG
# ========================
CHAIN = "mainnet"
LIMIT = 25  # top N holders
DB_FOLDER = r"C:\Users\daddy brian\Desktop\projects\degenvilla\database"
os.makedirs(DB_FOLDER, exist_ok=True)

# ========================
# Fetch top holders
# ========================
def fetch_top_holders(token_mint):
    db_path = os.path.join(DB_FOLDER, f"{token_mint}_top_holders.pkl")
    url = f"https://solana-gateway.moralis.io/token/{CHAIN}/{token_mint}/top-holders"
    headers = {"Accept": "application/json", "X-API-Key": MORALIS_API_KEY}
    params = {"limit": LIMIT}
    response = requests.get(url, headers=headers, params=params)
    response.raise_for_status()
    data = response.json().get("result", [])
    if not data:
        raise ValueError(f"No top holders found for token {token_mint}")
    df = pd.DataFrame(data)
    if "balance" in df.columns and "total_supply" in df.columns:
        df["percent_of_supply"] = (df["balance"] / df["total_supply"]) * 100
    dump(df, db_path)
    print(f"\n💾 Top {LIMIT} holders saved to {db_path}")
    print("\nTop holders preview:")
    print(df.head(LIMIT))
    return df

# ========================
# Fetch new listed pumpfun token
# ========================
def fetch_new_pumpfun():
    url = "https://solana-gateway.moralis.io/token/mainnet/exchange/pumpfun/new?limit=10"
    headers = {"Accept": "application/json", "X-API-Key": MORALIS_API_KEY}
    response = requests.get(url, headers=headers)
    data = response.json()
    print("Full response:")
    print(json.dumps(data, indent=2))
    items = data.get("results", [])
    print("\nNumber of items:", len(items))
    if items:
        print("First item:", items[0])
    else:
        print("No data")
    return items

# ========================
# Solana token analysis
# ========================
def solana_token_analysis(contract_address):
    url = f"https://deep-index.moralis.io/api/v2.2/tokens/{contract_address}/analytics?chain=solana"
    headers = {"Accept": "application/json", "X-API-Key": MORALIS_API_KEY}
    response = requests.get(url, headers=headers)
    try:
        data = response.json()
    except Exception as e:
        print("Error parsing response:", e)
        data = {}
    print(json.dumps(data, indent=2))
    return data

# ========================
# Top holder (alternative)
# ========================
def fetch_top_holders_alt(contract_address):
    url = f"https://solana-gateway.moralis.io/token/mainnet/{contract_address}/top-holders?limit=25"
    headers = {"Accept": "application/json", "X-API-Key": MORALIS_API_KEY}
    response = requests.get(url, headers=headers)
    try:
        data = response.json()
        print(json.dumps(data, indent=2))
    except Exception as e:
        print("Error parsing response as JSON:", e)
        print(response.text)
    return data

# ========================
# Birdeye new listing
# ========================
def fetch_birdeye_new_listing():
    url = "https://public-api.birdeye.so/defi/v2/tokens/new_listing"
    headers = {"accept": "application/json", "x-chain": "solana", "x-api-key": BIRDEYE_API_KEY}
    all_tokens = []
    batch_size = 20
    max_records = 100
    now = int(time.time())
    one_day_ago = now - 86400
    for offset in range(0, max_records, batch_size):
        params = {
            "limit": batch_size,
            "offset": offset,
            "meme_platform_enabled": 0,
            "sort_by": "created_at",
            "sort_order": "desc",
            "created_at_from": one_day_ago
        }
        response = requests.get(url, headers=headers, params=params)
        response.raise_for_status()
        data = response.json()
        tokens = data.get("data", {}).get("items", [])
        if not tokens:
            print(f"⚠️ No items at offset {offset}, stopping early.")
            break
        all_tokens.extend(tokens)
    df = pd.DataFrame(all_tokens)
    print(f"✅ Total tokens fetched: {len(df)}")
    print("\nDataFrame Preview:")
    print(df.head())
    save_path = os.path.join(DB_FOLDER, "birdeye_new_listings.joblib")
    if not df.empty:
        dump(df, save_path)
        print(f"\n💾 DataFrame saved to {save_path}")
    else:
        print("\n⚠️ No tokens fetched, nothing saved.")
    return df

# ========================
# First set of wallets that interact with a token address
# ========================
def fetch_first_wallets(token_mint):
    url = f"https://solana-gateway.moralis.io/token/mainnet/{token_mint}/swaps"
    headers = {"accept": "application/json", "X-API-Key": MORALIS_API_KEY}
    params = {"limit": 25, "order": "ASC", "transactionTypes": "buy,sell"}
    response = requests.get(url, headers=headers, params=params)
    if response.status_code != 200:
        print("Error:", response.status_code, response.text)
        raise SystemExit
    data = response.json()
    wallets = []
    for tx in data.get("result", []):
        wallet = tx.get("walletAddress")
        if wallet and wallet not in wallets:
            wallets.append(wallet)
    if wallets:
        print(f"\nFirst wallets that interacted with {token_mint}:\n")
        for i, wallet in enumerate(wallets[:20], start=1):
            print(f"{i}. {wallet}")
    else:
        print(f"No wallets found for token {token_mint}")
    return wallets

# ========================
# Show earliest buyer, timestamp, amount bought
# ========================
def fetch_earliest_buyer(token_mint):
    save_file = os.path.join(DB_FOLDER, f"{token_mint}_earliest_buyer.pkl")
    url = f"https://solana-gateway.moralis.io/token/mainnet/{token_mint}/swaps"
    headers = {"accept": "application/json", "X-API-Key": MORALIS_API_KEY}
    params = {"limit": 50, "order": "ASC", "transactionTypes": "buy"}
    response = requests.get(url, headers=headers, params=params)
    if response.status_code != 200:
        print("Error:", response.status_code, response.text)
        raise SystemExit
    results = response.json().get("result", [])
    if not results:
        print(f"⚠️ No buy transactions found for token {token_mint}.")
        raise SystemExit
    earliest_tx = results[0]
    earliest_buyer = {
        "walletAddress": earliest_tx.get("walletAddress"),
        "blockTimestamp": earliest_tx.get("blockTimestamp"),
        "transactionHash": earliest_tx.get("transactionHash"),
        "totalValueUsd": earliest_tx.get("totalValueUsd")
    }
    dump(earliest_buyer, save_file)
    print("✅ Earliest buyer saved successfully\n")
    print("Wallet:", earliest_buyer["walletAddress"])
    print("Timestamp:", earliest_buyer["blockTimestamp"])
    print("Transaction Hash:", earliest_buyer["transactionHash"])
    print("Total Value USD:", earliest_buyer["totalValueUsd"])
    print("Saved to:", save_file)
    return earliest_buyer

# ========================
# Load earliest buyer as DataFrame
# ========================
def load_earliest_buyer(path):
    data = load(path)
    if isinstance(data, list):
        df = pd.DataFrame(data)
    else:
        df = pd.DataFrame([data])
    print(df)
    return df

# ========================
# Daily inflow and outflow
# ========================
def fetch_daily_flow(token_mint):
    url = "https://public-api.birdeye.so/defi/v3/token/trade-data/single"
    headers = {"accept": "application/json", "x-chain": "solana", "x-api-key": BIRDEYE_API_KEY}
    params = {"address": token_mint, "ui_amount_mode": "scaled"}
    response = requests.get(url, headers=headers, params=params)
    if response.status_code != 200:
        print("Error:", response.status_code, response.text)
        raise SystemExit
    data = response.json().get("data", {})
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
    df = pd.DataFrame([daily_flow])
    print("\n📊 24h Token Flow Data:")
    print(df)
    return df

# ========================
# Load daily flows DataFrame
# ========================
def load_daily_flows(path):
    df = load(path)
    print(type(df))
    print(df.head(20))
    return df

# ========================
# Total bought, present value, profit estimation
# ========================
def wallet_profit_analysis(token_mint):
    DB_PATH = os.path.join(DB_FOLDER, "wallets.pkl")
    url = f"https://solana-gateway.moralis.io/token/{CHAIN}/{token_mint}/swaps"
    headers = {"accept": "application/json", "X-API-Key": MORALIS_API_KEY}
    params = {"limit": 100, "order": "ASC"}
    res = requests.get(url, headers=headers, params=params)
    res.raise_for_status()
    transfers = res.json().get("result", [])
    rows = []
    for tx in transfers:
        if tx.get("transactionType") == "buy":
            wallet = tx.get("walletAddress")
            bought = tx.get("bought")
            amount = 0
            if isinstance(bought, dict) and bought.get("address") == token_mint:
                try:
                    amount = float(bought.get("amount", 0))
                except Exception:
                    amount = 0
            if wallet and amount > 0:
                rows.append({"wallet": wallet, "wenwen_bought": amount})
    df = pd.DataFrame(rows)
    if df.empty:
        raise ValueError("⚠️ No WENWEN buy transactions found")
    wallet_buys = df.groupby("wallet", as_index=False)["wenwen_bought"].sum()
    price_url = "https://public-api.birdeye.so/defi/price"
    price_headers = {"accept": "application/json", "X-API-KEY": BIRDEYE_API_KEY, "x-chain": "solana"}
    price_params = {"address": token_mint}
    price_res = requests.get(price_url, headers=price_headers, params=price_params)
    price_res.raise_for_status()
    current_price = price_res.json()["data"]["value"]
    ESTIMATED_AVG_BUY_PRICE = current_price * 0.7
    wallet_buys["avg_buy_price"] = ESTIMATED_AVG_BUY_PRICE
    wallet_buys["current_price"] = current_price
    wallet_buys["cost_basis_usd"] = wallet_buys["wenwen_bought"] * wallet_buys["avg_buy_price"]
    wallet_buys["current_value_usd"] = wallet_buys["wenwen_bought"] * wallet_buys["current_price"]
    wallet_buys["estimated_profit_usd"] = wallet_buys["current_value_usd"] - wallet_buys["cost_basis_usd"]
    wallet_buys = wallet_buys.sort_values("estimated_profit_usd", ascending=False)
    print(wallet_buys.head(25))
    dump(wallet_buys, DB_PATH)
    print(f"\n💾 Data saved to {DB_PATH}")
    return wallet_buys

# ========================
# Most profitable wallet
# ========================
def most_profitable_wallet(token_mint):
    DB_PATH_FULL = os.path.join(DB_FOLDER, "wenwen_wallets.pkl")
    DB_PATH_TOP20 = os.path.join(DB_FOLDER, "wenwen_top20.pkl")
    url = f"https://solana-gateway.moralis.io/token/{CHAIN}/{token_mint}/swaps"
    headers = {"accept": "application/json", "X-API-Key": MORALIS_API_KEY}
    params = {"limit": 100, "order": "ASC"}
    res = requests.get(url, headers=headers, params=params)
    res.raise_for_status()
    transfers = res.json().get("result", [])
    rows = []
    for tx in transfers:
        if tx.get("transactionType") == "buy":
            wallet = tx.get("walletAddress")
            amount = 0
            try:
                bought = tx.get("bought")
                if isinstance(bought, dict) and bought.get("address") == token_mint:
                    amount = float(bought.get("amount", 0))
                usd_spent = float(tx.get("totalValueUsd", 0))
            except Exception:
                amount, usd_spent = 0, 0
            if wallet and amount > 0:
                rows.append({
                    "wallet": wallet,
                    "wenwen_bought": amount,
                    "total_bought_usd": usd_spent
                })
    df = pd.DataFrame(rows)
    if df.empty:
        raise ValueError("⚠️ No WENWEN buy transactions found")
    wallet_buys = df.groupby("wallet", as_index=False).agg({
        "wenwen_bought": "sum",
        "total_bought_usd": "sum"
    })
    price_url = "https://public-api.birdeye.so/defi/price"
    price_headers = {"accept": "application/json", "X-API-KEY": BIRDEYE_API_KEY, "x-chain": "solana"}
    price_params = {"address": token_mint}
    price_res = requests.get(price_url, headers=price_headers, params=price_params)
    price_res.raise_for_status()
    current_price = price_res.json()["data"]["value"]
    wallet_buys["current_value_usd"] = wallet_buys["wenwen_bought"] * current_price
    wallet_buys["estimated_profit_usd"] = wallet_buys["current_value_usd"] - wallet_buys["total_bought_usd"]
    wallet_buys["profit_percent"] = (wallet_buys["estimated_profit_usd"] / wallet_buys["total_bought_usd"]) * 100
    wallet_buys = wallet_buys.sort_values("profit_percent", ascending=False)
    dump(wallet_buys, DB_PATH_FULL)
    top20 = wallet_buys.head(20)
    dump(top20, DB_PATH_TOP20)
    print("🏆 Top 20 Most Profitable WENWEN Wallets (by ROI):\n")
    print(top20)
    print(f"\n💾 Full wallet data saved to {DB_PATH_FULL}")
    print(f"💾 Top 20 wallets saved to {DB_PATH_TOP20}")
    return top20

# ========================
# Example usage (uncomment to run)
# ========================
# token_mint = input("Enter Solana token mint address: ").strip()
# fetch_top_holders(token_mint)
# fetch_new_pumpfun()
# solana_token_analysis(token_mint)
# fetch_top_holders_alt(token_mint)
# fetch_birdeye_new_listing()
# fetch_first_wallets(token_mint)
# fetch_earliest_buyer(token_mint)
# load_earliest_buyer(r"C:\Users\daddy brian\Desktop\projects\degenvilla\database\earliest_buyer.pkl")
# fetch_daily_flow(token_mint)
# load_daily_flows(r"C:\Users\daddy brian\Desktop\projects\degenvilla\database\wenwen_daily_flows.pkl")
# wallet_profit_analysis(token_mint)
# most_profitable_wallet(token_mint)
