# app.py — Virtual-balance compounding bot (Trend-Riding + Profit-Capture Logic)
# -------------------------------------------------------
# • Executes BUY/SELL and rides the trend until signal changes
# • Captures profit every time unrealized P/L ≥ PROFIT_TARGET
# • Reopens new trade in same direction after compounding
# -------------------------------------------------------

import os, json, requests, logging
from decimal import Decimal, ROUND_DOWN
from flask import Flask, request, jsonify
from datetime import datetime

# ── Configure logging ───────────────────────────────────
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

app = Flask(__name__)

# ── ENV + CONSTANTS ─────────────────────────────────────
API_KEY   = os.getenv("OANDA_API_KEY")
ACCOUNT   = os.getenv("OANDA_ACCOUNT_ID")
BASE_BAL  = Decimal(os.getenv("OANDA_BASE_BALANCE", "1000"))
LEVERAGE  = Decimal(os.getenv("OANDA_LEVERAGE", "50"))
PROFIT_TARGET = Decimal(os.getenv("PROFIT_TARGET", "10"))
PAIR      = "EUR_USD"
BAL_FILE  = "balance.json"
TRADE_LOG = "trades.json"

if not API_KEY or not ACCOUNT:
    raise RuntimeError("OANDA_API_KEY or OANDA_ACCOUNT_ID missing")

BASE_URL = f"https://api-fxpractice.oanda.com/v3/accounts/{ACCOUNT}"
HEADERS  = {"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"}

# ── BALANCE PERSISTENCE ─────────────────────────────────
def load_balance() -> Decimal:
    if not os.path.exists(BAL_FILE):
        save_balance(BASE_BAL)
        return BASE_BAL
    try:
        with open(BAL_FILE) as f:
            data = json.load(f)
            return Decimal(data["balance"])
    except Exception as e:
        logger.warning(f"Load balance error: {e}")
        save_balance(BASE_BAL)
        return BASE_BAL

def save_balance(bal: Decimal):
    with open(BAL_FILE, "w") as f:
        json.dump({
            "balance": str(bal.quantize(Decimal('0.01'), ROUND_DOWN)),
            "last_updated": datetime.now().isoformat()
        }, f, indent=2)

# ── TRADE LOGGING ───────────────────────────────────────
def log_trade(trade_data: dict):
    try:
        trades = []
        if os.path.exists(TRADE_LOG):
            with open(TRADE_LOG) as f:
                trades = json.load(f)
        trades.append({**trade_data, "timestamp": datetime.now().isoformat()})
        with open(TRADE_LOG, "w") as f:
            json.dump(trades[-100:], f, indent=2)
    except Exception as e:
        logger.error(f"Trade log error: {e}")

# ── OANDA HELPERS ───────────────────────────────────────
def get_current_position():
    try:
        r = requests.get(f"{BASE_URL}/positions/{PAIR}", headers=HEADERS, timeout=10)
        if r.status_code == 404:
            return None
        r.raise_for_status()
        pos = r.json()["position"]
        long_u = Decimal(pos["long"]["units"])
        short_u= Decimal(pos["short"]["units"])
        unreal = Decimal(pos.get("unrealizedPL", "0"))
        if long_u + short_u == 0:
            return None
        return {"long_units": long_u, "short_units": short_u, "unrealized_pl": unreal}
    except Exception as e:
        logger.error(f"Get position error: {e}")
        return None

def close_position() -> Decimal:
    pos = get_current_position()
    if not pos:
        logger.info("No open position to close")
        return Decimal("0")
    body = {}
    if pos["long_units"] > 0:
        body["longUnits"] = "ALL"
    if pos["short_units"] < 0:
        body["shortUnits"] = "ALL"
    logger.info(f"Closing position body={body}")
    r = requests.put(f"{BASE_URL}/positions/{PAIR}/close", headers=HEADERS, json=body, timeout=10)
    r.raise_for_status()
    data = r.json()
    realized = Decimal("0")
    for side in ("longOrderFillTransaction", "shortOrderFillTransaction"):
        if side in data and "pl" in data[side]:
            pl = Decimal(data[side]["pl"])
            realized += pl
            logger.info(f"{side} P/L: {pl}")
    logger.info(f"Total realized P/L: {realized}")
    return realized

