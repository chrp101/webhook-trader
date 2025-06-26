# app.py — Virtual-balance compounding bot (IMPROVED)
# --------------------------------------------------------
# • Properly calculates maximum balance including unrealized P/L
# • True compounding: each new position uses the full accumulated balance
# • Better error handling and logging
# • Cleaner separation of concerns

import os, json, requests, logging
from decimal import Decimal, ROUND_DOWN
from flask import Flask, request, jsonify
from datetime import datetime

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

app = Flask(__name__)

# ── ENV + CONSTANTS ──────────────────────────────────────────
API_KEY   = os.getenv("OANDA_API_KEY")
ACCOUNT   = os.getenv("OANDA_ACCOUNT_ID")
BASE_BAL  = Decimal(os.getenv("OANDA_BASE_BALANCE", "5000"))
LEVERAGE  = Decimal("50")         # Buying power multiplier
PAIR      = "EUR_USD"
BAL_FILE  = "balance.json"
TRADE_LOG = "trades.json"

if not API_KEY or not ACCOUNT:
    raise RuntimeError("OANDA_API_KEY or OANDA_ACCOUNT_ID missing")

BASE_URL = f"https://api-fxpractice.oanda.com/v3/accounts/{ACCOUNT}"
HEADERS  = {"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"}

# ── BALANCE & LOGGING HANDLING ──────────────────────────────
def load_balance() -> Decimal:
    if not os.path.exists(BAL_FILE):
        save_balance(BASE_BAL)
        return BASE_BAL
    try:
        with open(BAL_FILE) as f:
            data = json.load(f)
            return Decimal(data["balance"])
    except (json.JSONDecodeError, KeyError) as e:
        logger.warning(f"Could not load balance file: {e}. Using base balance.")
        save_balance(BASE_BAL)
        return BASE_BAL

def save_balance(bal: Decimal):
    with open(BAL_FILE, "w") as f:
        json.dump({
            "balance": str(bal.quantize(Decimal('0.01'), ROUND_DOWN)),
            "last_updated": datetime.now().isoformat()
        }, f, indent=2)

def log_trade(trade_data: dict):
    """Log trade details for analysis"""
    try:
        trades = []
        if os.path.exists(TRADE_LOG):
            with open(TRADE_LOG) as f:
                trades = json.load(f)
        
        trades.append({
            **trade_data,
            "timestamp": datetime.now().isoformat()
        })
        
        with open(TRADE_LOG, "w") as f:
            json.dump(trades[-100:], f, indent=2)  # Keep last 100 trades
    except Exception as e:
        logger.error(f"Failed to log trade: {e}")

# ── OANDA HELPERS ───────────────────────────────────────────
def get_current_position():
    """Get current position details including unrealized P/L"""
    try:
        url = f"{BASE_URL}/positions/{PAIR}"
        r = requests.get(url, headers=HEADERS, timeout=10)
        
        if r.status_code == 404:
            return None
            
        r.raise_for_status()
        position = r.json()["position"]
        
        # Calculate total units and unrealized P/L
        long_units = Decimal(position["long"]["units"])
        short_units = Decimal(position["short"]["units"])
        total_units = long_units + short_units
        
        if total_units == 0:
            return None
            
        unrealized_pl = Decimal(position["unrealizedPL"])
        
        return {
            "units": total_units,
            "unrealized_pl": unrealized_pl,
            "long_units": long_units,
            "short_units": short_units
        }
    except Exception as e:
        logger.error(f"Error getting position: {e}")
        return None

def close_all_positions() -> dict:
    """Close all positions and return realized P/L details"""
    try:
        # First get current position to track unrealized P/L
        current_pos = get_current_position()
        if not current_pos:
            logger.info("No positions to close")
            return {"realized_pl": Decimal("0"), "position_value": Decimal("0")}
        
        unrealized_before_close = current_pos["unrealized_pl"]
        logger.info(f"Closing position with unrealized P/L: {unrealized_before_close}")
        
        # Close the position
        url = f"{BASE_URL}/positions/{PAIR}/close"
        body = {"longUnits": "ALL", "shortUnits": "ALL"}
        r = requests.put(url, headers=HEADERS, json=body, timeout=10)
        
        if r.status_code == 404:
            return {"realized_pl": Decimal("0"), "position_value": Decimal("0")}
            
        r.raise_for_status()
        
        # Calculate total realized P/L from close
        total_realized_pl = Decimal("0")
        data = r.json()
        
        for side in ("longOrderFillTransaction", "shortOrderFillTransaction"):
            if side in data and "pl" in data[side]:
                pl = Decimal(data[side]["pl"])
                total_realized_pl += pl
                logger.info(f"{side} P/L: {pl}")
        
        logger.info(f"Total realized P/L from close: {total_realized_pl}")
        
        return {
            "realized_pl": total_realized_pl,
            "position_value": unrealized_before_close  # This was the position value before closing
        }
        
    except Exception as e:
        logger.error(f"Error closing positions: {e}")
        raise

def get_current_price() -> Decimal:
    """Get current market price"""
    try:
        url = f"https://api-fxpractice.oanda.com/v3/accounts/{ACCOUNT}/pricing?instruments={PAIR}"
        r = requests.get(url, headers=HEADERS, timeout=10)
        r.raise_for_status()
        
        pricing = r.json()["prices"][0]
        # Use mid price for more accurate calculation
        bid = Decimal(pricing["bids"][0]["price"])
        ask = Decimal(pricing["asks"][0]["price"])
        mid_price = (bid + ask) / 2
        
        logger.info(f"Current price - Bid: {bid}, Ask: {ask}, Mid: {mid_price}")
        return mid_price
        
    except Exception as e:
        logger.error(f"Error getting price: {e}")
        raise

