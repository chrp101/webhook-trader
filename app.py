# app.py — Virtual-balance compounding bot with optional profit target
# -------------------------------------------------------
# • Reads OANDA_BASE_BALANCE for virtual starting balance
# • Optional profit capture via USE_PROFIT_TARGET + PROFIT_TARGET
# • True compounding: next trade uses full realized balance
# -------------------------------------------------------

import os, json, requests, logging
from decimal import Decimal, ROUND_DOWN
from flask import Flask, request, jsonify
from datetime import datetime

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

app = Flask(__name__)

# ── ENV CONFIG ──────────────────────────────────────────
API_KEY   = os.getenv("OANDA_API_KEY")
ACCOUNT   = os.getenv("OANDA_ACCOUNT_ID")
BASE_BAL  = Decimal(os.getenv("OANDA_BASE_BALANCE", "1000"))
LEVERAGE  = Decimal(os.getenv("LEVERAGE", "50"))
USE_PROFIT_TARGET = os.getenv("USE_PROFIT_TARGET", "true").lower() == "true"
PROFIT_TARGET = Decimal(os.getenv("PROFIT_TARGET", "2"))
PAIR = "EUR_USD"
BAL_FILE = "balance.json"
TRADE_LOG = "trades.json"

if not API_KEY or not ACCOUNT:
    raise RuntimeError("Missing OANDA_API_KEY or OANDA_ACCOUNT_ID")

BASE_URL = f"https://api-fxpractice.oanda.com/v3/accounts/{ACCOUNT}"
HEADERS = {"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"}

# ── BALANCE HANDLING ────────────────────────────────────
def load_balance() -> Decimal:
    if not os.path.exists(BAL_FILE):
        save_balance(BASE_BAL)
        return BASE_BAL
    try:
        with open(BAL_FILE) as f:
            return Decimal(json.load(f)["balance"])
    except Exception as e:
        logger.warning(f"Load balance error: {e}")
        save_balance(BASE_BAL)
        return BASE_BAL

def save_balance(bal: Decimal):
    with open(BAL_FILE, "w") as f:
        json.dump({"balance": str(bal.quantize(Decimal('0.01'))), "last_updated": datetime.now().isoformat()}, f)

# ── LOGGING ─────────────────────────────────────────────
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
        pos = r.json()["position"]
        long_u = Decimal(pos["long"]["units"])
        short_u = Decimal(pos["short"]["units"])
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
    logger.info(f"Closing position: {body}")
    r = requests.put(f"{BASE_URL}/positions/{PAIR}/close", headers=HEADERS, json=body, timeout=10)
    r.raise_for_status()
    data = r.json()
    realized = Decimal("0")
    for side in ("longOrderFillTransaction", "shortOrderFillTransaction"):
        if side in data and "pl" in data[side]:
            pl = Decimal(data[side]["pl"])
            realized += pl
            logger.info(f"{side} P/L: {pl}")
    return realized

def get_current_price() -> Decimal:
    r = requests.get(f"{BASE_URL}/pricing?instruments={PAIR}", headers=HEADERS, timeout=10)
    r.raise_for_status()
    p = r.json()["prices"][0]
    bid = Decimal(p["bids"][0]["price"])
    ask = Decimal(p["asks"][0]["price"])
    return (bid + ask) / 2

def place_market_order(units: int) -> dict:
    body = {"order": {"units": str(units), "instrument": PAIR, "timeInForce": "FOK", "type": "MARKET", "positionFill": "DEFAULT"}}
    logger.info(f"Placing market order: {units} units")
    r = requests.post(f"{BASE_URL}/orders", headers=HEADERS, json=body, timeout=10)
    r.raise_for_status()
    return r.json()

def calculate_position_size(balance: Decimal, price: Decimal, side: str) -> int:
    notional = balance * LEVERAGE
    units = int((notional / price).quantize(Decimal("1"), ROUND_DOWN))
    return -units if side == "SELL" else units

# ── CORE LOGIC ───────────────────────────────────────────
def execute_trade(side: str) -> dict:
    logger.info(f"=== EXECUTING {side} TRADE ===")
    bal = load_balance()
    pos = get_current_position()

    if USE_PROFIT_TARGET and pos and pos["unrealized_pl"] >= PROFIT_TARGET:
        logger.info(f"Profit target hit: {pos['unrealized_pl']} ≥ {PROFIT_TARGET}")
        realized = close_position()
        bal += realized
        save_balance(bal)
    elif not USE_PROFIT_TARGET:
        realized = close_position()
        bal += realized
        save_balance(bal)

    logger.info(f"Virtual balance for next position: {bal}")
    if bal <= Decimal("100"):
        raise ValueError("Balance too low")

    price = get_current_price()
    units = calculate_position_size(bal, price, side)
    if abs(units) < 100:
        raise ValueError("Too few units")

    order = place_market_order(units)
    log_trade({
        "side": side,
        "units": units,
        "entry_price": str(price),
        "virtual_balance_before": str(bal),
        "realized_pl": str(realized),
        "virtual_balance_after": str(bal)
    })

    return {
        "side": side,
        "units": units,
        "entry_price": str(price),
        "virtual_balance_before": str(bal),
        "realized_pl": str(realized),
        "virtual_balance_after": str(bal),
        "oanda_response": order,
        "message": f"Executed {side}"
    }

# ── ROUTES ───────────────────────────────────────────────
@app.route("/", methods=["POST"])
def webhook():
    payload = request.get_json(silent=True) or {}
    try:
        side = payload["strategy"]["order_action"].upper()
        if side not in ("BUY", "SELL"):
            raise ValueError
    except Exception:
        return jsonify(error="Invalid payload"), 400
    try:
        return jsonify(execute_trade(side)), 200
    except ValueError as e:
        return jsonify(error=str(e)), 400
    except Exception as e:
        logger.error(e)
        return jsonify(error="Trade failed", details=str(e)), 500

@app.route("/status")
def status():
    try:
        return jsonify(
            virtual_balance=str(load_balance()),
            current_price=str(get_current_price()),
            position=get_current_position()
        )
    except Exception as e:
        return jsonify(error=str(e)), 500

@app.route("/reset", methods=["POST"])
def reset_balance():
    save_balance(BASE_BAL)
    return jsonify(message=f"Balance reset to {BASE_BAL}"), 200

@app.route("/health")
def health():
    return f"Running | balance={load_balance()}", 200

if __name__ == "__main__":
    app.run(debug=True, port=int(os.getenv("PORT", 5000)))
