# app.py – Full $1000 Compounding with Position Closing Logic

import os, requests
from flask import Flask, request, jsonify

app = Flask(__name__)

# ── ENV VARS ─────────────────────────────────────────────
OANDA_API_KEY = os.getenv("OANDA_API_KEY")
OANDA_ACCOUNT_ID = os.getenv("OANDA_ACCOUNT_ID")
BASE = f"https://api-fxpractice.oanda.com/v3/accounts/{OANDA_ACCOUNT_ID}"
HDRS = {
    "Authorization": f"Bearer {OANDA_API_KEY}",
    "Content-Type": "application/json"
}

# ── SIMULATED STATE ─────────────────────────────────────
VIRTUAL_BALANCE = 1000.0  # Starting virtual balance
STOP_PIPS = 100           # Stop loss distance
UNIT_STEP = 100           # Unit rounding step
RISK_PERCENT = 100        # Risk 100% per trade (full balance)

# ── HELPERS ──────────────────────────────────────────────
def calculate_units(balance):
    risk_usd = balance * (RISK_PERCENT / 100)
    pip_value_needed = risk_usd / STOP_PIPS
    units = pip_value_needed / 0.0001  # EUR/USD pip value
    return int(round(units / UNIT_STEP) * UNIT_STEP) or UNIT_STEP

def oanda_order(units):
    order = {
        "order": {
            "units": str(units),
            "instrument": "EUR_USD",
            "timeInForce": "FOK",
            "type": "MARKET",
            "positionFill": "DEFAULT"
        }
    }
    r = requests.post(f"{BASE}/orders", headers=HDRS, json=order, timeout=10)
    r.raise_for_status()
    return r.json()

def close_all_positions():
    r = requests.put(f"{BASE}/positions/EUR_USD/close", headers=HDRS, json={"longUnits": "ALL", "shortUnits": "ALL"}, timeout=10)
    if r.status_code == 200:
        return r.json()
    return {}

# ── ROUTES ───────────────────────────────────────────────
@app.route("/", methods=["POST"])
def webhook():
    global VIRTUAL_BALANCE

    data = request.get_json(silent=True) or {}
    try:
        action = data["strategy"]["order_action"].upper()
        assert action in ("BUY", "SELL")
    except Exception:
        return jsonify(error="Invalid payload"), 400

    # Close current positions before placing new one
    try:
        close_all_positions()
    except Exception as e:
        return jsonify(error="Failed to close existing positions", details=str(e)), 500

    starting_balance = VIRTUAL_BALANCE
    units = calculate_units(VIRTUAL_BALANCE)
    if action == "SELL":
        units = -units

    try:
        response = oanda_order(units)
    except requests.HTTPError as e:
        return jsonify(error="Order failed", details=e.response.json()), e.response.status_code

    # Simulate profit of 1% on each trade
    VIRTUAL_BALANCE *= 1.01

    return jsonify({
        "message": "Order placed with compounding",
        "side": action,
        "units": units,
        "virtual_start_balance": f"${starting_balance:.2f}",
        "virtual_end_balance": f"${VIRTUAL_BALANCE:.2f}",
        "oanda_response": response
    }), 200

@app.route("/health")
def health():
    return "Webhook running with full-balance compounding ✅", 200

if __name__ == "__main__":
    app.run(debug=True)
