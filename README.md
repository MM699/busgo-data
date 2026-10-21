# busgo-data

Data files used by the BusGo app.

## gmb_fares.json

Green minibus (GMB) boarding fares, rebuilt daily by GitHub Actions
(`.github/workflows/update-gmb-fares.yml`) with `scripts/build_gmb_fares.py`.

- `fares["<routeId>|<routeSeq>"]` is either a number (flat fare) or an array indexed by
  boarding stop sequence (`stop_seq` / TD `ON_SEQ`), where each value is the highest fare
  from that stop. `routeId` / `routeSeq` / `stop_seq` match the data.etagmb.gov.hk API.
- `td_generated` is the generation time of the source XML.

URL: https://raw.githubusercontent.com/MM699/busgo-data/main/gmb_fares.json

## Data source

Transport Department, HKSAR Government — open data on DATA.GOV.HK
(`https://static.data.gov.hk/td/routes-fares-xml/FARE_GMB.xml`).
Use is subject to the DATA.GOV.HK terms and conditions.
