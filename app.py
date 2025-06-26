# app.py  (compounding + live balance)

import os, requests
from flask import Flask, request, jsonify

app = Flask(__name__)

# ── ENV VARS (set in Render) ─────────────────────────────────────
OANDA_API_KEY   = os.getenv("OANDA_API_KEY")        # practice token
OANDA_ACCOUNT_ID = os.getenv("OANDA_ACCOUNT_ID")    # 101-xxx-…-001

if not OANDA_API_KEY or not OANDA_ACCOUNT_ID:
    raise RuntimeError("OANDA_API_KEY or OANDA_ACCOUNT_ID not set!")

BASE = f"https://api-fxpractice.oanda.com/v3/accounts/{OANDA_ACCOUNT_ID}"
HDRS = {"Authorization": f"Bearer {OANDA_API_KEY}",
        "Content-Type": "application/json"}

# ── RISK SETTINGS ────────────────────────────────────────────────
RISK_USD        = 100      # dollars you’re willing to lose per trade
STOP_PIPS       = 100      # assumed SL distance
UNIT_STEP       = 100      # round units to nearest 100

# ── HELPERS ──────────────────────────────────────────────────────
def balance():
    r = requests.get(f"{BASE}/summary", headers=HDRS, timeout=10)
    r.raise_for_status()
    return float(r.json()["account"]["balance"])

def units_for_risk():
    pip_value_needed = RISK_USD / STOP_PIPS        # $ / pip
    units = pip_value_needed / 0.0001              # for EURUSD
    return int(round(units / UNIT_STEP) * UNIT_STEP) or UNIT_STEP

def oanda_order(units: int):
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

# ── ROUTES ───────────────────────────────────────────────────────
@app.route("/", methods=["POST"])
def webhook():
    data = request.get_json(silent=True) or {}
    try:
        action = data["strategy"]["order_action"].upper()
        assert action in ("BUY", "SELL")
    except Exception:
        return jsonify(error="Invalid payload"), 400

    try:
        bal_before = balance()
    except Exception as e:
        return jsonify(error="Cannot fetch balance", details=str(e)), 500

    units = units_for_risk()
    if action == "SELL":
        units = -units

    try:
        resp_json = oanda_order(units)
    except requests.HTTPError as e:
        return jsonify(error="Order failed", details=e.response.json()), e.response.status_code

    return jsonify(
        message="Order placed",
        side=action,
        units=units,
        balance_before=f"{bal_before:.2f}",
        oanda_response=resp_json
    ), 200

@app.route("/health")
def health():
    return "Webhook up & compounding ✅", 200

if __name__ == "__main__":
    app.run(debug=True)
