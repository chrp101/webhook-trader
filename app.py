import os, requests, json
from flask import Flask, request, jsonify

app = Flask(__name__)

# ── ENV VARS ─────────────────────────────────────────────
OANDA_API_KEY    = os.getenv("OANDA_API_KEY")
OANDA_ACCOUNT_ID = os.getenv("OANDA_ACCOUNT_ID")

if not OANDA_API_KEY or not OANDA_ACCOUNT_ID:
    raise RuntimeError("OANDA_API_KEY or OANDA_ACCOUNT_ID not set!")

BASE = f"https://api-fxpractice.oanda.com/v3/accounts/{OANDA_ACCOUNT_ID}"
HDRS = {
    "Authorization": f"Bearer {OANDA_API_KEY}",
    "Content-Type": "application/json"
}

# ── SIMULATION STATE ─────────────────────────────────────
BALANCE_FILE = "balance.json"
START_BALANCE = 1000

# ── HELPERS ──────────────────────────────────────────────
def load_balance():
    if not os.path.exists(BALANCE_FILE):
        with open(BALANCE_FILE, "w") as f:
            json.dump({"balance": START_BALANCE}, f)
    with open(BALANCE_FILE) as f:
        return float(json.load(f)["balance"])

def save_balance(new_balance):
    with open(BALANCE_FILE, "w") as f:
        json.dump({"balance": round(new_balance, 2)}, f)

def close_all_positions():
    url = f"{BASE}/positions/EUR_USD/close"
    body = {"longUnits": "ALL", "shortUnits": "ALL"}
    r = requests.put(url, headers=HDRS, json=body)
    return r.ok

def place_order(units):
    body = {
        "order": {
            "units": str(units),
            "instrument": "EUR_USD",
            "timeInForce": "FOK",
            "type": "MARKET",
            "positionFill": "DEFAULT"
        }
    }
    r = requests.post(f"{BASE}/orders", headers=HDRS, json=body)
    r.raise_for_status()
    return r.json()

def get_latest_trade_pl():
    r = requests.get(f"{BASE}/trades", headers=HDRS)
    r.raise_for_status()
    trades = r.json().get("trades", [])
    if not trades:
        return 0
    latest = max(trades, key=lambda t: t["openTime"])
    return float(latest.get("unrealizedPL", 0))

# ── ROUTES ───────────────────────────────────────────────
@app.route("/", methods=["POST"])
def webhook():
    data = request.get_json(silent=True) or {}
    try:
        action = data["strategy"]["order_action"].upper()
        assert action in ("BUY", "SELL")
    except Exception:
        return jsonify(error="Invalid signal payload"), 400

    # Close existing trades to prevent overlap
    close_all_positions()

    # Use simulated balance only
    sim_balance = load_balance()
    entry_price = 1.17  # approximation; could be fetched live for precision
    margin_per_unit = 0.02  # estimated margin needed per unit
    max_units = int(sim_balance / margin_per_unit)

    if max_units < 100:
        return jsonify(error="Balance too low to trade"), 400

    units = max_units if action == "BUY" else -max_units
    try:
        resp = place_order(units)
    except requests.HTTPError as e:
        return jsonify(error="Order failed", details=e.response.json()), 500

    save_balance(sim_balance)  # temporary until profit realized
    return jsonify(message="Order placed",
                   side=action,
                   units=units,
                   balance=f"${sim_balance:,.2f}",
                   oanda_response=resp), 200

@app.route("/health")
def health():
    return "Compounding bot is running ✅", 200

if __name__ == "__main__":
    app.run(debug=True)