def place_market_order(units: int) -> dict:
    """Place a market order"""
    try:
        body = {
            "order": {
                "units": str(units),
                "instrument": PAIR,
                "timeInForce": "FOK",
                "type": "MARKET",
                "positionFill": "DEFAULT"
            }
        }
        
        logger.info(f"Placing order: {units} units")
        r = requests.post(f"{BASE_URL}/orders", headers=HEADERS, json=body, timeout=10)
        r.raise_for_status()
        
        return r.json()
        
    except Exception as e:
        logger.error(f"Error placing order: {e}")
        raise

def calculate_position_size(balance: Decimal, price: Decimal, side: str) -> int:
    """Calculate position size based on virtual balance and leverage"""
    notional_value = balance * LEVERAGE
    raw_units = notional_value / price
    
    # Round down to avoid over-leveraging
    units = int(raw_units.quantize(Decimal("1"), ROUND_DOWN))
    
    # Apply direction
    if side == "SELL":
        units = -units
    
    logger.info(f"Position calculation: Balance={balance}, Price={price}, "
                f"Notional={notional_value}, Units={units}")
    
    return units

# ── MAIN TRADING LOGIC ──────────────────────────────────────
def execute_trade(side: str) -> dict:
    """Main trading logic with proper compounding"""
    
    logger.info(f"=== EXECUTING {side} TRADE ===")
    
    # 1️⃣ Get current virtual balance
    current_virtual_balance = load_balance()
    logger.info(f"Current virtual balance: {current_virtual_balance}")
    
    # 2️⃣ Close all positions and get realized P/L
    close_result = close_all_positions()
    realized_pl = close_result["realized_pl"]
    
    # 3️⃣ Calculate new maximum balance (THIS IS THE KEY IMPROVEMENT)
    # The new balance includes the realized P/L from closing the position
    new_virtual_balance = current_virtual_balance + realized_pl
    
    logger.info(f"Balance after closing: {current_virtual_balance} + {realized_pl} = {new_virtual_balance}")
    
    # 4️⃣ Save the updated balance
    save_balance(new_virtual_balance)
    
    # 5️⃣ Check if balance is sufficient for trading
    if new_virtual_balance <= Decimal("100"):
        raise ValueError(f"Virtual balance too low: {new_virtual_balance}")
    
    # 6️⃣ Get current price and calculate position size
    current_price = get_current_price()
    position_units = calculate_position_size(new_virtual_balance, current_price, side)
    
    if abs(position_units) < 100:
        raise ValueError(f"Position size too small: {position_units} units")
    
    # 7️⃣ Place the new order
    order_response = place_market_order(position_units)
    
    # 8️⃣ Log the trade
    trade_data = {
        "side": side,
        "units": position_units,
        "entry_price": str(current_price),
        "virtual_balance_before": str(current_virtual_balance),
        "realized_pl": str(realized_pl),
        "virtual_balance_after": str(new_virtual_balance),
        "leverage": str(LEVERAGE),
        "notional_value": str(new_virtual_balance * LEVERAGE)
    }
    
    log_trade(trade_data)
    
    return {
        **trade_data,
        "oanda_response": order_response,
        "message": f"Successfully executed {side} trade"
    }

# ── ROUTES ─────────────────────────────────────────────────
@app.route("/", methods=["POST"])
def webhook():
    """Handle TradingView webhook"""
    try:
        payload = request.get_json(silent=True) or {}
        
        # Validate payload
        try:
            side = payload["strategy"]["order_action"].upper()
            if side not in ("BUY", "SELL"):
                raise ValueError("Invalid side")
        except (KeyError, TypeError):
            return jsonify(error="Invalid TradingView payload. Expected: {'strategy': {'order_action': 'BUY/SELL'}}"), 400
        
        # Execute the trade
        result = execute_trade(side)
        
        return jsonify(result), 200
        
    except ValueError as e:
        logger.warning(f"Trade validation error: {e}")
        return jsonify(error=str(e)), 400
    except Exception as e:
        logger.error(f"Trade execution error: {e}")
        return jsonify(error="Trade execution failed", details=str(e)), 500

@app.route("/status")
def status():
    """Get current bot status"""
    try:
        virtual_balance = load_balance()
        current_position = get_current_position()
        current_price = get_current_price()
        
        status_data = {
            "virtual_balance": str(virtual_balance),
            "current_price": str(current_price),
            "leverage": str(LEVERAGE),
            "buying_power": str(virtual_balance * LEVERAGE),
            "position": current_position,
            "timestamp": datetime.now().isoformat()
        }
        
        return jsonify(status_data), 200
        
    except Exception as e:
        logger.error(f"Status error: {e}")
        return jsonify(error="Could not get status", details=str(e)), 500

@app.route("/health")
def health():
    """Simple health check"""
    try:
        balance = load_balance()
        return f"Bot is alive | Virtual balance: ${balance} USD", 200
    except Exception as e:
        return f"Bot error: {str(e)}", 500

@app.route("/reset", methods=["POST"])
def reset_balance():
    """Reset virtual balance to base amount (use with caution!)"""
    try:
        save_balance(BASE_BAL)
        logger.info(f"Balance reset to {BASE_BAL}")
        return jsonify(message=f"Balance reset to {BASE_BAL}"), 200
    except Exception as e:
        return jsonify(error="Reset failed", details=str(e)), 500

if __name__ == "__main__":
    logger.info("Starting trading bot...")
    logger.info(f"Base balance: {BASE_BAL}, Leverage: {LEVERAGE}")
    app.run(debug=True, port=5000)