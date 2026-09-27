-- sheet: Top 10 Stores 2011
-- The 10 stores with the highest total sales in 2011.
-- Data: Walmart Dataset by M Yasser H (Kaggle), https://www.kaggle.com/datasets/yasserh/walmart-dataset
SELECT
    store,
    ROUND(SUM(weekly_sales), 2) AS total_sales_2011
FROM kaggle.walmart_sales
WHERE EXTRACT(YEAR FROM date) = 2011
GROUP BY store
ORDER BY total_sales_2011 DESC
LIMIT 10;
