import os, requests
from flask import Flask, request, jsonify

app = Flask(__name__)

# ── ENV VARS ────────────────────────────────
OANDA_API_KEY = os.getenv("OANDA_API_KEY")
OANDA_ACCOUNT_ID = os.getenv("OANDA_ACCOUNT_ID")
BASE = f"https://api-fxpractice.oanda.com/v3/accounts/{OANDA_ACCOUNT_ID}"
HEADERS = {
    "Authorization": f"Bearer {OANDA_API_KEY}",
    "Content-Type": "application/json"
}

# ── SETTINGS ────────────────────────────────
STARTING_BALANCE = 1000  # Use this only for the first trade simulation
USE_STATIC_BALANCE = True  # Toggle to use fixed $1000 or live balance

# ── HELPERS ────────────────────────────────
def get_balance():
    r = requests.get(f"{BASE}/summary", headers=HEADERS, timeout=10)
    r.raise_for_status()
    return float(r.json()["account"]["balance"])

def calculate_units(balance):
    # Full balance used with 1 pip move ($0.0001)
    units = balance / 0.0001
    return int(units)

def close_open_positions():
    r = requests.get(f"{BASE}/openPositions", headers=HEADERS, timeout=10)
    r.raise_for_status()
    data = r.json()

    for position in data.get("positions", []):
        instrument = position["instrument"]
        net_units = float(position["long"]["units"]) - float(position["short"]["units"])
        if net_units != 0:
            close_side = "short" if net_units > 0 else "long"
            close_data = {
                "longUnits": "ALL" if close_side == "long" else "NONE",
                "shortUnits": "ALL" if close_side == "short" else "NONE"
            }
            resp = requests.put(f"{BASE}/positions/{instrument}/close", headers=HEADERS, json=close_data, timeout=10)
            resp.raise_for_status()

def place_order(units, side):
    if side == "SELL":
        units = -abs(units)
    else:
        units = abs(units)

    order = {
        "order": {
            "units": str(units),
            "instrument": "EUR_USD",
            "timeInForce": "FOK",
            "type": "MARKET",
            "positionFill": "DEFAULT"
        }
    }
    r = requests.post(f"{BASE}/orders", headers=HEADERS, json=order, timeout=10)
    r.raise_for_status()
    return r.json()

# ── ROUTES ────────────────────────────────
@app.route("/", methods=["POST"])
def webhook():
    data = request.get_json(silent=True) or {}
    try:
        action = data["strategy"]["order_action"].upper()
        assert action in ("BUY", "SELL")
    except Exception:
        return jsonify(error="Invalid payload"), 400

    try:
        # Optional: Static balance simulation on first trade
        balance = STARTING_BALANCE if USE_STATIC_BALANCE else get_balance()
        units = calculate_units(balance)
        close_open_positions()  # Make sure no position is open
        resp_json = place_order(units, action)
    except Exception as e:
        return jsonify(error="Execution failed", details=str(e)), 500

    return jsonify(
        message="Order placed",
        side=action,
        units=units,
        balance=f"${balance:,.2f}",
        oanda_response=resp_json
    ), 200

@app.route("/health")
def health():
    return "Webhook live and only one trade at a time ✅", 200

if __name__ == "__main__":
    app.run(debug=True)
