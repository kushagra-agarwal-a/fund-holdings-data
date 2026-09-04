# Reference AMC Python fetchers

These are **reference** AMC disclosure fetchers (YYYY-MM repeatable downloads) from a prior toolkit. Use them when porting or debugging Node adapters.

## Policy

- **AMC websites/APIs only.** Do **not** use Advisorkhoj or other third-party aggregators as primary sources.
- **Node project adapters** under this repo are the primary runtime; Python here is reference for porting.

## Exclusions / notes

| Item | Status |
|------|--------|
| `fetch_edelweiss.py` | **AMC-direct** — `api.edelweissmf.com` statutory menu + AES decrypt via `curl_cffi` (no Playwright). |
| Kotak (`fetch_kotak.py`, `fetch_kotak_playwright.py`, `kotak_api_probe.py`) | Prefer `--use-api` / Playwright against **kotakmf.com**. Ignore the Advisorkhoj `PAGE_URL` default in `fetch_kotak.py`. |

## AMC → official domain (from script headers)

| AMC / script | Official domain |
|--------------|-----------------|
| 360 ONE (`fetch_360_one.py` monthly; `fetch_360one.py` FN) | www.360.one/asset/mutual-funds/downloads/ (not archive.iiflmf.com) |
| Abakkus (`fetch_abakkus.py`) | www.abakkusmf.com |
| Aditya Birla SL (`fetch_absl.py`) | mutualfund.adityabirlacapital.com |
| Angel One (`fetch_angel_one.py`) | www.angelonemf.com |
| Axis (`fetch_axis.py`) | www.axismf.com |
| Bajaj (`fetch_bajaj.py`) | www.bajajamc.com |
| Bandhan (`fetch_bandhan.py`) | bandhanmutual.com / cmsnew.bandhanmutual.com |
| Baroda BNP (`fetch_baroda_bnp.py`) | www.barodabnpparibasmf.in |
| Bank of India (`fetch_boi.py`) | www.boimf.in |
| Canara Robeco (`fetch_canara_robeco.py`) | www.canararobeco.com |
| Capitalmind (`fetch_capitalmind.py`) | capitalmindmf.com |
| Choice (`fetch_choice.py`) | choicemf.com / doc.choicemf.com |
| DSP (`fetch_dsp.py`) | www.dspim.com |
| Franklin (`fetch_franklin.py`) | www.franklintempletonindia.com |
| Groww (`fetch_groww.py`) | growwmf.in |
| HDFC (`fetch_hdfc.py`) | www.hdfcfund.com / cms.hdfcfund.com |
| Helios (`fetch_helios.py`) | www.heliosmf.in |
| HSBC (`fetch_hsbc.py`) | www.assetmanagement.hsbc.co.in |
| ICICI Prudential (`fetch_icici.py`) | www.icicipruamc.com / apps.digital.icicipruamc.com |
| IL&FS (`fetch_ilfs.py`) | www.ilfsinfrafund.com |
| Invesco (`fetch_invesco.py`) | invescomutualfund.com |
| ITI (`fetch_iti.py`) | itiamc.com / www.itiamc.com |
| Jio BlackRock (`fetch_jio_blackrock.py`) | www.jioblackrockamc.com |
| JM Financial (`fetch_jm_financial.py`) | www.jmfinancialmf.com / jmmfapi.jmfinancialmf.com |
| Kotak (`fetch_kotak.py`, Playwright) | **www.kotakmf.com** (AMC API; avoid Advisorkhoj default) |
| LIC (`fetch_lic.py`) | www.licmf.com |
| Mahindra Manulife (`fetch_mahindra_manulife.py`) | www.mahindramanulife.com |
| Mirae (`fetch_mirae.py`) | www.miraeassetmf.co.in |
| Motilal (`fetch_motilal.py`) | www.motilaloswalmf.com |
| Navi (`fetch_navi.py`) | navi.com |
| Nippon (`fetch_nippon.py`) | mf.nipponindiaim.com |
| NJ (`fetch_nj.py`) | downloads.njmutualfund.com |
| Old Bridge (`fetch_oldbridge.py`) | www.oldbridgemf.com |
| PGIM (`fetch_pgim.py`) | www.pgimindia.com |
| PPFAS (`fetch_ppfas.py`) | amc.ppfas.com |
| Quantum (`fetch_quantum.py`) | www.quantumamc.com |
| Samco (`fetch_samco.py`) | www.samcomf.com |
| SBI (`fetch_sbi.py`) | www.sbimf.com |
| Shriram (`fetch_shriram.py`) | www.shriramamc.in / cdn.shriramamc.in |
| Sundaram (`fetch_sundaram.py`) | www.sundarammutual.com |
| Tata (`fetch_tata.py`) | www.tatamutualfund.com |
| Taurus (`fetch_taurus.py`) | www.taurusmutualfund.com |
| Trust (`fetch_trust.py`) | www.trustmf.com |
| Union (`fetch_union.py`) | www.unionmf.com |
| UTI (`fetch_uti.py`) | www.utimf.com |

Orchestrator: `fetch_all_amcs.py` (still references edelweiss in its list — that script is not present here).

Helper / Kotak tooling also copied: `extract_*.py`, `kotak_api_probe.py`, `seed_kotak_placeholders.py`, requirements `*.txt`, and `fixtures/`.
