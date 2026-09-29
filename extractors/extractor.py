import os
import re
from datetime import datetime, timezone, timedelta
from collections import defaultdict


import requests
import pandas as pd
import yfinance
import mysql.connector
from dotenv import load_dotenv
from bs4 import BeautifulSoup

import en_core_web_sm
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from webdriver_manager.chrome import ChromeDriverManager
import feedparser

from database import migrate

# Load environment variables
load_dotenv()

# Load SpaCy NLP Model
nlp = en_core_web_sm.load()


def get_db_connection():
    """Helper function to establish a fresh database connection."""
    return mysql.connector.connect(
        host=os.getenv('DB_HOST'),
        user=os.getenv('DB_USER'),
        password=os.getenv('DB_PASSWORD'),
        database=os.getenv('DB_NAME')
    )


def run_binance_extraction(conn, curse):
    """Extracts historical price data from Binance API and uploads to MySQL."""
    symbol = "BTCUSDT"
    parameters = {
        "symbol": symbol,
        "interval": os.getenv('INTERVAL', '4h'),
        "limit": os.getenv('LIMIT', '500')
    }

    # Binance Public K-lines Endpoint
    url = "https://api.binance.com/api/v3/klines"

    try:
        response = requests.get(url, params=parameters)
        response.raise_for_status()
        data = response.json()

        print("🧹 Cleaning and uploading prices to database...")
        dfi = pd.DataFrame(data)
        dfi = dfi[[0, 1, 2, 3, 4, 7]]
        dfi.columns = ['timestamp_ms', 'open', 'high', 'low', 'close', 'volume_usdt']
        dfi['db_time'] = pd.to_datetime(dfi['timestamp_ms'], unit='ms').dt.strftime('%Y-%m-%d %H:%M:%S')

        symboldict = {"BTCUSDT": "btc", "ETHUSDT": "eth", "SOLUSDT": "sol", "DOGEUSDT": "doge", "XRPUSDT": "xrp"}

        batch_data = []
        for _, row in dfi.iterrows():
            batch_data.append((
                symboldict.get(symbol, "btc"),
                row['db_time'],
                float(row['close']),
                float(row['volume_usdt'])
            ))

        sql = "INSERT IGNORE INTO coin_prices(asset, timestamp, price_close, volume_usdt) VALUES (%s, %s, %s, %s)"
        curse.executemany(sql, batch_data)
        conn.commit()
        print("✅ Binance data uploaded successfully.")
    except Exception as e:
        print("❌ Unable to upload Binance data to database:")
        print(e)


def get_usd_data(conn, curse):
    """Fetches DXY macroeconomic indicator data using yfinance and stores in database."""
    ticker = yfinance.Ticker("DX-Y.NYB")
    df = ticker.history(period="5d", interval="1h")

    if df.empty:
        print("⚠️ No data fetched for USD Index (DXY).")
        return

    df.index = df.index.tz_localize(None)
    try:
        df.index.name = "timestamp"
        df = df.reset_index()
        batch_data = []
        for _, row in df.iterrows():
            batch_data.append((
                row['timestamp'].strftime('%Y-%m-%d %H:%M:%S'),
                'DXY',
                float(row['Close'])
            ))
        sql = "INSERT IGNORE INTO macro_indicators (timestamp, indicator_code, value) VALUES (%s, %s, %s)"
        curse.executemany(sql, batch_data)
        conn.commit()
        print("✅ DXY data uploaded successfully.")
    except Exception as e:
        print("❌ Unable to upload DXY data:")
        print(e)


# --- Sentiment Analysis Helper Methods ---

def get_symbol(pattern, text):
    symbols = re.findall(pattern, text, re.IGNORECASE)
    return symbols[0].lower() if symbols else "market"


def load_headers():
    return {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept-Language": "en-US,en;q=0.9",
        "Connection": "keep-alive",
        "Referer": "https://www.google.com/",
        "Upgrade-Insecure-Requests": "1",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,webp,image/apng,*/*;q=0.8",
    }

def load_reddit_headers():
    """Returns headers required to bypass Reddit's bot block on .json endpoints."""
    return {
        # Reddit requires a unique, descriptive User-Agent identifier
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept-Language": "en-US,en;q=0.9",
        "Connection": "keep-alive",
        "Referer": "https://www.google.com/",
        "Upgrade-Insecure-Requests": "1",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,webp,image/apng,*/*;q=0.8",
    }

