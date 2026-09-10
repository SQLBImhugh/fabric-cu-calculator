# FUAM: read the Capacity Metrics app without XMLA

A proposal for `microsoft/fabric-toolbox` → `monitoring/fabric-unified-admin-monitoring`.

## Problem

FUAM's three capacity-metrics notebooks read the Capacity Metrics app with
`fabric.evaluate_dax`. Microsoft documents that call as XMLA-based:

> "Data is retrieved using XMLA and therefore requires at least XMLA read-only to be enabled"
> — [read-write-power-bi-python](https://learn.microsoft.com/fabric/data-science/read-write-power-bi-python)

(By contrast the same page notes `evaluate_measure` "is **not** retrieved using XMLA".)

XMLA endpoints exist only on P/F capacities, so `How_to_deploy_FUAM.md` requires:

> "Fabric Capacity Metrics app (workspace) **with attached P or F-capacity** with **enabled XMLA endpoint** (at least 'Read')"

That excludes a Capacity Metrics app installed into a **Pro workspace**, which is the default
outcome for anyone who installs the app and does not also assign its workspace to a capacity.
It also breaks when a capacity admin leaves the XMLA endpoint off.

Related upstream issues: #372 (where is the XMLA setting), #418 and #436 (the
"data structure is not compatible **or connection to capacity metrics is not possible**" error),
#417 (SemPy execute DAX not working on new capacities).

## Proposal

Keep XMLA as the primary path and fall back to the Power BI REST `executeQueries` endpoint,
which runs the same DAX with no XMLA endpoint and no capacity requirement on the hosting
workspace. See `evaluate_dax_compat.py`.

The change is small because **FUAM renames the returned columns positionally**:

```python
capacity_df = fabric.evaluate_dax(...)
capacity_df.columns = ['CapacityId', 'TimePoint', ...]   # positional
```

so a replacement only has to preserve column **order and count**, not names.

Call sites: 6 `fabric.evaluate_dax` calls in each of
`01_Transfer_CapacityMetricData_Timepoints_Unit`,
`02_Transfer_CapacityMetricData_ItemKind_Unit` and
`03_Transfer_CapacityMetricData_ItemOperation_Unit`.

## Evidence

Measured 2026-09-10 against a live Capacity Metrics app (**v53**), capacity `Trial-Central64`
(FTL64), date 2026-09-08. FUAM's own `dax_query_v53` strings were extracted from the deployed
notebooks and replayed verbatim.

### Query results over `executeQueries`

| FUAM query | Rows | Cols | Values | % of the 1,000,000-value cap |
|---|--:|--:|--:|--:|
| Timepoints | 2,876 | 21 | 60,396 | 6.0% |
| ItemKind | 29 | 15 | 435 | 0.04% |
| ItemOperation | 158 | 17 | 2,686 | 0.3% |

### End-to-end run inside Fabric

The helper in this folder was pasted verbatim into a notebook in the FUAM workspace and run against
the live Capacity Metrics app, alongside the existing XMLA call, then pushed through FUAM's real
downstream path — positional rename, `spark.createDataFrame`, Delta append.

| Check | Result |
|---|---|
| Rows, XMLA vs REST | 2,876 vs 2,876 |
| Column count and order | 21 vs 21, identical order |
| Per-cell diff across all 21 columns | **0 mismatches** |
| `SUM(TotalCUs)` | 71,426.739 vs 71,426.739 |
| Spark schema equality | **identical, empty diff** |
| Delta append of REST rows onto an XMLA-written table | **succeeded**, 5,752 rows = 2,876 + 2,876 |
| `TimePoint` column type in Delta | `timestamp` (not string) |
| Truncation guard | fired correctly on an over-cap query |
| Query duration | XMLA 20.4 s, REST **7.4 s** |

Column parity for the Timepoints query, matching FUAM's positional rename list:

| # | Returned | FUAM renames to |
|--:|---|---|
| 0 | `Capacities[Capacity Id]` | `CapacityId` |
| 1 | `Timepoints[Timepoint]` | `TimePoint` |
| 2 | `[B_P]` | `BackgroundPercentage` |
| … | … | … |
| 20 | `[Exp_BD_M]` | `ExpectedBurndownInMin` |