def get_current_price() -> Decimal:
    url = f"{BASE_URL}/pricing?instruments={PAIR}"
    r = requests.get(url, headers=HEADERS, timeout=10)
    r.raise_for_status()
    price_data = r.json()["prices"][0]
    bid = Decimal(price_data["bids"][0]["price"])
    ask = Decimal(price_data["asks"][0]["price"])
    mid = (bid + ask) / 2
    logger.info(f"Price mid={mid}")
    return mid

def place_market_order(units: int) -> dict:
    body = {"order": {"units": str(units), "instrument": PAIR, "timeInForce": "FOK", "type": "MARKET", "positionFill": "DEFAULT"}}
    logger.info(f"Placing order units={units}")
    r = requests.post(f"{BASE_URL}/orders", headers=HEADERS, json=body, timeout=10)
    r.raise_for_status()
    return r.json()

def calculate_position_size(balance: Decimal, price: Decimal, side: str) -> int:
    notional = balance * LEVERAGE
    raw = notional / price
    units = int(raw.quantize(Decimal("1"), ROUND_DOWN))
    if side == "SELL": units = -units
    logger.info(f"Calc units={units} from balance={balance}, price={price}")
    return units

# ── CORE LOGIC ───────────────────────────────────────────
def manage_trend_trade(side: str) -> dict:
    logger.info(f"=== MANAGE {side} ===")
    pos = get_current_position()
    if pos:
        unreal = pos["unrealized_pl"]
        logger.info(f"Unrealized PL: {unreal} | Target: {PROFIT_TARGET}")
        if unreal >= PROFIT_TARGET:
            bal = load_balance()
            realized = close_position()
            new_bal = bal + realized
            save_balance(new_bal)
            logger.info(f"Captured profit. New balance: {new_bal}")
            price = get_current_price()
            units = calculate_position_size(new_bal, price, side)
            resp = place_market_order(units)
            trade_data = {"side": side, "units": units, "entry_price": str(price), "virtual_balance_before": str(bal), "realized_pl": str(realized), "virtual_balance_after": str(new_bal)}
            log_trade(trade_data)
            return {**trade_data, "oanda_response": resp, "message": f"Profit-capture + Reentry {side}"}
        return {"message": "Trend active. No action taken."}
    else:
        bal = load_balance()
        price = get_current_price()
        units = calculate_position_size(bal, price, side)
        resp = place_market_order(units)
        trade_data = {"side": side, "units": units, "entry_price": str(price), "virtual_balance_before": str(bal), "realized_pl": "0", "virtual_balance_after": str(bal)}
        log_trade(trade_data)
        return {**trade_data, "oanda_response": resp, "message": f"Opened new {side} position"}

# ── ROUTES ───────────────────────────────────────────────
@app.route("/", methods=["POST"])
def webhook():
    payload = request.get_json(silent=True) or {}
    try:
        side = payload["strategy"]["order_action"].upper()
        if side not in ("BUY","SELL"): raise ValueError
    except Exception:
        return jsonify(error="Invalid payload"),400
    try:
        result = manage_trend_trade(side)
        return jsonify(result),200
    except ValueError as e:
        logger.warning(e)
        return jsonify(error=str(e)),400
    except Exception as e:
        logger.error(e)
        return jsonify(error="Trade failed", details=str(e)),500

@app.route("/status")
def status():
    try:
        vb = load_balance()
        pos = get_current_position()
        price = get_current_price()
        return jsonify(virtual_balance=str(vb), current_price=str(price), position=pos),200
    except Exception as e:
        return jsonify(error=str(e)),500

@app.route("/health")
def health():
    try:
        return f"Alive | balance={load_balance()}",200
    except Exception as e:
        return str(e),500

@app.route("/reset", methods=["POST"])
def reset_balance():
    save_balance(BASE_BAL)
    return jsonify(message=f"Reset to {BASE_BAL}"),200

if __name__ == "__main__":
    logger.info(f"Starting bot base={BASE_BAL} lev={LEVERAGE} profit_target={PROFIT_TARGET}")
    app.run(debug=True, port=int(os.getenv("PORT",5000)))
