from flask import Flask, request, jsonify
import requests
import os

app = Flask(__name__)

# Load from environment
OANDA_API_KEY = os.environ["a43082971ce6143beff20a4a5f17c57d-b23f0b5264cea54363dd610e1ee406df"]
ACCOUNT_ID = os.environ["101-001-35645176-001"]
OANDA_URL = "https://api-fxpractice.oanda.com/v3/accounts"

# Start with $1,000
balance = 1000

def calculate_trade_size(balance):
    risk = 100  # fixed $100 SL
    pip_value = risk / 100
    return round(pip_value * 10000)  # convert to units (0.1 lot = 10,000 units)

@app.route("/webhook", methods=["POST"])
def webhook():
    global balance
    data = request.get_json()
    signal = data.get("signal")

    if signal not in ["BUY", "SELL"]:
        return jsonify({"error": "Invalid signal"}), 400

    units = calculate_trade_size(balance)
    if signal == "SELL":
        units *= -1

    order = {
        "order": {
            "units": str(units),
            "instrument": "EUR_USD",
            "timeInForce": "FOK",
            "type": "MARKET",
            "positionFill": "DEFAULT",
            "stopLossOnFill": {
                "distance": "0.0100"
            }
        }
    }

    headers = {
        "Authorization": f"Bearer {OANDA_API_KEY}",
        "Content-Type": "application/json"
    }

    response = requests.post(
        f"{OANDA_URL}/{ACCOUNT_ID}/orders",
        headers=headers,
        json=order
    )

    if response.status_code == 201:
        print("Trade executed")
        return jsonify({"status": "Trade executed"}), 200
    else:
        print("Error:", response.text)
        return jsonify({"error": response.text}), 400

if __name__ == "__main__":
    app.run()
