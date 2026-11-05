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

## route_first_last.json

First / last departure times per route direction, rebuilt on Mondays (and
`workflow_dispatch`) by `.github/workflows/update-route-first-last.yml` with
`scripts/build_route_first_last.py`.

Key scheme:

| Key | Meaning |
| --- | --- |
| `KMB\|<route>\|<O\|I>` | KMB + LWB (LWB agency rows are stored as KMB) |
| `CTB\|<route>\|<O\|I>` | Citybus; joint `KMB+CTB` / `LWB+CTB` rows are duplicated under KMB **and** CTB |
| `NLB\|<route>\|<O\|I>` | New Lantao Bus (O/I from GTFS long_name; BusGo often keeps bound=`O` and splits directions via `serviceType`) |
| `GMB\|<routeId>\|<O\|I>` | Green minibus; `routeId` matches etagmb / `gmb_fares` (short names collide) |

Each value is `{"first":"HH:MM","last":"HH:MM","orig":"…","dest":"…"}`.

- First/last = earliest / latest origin departure on a **normal weekday** (Mon–Fri calendar),
  from GTFS `stop_times` (`stop_sequence=1`) plus `frequencies` windows.
- Bound O/I: match `route_long_name` `A - B` so A→B is O and B→A is I when possible;
  otherwise assign by sorted origin `stop_id`.
- KMB multi-`serviceType` is **not** in the key (no stable GTFS mapping); variants with the
  same company+route+bound are merged (earliest first / latest last).
- Hours ≥ 24 are stored modulo 24 (e.g. `25:10` → `01:10`).

### Manual overrides

`overrides/route_first_last_overrides.json` is merged after each GTFS build (keys replace
GTFS). Used for routes missing from TD GTFS:

| Key | first–last | Source |
| --- | --- | --- |
| `NLB\|B12\|O` | 06:00–00:30 | NLB news ([detail/649](https://www.nlb.com.hk/news/detail/649)); `/tc/route/117` unavailable when recorded |
| `NLB\|B12\|I` | 06:00–00:30 | NLB [route/118](https://www.nlb.com.hk/tc/route/118) timetable (皇崗口岸開出 06:00–00:30); both dirs confirmed in news |

URL: https://raw.githubusercontent.com/MM699/busgo-data/main/route_first_last.json

## Data source

Transport Department, HKSAR Government — open data on DATA.GOV.HK:

- GMB fares XML: `https://static.data.gov.hk/td/routes-fares-xml/FARE_GMB.xml`
- Public transport headway GTFS: `https://static.data.gov.hk/td/pt-headway-tc/gtfs.zip`
  ([dataset](https://data.gov.hk/tc-data/dataset/hk-td-tis_11-pt-headway-en))

Use is subject to the DATA.GOV.HK terms and conditions.
