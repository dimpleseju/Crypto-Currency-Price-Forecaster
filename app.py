import warnings

from flask import request, Flask, render_template, jsonify, redirect, session, Response
from flask_cors import CORS
import mysql.connector
import pandas as pd
import joblib
import yfinance as yf
import json
import os

from werkzeug.utils import secure_filename


# Suppress deprecation warnings originating from third-party libraries
warnings.filterwarnings("ignore", category=FutureWarning)
warnings.filterwarnings("ignore", category=UserWarning, module="yfinance")
app = Flask(__name__)
CORS(app)
app.secret_key = "my_random_kit"

# --- DATABASE CONFIG ---
DB_CONFIG = {
    'host': 'localhost',
    'user': 'root',
    'password': '',
    'database': 'crypto_radar_db'
}
symboldict = {"btc":"bitcoin","eth":"ethereum","sol":"solana","doge":"dogecoin","xrp":"ripple"}
# --- UPLOAD CONFIG ---
UPLOAD_FOLDER = 'static/uploads/profiles/'
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

# Automatically create the folder structure so it doesn't crash on the first upload!
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# --- LOAD MODEL ---
MODEL_PATH = "models/crypto_pulse_brain.pkl"
CONFIG_PATH = "model_lab/model_config.json"

model_accuracy = 0.0
try:
    model = joblib.load(MODEL_PATH)
    print("✅ AI Model Loaded")
    # Load Accuracy from the JSON file created by model.ipynb
    with open(CONFIG_PATH, "r") as f:
        config = json.load(f)
        model_accuracy = config.get("expected_accuracy", 0.0)
    print(f"✅ Loaded Model Accuracy: {model_accuracy:.1%}")
except Exception as e:
    model = None
    print(f"❌ Model/Config not found: {e}")

try:
    model = joblib.load(MODEL_PATH)
    print("✅ AI Model Loaded")
except Exception as e:
    model = None
    print(f"❌ Model not found: {e}")


def get_db_connection():
    return mysql.connector.connect(**DB_CONFIG)


# ==========================================
# 1. LIVE DATA ENGINE (Replaces SQL for Visuals)
# ==========================================

def fetch_historical_data(symbol='btc', period='1D'):
    try:
        # STABLE INTERVALS
        yf_period = "1d"
        yf_interval = "15m"

        if period == '1W':
            yf_period = "5d"  # 5 Trading Days = 1 Week
            yf_interval = "1h"  # 1h is stable. 90m causes fluctuations.
        elif period == '1M':
            yf_period = "1mo"
            yf_interval = "1d"

        ticker = f"{symbol.upper()}-USD"
        df = yf.download(ticker, period=yf_period, interval=yf_interval, progress=False)

        if df.empty: return {"labels": [], "prices": [], "current_price": 0}

        # Structure Fix
        if isinstance(df.columns, pd.MultiIndex):
            if 'Close' in df.columns.get_level_values(0):
                df = df.xs('Close', axis=1, level=0, drop_level=True)
            else:
                df.columns = df.columns.droplevel(0)

        df = df.reset_index()

        # TIMESTAMP FIX
        date_col = 'Datetime' if 'Datetime' in df.columns else 'Date'
        if date_col not in df.columns: date_col = df.columns[0]

        # Format labels nicely based on timeframe
        # 1W needs Month + Day + Time to be useful
        if period == '1W':
            labels = df[date_col].dt.strftime('%b %d %H:%M').tolist()
        elif period == '1M':
            labels = df[date_col].dt.strftime('%b %d').tolist()
        else:  # 1D
            labels = df[date_col].dt.strftime('%H:%M').tolist()

        prices = df.iloc[:, -1].ffill().tolist()  # Get last column (Price) safely

        return {
            "labels": labels,
            "prices": prices,
            "current_price": float(prices[-1]) if prices else 0
        }
    except Exception as e:
        print(f"Chart Error: {e}")
        return {"labels": [], "prices": [], "current_price": 0}

def get_24h_change(symbol='btc'):
    """
    Fetches real-time 24h change from YFinance.
    """
    try:
        ticker = yf.Ticker(f"{symbol.upper()}-USD")
        # Fast method: Use history
        hist = ticker.history(period="7d")

        if len(hist) < 2: return 0.0

        current = hist['Close'].iloc[-1]
        yesterday = hist['Close'].iloc[-2]

        change = ((current - yesterday) / yesterday) * 100
        return round(change, 2)
    except Exception as e:
        return 0.0


