# app.py
import os
import requests
from flask import Flask, request, jsonify

app = Flask(__name__)

# ▸ READ SECRETS FROM ENV  (do **not** put the real string here)
OANDA_API_KEY   = os.getenv("a43082971ce6143beff20a4a5f17c57d-b23f0b5264cea54363dd610e1ee406df")       # <-- just the var name
OANDA_ACCOUNT_ID = os.getenv("101-001-35645176-001")   # e.g. 101-001-1234567-001

# sanity-check at start-up (will show in Render logs)
if not all([OANDA_API_KEY, OANDA_ACCOUNT_ID]):
    raise RuntimeError("OANDA_API_KEY or OANDA_ACCOUNT_ID env-vars not set!")

OANDA_API_URL = f"https://api-fxpractice.oanda.com/v3/accounts/{OANDA_ACCOUNT_ID}/orders"

@app.route("/", methods=["POST"])
def webhook():
    data = request.get_json(force=True, silent=True)

    # basic payload validation
    try:
        action = data["strategy"]["order_action"].upper()
        assert action in ("BUY", "SELL")
    except Exception:
        return jsonify(error="Invalid payload"), 400

    units = "1000" if action == "BUY" else "-1000"

    order = {
        "order": {
            "units": units,
            "instrument": "EUR_USD",
            "timeInForce": "FOK",
            "type": "MARKET",
            "positionFill": "DEFAULT"
        }
    }

    headers = {
        "Authorization": f"Bearer {OANDA_API_KEY}",
        "Content-Type": "application/json"
    }

    r = requests.post(OANDA_API_URL, json=order, headers=headers, timeout=10)

    if r.status_code == 201:
        return jsonify(message="Order placed", details=r.json()), 200
    else:
        return jsonify(error="Order failed", details=r.json()), r.status_code

@app.route("/health", methods=["GET"])
def health():
    return "Webhook is alive!", 200

if __name__ == "__main__":
    app.run(debug=True)



