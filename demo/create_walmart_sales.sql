-- Schema and table for the Kaggle "Walmart Dataset" by M Yasser H:
-- https://www.kaggle.com/datasets/yasserh/walmart-dataset
-- Run the whole script in DBeaver's SQL editor (Alt+X), or let demo/load_walmart.py run it.

CREATE SCHEMA IF NOT EXISTS kaggle;

CREATE TABLE IF NOT EXISTS kaggle.walmart_sales (
    store         INTEGER        NOT NULL,
    date          DATE           NOT NULL,
    weekly_sales  NUMERIC(12,2)  NOT NULL,
    holiday_flag  SMALLINT       NOT NULL,
    temperature   NUMERIC(6,2),
    fuel_price    NUMERIC(6,3),
    cpi           NUMERIC(12,7),
    unemployment  NUMERIC(6,3),
    PRIMARY KEY (store, date)  -- stops the same file from being imported twice
);

-- Keeps the credit visible in the database itself (DBeaver shows it as the table comment)
COMMENT ON TABLE kaggle.walmart_sales IS
    'Walmart Dataset by M Yasser H (Kaggle): https://www.kaggle.com/datasets/yasserh/walmart-dataset';