def calculate_market_metrics(symbol='btc'):
    """
    Fetches RSI, Support, and Volatility.
    FIXED: Forces single float values to prevent Series errors.
    """
    try:
        # Get 14 days of hourly data
        df = yf.download(f"{symbol.upper()}-USD", period="14d", interval="1h", progress=False)

        if df.empty: return None

        # --- THE FIX: Ensure 'prices' is a 1D Series ---
        if isinstance(df.columns, pd.MultiIndex):
            prices = df.xs('Close', axis=1, level=0, drop_level=True)
        else:
            prices = df['Close']

        # Squeeze to ensure it's a Series, not a 1-column DataFrame
        prices = prices.squeeze()

        # 1. RSI (14)
        delta = prices.diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
        rs = gain / loss
        rsi = 100 - (100 / (1 + rs))

        # FIX: Force extraction of the last scalar value
        current_rsi = float(rsi.iloc[-1])

        # 2. Support (Lowest low of last 7 days)
        support_price = float(prices.tail(168).min())

        # 3. Volatility
        returns = prices.pct_change()
        # FIX: Force float conversion
        volatility_score = float(returns.tail(24).std() * 100)

        vol_level = "LOW"
        if volatility_score > 1.5:
            vol_level = "HIGH"
        elif volatility_score > 0.5:
            vol_level = "MED"

        return {
            "rsi": round(current_rsi, 1),
            "volatility_score": round(volatility_score, 3),
            "volatility_level": vol_level,
            "support": round(support_price, 2)
        }
    except Exception as e:
        print(f"Metrics Error: {e}")
        return None


# ==========================================
# 2. AI ENGINE (Keeps using SQL for consistency)
# ==========================================

