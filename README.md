# fund-holdings-data

Public AMFI mutual-fund holdings snapshots (zero paid cloud).

## Layout

- `holdings/latest/{amfi}.json` — shaped holdings payload
- `catalog/amfi-lookup.json` — scheme index (`github_key` / `github_url`)
- `holdings/asof/{yyyy-mm}/{amfi}.json` — optional dated snapshots
- `meta.json` — last sync summary

## CDN (jsDelivr)

```
https://cdn.jsdelivr.net/gh/kushagra-agarwal-a/fund-holdings-data@main/holdings/latest/{amfi}.json
https://cdn.jsdelivr.net/gh/kushagra-agarwal-a/fund-holdings-data@main/catalog/amfi-lookup.json
```

Synced from the fund-disclosures pipeline via `scripts/sync-holdings-to-github.mjs`.
