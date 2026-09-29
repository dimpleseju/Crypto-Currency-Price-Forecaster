import time
from binance.spot import Spot
import mysql.connector

# DB CONFIG
DB_CONFIG = {
    'host': 'localhost',
    'user': 'root',
    'password': '',
    'database': 'crypto_radar_db'
}

# No API keys needed for public data!
client = Spot()


def fetch_binance_data():
    print(f"⏰ Fetching from Binance...")
    conn = mysql.connector.connect(**DB_CONFIG)
    cursor = conn.cursor()

    pairs = [
        ('btc', 'BTCUSDT'),
        ('eth', 'ETHUSDT'),
        ('sol', 'SOLUSDT'),
        ('xrp', 'XRPUSDT'),
        ('doge', 'DOGEUSDT')
    ]

    try:
        for name, symbol in pairs:
            # Get latest 1-hour candle (limit=1)
            # klines returns: [Open Time, Open, High, Low, Close, Volume, ...]
            klines = client.klines(symbol, "4h", limit=2)

            if klines:
                latest = klines[0]
                # Binance time is in ms, convert to seconds
                ts = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(latest[0] / 1000))
                price = float(latest[4])  # Close price
                volume = float(latest[5])  # Volume

                sql = "INSERT IGNORE INTO coin_prices (asset, timestamp, price_close, volume_usdt) VALUES (%s, %s, %s, %s)"
                cursor.execute(sql, (name, ts, price, volume))
                print(f"   ✅ {name.upper()}: ${price}")

        conn.commit()

    except Exception as e:
        print(f"❌ Error: {e}")
    finally:
        conn.close()


if __name__ == "__main__":
    while True:
        fetch_binance_data()
        # Sleep for 1 hour
        time.sleep(3600)