def is_question(text):
    text_lower = text.lower()
    if text_lower.endswith('?'):
        return True

    question_starters = ['is', 'will', 'should', 'can']
    if text_lower.split() and text_lower.split()[0] in question_starters:
        return True

    panic_triggers = ['sink', 'plunge', 'tumble', 'liquidated', 'dump', 'bloodbath']
    panic_word_pattern = r'\b(' + '|'.join(panic_triggers) + r')\b'
    if re.search(panic_word_pattern, text_lower):
        return False

    info_keywords = [
        'how', 'why', 'what', 'thoughts', 'opinions', 'guide', 'help',
        'question', 'tutorial', 'technical', 'understand',
        'explain', 'recommendation', 'portfolio', 'comparison'
    ]
    keywords_re = "|".join(info_keywords)
    if re.search(rf'^(?:\s*\S+){{0,4}}\s*\b({keywords_re})\b', text_lower):
        return True

    return False


def lemmatize_text(text):
    doc = nlp(text)
    return " ".join([token.lemma_ for token in doc])


def sentiment_analysis(lemm_text):
    crypto_custom_weights = {
        'adoption': 3.5, 'integration': 3.0, 'merchant': 2.5, 'exemption': 2.5,
        'enshrine': 2.5, 'rollout': 2.5, 'expansion': 2.5, 'sovereign': 3.5,
        'treasury': 3.0, 'reserve': 3.0, 'accumulation': 2.5, 'breakout': 3.5,
        'rebound': 2.5, 'inflow': 3.0, 'outflow': -3.5, 'liquidate': -3.5,
        'capitulation': -4.0, 'oversold': 2.0, 'correction': -1.5, 'upgrade': 3.0,
        'mainnet': 2.5, 'scalability': 2.0, 'exploit': -4.0, 'outage': -3.0,
        'halt': -3.0, 'boom': 4.0, 'eye': 1.0, 'rally': 3.0, 'retreat': -2.0,
        'slide': -2.0, 'sink': -3.5, 'plunge': -4.0, 'tumble': -3.5,
        'bloodbath': -4.0, 'moon': 4.0, 'pump': 3.5, 'bullish': 3.5, 'ath': 3.0,
        'acquire': 3.0, 'primed': 2.0, 'overperform': 3.0, 'outperform': 3.0,
        'rug': -4.0, 'dump': -4.0, 'bearish': -3.5, 'sec': -2.0, 'underperform': -3.0,
        'lag': -2.5, 'fud': -2.5, 'hack': -4.0, 'incline': 2.0, 'decline': -2.5,
        'surge': 3.5, 'stake': 2.5, 'optimism': 2.0, 'fade': -3.0, 'erase': -3.0,
        'shrink': -2.0, 'diverge': -1.5, 'slip': -2.0, 'liquidation': -3.5
    }

    analyzer = SentimentIntensityAnalyzer()
    analyzer.lexicon.update(crypto_custom_weights)
    vs = analyzer.polarity_scores(lemm_text.lower())
    return vs['compound']  # Returns compound score between -1.0 and 1.0


def get_reddit_sentiments(conn, curse):
    """Fetches Reddit posts from r/CryptoMarkets and performs sentiment analysis."""
    headers = load_reddit_headers()
    url = "https://www.reddit.com/r/CryptoMarkets/.rss"

    try:
        response = requests.get(url, headers=headers,timeout=10)
        response.raise_for_status()
        feed = feedparser.parse(response.content)

        pattern = r"\b(bitcoin|ethereum|ether|dogecoin|btc|eth|doge|solana|sol|xrp|ripple|bnb|ada|cardano|dot|polkadot|litecoin|ltc|chainlink|link|ftx|luna|terra|avax|avalanche|matic|polygon)\b"

        for entry in feed.entries:
            title = getattr(entry, 'title', 'No Title')
            summary = getattr(entry, 'summary', '')

            # Remove HTML tags from RSS content
            clean_summary = re.sub(r'<[^>]+>', '', summary)
            signal_text = title if len(title) > 30 else f"{title}. {clean_summary}"
            symbol = get_symbol(pattern, signal_text)

            # Extract publication date
            if hasattr(entry, 'published_parsed') and entry.published_parsed:
                time_str = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
            else:
                time_str = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')

            if symbol and is_question(title):
                senti_score = 0.5
                model_weight = 1.0
            elif symbol and not is_question(title):
                lemmatized_text = lemmatize_text(signal_text)
                raw_score = sentiment_analysis(lemmatized_text)
                senti_score = (raw_score + 1) / 2  # Normalize VADER [-1, 1] to [0, 1]
                model_weight = 1.0
            else:
                continue

            inslist = [symbol, time_str, signal_text, senti_score, model_weight]
            curse.execute(
                "INSERT IGNORE INTO posts_logs (asset, timestamp, post_text, senti_score, weight) VALUES (%s, %s, %s, %s, %s)",
                inslist
            )
        conn.commit()
        print("✅ Reddit sentiments uploaded successfully.")
    except Exception as e:
        print("❌ Reddit sentiment extraction failed:")
        print(e)


