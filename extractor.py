import os
from dotenv import load_dotenv
from database import run_migration
import requests
import mysql.connector
import pandas as pd
import yfinance

load_dotenv()

def main():
    print("staring scrapper pipeline...")

    run_binance_extraction()

def run_binance_extraction():

    from bs4 import BeautifulSoup

    # --- Getting the bitcoin data of last 6 Months interval: 4 hours ---
    parameters = {
        "symbol": "BTCUSDT",
        "interval": os.getenv('INTERVAL'),
        "limit": os.getenv('LIMIT')
    }
    response = requests.get(os.getenv('BINANCE_API_KEY'), params=parameters).json()
    # get function is the GET request from https
    # url takes the get function to the binance k-lines(candlestick) end-point
    # params: instead of writing a long url; it straight away requests
    # how it does it:
    #   1. The params=params argument tells Python to:
    #   2. Add a ? at the end of the base url.
    #   3. Take every Key and Value from your dictionary.
    #   4. Join them with an = sign.
    #   5. Separate multiple pairs with an & symbol.
    # the 'get' function will return the response from the server(ref. client-server arc response) along with raw data(wiz. json)
    # .json will return the .json from the request

    print(response)

    symboldict = {"BTCUSDT": "btc", "ETHUSDT": "eth", "SOLUSDT": "sol", "DOGEUSDT": "doge", "XRPUSDT": "xrp"}

    config = {'host': os.getenv('DB_HOST'), 'user': os.getenv('DB_USER'), 'password': os.getenv('DB_PASSWORD'), 'database': os.getenv('DB_NAME')}
    conn = mysql.connector.connect(**config)
    curse = conn.cursor()
    try:
        print("🧹cleaning and uploading prices to database")
        dfi = pd.DataFrame(response)
        dfi = dfi[[0, 1, 2, 3, 4, 7]]
        dfi.columns = ['timestamp_ms', 'open', 'high', 'low', 'close', 'volume_usdt']
        dfi['db_time'] = pd.to_datetime(dfi['timestamp_ms'], unit='ms').dt.strftime('%Y-%m-%d %H:%M:%S')

        batch_data = []
        for _, row in dfi.iterrows():
            batch_data.append((symboldict.get(parameters.get("symbol")), row['db_time'], float(row['close']),
                               float(row['volume_usdt'])))

        # INSERT IGNORE avoids duplicates if you run the script twice
        sql = "INSERT IGNORE INTO  coin_prices(asset,timestamp, price_close, volume_usdt) VALUES (%s,%s, %s, %s)"
        curse.executemany(sql, batch_data)
        conn.commit()
    except Exception as e:
        print("❌ Unable to upload to database")
        print(e)
    finally:
        curse.close()
        conn.close()

def get_usd_data():
    # --- Getting the data of USD for last 5 Days on intervals of 1 Hrs ---
    ticker = yfinance.Ticker("DX-Y.NYB")
    df = ticker.history(period="5d", interval="1h")
    print(df)
    config = {'host': 'localhost', 'user': 'root', 'password': '', 'database': 'crypto_radar_db'}
    conn = mysql.connector.connect(**config)
    curse = conn.cursor()

    df = ticker.history(period="1d", interval="1h")
    print(df)
    df.index = df.index.tz_localize(None)
    try:
        # fix
        df.index.name = "timestamp"
        df = df.reset_index()
        batch_data = []
        for _, row in df.iterrows():
            batch_data.append((row['timestamp'].strftime('%Y-%m-%d %H:%M:%S'), 'DXY', float(row['Close'])))
        sql = "INSERT IGNORE INTO macro_indicators (timestamp, indicator_code, value) VALUES (%s, %s, %s)"
        curse.executemany(sql, batch_data)
        conn.commit()
    except Exception as e:
        print(e)
    finally:
        curse.close()
        conn.close()