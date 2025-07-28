import os, json, requests, logging
from flask import Flask, request, jsonify
from decimal import Decimal, ROUND_DOWN

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)

# ── ENV VARIABLES ──────────────────────────────────────
OANDA_API_KEY        = os.getenv("OANDA_API_KEY")
ACCOUNT_ID           = os.getenv("OANDA_ACCOUNT_ID")
DEFAULT_PAIR         = os.getenv("OANDA_DEFAULT_PAIR", "EUR_USD")
LEVERAGE             = Decimal(os.getenv("OANDA_LEVERAGE", "50"))
RESERVE_RATIO        = Decimal(os.getenv("OANDA_RESERVE_RATIO", "0"))
TRAIL_TRIGGER_PCT    = Decimal(os.getenv("TRAIL_TRIGGER_PCT", "2"))   # % gain before trailing activates
TRAIL_BACK_PCT       = Decimal(os.getenv("TRAIL_BACK_PCT", "0.5"))    # % to trail back from peak

if not OANDA_API_KEY or not ACCOUNT_ID:
    raise RuntimeError("Missing OANDA_API_KEY or OANDA_ACCOUNT_ID")

BASE_URL = f"https://api-fxpractice.oanda.com/v3/accounts/{ACCOUNT_ID}"
HEADERS  = {"Authorization": f"Bearer {OANDA_API_KEY}", "Content-Type": "application/json"}


# ── HELPERS ─────────────────────────────────────────────
def get_price(pair):
    r = requests.get(f"{BASE_URL}/pricing?instruments={pair}", headers=HEADERS)
    r.raise_for_status()
    data = r.json()["prices"][0]
    bid = Decimal(data["bids"][0]["price"])
    ask = Decimal(data["asks"][0]["price"])
    return (bid + ask) / 2

def get_balance():
    r = requests.get(BASE_URL, headers=HEADERS)
    r.raise_for_status()
    return Decimal(r.json()["account"]["balance"])

def get_open_position(pair):
    r = requests.get(f"{BASE_URL}/openPositions", headers=HEADERS)
    if r.status_code != 200:
        return Decimal("0")
    for p in r.json().get("positions", []):
        if p["instrument"] == pair:
            long_units = Decimal(p["long"]["units"])
            short_units = Decimal(p["short"]["units"])
            return long_units - short_units
    return Decimal("0")

def close_all_positions(pair):
    requests.put(f"{BASE_URL}/positions/{pair}/close", headers=HEADERS,
                 json={"longUnits": "ALL", "shortUnits": "ALL"})

def calculate_units(balance: Decimal, price: Decimal, side: str) -> int:
    reserve = balance * RESERVE_RATIO if side == "SELL" else Decimal("0")
    equity = balance - reserve
    if equity <= 0:
        raise ValueError("No funds available to trade.")
    units = (equity * LEVERAGE / price).quantize(Decimal("1"), ROUND_DOWN)
    return int(units if side == "BUY" else -units)

def place_order(pair: str, units: int):
    payload = {
        "order": {
            "units": str(units),
            "instrument": pair,
            "timeInForce": "FOK",
            "type": "MARKET",
            "positionFill": "DEFAULT"
        }
    }
    response = requests.post(f"{BASE_URL}/orders", headers=HEADERS, json=payload)
    response.raise_for_status()
    return response.json()


# ── ROUTES ──────────────────────────────────────────────
@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.get_json()
    side = data.get("signal")
    pair = data.get("symbol", DEFAULT_PAIR)

    if side not in ["BUY", "SELL"]:
        return jsonify({"error": "Invalid signal"}), 400

    try:
        current_position = get_open_position(pair)
        if (side == "BUY" and current_position > 0) or (side == "SELL" and current_position < 0):
            return jsonify({"status": "Already in position", "pair": pair}), 200

        if current_position != 0:
            close_all_positions(pair)

        price = get_price(pair)
        balance = get_balance()
        units = calculate_units(balance, price, side)
        place_order(pair, units)

        return jsonify({
            "status": f"Executed {side}",
            "pair": pair,
            "units": units
        }), 200

    except Exception as e:
        logging.exception("Trade failed")
        return jsonify({"error": str(e)}), 500


@app.route("/")
def home():
    return "Trading bot is running."


if __name__ == "__main__":
    app.run(debug=True, port=int(os.getenv("PORT", 5000)))
