# OpenFin monorepo — `kushagra-agarwal-a/fund-holdings-data`

Single repo for OpenFin: **published holdings** at repo root + **parser** in `pipeline/`.

```text
fund-holdings-data/
├── catalog/           ← OpenFin CDN
├── portfolios/        ← OpenFin CDN
├── meta.json
└── pipeline/          ← fetch, parse, sync scripts (this tree)
```

## Run parser (from `pipeline/`)

```bash
cd pipeline
npm ci
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
export GH_TOKEN="$HOLDINGS_GH_TOKEN"
npm run holdings:cloud -- --push
```

Sync writes to the **parent repo root** (not `.tmp/`).

## Mirror

`subscriptionmanager26-png/fund-disclosures` is a parser-only mirror (no holdings). After commits here, run `npm run parser:mirror-subscriptionmanager` from `pipeline/`.
