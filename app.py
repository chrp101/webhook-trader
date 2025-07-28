import os
from flask import Flask, request, jsonify
from oandapyV20 import API
from oandapyV20.endpoints.accounts import AccountSummary
from oandapyV20.endpoints.pricing import PricingInfo
from oandapyV20.endpoints.orders import OrderCreate

# Load environment variables
OANDA_API_KEY    = os.getenv("OANDA_API_KEY")
OANDA_ACCOUNT_ID = os.getenv("OANDA_ACCOUNT_ID")
RESERVE_RATIO    = float(os.getenv("OANDA_RESERVE_RATIO", 0.2))  # 20% reserved for short trades

if not OANDA_API_KEY or not OANDA_ACCOUNT_ID:
    raise RuntimeError("OANDA_API_KEY and OANDA_ACCOUNT_ID must be set as environment variables.")

client = API(access_token=OANDA_API_KEY, environment="live")  # or "practice"

app = Flask(__name__)

@app.route("/webhook", methods=["POST"])
def webhook():
    try:
        data = request.get_json(force=True)
        print("Webhook received:", data)

        # Accept either 'side' or 'signal'
        side = data.get("side", "").lower() or data.get("signal", "").lower()
        instrument = data.get("symbol") or data.get("ticker") or "EUR_USD"

        if side not in ("buy", "sell"):
            return jsonify({"error": "missing or invalid 'side'/'signal'"}), 400

        # Fetch account margin
        acct_summary = AccountSummary(accountID=OANDA_ACCOUNT_ID)
        acct_data = client.request(acct_summary)["account"]
        margin_available = float(acct_data["marginAvailable"])

        # Fetch latest price
        pricing = PricingInfo(accountID=OANDA_ACCOUNT_ID, params={"instruments": instrument})
        price_data = client.request(pricing)["prices"][0]
        price = float(price_data["asks"][0]["price"] if side == "buy" else price_data["bids"][0]["price"])

        # Determine how much to use
        margin_to_use = margin_available * (1.0 if side == "buy" else (1.0 - RESERVE_RATIO))
        units = int(margin_to_use / price)

        if units < 1:
            return jsonify({
                "error": "insufficient funds to open position",
                "margin_available": margin_available,
                "price": price,
                "units": units
            }), 400

        # Place order
        order_data = {
            "order": {
                "instrument": instrument,
                "units": str(units if side == "buy" else -units),
                "type": "MARKET",
                "positionFill": "DEFAULT"
            }
        }
        order_request = OrderCreate(accountID=OANDA_ACCOUNT_ID, data=order_data)
        response = client.request(order_request)

        return jsonify({
            "status": "order placed",
            "side": side,
            "instrument": instrument,
            "units": units,
            "price": price,
            "response": response.get("orderCreateTransaction", {})
        }), 200

    except Exception as e:
        print("Error:", str(e))
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)))
