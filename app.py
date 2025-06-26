import os
import requests
from flask import Flask, request, jsonify

app = Flask(__name__)

# ── ENV VARS ───────────────────────────────────────────
OANDA_API_KEY = os.getenv("OANDA_API_KEY")
OANDA_ACCOUNT_ID = os.getenv("OANDA_ACCOUNT_ID")

if not OANDA_API_KEY or not OANDA_ACCOUNT_ID:
    raise RuntimeError("OANDA_API_KEY or OANDA_ACCOUNT_ID not set!")

BASE_URL = f"https://api-fxpractice.oanda.com/v3/accounts/{OANDA_ACCOUNT_ID}"
HEADERS = {
    "Authorization": f"Bearer {OANDA_API_KEY}",
    "Content-Type": "application/json"
}

# ── HELPERS ────────────────────────────────────────────
def get_balance():
    url = f"{BASE_URL}/summary"
    r = requests.get(url, headers=HEADERS, timeout=10)
    r.raise_for_status()
    return float(r.json()["account"]["balance"])

def get_price():
    url = f"https://api-fxpractice.oanda.com/v3/pricing?instruments=EUR_USD&accountID={OANDA_ACCOUNT_ID}"
    r = requests.get(url, headers=HEADERS, timeout=10)
    r.raise_for_status()
    prices = r.json()["prices"][0]
    return float(prices["bids"][0]["price"]), float(prices["asks"][0]["price"])

def calc_units(balance):
    # Use full balance to calculate max units assuming $100 margin per 1000 units
    return int((balance / 100.0) * 1000)

def place_order(units):
    order_data = {
        "order": {
            "units": str(units),
            "instrument": "EUR_USD",
            "timeInForce": "FOK",
            "type": "MARKET",
            "positionFill": "DEFAULT"
        }
    }
    r = requests.post(f"{BASE_URL}/orders", headers=HEADERS, json=order_data, timeout=10)
    r.raise_for_status()
    return r.json()

# ── ROUTES ─────────────────────────────────────────────
@app.route("/", methods=["POST"])
def webhook():
    data = request.get_json(silent=True) or {}
    try:
        action = data["strategy"]["order_action"].upper()
        assert action in ("BUY", "SELL")
    except Exception:
        return jsonify(error="Invalid TradingView payload"), 400

    try:
        balance = get_balance()
    except Exception as e:
        return jsonify(error="Failed to get account balance", details=str(e)), 500

    units = calc_units(balance)
    if action == "SELL":
        units = -units

    try:
        order_result = place_order(units)
    except requests.HTTPError as e:
        return jsonify(error="Order failed", details=e.response.json()), e.response.status_code

    return jsonify({
        "message": "Order placed",
        "side": action,
        "units": units,
        "balance": f"${balance:.2f}",
        "oanda_response": order_result
    }), 200

@app.route("/health", methods=["GET"])
def health():
    return "Webhook is live and compounding ✅", 200

if __name__ == "__main__":
    app.run(debug=True)
