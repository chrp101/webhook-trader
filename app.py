# app.py — OANDA × TradingView Bot (Full Cycle)
# -------------------------------------------------------
# ✅ Buy/Sell via TradingView alerts
# ✅ Uses full balance with compounding
# ✅ Stop-loss & take-profit in pips (optional)
# ✅ Short + long position support
# ✅ Optional multi-symbol support
# -------------------------------------------------------

import os, json, requests, logging
from flask import Flask, request, jsonify
from decimal import Decimal, ROUND_DOWN

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)

# ── ENV VARIABLES ──────────────────────────────────────
OANDA_API_KEY      = os.getenv("OANDA_API_KEY")
ACCOUNT_ID         = os.getenv("OANDA_ACCOUNT_ID")
DEFAULT_PAIR       = os.getenv("OANDA_DEFAULT_PAIR", "EUR_USD")
LEVERAGE           = Decimal(os.getenv("OANDA_LEVERAGE", "50"))
TAKE_PROFIT_PIPS   = Decimal(os.getenv("OANDA_TP_PIPS", "0"))  # Set to 0 to disable
STOP_LOSS_PIPS     = Decimal(os.getenv("OANDA_SL_PIPS", "0"))  # Set to 0 to disable

if not OANDA_API_KEY or not ACCOUNT_ID:
    raise RuntimeError("Missing OANDA_API_KEY or OANDA_ACCOUNT_ID")

BASE_URL = f"https://api-fxpractice.oanda.com/v3/accounts/{ACCOUNT_ID}"
HEADERS  = {"Authorization": f"Bearer {OANDA_API_KEY}", "Content-Type": "application/json"}

# ── HELPERS ─────────────────────────────────────────────
def get_price(pair):
    url = f"{BASE_URL}/pricing?instruments={pair}"
    r = requests.get(url, headers=HEADERS, timeout=10)
    r.raise_for_status()
    price_data = r.json()["prices"][0]
    bid = Decimal(price_data["bids"][0]["price"])
    ask = Decimal(price_data["asks"][0]["price"])
    return (bid + ask) / 2

def get_balance():
    r = requests.get(BASE_URL, headers=HEADERS, timeout=10)
    r.raise_for_status()
    data = r.json()
    return Decimal(data["account"]["balance"])

def close_all_positions(pair):
    r = requests.put(f"{BASE_URL}/positions/{pair}/close", headers=HEADERS, json={"longUnits":"ALL", "shortUnits":"ALL"})
    if r.status_code not in (200, 201):
        logging.warning("No position to close or already flat.")

# ── ORDER CREATION ─────────────────────────────────────
def place_order(pair: str, units: int, price: Decimal):
    order = {
        "order": {
            "units": str(units),
            "instrument": pair,
            "timeInForce": "FOK",
            "type": "MARKET",
            "positionFill": "DEFAULT"
        }
    }
    if STOP_LOSS_PIPS > 0 or TAKE_PROFIT_PIPS > 0:
        sl = str((STOP_LOSS_PIPS / 10000).quantize(Decimal("0.00001")))
        tp = str((TAKE_PROFIT_PIPS / 10000).quantize(Decimal("0.00001")))
        if units > 0:
            order["order"]["stopLossOnFill"] = {"distance": sl}
            order["order"]["takeProfitOnFill"] = {"distance": tp}
        else:
            order["order"]["stopLossOnFill"] = {"distance": sl}
            order["order"]["takeProfitOnFill"] = {"distance": tp}
    r = requests.post(f"{BASE_URL}/orders", headers=HEADERS, json=order)
    r.raise_for_status()
    return r.json()

def calculate_units(balance: Decimal, price: Decimal, side: str) -> int:
    notional = balance * LEVERAGE
    raw_units = notional / price
    units = int(raw_units.quantize(Decimal("1"), ROUND_DOWN))
    return units if side == "BUY" else -units

# ── ROUTES ──────────────────────────────────────────────
@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.get_json()
    side = data.get("signal")
    pair = data.get("symbol", DEFAULT_PAIR)

    if side not in ["BUY", "SELL"]:
        return jsonify({"error": "Invalid signal"}), 400

    try:
        close_all_positions(pair)
        price = get_price(pair)
        balance = get_balance()
        units = calculate_units(balance, price, side)
        response = place_order(pair, units, price)
        return jsonify({"status": f"Executed {side}", "units": units, "pair": pair}), 200
    except Exception as e:
        logging.exception("Trade failed")
        return jsonify({"error": str(e)}), 500

@app.route("/")
def home():
    return "Trading bot is running."

if __name__ == "__main__":
    app.run(debug=True, port=int(os.getenv("PORT", 5000)))
