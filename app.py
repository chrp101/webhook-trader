# app.py (Compounding using FULL BALANCE per trade, no SL)

import os, requests
from flask import Flask, request, jsonify

app = Flask(__name__)

# ── ENV VARS (Render secrets) ─────────────────────────────
OANDA_API_KEY = os.getenv("OANDA_API_KEY")
OANDA_ACCOUNT_ID = os.getenv("OANDA_ACCOUNT_ID")

if not OANDA_API_KEY or not OANDA_ACCOUNT_ID:
    raise RuntimeError("OANDA_API_KEY or OANDA_ACCOUNT_ID not set!")

BASE_URL = f"https://api-fxpractice.oanda.com/v3/accounts/{OANDA_ACCOUNT_ID}"
HEADERS = {
    "Authorization": f"Bearer {OANDA_API_KEY}",
    "Content-Type": "application/json"
}

# ── CONFIG ───────────────────────────────────────────────
MAX_LEVERAGE = 20      # OANDA default margin leverage
UNIT_STEP = 100        # round units to nearest 100
PAIR = "EUR_USD"
PIP_SIZE = 0.0001      # for EUR/USD

# ── UTILS ────────────────────────────────────────────────
def fetch_balance():
    r = requests.get(f"{BASE_URL}/summary", headers=HEADERS, timeout=10)
    r.raise_for_status()
    return float(r.json()["account"]["balance"])

def calculate_max_units(balance: float, price: float) -> int:
    # Assume using full balance with max leverage
    notional = balance * MAX_LEVERAGE
    units = notional / price
    return int(round(units / UNIT_STEP) * UNIT_STEP)

def fetch_price(side: str) -> float:
    r = requests.get(f"{BASE_URL.replace('/accounts/', '/pricing')}?instruments={PAIR}", headers=HEADERS)
    r.raise_for_status()
    prices = r.json()["prices"][0]
    return float(prices["asks"][0]["price"] if side == "buy" else prices["bids"][0]["price"])

def send_order(units: int):
    order = {
        "order": {
            "units": str(units),
            "instrument": PAIR,
            "timeInForce": "FOK",
            "type": "MARKET",
            "positionFill": "DEFAULT"
        }
    }
    r = requests.post(f"{BASE_URL}/orders", headers=HEADERS, json=order, timeout=10)
    r.raise_for_status()
    return r.json()

# ── ROUTES ───────────────────────────────────────────────
@app.route("/", methods=["POST"])
def webhook():
    data = request.get_json(silent=True) or {}
    try:
        action = data["strategy"]["order_action"].upper()
        assert action in ("BUY", "SELL")
    except Exception:
        return jsonify(error="Invalid payload"), 400

    try:
        balance = fetch_balance()
        side = "buy" if action == "BUY" else "sell"
        price = fetch_price(side)
        units = calculate_max_units(balance, price)
        if side == "sell":
            units = -units
        response = send_order(units)
    except Exception as e:
        return jsonify(error="Execution failed", details=str(e)), 500

    return jsonify({
        "message": "Order placed",
        "side": action,
        "units": units,
        "balance_used": f"${balance:.2f}",
        "entry_price": price,
        "response": response
    }), 200

@app.route("/health")
def health():
    return "Webhook live & compounding ✅", 200

if __name__ == "__main__":
    app.run(debug=True)
