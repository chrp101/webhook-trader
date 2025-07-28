# app.py
import os
from flask import Flask, request, jsonify
from oandapyV20 import API
from oandapyV20.endpoints.accounts import AccountSummary
from oandapyV20.endpoints.pricing import PricingInfo
from oandapyV20.endpoints.orders import OrderCreate

# ——— Configuration from env vars —————————————————————————————————————
OANDA_API_KEY      = os.getenv("OANDA_API_KEY")
OANDA_ACCOUNT_ID   = os.getenv("OANDA_ACCOUNT_ID")
RESERVE_RATIO      = float(os.getenv("OANDA_RESERVE_RATIO", 0.2))

if not OANDA_API_KEY or not OANDA_ACCOUNT_ID:
    raise RuntimeError("Missing OANDA_API_KEY or OANDA_ACCOUNT_ID env vars")

client = API(access_token=OANDA_API_KEY, environment="live")  # or "practice"

app = Flask(__name__)

@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.get_json(force=True)
    side       = data.get("side", "").lower()      # expect "buy" or "sell"
    instrument = data.get("symbol") or data.get("ticker")
    if side not in ("buy", "sell") or not instrument:
        return jsonify({"error": "malformed payload"}), 400

    # 1) Fetch account summary to see how much margin you have available
    acct_req = AccountSummary(accountID=OANDA_ACCOUNT_ID)
    acct_resp = client.request(acct_req)["account"]
    margin_available = float(acct_resp["marginAvailable"])

    # 2) Fetch latest price for your instrument
    pricing_req = PricingInfo(
        accountID=OANDA_ACCOUNT_ID,
        params={ "instruments": instrument }
    )
    px = client.request(pricing_req)["prices"][0]
    price = float(px["asks"][0]["price"] if side=="buy" else px["bids"][0]["price"])

    # 3) Determine what fraction of margin to use
    use_frac = 1.0 if side == "buy" else (1.0 - RESERVE_RATIO)
    alloc_margin = margin_available * use_frac

    # 4) Compute units = how many base-currency units you can buy/sell
    units = int(alloc_margin / price)
    if units < 1:
        return jsonify({
            "error": "insufficient funds",
            "details": {
                "margin_available": margin_available,
                "allocated_margin": alloc_margin,
                "price": price
            }
        }), 400

    # 5) Create market order: positive units for buy, negative for sell
    order_data = {
        "order": {
            "instrument": instrument,
            "units": str(units if side=="buy" else -units),
            "type": "MARKET",
            "positionFill": "DEFAULT"
        }
    }
    order_req = OrderCreate(accountID=OANDA_ACCOUNT_ID, data=order_data)
    order_resp = client.request(order_req)

    return jsonify({
        "status":     "order placed",
        "side":       side,
        "instrument": instrument,
        "units":      units,
        "price":      price,
        "order":      order_resp.get("orderCreateTransaction", {})
    }), 200

if __name__ == "__main__":
    # Render.com will use `gunicorn app:app`—this is just for local dev
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)))
