# fund-holdings-data

Public AMFI mutual-fund holdings (zero paid cloud).

## Dedup model

- **Portfolios** — one JSON per unique book: `portfolios/latest/{portfolio_id}.json`
- **Catalog** — all AMFI schemes; holdings rows link via `portfolio_id`

Sibling share-classes share one portfolio object.

## CDN

```
https://cdn.jsdelivr.net/gh/subscriptionmanager26-png/fund-holdings-data@main/catalog/amfi-lookup.json
https://cdn.jsdelivr.net/gh/subscriptionmanager26-png/fund-holdings-data@main/portfolios/latest/{portfolio_id}.json
```

Resolve: catalog[amfi].portfolio_id → portfolios/latest/{id}.json

Synced via `scripts/sync-holdings-to-github.mjs`.