Service principal compatibility — `executeQueries` refuses service principals against RLS-enabled
models, so this was checked:

```
isEffectiveIdentityRequired      : False
isEffectiveIdentityRolesRequired : False
```

No RLS on the Capacity Metrics model, so both user and service principal identities work.

## Two findings that shaped the implementation

### 1. The REST endpoint returns dates as strings, not datetimes

Only visible in a real run. XMLA returns `Timepoints[Timepoint]` as `datetime64[ns]`; the REST
endpoint returns it as an object/string column. FUAM feeds the frame straight into
`spark.createDataFrame` and appends to Delta, so an uncorrected fallback would write `TimePoint` as
a **string** and conflict with the existing `timestamp` column.

`_coerce_datetime_columns` restores the dtype. It converts an object column only when *every*
non-null value parses as a date, which leaves the GUID columns (`CapacityId`, `ItemId`,
`WorkspaceId`) untouched. After coercion the two frames are byte-identical and the Delta schemas
match exactly.

### 2. `executeQueries` truncates silently — it does not error

Measured directly:

| Requested | Returned | HTTP |
|---|---|---|
| 150,000 rows × 1 col | **exactly 100,000 rows** | 200 |
| 95,000 rows × 12 cols = 1,140,000 values | **83,333 rows** (999,996 values) | 200 |
| 95,000 rows × 10 cols = 950,000 values | 95,000 rows | 200 |

No warning, no error, no flag in the payload. XMLA has no such cap, so a naive fallback could
silently write a partial day of capacity metrics and report success. `_evaluate_dax_rest` therefore
raises when a result lands exactly on either limit.

## Are the caps a deal breaker? No — with the guard

| Query | Scales with | Ceiling | Observed max | Headroom |
|---|---|--:|--:|--:|
| Timepoints | nothing — 2,880 timepoints/day is fixed | 47,619 rows @ 21 cols | 2,876 | **structurally safe** |
| ItemKind | item kinds (~20–30) | 66,666 rows @ 15 cols | 29 | ~2,300× |
| ItemOperation | item × operation count | **58,823 rows @ 17 cols** | 1,948 | **30×** |

The value cap binds before the row cap at these widths. The Timepoints query can never approach the
limit no matter how large the tenant, because a day contains exactly 2,880 thirty-second timepoints.

`ItemOperation` is the only query that grows with tenant size. Tripping it needs roughly **58,800
distinct item × operation combinations on one capacity in one day** — perhaps 6,000–12,000 active
items on a single capacity. Large, but reachable on a big F2048 estate. Since FUAM already issues one
query per capacity per day, the natural mitigation if it is ever hit is to split further, for example
by item kind; the guard turns a silent data-loss bug into a clear error that says so.

## Trade-offs, stated honestly

- **It swaps one prerequisite for another.** `executeQueries` needs the *Dataset Execute Queries
  REST API* tenant setting and Build permission on the model. The gain is that neither requires a
  capacity, so the Pro case is unblocked. It is not a pure removal of prerequisites.
- **Request rate.** 120 requests/minute per identity. FUAM loops capacities × days, so a tenant with
  many capacities and `metric_days_in_scope > 2` can hit it; the helper backs off and retries.
- **Fallback, not replacement.** XMLA stays the primary path, so existing deployments are unaffected;
  only environments where XMLA fails take the new path.

## What was not tested

- A genuinely Pro-hosted Capacity Metrics app. The evidence is indirect but consistent: XMLA against
  this workspace was refused with *"does not have permission to call the Discover method"* while
  `executeQueries` against the same model succeeded.
- Query variants `v37` / `v40` / `v44` / `v47`, and `v56`/`v57` reported in #436. Only `v53` — the
  version installed here — was exercised. The mechanism is version-independent, but the column
  contract should be re-checked per variant.
- The `ItemKind` and `ItemOperation` notebooks were validated at the query and column level but not
  through a full Delta append; only `Timepoints` had the complete end-to-end run.
- Very large tenants, per the headroom table above.
