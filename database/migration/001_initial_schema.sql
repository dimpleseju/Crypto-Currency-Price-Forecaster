CREATE TABLE IF NOT EXISTS news_logs (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    asset VARCHAR(10),
    timestamp DATETIME NOT NULL,
    headline VARCHAR(500) UNIQUE,
    senti_score DECIMAL(5, 4)
);

CREATE TABLE IF NOT EXISTS posts_logs (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    asset VARCHAR(10),
    timestamp DATETIME NOT NULL,
    post_text VARCHAR(500) UNIQUE,
    senti_score DECIMAL(5, 4),
    weight DECIMAL(20,4)
);

CREATE TABLE IF NOT EXISTS coin_prices (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    asset VARCHAR(10),
    timestamp DATETIME NOT NULL,
    price_close DECIMAL(20,8),
    volume_usdt DECIMAL(30, 2)
);

 CREATE TABLE IF NOT EXISTS macro_indicators (
                id INT AUTO_INCREMENT PRIMARY KEY,
                timestamp DATETIME UNIQUE,
                indicator_code VARCHAR(10),
                value DECIMAL(15, 6)
);

CREATE TABLE IF NOT EXISTS users (
    id INT AUTO_INCREMENT PRIMARY KEY,
    username VARCHAR(100) NOT NULL,
    email VARCHAR(150) NOT NULL UNIQUE,
    password VARCHAR(255) NOT NULL,
    profile_pic VARCHAR(255) DEFAULT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);