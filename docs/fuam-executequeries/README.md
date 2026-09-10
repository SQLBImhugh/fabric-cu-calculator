# FUAM and the Capacity Metrics app: the XMLA prerequisite

Investigation notes for `microsoft/fabric-toolbox` → `monitoring/fabric-unified-admin-monitoring`.

## Verdict

Two separate conclusions, both now backed by direct evidence.

**1. FUAM works, unmodified, against a Capacity Metrics app in a Pro workspace.** Proven end to end
on 2026-09-10 against a freshly installed app in a workspace the admin API reports as
`isOnDedicatedCapacity: False` with an empty `capacityId`. FUAM's own `Load_Capacity_Metrics_E2E`
pipeline ran to completion and merged new rows into all three gold tables. No code change is needed —
the deployment prerequisite is simply stricter than the code requires.

**2. The proposed transport swap should not be merged.** It was built and fully validated before
conclusion 1 was established. It is unnecessary, and it would make FUAM less safe: `executeQueries`
truncates silently at 100,000 rows where XMLA has no cap.

The code in this folder is kept as the evidence trail and as a warning about `executeQueries`.

## Proof that FUAM works on a Pro-hosted Metrics App

The workspace, confirmed shared before anything else was run:

```
GET /v1.0/myorg/admin/groups?$filter=id eq '2bccc391-...'
  name                  : Microsoft Fabric Capacity Metrics
  isOnDedicatedCapacity : False
  capacityId            : (empty)
```

FUAM's own code was then exercised against it, unmodified, in three stages.

**Stage 1 — version detection.** FUAM identifies the app version by trial-running probe DAX through
`fabric.evaluate_dax`. This matters because it is the single point where a blocked XMLA endpoint would
surface, and it fails with the exact message reported in #418 and #436:

> ERROR: Capacity Metrics data structure is not compatible or connection to capacity metrics is not possible.

| Probe | Result against the Pro-hosted app |
|---|---|
| v53 | **succeeded**, 1 row |
| v47 | succeeded, 0 rows |
| v40 | succeeded, 1 row |
| version FUAM selects | **v53** |