def fetch_live_features(symbol='btc'):
    """
    Fetches data from SQL for the AI.
    NOTE: You MUST run your data collector script for the AI to update!
    """
    try:
        conn = get_db_connection()
        # Using the "Noise Filter" logic you requested
        query = f"""
                WITH all_sentiment AS (
                    SELECT timestamp, senti_score, weight, asset FROM posts_logs 
                    WHERE asset IN ('{symbol}', 'bitcoin', 'ethereum', 'market', 'crypto')
                    UNION ALL
                    SELECT timestamp, senti_score, 1.0 as weight, asset FROM news_logs 
                    WHERE asset IN ('{symbol}', 'bitcoin', 'ethereum', 'market', 'crypto')
                )
                SELECT
                    p.timestamp,
                    p.price_close as price,
                    p.volume_usdt as volume,

                    -- LOCAL SENTIMENT (The coin itself)
                    (SELECT SUM(senti_score * weight) / SUM(weight) FROM all_sentiment
                     WHERE asset IN ('{symbol}', '{symboldict[symbol]}') 
                     AND timestamp BETWEEN DATE_SUB(p.timestamp, INTERVAL 1 DAY) AND p.timestamp
                    ) as sentiment_coin,

                    -- GLOBAL SENTIMENT (Bitcoin)
                    (SELECT SUM(senti_score * weight) / SUM(weight) FROM all_sentiment
                     WHERE asset = 'bitcoin'
                     AND timestamp BETWEEN DATE_SUB(p.timestamp, INTERVAL 1 DAY) AND p.timestamp
                    ) as sentiment_btc,

                    (SELECT value FROM macro_indicators WHERE indicator_code = 'DXY' ORDER BY ABS(TIMESTAMPDIFF(SECOND, timestamp, p.timestamp)) ASC LIMIT 1) as dxy_index
                FROM coin_prices p
                WHERE p.asset = '{symbol}'
                ORDER BY p.timestamp DESC LIMIT 100
                """

        df = pd.read_sql(query, conn)
        conn.close()

        if len(df) < 50: return None

        df = df.sort_values('timestamp').reset_index(drop=True)
        df['sentiment_coin'] = df['sentiment_coin'].fillna(0.5)
        df['sentiment_btc'] = df['sentiment_btc'].fillna(0.5)
        df['dxy_index'] = df['dxy_index'].ffill().bfill()

        # Features
        df['sentiment_trend_coin'] = df['sentiment_coin'].rolling(window=3).mean()
        df['sentiment_trend_btc'] = df['sentiment_btc'].rolling(window=3).mean()
        df['price_change_pct'] = df['price'].pct_change()

        # TECHNICALS (For Noise Filter Model)
        delta = df['price'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
        rs = gain / loss
        df['rsi'] = 100 - (100 / (1 + rs))

        df['volatility'] = df['price'].pct_change().rolling(window=24).std()

        df['sma_50'] = df['price'].rolling(window=50).mean()
        df['dist_from_sma'] = (df['price'] / df['sma_50']) - 1

        latest = df.iloc[[-1]].dropna()
        return latest if not latest.empty else None

    except Exception as e:
        print(f"AI Feature Error: {e}")
        return None


# --- ROUTES ---
@app.route('/',methods=["GET","POST"])
def login():
    if request.method == "POST":
        conn = get_db_connection()
        curse = conn.cursor(dictionary=True)
        user = request.form.get("username")
        password = request.form.get("password")
        curse.execute("SELECT * FROM users WHERE ((username = %s OR email = %s) AND password=%s) LIMIT 1",[user,user,password])
        info = curse.fetchone()
        curse.close()
        conn.close()
        if info:
            if info['password'] == password:
                session['user_id'] = info['id']
                return redirect("/dashboard")
            else:
                return redirect("/"),401
        else:
            return redirect("/"),401




    return render_template('login-page.html')


@app.route('/dashboard')
def dashboard(): return render_template('dashboard.html')


@app.route('/coin-details')
def coin_details():
    # 1. Catch the symbol from the URL search bar (default to BTC if empty)
    searched_coin = request.args.get('symbol', 'BTC').upper().strip()

    # 2. The "Translation Dictionary"
    # If a user types the full name, this converts it to the ticker symbol!
    coin_map = {
        "BITCOIN": "btc",
        "ETHEREUM": "eth",
        "SOLANA": "sol",
        "RIPPLE": "xrp",
        "DOGECOIN": "doge"
    }

    # 3. Translate if it's a full name, otherwise keep what they typed
    if searched_coin in coin_map:
        final_symbol = coin_map[searched_coin]
    else:
        final_symbol = searched_coin

    # 4. Pass the final, clean symbol to your HTML template!

    return render_template('coin-details-page.html',symbol=final_symbol.lower())




@app.route('/api/dashboard')
def api_dashboard():
    symbol = request.args.get('symbol', 'btc')
    period = request.args.get('period', '1D')

    # 1. Fetch Chart
    chart_data = fetch_historical_data(symbol, period)

    # 2. Fetch Metrics
    metrics = calculate_market_metrics(symbol)
    if not metrics:
        metrics = {"rsi": 50, "volatility_level": "LOW", "support": 0, "volatility_score": 0}

    # 3. Fetch AI Logic
    ai_response = {"direction": "NEUTRAL", "confidence": 0, "signal": "WAITING...","accuracy": round(model_accuracy * 100, 1),"target_price": 0}
    sentiment_data = {"score": 50, "label": "NEUTRAL", "color": "text-github-text-muted"}


    if model:
        live_df = fetch_live_features(symbol)
        if live_df is not None:
            try:
                # Update features list to match your new model
                features = [
                    'volume', 'dxy_index', 'price_change_pct',
                    'sentiment_coin', 'sentiment_trend_coin','sentiment_btc','sentiment_trend_btc', 'rsi', 'volatility', 'dist_from_sma'
                ]
                X_live = live_df[features]
                probabilities = model.predict_proba(X_live)[0]
                bearish_prob = probabilities[0]
                bullish_prob = probabilities[1]
                print(bullish_prob,bearish_prob)

                current_price = float(live_df['price'].values[0])
                raw_vol = metrics['volatility_score'] / 100
                volatility_factor = max(0.005, min(raw_vol, 0.10))

                if bullish_prob>=0.6:
                    direction = "BULLISH"
                    signal = "BUY"
                    target_price = current_price * (1 + volatility_factor)
                    confidence = bullish_prob
                elif bearish_prob >= 0.6:
                    direction = "BEARISH"
                    signal = "SELL"
                    target_price = current_price * (1 - volatility_factor)
                    confidence = bearish_prob
                else:
                    direction = "NEUTRAL"
                    signal = "HOLD"
                    target_price = current_price * (1 + volatility_factor)
                    confidence = max(bearish_prob,bullish_prob)

                ai_response = {
                    "direction": direction,
                    "confidence": round(confidence * 100, 1),
                    "accuracy":round(model_accuracy*100,1),
                    "signal": signal,
                    "target_price": round(target_price,2)
                }

                score = int(live_df['sentiment_trend_coin'].values[0] * 100)
                print(int(live_df['sentiment_trend_coin'].values[0] * 100))
                if score > 60:
                    label, color = "GREED", "text-accent-green"
                elif score < 40:
                    label, color = "FEAR", "text-accent-red"
                else:
                    label, color = "NEUTRAL", "text-accent-yellow"
                sentiment_data = {"score": score, "label": label, "color": color}
            except Exception:
                print("Not Enough data (need at least 50 rows of each and every data to make prediction)")
                pass


    # 4. NEW: Fetch Market Stats (Fixes "Loading..." issue)
    stats = {"market_cap": 0, "volume": 0, "supply": 0}
    try:
        ticker_obj = yf.Ticker(f"{symbol.upper()}-USD")
        # Fast info fetch
        info = ticker_obj.fast_info
        stats['market_cap'] = info.market_cap
        stats['volume'] = info.last_volume
        stats['supply'] = info.shares_outstanding  # Crypto 'supply' maps to this in fast_info
    except:
        # Fallback to standard info if fast_info fails
        try:
            info = ticker_obj.info
            stats['market_cap'] = info.get('marketCap', 0)
            stats['volume'] = info.get('volume24Hr', 0)
            stats['supply'] = info.get('circulatingSupply', 0)
        except:
            pass

    change_pct = get_24h_change(symbol)

    return jsonify({
        "chart": chart_data,
        "ai": ai_response,
        "protocol": {
            "rsi": metrics['rsi'],
            "volatility": metrics['volatility_level'],
            "volatility_score":metrics['volatility_score'],
            "support": metrics['support'],
            "text": f"Support at ${metrics['support']:,.0f}"
        },
        "sentiment": sentiment_data,
        "change_24": change_pct,
        "stats": stats
    })

# --- NEW: Dashboard Overview API ---
# --- REPLACEMENT FOR market_overview IN app.py ---

@app.route('/api/market-overview')
def market_overview():
    try:
        coins = {
            'BTC': 'BTC-USD', 'ETH': 'ETH-USD', 'SOL': 'SOL-USD',
            'XRP': 'XRP-USD', 'DOGE': 'DOGE-USD', 'DOT': 'DOT-USD'
        }

        # FIX: Fetch 7 days instead of 2d to prevent "out-of-bounds" errors
        tickers = list(coins.values())
        data = yf.download(tickers, period="7d", interval="1d", progress=False)

        # Handle different YFinance return structures
        if isinstance(data.columns, pd.MultiIndex):
            try:
                closes = data.xs('Close', axis=1, level=0, drop_level=True)
            except:
                closes = data['Close']
        elif 'Close' in data.columns:
            closes = pd.DataFrame(data['Close'])
            if len(tickers) == 1: closes.columns = tickers
        else:
            closes = data

        overview_data = []

        for symbol, ticker in coins.items():
            try:
                if ticker not in closes.columns: continue

                # Get clean data
                series = closes[ticker].dropna()
                if len(series) < 2: continue  # Skip if not enough data

                current = float(series.iloc[-1])
                prev = float(series.iloc[-2])
                change = ((current - prev) / prev) * 100

                # Simple Confidence Logic
                conf = min(50 + (abs(change) * 10), 95)
                signal = "BULLISH" if change > 0 else "BEARISH"

                overview_data.append({
                    "symbol": symbol,
                    "price": current,
                    "change": round(change, 2),
                    "confidence": int(conf),
                    "signal": signal
                })
            except Exception as e:
                print(f"Error {symbol}: {e}")

        # Global Stats Approximation
        btc_change = next((x['change'] for x in overview_data if x['symbol'] == 'BTC'), 0)

        return jsonify({
            "coins": overview_data,
            "global": {
                "sentiment_score": int(50 + btc_change * 5),
                "sentiment_label": "Greed" if btc_change > 0 else "Fear",
                "btc_dominance": 52.4,
                "global_vol": "84.2B"
            }
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/signup',methods=['GET','POST'])

def signup():
    if request.method == 'POST':
        try:
            full_name = request.form.get('username')
            email = request.form.get('email')
            password = request.form.get('password')
            conn = get_db_connection()
            curse = conn.cursor(dictionary=True)
            curse.execute("SELECT * FROM USERS WHERE EMAIL = %s",(email,))
            exists = curse.fetchone()

            if exists:
                curse.close()
                conn.close()
                return jsonify({"status":"error","message":"You are already onboard! try logging in.",}),409
            curse.execute("INSERT INTO users (username, email, password) VALUES (%s,%s, %s)", (full_name,email, password))
            conn.commit()

            curse.close()
            conn.close()

            # Return success signal
            return jsonify({"status": "success", "message": "Account created!"}), 200
        except Exception as e:
            return jsonify("there is a problem in signin: {e}")

    return render_template("signup-page.html")

@app.route("/api/export-csv")
def export_csv():
    # 1. Grab the exact coin and timeframe from the URL
    symbol = request.args.get("symbol", "BTC").upper()
    period = request.args.get("period", "1D")

    # 2. Match the exact logic from your fetch_historical_data function
    yf_period = "1d"
    yf_interval = "15m"

    if period == '1W':
        yf_period = "5d"
        yf_interval = "1h"
    elif period == '1M':
        yf_period = "1mo"
        yf_interval = "1d"

    # 3. Download the raw Pandas DataFrame from Yahoo Finance
    ticker = f"{symbol}-USD"
    df = yf.download(ticker, period=yf_period, interval=yf_interval, progress=False)

    if df.empty:
        return "No data found for this asset.", 404

    # 4. Convert the beautiful Pandas DataFrame directly into a CSV string!
    csv_data = df.to_csv()

    # 5. Send it back as a physical file download!
    return Response(
        csv_data,
        mimetype="text/csv",
        headers={"Content-disposition": f"attachment; filename={symbol}_{period}_Historical_Data.csv"}
    )


@app.route("/profile", methods=['GET', 'POST'])
def profile():
    # 1. Security Check
    if "user_id" not in session:
        return redirect("/")

    current_user_id = session['user_id']

    # 2. Open Connection
    conn = get_db_connection()
    curse = conn.cursor(dictionary=True)

    # 3. Get User Data
    curse.execute("SELECT * FROM users WHERE id = %s", (current_user_id,))
    user_data = curse.fetchone()

    # 4. Handle Saves (POST)
    if request.method == 'POST':
        new_username = request.form.get('username')
        new_pwd = request.form.get('new_password')

        # Check if the new password field actually has text!
        if new_pwd:
            # Make sure this matches the 'name' attribute in your HTML exactly!
            current_pwd = request.form.get('current-pwd')

            if user_data['password'] != current_pwd:
                curse.close()
                conn.close()
                return "incorrect password", 401

            curse.execute(
                "UPDATE users SET username = %s, password = %s WHERE id = %s",
                [new_username, new_pwd, current_user_id]
            )
        else:
            # They left the password blank, just update username
            curse.execute(
                "UPDATE users SET username = %s WHERE id = %s",
                [new_username, current_user_id]
            )

        if "profile_picture" in request.files:
            file = request.files['profile_picture']

            if file and file.filename != "":
                safe_filename = secure_filename(file.filename)
                unique_filename = f"user_{current_user_id}_{safe_filename}"
                save_path = os.path.join(app.config['UPLOAD_FOLDER'], unique_filename)

                # 5. Physically save the image to your PyCharm project folder
                file.save(save_path)

                # 6. Save the web path to the database (adding ONE slash so the browser finds it)
                db_image_url = "/" + save_path.replace("\\", "/")

                # -> I DELETED THE EXTRA f"/{db_image_url}" LINE HERE! <-

                curse.execute(
                    "UPDATE users SET profile_pic = %s WHERE id = %s",
                    [db_image_url, current_user_id]
                )

        conn.commit()
        curse.close()
        conn.close()
        return "Profile Updated", 200

    # 5. Handle Viewing (GET)
    # CRITICAL: We must close the connection before rendering the page!
    curse.close()
    conn.close()

    return render_template("profile-page.html", user=user_data)


@app.route('/logout')
def logout():
    # 1. Destroy the cookie entirely!
    session.clear()

    # 2. Kick them back to the login screen
    return redirect('/')

if __name__ == '__main__':
    app.run(debug=True, port=5000)