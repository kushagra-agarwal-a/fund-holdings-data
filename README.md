# fund-holdings-data (OpenFin)

Public mutual-fund holdings CDN for [OpenFin](https://openfin.pocketedge.in).

| Path | Purpose |
|------|---------|
| `catalog/`, `portfolios/`, `meta.json` | Published data (API + jsDelivr) |
| `pipeline/` | Parser — fetch, parse, sync |

Work in `pipeline/`:

```bash
cd pipeline && npm ci && npm run holdings:cloud -- --push
```

Account: **kushagra-agarwal-a** only. No holdings copies elsewhere.
