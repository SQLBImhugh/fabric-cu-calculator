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
notebooks and replayed verbatim through `executeQueries`.

| FUAM query | Rows | Cols | Values | % of the 1,000,000-value cap |
|---|--:|--:|--:|--:|
| Timepoints | 2,876 | 21 | 60,396 | 6.0% |
| ItemKind | 29 | 15 | 435 | 0.04% |
| ItemOperation | 158 | 17 | 2,686 | 0.3% |

Column parity for the Timepoints query — 21 returned, 21 expected, order identical to FUAM's
positional rename list:

| # | Returned by executeQueries | FUAM renames to |
|--:|---|---|
| 0 | `Capacities[Capacity Id]` | `CapacityId` |
| 1 | `Timepoints[Timepoint]` | `TimePoint` |
| 2 | `[B_P]` | `BackgroundPercentage` |
| … | … | … |
| 20 | `[Exp_BD_M]` | `ExpectedBurndownInMin` |

After applying FUAM's positional rename the frame is complete and usable: 2,876 rows covering
`00:00:00` → `23:59:30` (a full day of 30-second timepoints), **0 nulls**.

Service principal compatibility — `executeQueries` refuses service principals against
RLS-enabled models, so this was checked:

```
isEffectiveIdentityRequired      : False
isEffectiveIdentityRolesRequired : False
```

No RLS on the Capacity Metrics model, so both user and service principal identities work.

## Trade-offs, stated honestly

- **It swaps one prerequisite for another.** `executeQueries` needs the *Dataset Execute Queries
  REST API* tenant setting and Build permission on the model. The gain is that neither requires a
  capacity, so the Pro case is unblocked. It is not a pure removal of prerequisites.
- **Row and value caps.** `executeQueries` caps at 100,000 rows / 1,000,000 values / 15 MB per
  query, which XMLA does not. FUAM already issues one query per capacity per day, so the largest
  measured result used 6% of the value cap. The Timepoints query is naturally bounded at 2,880
  rows/day. `ItemKind` and `ItemOperation` scale with item count, so a very large tenant is the
  case to watch — a row-count guard that warns near the cap would be prudent.
- **Request rate.** 120 requests/minute per identity. FUAM loops capacities × days, so a tenant
  with many capacities and `metric_days_in_scope > 2` can hit it; the proposed helper backs off
  and retries rather than failing.
- **Fallback, not replacement.** Keeping XMLA first means no behaviour change for existing
  deployments; only environments where XMLA fails take the new path.

## What was not tested

- Running the modified notebooks end to end inside Fabric. The DAX and column contract were
  validated out-of-band; the notebook edit itself is unexercised.
- A genuinely Pro-hosted Capacity Metrics app. The evidence here is indirect but consistent: XMLA
  against this workspace was refused with *"does not have permission to call the Discover method"*
  while `executeQueries` against the same model succeeded.
- Query variants `v37` / `v40` / `v44` / `v47`, and `v56`/`v57` reported in #436. Only `v53` — the
  version installed here — was exercised. The mechanism is version-independent, but the column
  contract should be re-checked per variant.
- Very large tenants, per the row-cap note above.
