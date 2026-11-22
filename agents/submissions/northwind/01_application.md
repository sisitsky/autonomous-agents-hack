# Coverage application: Northwind Goods (FICTIONAL demo company)

**Company:** Northwind Goods, Inc., an online home-goods retailer based in Portland, Oregon.
**System:** ShopBot, the customer-service chatbot on northwind.example and in our mobile app.

## What ShopBot does
ShopBot answers customer questions about orders, returns, refunds, shipping, and
product warranties. It can look up an order by order number and tell the customer
its status. It does not issue refunds itself: it tells customers whether an item
qualifies and opens a return request, which our support team processes.

## Who uses it
Retail customers. About 70% of orders ship within the US (largest states:
California, Oregon, Texas, Washington) and the rest to Canada. We do not sell to
the EU or UK.

## Volume
Roughly 2 million chatbot responses per year (about 400,000 conversations,
five responses each on average).

## Coverage requested
$1,000,000 per claim, $2,000,000 aggregate, $10,000 deductible.

## Testing
You may red-team ShopBot at our staging endpoint: http://127.0.0.1:8765/chat
