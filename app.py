import os
import requests
from flask import Flask, request, jsonify

app = Flask(__name__)

# Environment variables (set these in Render dashboard)
OANDA_API_KEY = os.environ.get("a43082971ce6143beff20a4a5f17c57d-b23f0b5264cea54363dd610e1ee406df")
OANDA_ACCOUNT_ID = os.environ.get("101-001-35645176-001")
OANDA_URL = f"https://api-fxpractice.oanda.com/v3/accounts/{OANDA_ACCOUNT_ID}/orders"
HEADERS = {
    "Authorization": f"Bearer {OANDA_API_KEY}",
    "Content-Type": "application/json"
}

# Your paper trading balance start
current_balance = 1000  # USD
stop_loss_dollars = 100  # fixed stop loss

def calculate_trade_units(price, direction):
    global current_balance
    risk_amount = stop_loss_dollars

    if direction == "buy":
        units = int(current_balance / price)
    else:
        units = int(current_balance / price) * -1

    return units

def place_order(units, instrument="EUR_USD"):
    order_data = {
        "order": {
            "units": str(units),
            "instrument": instrument,
            "timeInForce": "FOK",
            "type": "MARKET",
            "positionFill": "DEFAULT"
        }
    }

    response = requests.post(OANDA_URL, headers=HEADERS, json=order_data)
    return response.json()

@app.route("/webhook", methods=["POST"])
def webhook():
    global current_balance

    data = request.get_json()

    if not data or "strategy" not in data or "order_action" not in data["strategy"]:
        return jsonify({"error": "Invalid payload"}), 400

    signal = data["strategy"]["order_action"].upper()

    # Simulate price coming from TradingView or set manually
    mock_price = float(data.get("price", 1.1000))  # you can override in TV alert message

    if signal == "BUY":
        units = calculate_trade_units(mock_price, "buy")
        result = place_order(units)
        current_balance += 200  # mock profit
        return jsonify({"message": "Buy order executed", "units": units, "mock_balance": current_balance, "response": result})

    elif signal == "SELL":
        units = calculate_trade_units(mock_price, "sell")
        result = place_order(units)
        current_balance += 200  # mock profit
        return jsonify({"message": "Sell order executed", "units": units, "mock_balance": current_balance, "response": result})

    else:
        return jsonify({"error": "Invalid signal"}), 400


@app.route("/", methods=["GET"])
def health():
    return "Webhook listener running!", 200
