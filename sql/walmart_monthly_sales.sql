-- sheet: Monthly Sales
-- Monthly sales per store.
-- Data: Walmart Dataset by M Yasser H (Kaggle), https://www.kaggle.com/datasets/yasserh/walmart-dataset
SELECT
    store,
    DATE_TRUNC('month', date)::DATE AS sales_month,
    ROUND(SUM(weekly_sales), 2)     AS total_sales
FROM kaggle.walmart_sales
GROUP BY store, DATE_TRUNC('month', date)
ORDER BY store, sales_month;
