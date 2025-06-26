from flask import Flask, request, jsonify
import requests
import os

app = Flask(__name__)

OANDA_API_KEY = os.getenv("a43082971ce6143beff20a4a5f17c57d-b23f0b5264cea54363dd610e1ee406df")
OANDA_ACCOUNT_ID = os.getenv("101-001-35645176-001")
OANDA_API_URL = "https://api-fxpractice.oanda.com/v3/accounts"

@app.route("/", methods=["POST"])
def webhook():
    data = request.get_json()

    if not data or "strategy" not in data or "order_action" not in data["strategy"]:
        return jsonify({"error": "Invalid payload"}), 400

    order_action = data["strategy"]["order_action"].upper()
    price = data.get("price", "market")

    if order_action not in ["BUY", "SELL"]:
        return jsonify({"error": "Unsupported action"}), 400

    side = "buy" if order_action == "BUY" else "sell"

    order_data = {
        "order": {
            "units": "1000" if side == "buy" else "-1000",
            "instrument": "EUR_USD",
            "timeInForce": "FOK",
            "type": "MARKET",
            "positionFill": "DEFAULT"
        }
    }

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {OANDA_API_KEY}"
    }

    response = requests.post(
        f"{OANDA_API_URL}/{OANDA_ACCOUNT_ID}/orders",
        headers=headers,
        json=order_data
    )

    if response.status_code != 201:
        return jsonify({"error": "Order failed", "details": response.json()}), 500

    return jsonify({"message": "Order placed successfully", "details": response.json()})


