-- sheet: Holiday vs Normal
-- Average weekly sales: holiday weeks compared with normal weeks.
-- Data: Walmart Dataset by M Yasser H (Kaggle), https://www.kaggle.com/datasets/yasserh/walmart-dataset
SELECT
    CASE WHEN holiday_flag = 1 THEN 'Holiday' ELSE 'Normal' END AS week_type,
    COUNT(*)                        AS week_count,
    ROUND(AVG(weekly_sales), 2)     AS avg_weekly_sales
FROM kaggle.walmart_sales
GROUP BY 1;
