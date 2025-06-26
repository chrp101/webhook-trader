# app.py — Virtual Balance driven by ENV var `OANDA_BASE_BALANCE`
# -------------------------------------------------------------
# • Reads starting balance from environment (default $1 000)
# • Persists virtual balance in balance.json (survives restarts)
# • Closes any open EUR‑USD trade before new one
# • Uses 50× leverage on *virtual* balance to size the order
# -------------------------------------------------------------

import os, json, requests
from decimal import Decimal, ROUND_DOWN
from flask import Flask, request, jsonify

app = Flask(__name__)

# ── ENV & CONSTANTS ───────────────────────────────────────────
API_KEY   = os.getenv("OANDA_API_KEY")
ACCOUNT   = os.getenv("OANDA_ACCOUNT_ID")
BASE_BAL  = Decimal(os.getenv("OANDA_BASE_BALANCE", "1000"))  # dynamic baseline ✅
LEVERAGE  = Decimal("50")                                       # leverage to apply
PAIR      = "EUR_USD"
BAL_FILE  = "balance.json"

if not API_KEY or not ACCOUNT:
    raise RuntimeError("Missing OANDA_API_KEY or OANDA_ACCOUNT_ID env vars")

BASE_URL = f"https://api-fxpractice.oanda.com/v3/accounts/{ACCOUNT}"
HEADERS  = {"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"}

# ── BALANCE PERSISTENCE ───────────────────────────────────────

def load_balance() -> Decimal:
    """Load virtual balance; create file if absent with baseline."""
    if not os.path.exists(BAL_FILE):
        save_balance(BASE_BAL)
    with open(BAL_FILE, "r") as f:
        return Decimal(json.load(f)["balance"])

def save_balance(bal: Decimal):
    with open(BAL_FILE, "w") as f:
        json.dump({"balance": str(bal.quantize(Decimal("0.01"), ROUND_DOWN))}, f)

# ── OANDA HELPERS ─────────────────────────────────────────────

def close_position():
    resp = requests.put(f"{BASE_URL}/positions/{PAIR}/close", headers=HEADERS,
                        json={"longUnits": "ALL", "shortUnits": "ALL"}, timeout=10)
    if resp.status_code == 404:
        return Decimal("0")
    resp.raise_for_status()
    pl = Decimal("0")
    data = resp.json()
    for side in ("longOrderFillTransaction", "shortOrderFillTransaction"):
        if side in data and "pl" in data[side]:
            pl += Decimal(data[side]["pl"])
    return pl

def get_price() -> Decimal:
    r = requests.get(f"{BASE_URL.replace('/accounts/', '/pricing')}?instruments={PAIR}", headers=HEADERS, timeout=10)
    r.raise_for_status()
    return Decimal(r.json()["prices"][0]["asks"][0]["price"])

def place_order(units: int):
    body = {
        "order": {
            "units": str(units),
            "instrument": PAIR,
            "timeInForce": "FOK",
            "type": "MARKET",
            "positionFill": "DEFAULT"
        }
    }
    r = requests.post(f"{BASE_URL}/orders", headers=HEADERS, json=body, timeout=10)
    r.raise_for_status()
    return r.json()

# ── ROUTES ───────────────────────────────────────────────────
@app.route("/", methods=["POST"])
def webhook():
    payload = request.get_json(silent=True) or {}
    try:
        side = payload["strategy"]["order_action"].upper()
        assert side in ("BUY", "SELL")
    except Exception:
        return jsonify(error="Invalid TradingView payload"), 400

    # 1. Close existing position and update virtual balance
    realized_pl = close_position()
    virt_balance = load_balance() + realized_pl
    save_balance(virt_balance)

    # 2. Calculate units from virtual balance only
    price   = get_price()
    notional= virt_balance * LEVERAGE
    units   = int((notional / price).quantize(Decimal("1")))
    if side == "SELL":
        units = -units

    if abs(units) < 100:  # minimum trade size guard
        return jsonify(error="Virtual balance too small for trade", balance=str(virt_balance)), 400

    # 3. Place order
    try:
        oanda_resp = place_order(units)
    except Exception as e:
        return jsonify(error="Order failed", details=str(e)), 500

    return jsonify(
        message          = "Order placed",
        side             = side,
        units            = units,
        virtual_balance  = str(virt_balance),
        leverage         = str(LEVERAGE),
        notional_value   = f"{(notional):.2f}",
        entry_price      = str(price),
        pl_from_close    = str(realized_pl),
        oanda_response   = oanda_resp
    ), 200

@app.route("/health")
def health():
    return f"Bot live | virtual balance: {load_balance()} USD", 200

if __name__ == "__main__":
    app.run(debug=True)
