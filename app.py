from flask import Flask, request, jsonify
import os
import requests

app = Flask(__name__)

# Load OANDA credentials from environment variables
OANDA_API_KEY = os.environ.get("a43082971ce6143beff20a4a5f17c57d-b23f0b5264cea54363dd610e1ee406df")
ACCOUNT_ID = os.environ.get("01-001-35645176-001")
OANDA_URL = f"https://api-fxpractice.oanda.com/v3/accounts/{ACCOUNT_ID}/orders"

# Define the trading logic
@app.route('/', methods=['POST'])
def webhook():
    data = request.json
    print("Received data:", data)

    try:
        order_action = data['strategy']['order_action']
        price = float(data['price'])  # Ensure it's a float
    except (KeyError, ValueError, TypeError):
        return jsonify({"error": "Invalid payload format"}), 400

    # Define units: 1000 micro lots (adjust based on your risk preference)
    units = "1000" if order_action == "BUY" else "-1000"

    order_data = {
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

    response = requests.post(OANDA_URL, headers=headers, json=order_data)
    print("OANDA response:", response.status_code, response.text)

    if response.status_code >= 200 and response.status_code < 300:
        return jsonify({"message": "Order placed successfully"}), 200
    else:
        return jsonify({"error": "Failed to place order", "details": response.text}), 500

# Health check route
@app.route('/', methods=['GET'])
def health_check():
    return "Webhook listener is running!", 200

# For local testing
if __name__ == "__main__":
    app.run(debug=True)

