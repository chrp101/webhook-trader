# app.py — Simulated Compounding Strategy from Fixed $1,000

import os, requests
from flask import Flask, request, jsonify

app = Flask(__name__)

# ── SIMULATION STATE ───────────────────────────────────────────────
simulated_balance = 1000.00  # starting capital
open_trade = None            # hold current open position

# ── OANDA SETTINGS (set these in Render environment) ───────────────
OANDA_API_KEY    = os.getenv("OANDA_API_KEY")
OANDA_ACCOUNT_ID = os.getenv("OANDA_ACCOUNT_ID")

if not OANDA_API_KEY or not OANDA_ACCOUNT_ID:
    raise RuntimeError("Missing OANDA_API_KEY or OANDA_ACCOUNT_ID!")

BASE = f"https://api-fxpractice.oanda.com/v3/accounts/{OANDA_ACCOUNT_ID}"
HEADERS = {
    "Authorization": f"Bearer {OANDA_API_KEY}",
    "Content-Type": "application/json"
}

# ── STRATEGY SETTINGS ─────────────────────────────────────────────
LEVERAGE = 50
PIP_VALUE_PER_1000 = 0.1   # Approximate pip value for EUR/USD per 1,000 units
INSTRUMENT = "EUR_USD"

# ── OANDA FUNCTIONS ────────────────────────────────────────────────
def close_open_trades():
    r = requests.get(f"{BASE}/openTrades", headers=HEADERS, timeout=10)
    r.raise_for_status()
    trades = r.json().get("trades", [])
    for trade in trades:
        trade_id = trade["id"]
        close_url = f"{BASE}/trades/{trade_id}/close"
        resp = requests.put(close_url, headers=HEADERS)
        resp.raise_for_status()
    return len(trades)

def oanda_order(units):
    data = {
        "order": {
            "units": str(units),
            "instrument": INSTRUMENT,
            "timeInForce": "FOK",
            "type": "MARKET",
            "positionFill": "DEFAULT"
        }
    }
    r = requests.post(f"{BASE}/orders", headers=HEADERS, json=data, timeout=10)
    r.raise_for_status()
    return r.json()

# ── MAIN WEBHOOK ───────────────────────────────────────────────────
@app.route("/", methods=["POST"])
def webhook():
    global simulated_balance

    data = request.get_json(silent=True) or {}
    try:
        action = data["strategy"]["order_action"].upper()
        assert action in ("BUY", "SELL")
    except Exception:
        return jsonify(error="Invalid TradingView payload"), 400

    try:
        close_open_trades()
    except Exception as e:
        return jsonify(error="Failed to close trades", details=str(e)), 500

    # calculate maximum position size based on simulated balance
    margin = simulated_balance
    max_position = margin * LEVERAGE
    price = 1.10  # assumed fixed price for simulation
    units = int(max_position / price)
    if action == "SELL":
        units = -units

    try:
        response = oanda_order(units)
    except Exception as e:
        return jsonify(error="Order failed", details=str(e)), 500

    return jsonify(
        message="Simulated order placed",
        side=action,
        units=units,
        balance=f"${simulated_balance:,.2f}",
        oanda_response=response
    )

@app.route("/health")
def health():
    return "Webhook running (Simulated $1,000 Compounding Mode)", 200

if __name__ == "__main__":
    app.run(debug=True)