def get_crypto_news(conn, curse):
    """Scrapes news headlines from CoinDesk using Selenium and analyzes sentiment."""
    chrome_options = Options()
    chrome_options.add_argument("--headless")  # Headless mode for reliability
    chrome_options.add_argument("--window-size=1200,800")
    chrome_options.add_argument('--log-level=3')

    driver = webdriver.Chrome(
        service=Service(ChromeDriverManager().install()),
        options=chrome_options
    )

    try:
        driver.get("https://www.coindesk.com/markets")
        soup = BeautifulSoup(driver.page_source, 'html.parser')

        headings = [h2.text.strip() for h2 in soup.find_all('h2')]
        paras = soup.find_all('p')
        texts = []
        for para in paras:
            texts.extend([i.get_text() for i in para.find_all('span')])

        dates = []
        for text in texts:
            dateobj = None
            if "minutes ago" in text:
                dateobj = datetime.now() - timedelta(minutes=int(text.split()[0]))
            elif "hours ago" in text:
                dateobj = datetime.now() - timedelta(hours=int(text.split()[0]))
            elif str(datetime.now().year) in text:
                text = text.replace(",", "")
                try:
                    dateobj = datetime.strptime(text, "%b %d %Y")
                except ValueError:
                    continue
            else:
                continue

            if dateobj:
                dates.append(dateobj.strftime('%Y-%m-%d %H:%M:%S'))

        pattern = r"bitcoin|ethereum|market|dogecoin|solana|sol|xrp"
        pattern2 = r"\b[A-Z]{3,5}\b"
        blacklist = {'ceo', 'cftc', 'market', 'etf', 'sec', 'usa', 'cnbc'}

        t = defaultdict(list)
        for heading, time_str in zip(headings, dates):
            found = re.findall(pattern, heading, re.IGNORECASE) + re.findall(pattern2, heading)
            symbols = set([s.lower() for s in found if s.lower() not in blacklist])

            for coin in symbols:
                lemm_text = lemmatize_text(heading)
                raw_score = sentiment_analysis(lemm_text)
                norm_score = (raw_score + 1) / 2  # Scale [-1, 1] to [0, 1]
                t[coin].append({'headline': heading, 'time': time_str, 'sentiment_score': norm_score})

        for asset, data in t.items():
            for item in data:
                inslist = (asset, item['time'], item['headline'], item['sentiment_score'])
                curse.execute(
                    "INSERT IGNORE INTO news_logs (asset, timestamp, headline, senti_score) VALUES (%s, %s, %s, %s)",
                    inslist
                )

        conn.commit()
        print("✅ CoinDesk news sentiments uploaded successfully.")
    except Exception as e:
        print("❌ CoinDesk extraction failed:")
        print(e)
    finally:
        driver.quit()


def main():
    print("🚀 Starting scraper pipeline...")
    migrate.run_migration()

    conn = get_db_connection()
    curse = conn.cursor(dictionary=True)

    try:
        run_binance_extraction(conn, curse)
        get_usd_data(conn, curse)
        get_reddit_sentiments(conn, curse)
        get_crypto_news(conn, curse)
    finally:
        curse.close()
        conn.close()
        print("🔒 Database connection cleanly closed.")


if __name__ == "__main__":
    main()