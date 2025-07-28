import os, json, requests, logging
from flask import Flask, request, jsonify
from decimal import Decimal, ROUND_DOWN

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)

# ── ENV VARS ─────────────────────────────────────────
OANDA_API_KEY     = os.getenv("OANDA_API_KEY")
ACCOUNT_ID        = os.getenv("OANDA_ACCOUNT_ID")
DEFAULT_PAIR      = os.getenv("OANDA_DEFAULT_PAIR", "EUR_USD")
LEVERAGE          = Decimal(os.getenv("OANDA_LEVERAGE", "50"))
RESERVE_RATIO     = Decimal(os.getenv("OANDA_RESERVE_RATIO", "0"))
TRAIL_BACK_PCT    = Decimal(os.getenv("TRAIL_BACK_PCT", "0.5"))  # pct of price

if not OANDA_API_KEY or not ACCOUNT_ID:
    raise RuntimeError("Missing OANDA_API_KEY or OANDA_ACCOUNT_ID")

BASE_URL = f"https://api-fxpractice.oanda.com/v3/accounts/{ACCOUNT_ID}"
HEADERS  = {
    "Authorization": f"Bearer {OANDA_API_KEY}",
    "Content-Type":  "application/json"
}

# ── HELPERS ─────────────────────────────────────────────
def get_price(pair):
    r = requests.get(f"{BASE_URL}/pricing?instruments={pair}", headers=HEADERS)
    r.raise_for_status()
    p = r.json()["prices"][0]
    bid = Decimal(p["bids"][0]["price"])
    ask = Decimal(p["asks"][0]["price"])
    return (bid + ask) / 2

def get_balance():
    r = requests.get(BASE_URL, headers=HEADERS)
    r.raise_for_status()
    bal = Decimal(r.json()["account"]["balance"])
    logging.info(f"Account balance: {bal}")
    return bal

def get_open_position(pair):
    r = requests.get(f"{BASE_URL}/openPositions", headers=HEADERS)
    if r.status_code != 200:
        return Decimal("0")
    for pos in r.json().get("positions", []):
        if pos["instrument"] == pair:
            long_u  = Decimal(pos["long"]["units"])
            short_u = Decimal(pos["short"]["units"])
            return long_u - short_u
    return Decimal("0")

def close_all_positions(pair):
    logging.info(f"Closing positions for {pair}")
    requests.put(
        f"{BASE_URL}/positions/{pair}/close", headers=HEADERS,
        json={"longUnits":"ALL","shortUnits":"ALL"}
    )

def calculate_units(balance, price, side):
    reserve = balance * RESERVE_RATIO if side=="SELL" else Decimal("0")
    equity  = balance - reserve
    if equity <= 0:
        raise ValueError("Equity too low")
    units = int((equity * LEVERAGE / price).quantize(Decimal("1"), ROUND_DOWN))
    logging.info(f"Side={side} | reserve={reserve} | equity={equity} | units={units}")
    return units if side=="BUY" else -units

def place_order(pair, units):
    price = get_price(pair)
    payload = {
        "order": {
            "instrument": pair,
            "units": str(units),
            "type": "MARKET",
            "timeInForce": "FOK",
            "positionFill": "DEFAULT",
            # attach a trailing‐stop at TRAIL_BACK_PCT% of entry price
            "trailingStopLossOnFill": {
                "distance": str((TRAIL_BACK_PCT/100 * price).quantize(Decimal("0.00001")))
            }
        }
    }
    logging.info(f"Placing order: {payload}")
    r = requests.post(f"{BASE_URL}/orders", headers=HEADERS, json=payload)
    r.raise_for_status()
    return r.json()

# ── FLASK ROUTES ────────────────────────────────────────
@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.get_json(force=True, silent=True) or json.loads(request.data)
    side = data.get("signal")
    pair = data.get("symbol", DEFAULT_PAIR)
    if side not in ("BUY","SELL"):
        return jsonify({"error":"Invalid signal"}), 400

    try:
        pos = get_open_position(pair)
        # skip if already in the right direction
        if (side=="BUY" and pos>0) or (side=="SELL" and pos<0):
            return jsonify({"status":"Already in position","pair":pair}), 200
        if pos!=0:
            close_all_positions(pair)

        bal   = get_balance()
        price = get_price(pair)
        units = calculate_units(bal, price, side)
        res   = place_order(pair, units)
        return jsonify({"status":f"Executed {side}", "units":units, "pair":pair}), 200

    except Exception as e:
        logging.exception("Trade failed")
        return jsonify({"error":str(e)}), 500

@app.route("/")
def home():
    return "Trading bot is up."

if __name__=="__main__":
    app.run(debug=True, port=int(os.getenv("PORT",5000)))