**Stage 2 — the real data query, both transports.** FUAM's v53 query shape (`MPARAMETER
'CapacitiesList'` plus two `TREATAS` filters) for one capacity and one day, the identical string sent
over each transport from the same notebook:

| Transport | Rows | Time |
|---|--:|--:|
| `fabric.evaluate_dax` (XMLA) | **2,880** | 14.8 s |
| `POST .../executeQueries` (REST) | **2,880** | 4.3 s |

XMLA is not blocked, and it is not degraded — it returns the full day of 2,880 thirty-second
timepoints, matching REST exactly.

**Stage 3 — the actual pipeline, end to end.** `Load_Capacity_Metrics_E2E` was run with the Pro-hosted
app as its target. Delta history on the three gold tables in `FUAM_Lakehouse`:

| Gold table | Version | Operation | Rows after |
|---|--:|---|--:|
| `capacity_metrics_by_timepoint` | 13 | MERGE | 81,656 |
| `capacity_metrics_by_item_kind_by_day` | 13 | MERGE | 1,457 |
| `capacity_metrics_by_item_by_operation_by_day` | 13 | MERGE | 8,472 |

A trap worth recording: the three `*_silver` staging tables read **0 rows** afterwards, which looks
like total failure. It is not. The notebooks end with `DELETE FROM <silver_table>` once the merge to
gold succeeds, so an empty silver table is the normal post-run state. Checking silver alone would have
produced exactly the wrong conclusion — and because these notebooks call
`notebookutils.notebook.exit()` on failure, the job status reads `Completed` either way. Gold Delta
history is the only reliable check.

## Scope of the claim

- **FUAM's own workspace still needs a Fabric capacity.** Its notebooks, pipelines, Lakehouse and
  Direct Lake models are all capacity workloads. Only the *Metrics App's* workspace is in question.
- **XMLA read on shared capacity is undocumented.** The Power BI service description feature table
  still lists *"XMLA endpoint read/write connectivity — Power BI Pro: No"*. It works here and it
  worked repeatedly, but Microsoft has not documented it, so it carries no support guarantee. That
  ambiguity is the substance of the documentation question, not a reason to change code.
- Tested with Capacity Metrics app v53 and a capacity-admin identity in one tenant.

## The original premise, and why it looked right

FUAM's three capacity-metrics notebooks read the Capacity Metrics app with
`fabric.evaluate_dax`. Microsoft documents that call as XMLA-based:

> "Data is retrieved using XMLA and therefore requires at least XMLA read-only to be enabled"
> — [read-write-power-bi-python](https://learn.microsoft.com/fabric/data-science/read-write-power-bi-python)

(By contrast the same page notes `evaluate_measure` "is **not** retrieved using XMLA".)

XMLA endpoints are documented as a capacity feature — the Power BI service description's feature
table lists *"XMLA endpoint read/write connectivity — Power BI Pro: **No**"*
([service description](https://learn.microsoft.com/office365/servicedescriptions/power-bi-service-description#feature-availability))
— so `How_to_deploy_FUAM.md` requires:

> "Fabric Capacity Metrics app (workspace) **with attached P or F-capacity** with **enabled XMLA endpoint** (at least 'Read')"

That reads as excluding a Capacity Metrics app installed into a **Pro workspace**. Related upstream
issues: #372 (where is the XMLA setting), #418 and #436 (the "data structure is not compatible **or
connection to capacity metrics is not possible**" error), #417 (SemPy execute DAX not working on new
capacities).

## The earlier, weaker test

Before the fresh Pro-hosted Metrics App was available, the same question was approached indirectly
with a self-authored model. That test is superseded by the one above but is retained because it
isolates *which* XMLA operation is gated.

A workspace was created with no capacity assigned and confirmed shared, then an import model holding
**150,000 rows** was deployed into it and queried two ways. 150,000 is the discriminator: the REST
endpoint hard-caps at 100,000 rows, XMLA has no such cap.

| Call, same model and same DAX | Rows returned | Time |
|---|--:|--:|
| `fabric.evaluate_dax(...)` | **150,000** | 10.7 s |
| `POST .../executeQueries` | **100,000** (truncated, HTTP 200) | 1.2 s |

| Call | Result |
|---|---|
| `evaluate_dax` on Pro model | ok, 2 rows |
| `evaluate_dax_compat` on Pro model | ok, 2 rows |
| `fabric.list_datasets` on Pro workspace | **fails**: *"user does not have permission to call the Discover method"* |

So the boundary is not XMLA-versus-no-XMLA. It is **Execute versus Discover**: running a DAX query
worked, enumerating metadata did not. FUAM passes workspace and dataset **GUIDs**, so it never needs
Discover — which is consistent with it working end to end above.

## What the transport swap was, and its validation

Kept for the record. Keep XMLA as the primary path and fall back to REST `executeQueries`; see
`evaluate_dax_compat.py`. The change is small because **FUAM renames the returned columns
positionally**:

```python
capacity_df = fabric.evaluate_dax(...)
capacity_df.columns = ['CapacityId', 'TimePoint', ...]   # positional
```

so a replacement only has to preserve column **order and count**, not names. Call sites: 6
`fabric.evaluate_dax` calls in each of `01_Transfer_CapacityMetricData_Timepoints_Unit`,
`02_Transfer_CapacityMetricData_ItemKind_Unit` and
`03_Transfer_CapacityMetricData_ItemOperation_Unit`. Patched notebooks are in
`patched-notebooks/`, regenerable from current upstream with `build_patched_notebooks.py`.

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
downstream path for **all three notebooks** — positional rename, `spark.createDataFrame`, Delta append
onto a table already written by the XMLA path.

| Check | Timepoints | ItemKind | ItemOperation |
|---|---|---|---|
| Rows, XMLA vs REST | 2,876 vs 2,876 | 29 vs 29 | 158 vs 158 |
| Column count and order | 21, identical | 15, identical | 17, identical |
| Per-cell diff, every column | **0 mismatches** | **0 mismatches** | **0 mismatches** |
| Column sums equal | yes | yes | yes (`TotalCUs` 71,856.862) |
| Spark schema equality | **identical** | **identical** | **identical** |
| Delta append onto an XMLA-written table | **succeeded** | **succeeded** | **succeeded** |

`TotalCUs` of 71,856.862 for the ItemOperation query matches the tenant total measured independently
from the FUAM Lakehouse, so the REST path is not just self-consistent — it agrees with the number
FUAM already stores.

The Timepoints query was also timed: XMLA 20.4 s, REST **7.4 s**. Truncation guard fired correctly on
a deliberately over-cap query.

The **patched notebooks themselves** were then executed in Fabric to confirm the inserted helper cell
runs as written: `evaluate_dax_compat`, `_evaluate_dax_rest` and `_coerce_datetime_columns` were all
defined, the real Metrics App returned rows through both the compat wrapper and the REST-only path,
and the notebook's template default parameters (a placeholder workspace GUID) failed cleanly through
the fallback with a 401 rather than hanging or silently returning empty.

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

### The other Metrics App versions

FUAM branches on the app version (v37 … v53) to pick a DAX string, but each notebook applies exactly
**one** positional rename, *after* the branch:

```python
df.columns = ["CapacityId", "TimePoint", "BackgroundPercentage", ...]
```

So every version variant must already return the same column count in the same order — that is FUAM's
own invariant, not one this change introduces. Because the transport swap preserves column count and
order (verified above), it preserves that invariant for every variant, and the source column names
the two transports label things with are never read. Only v53 was executed live.


## Two findings that shaped the implementation

### 1. The REST endpoint returns dates as strings, not datetimes

Only visible in a real run. XMLA returns `Timepoints[Timepoint]` as `datetime64[ns]`; the REST
endpoint returns it as an object/string column. FUAM feeds the frame straight into
`spark.createDataFrame` and appends to Delta, so an uncorrected fallback would write `TimePoint` as
a **string** and conflict with the existing `timestamp` column.

`_coerce_datetime_columns` restores the dtype — but it has to be **strict**, which a first attempt was
not. `DateKey` arrives as the string `"20260908"`, and `pd.to_datetime` happily parses that as a date.
Converting it produced a real failure on the `ItemKind` and `ItemOperation` notebooks:

```
DELTA_FAILED_TO_MERGE_FIELDS: Failed to merge fields 'DateKey' and 'DateKey'
```

XMLA returns `DateKey` as a string, so the fallback has to as well. The rule is therefore: convert an
object column only when every non-null value both parses as a date **and** contains a `-`, `/` or `:`
separator. That leaves `DateKey` alone and leaves the GUID columns (`CapacityId`, `ItemId`,
`WorkspaceId`) untouched. With the strict rule all three notebooks produce a Spark schema identical to
the XMLA path and append to Delta cleanly.

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

## Cap headroom for FUAM's three queries

| Query | Scales with | Ceiling | Observed max | Headroom |
|---|---|--:|--:|--:|
| Timepoints | nothing — 2,880 timepoints/day is fixed | 47,619 rows @ 21 cols | 2,876 | **structurally safe** |
| ItemKind | item kinds (~20–30) | 66,666 rows @ 15 cols | 29 | ~2,300× |
| ItemOperation | item × operation count | **58,823 rows @ 17 cols** | 1,948 | **30×** |

The value cap binds before the row cap at these widths. The Timepoints query can never approach the
limit no matter how large the tenant, because a day contains exactly 2,880 thirty-second timepoints.

`ItemOperation` is the only query that grows with tenant size. Tripping it needs roughly **58,800
distinct item × operation combinations on one capacity in one day** — perhaps 6,000–12,000 active
items on a single capacity. Large, but reachable on a big estate.

So the caps are survivable for a small or mid-size tenant, and the guard converts the silent-data-loss
case into a clear error. That is still worse than the transport it would replace: XMLA has no cap and
needs no guard. The headroom analysis is why the swap looked acceptable, not a reason to make it.

## Trade-offs, stated honestly

- **It swaps one prerequisite for another.** `executeQueries` needs the *Semantic Model Execute
  Queries REST API* tenant setting and Build permission on the model. It is not a pure removal of
  prerequisites.
- **Request rate.** 120 requests/minute per identity. FUAM loops capacities × days, so a tenant with
  many capacities and `metric_days_in_scope > 2` can hit it; the helper backs off and retries.
- **It trades an uncapped transport for a capped one.** This is the decisive one. XMLA has no row
  cap; `executeQueries` truncates at 100,000 rows / 1,000,000 values. Even with the guard, the best
  case is that a large tenant gets a hard error where XMLA would simply have worked.

## What was not tested

- A genuinely Pro-hosted *Capacity Metrics app*. A Pro-hosted **semantic model** was tested and XMLA
  read worked against it, but the Metrics App's own model was not relocated to Pro — doing so would
  have broken the live FUAM schedule this analysis depends on.
- Query variants `v37` / `v40` / `v44` / `v47`, and `v56`/`v57` reported in #436. Only `v53` — the
  version installed here — was executed. The argument that the others are covered is structural, not
  empirical: each notebook applies a single positional rename after the version branch, so all
  variants must already share a column count and order, and the transport swap preserves both.
- The patched notebooks running against **real** parameters, which would append to
  `FUAM_Staging_Lakehouse.*_silver`. The helper cell was executed inside the patched notebooks and the
  three queries were validated end-to-end through Delta in a scratch table, but a full production run
  was not attempted.
- Whether XMLA-on-shared-capacity behaves the same in other tenants. It is undocumented, so it may
  not.

## What is actually worth raising upstream

Not a transport change. A **documentation change**, now supported by an end-to-end run rather than
inference:

- Microsoft's own install guidance for the Metrics App says to put it on Pro —
  *"To avoid throttling due to capacity overutilization, install the app in a workspace with a Pro
  license"* ([metrics-app-install](https://learn.microsoft.com/fabric/enterprise/metrics-app-install)) —
  and lists the access requirement as *"A Power BI license (Pro, Premium Per User, or a Power BI
  individual trial)"*.
- FUAM's `How_to_deploy_FUAM.md` requires the opposite: the Metrics App workspace must have an
  *"attached P or F-capacity with enabled XMLA endpoint (at least 'Read')"*, and states that
  *"PPU, Pro 'shared' workspaces are not supported"*.

A user who follows Microsoft's recommendation cannot satisfy FUAM's stated prerequisite — yet FUAM
runs against exactly that configuration, as demonstrated above. The prerequisite appears to be
stricter than the code requires.

The honest caveat belongs in the same breath: XMLA read on shared capacity is undocumented, so
"it works" and "it is supported" are not the same claim. That is precisely why the wording is worth
settling by someone who can say which it is.
