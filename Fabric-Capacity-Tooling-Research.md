# Fabric Capacity Metrics — analysis and troubleshooting tooling

Research date: 2026-09-01. Four parallel research agents: official guidance, data extraction paths,
OSS/GitHub tooling, MCP/agent integration. Every claim below carries a source URL.

## Conclusion first

There is **no public REST API that returns CU utilization data**. Confirmed independently by two agents
against both the Fabric REST API (`/v1/capacities`) and Azure ARM (`Microsoft.Fabric/capacities`) — both
return SKU, state, region and admin members only. Azure Monitor publishes no metrics namespace for
`Microsoft.Fabric/capacities`.

Two paths reach real CU and throttling data:

| | Supported | History | Granularity |
|---|---|---|---|
| **Real-Time Hub Capacity Overview Events** | Yes, GA | None (no backfill — start collecting before you need it) | 30 s |
| **DAX against the Capacity Metrics App semantic model** | **No** (support disclaimer) | 14 days | Per operation / per timepoint |

Three of the four agents converged on the same recommended architecture:
**Capacity Overview Events → Eventstream → Eventhouse → KQL**, with DAX-against-the-Metrics-App as the
"works today, unsupported" fallback.

## 1. The support disclaimer, precisely

`learn.microsoft.com/fabric/enterprise/metrics-app` states:

> "The semantic model used by the Microsoft Fabric Capacity Metrics application is only supported for use
> by the reports provided in the application. Any consumption from, usage of, or modification of the
> semantic model isn't supported."

This is a **support disclaimer, not a technical block**. XMLA and the Power BI `executeQueries` REST API
will not refuse the call. The real risk is schema drift: FUAM's changelog pins to Metrics App versions
(v44, v53) and its README warns that "elements of that App could change without notice."

## 2. Extraction paths

| Path | CU data? | Throttling? | Status | Notes |
|---|---|---|---|---|
| XMLA → Metrics App model | Yes | Yes | Unsupported | Needs XMLA Read on the capacity |
| Power BI REST `executeQueries` | Yes | Yes | Unsupported | 100k rows / 1M values / 15 MB / 120 queries per min |
| `sempy.evaluate_dax()` | Yes | Yes | Unsupported | `read_table`/`evaluate_dax` use XMLA; `evaluate_measure` does not |
| ARM `Microsoft.Fabric/capacities` | No | No | GA | Config only; suspend/resume |
| Power BI `/v1.0/myorg/capacities` | No | No | GA | Config only |
| Workspace Monitoring (Eventhouse) | Proxy only (`CpuTimeMs`) | No | Preview | 30-day retention; per-workspace, no capacity aggregates |
| Azure Monitor `PowerBIDatasetsWorkspace` | Proxy only | No | GA | Mutually exclusive with Workspace Monitoring |
| **Real-Time Hub Capacity Events** | **Yes** | **Yes** | **GA** | 30 s windows, no backfill |
| Manual UI export | Yes | Yes | GA | 150k rows CSV / 30k XLSX, **and sampling may occur** |

`CpuTimeMs` is engine CPU, not smoothed CU — it does not account for bursting or carry-forward.

### Capacity Overview Events schema
`learn.microsoft.com/fabric/real-time-hub/explore-fabric-capacity-overview-events`

- `Microsoft.Fabric.Capacity.Summary` — every 30 s
- `Microsoft.Fabric.Capacity.State` — on state change only

Key fields: `capacityUnitMs`, `baseCapacityUnits`, `interactiveDelayThresholdPercentage`,
`interactiveRejectionThresholdPercentage`, `backgroundRejectionThresholdPercentage`,
`overageAdd/Burndown/TotalCapacityUnitMs`, `utilizationInteractive`, `utilizationBackground`,
`capacityUnitUtilizationBreakdown` (per workload: AS, SparkCore, Kusto, DMS, DI, AI …).
State events carry `capacityState` and `stateChangeReason`.

Utilization percent = `capacityUnitMs / (baseCapacityUnits * 1000 * 30) * 100`.

Gotchas: best-effort delivery, so dedupe with `summarize take_any(*) by windowStartTime, windowEndTime,
capacityId`; paused capacities flush all smoothed CU on resume, producing false spikes.

One-click deploy: `jumpstart.fabric.microsoft.com/catalog/fpm-capacity-events/`

## 2b. FUAM's own CU footprint — measured

Measured 2026-09-01 against the live `EmbeddedMon-Accelerator` deployment
(tenant `MngEnvMCAP777813`, workspace `5cffbecd-9608-494b-984e-46bf4f774e12`) by querying FUAM's own
`capacity_metrics_by_item_by_operation_by_day` table over `executeQueries`. FUAM measures itself.

Extract window 2026-07-02 → 07-15; the FUAM workspace shows activity on **5 days**:

| Date | CU(s) | Operations | Note |
|---|--:|--:|---|
| 2026-07-09 | 15,111.9 | 10,508 | initial deployment |
| 2026-07-10 | 23,116.2 | 16,357 | peak — repair session |
| 2026-07-11 | 6,873.4 | 1,981 | post-repair |
| 2026-07-14 | 5,731.9 | 1,158 | quietest full day |
| 2026-07-15 | 10,289.0 | 3,403 | CU analysis queries |
| **Total** | **61,122.4** | **33,407** | |

Where it goes (whole window):

| Item kind | Operation | CU(s) | Share |
|---|---|--:|--:|
| Lakehouse | OneLake Other Operations Via Redirect | 21,481.0 | 35.1% |
| SynapseNotebook | Notebook Pipeline Run | 19,644.4 | 32.1% |
| Pipeline | DataMovement | 5,400.0 | 8.8% |
| Pipeline | ActivityRun | 4,757.8 | 7.8% |
| SynapseNotebook | Notebook Scheduled Run | 3,377.0 | 5.5% |
| Dataset | Query | 1,192.9 | 2.0% |
| Lakehouse | OneLake Write via Redirect | 1,016.2 | 1.7% |

Against a daily budget:

| SKU | Peak day (23,116 CU(s)) | Quiet day (5,732 CU(s)) |
|---|--:|--:|
| **F2** (172,800 CU(s)/day) | **13.4%** | **3.3%** |
| **F64** (5,529,600 CU(s)/day) | 0.42% | 0.10% |

For comparison, Workspace Monitoring's always-on core measured ~2,110 CU(s)/day ≈ **1.2% of an F2**
(`D:\Copilot\PBIEmbeddedMonitoring\docs\architecture.md`). On these July figures FUAM looked like
3–11× Workspace Monitoring depending on the day; the measured steady-state multiple is **6×**.

**Read these as deployment-period numbers, not steady state.** July 9–10 include the initial full
extraction and a repair session where the pipeline was re-run repeatedly by hand; the deployment was
never put on a schedule and has been dormant since (last model refresh 2026-07-14). FUAM was 0.8% of
all tenant CU in the window. **A prediction made here — that a scheduled FUAM would sit near
~5,700–6,900 CU(s)/day, 3.3–4.0% of an F2 — turned out to be wrong by roughly 2×. See the steady-state
section below.**

### Steady state — measured over six scheduled days (2026-09-09)

`Load_FUAM_Data_E2E` ran **daily at 05:00 UTC, seven consecutive days, all Completed**, 21–28 minutes
each. Six days carry exactly one scheduled run and no other activity, so they are the clean sample.
2026-09-02 is excluded (it contains the deliberately generated 58,588 CU spike from §3g plus a manual
run) and 09-09 is partial.

| Date | FUAM CU(s) | Tenant CU(s) | FUAM share | Pipeline |
|---|--:|--:|--:|--:|
| 2026-09-03 | 15,338.6 | 65,854.8 | 23.3% | 21.1 min |
| 2026-09-04 | 12,779.6 | 83,660.5 | 15.3% | 27.8 min |
| 2026-09-05 | 11,137.9 | 103,316.9 | 10.8% | 27.5 min |
| 2026-09-06 | 12,612.3 | 79,286.7 | 15.9% | 28.2 min |
| 2026-09-07 | 11,835.2 | 70,501.9 | 16.8% | 27.3 min |
| 2026-09-08 | 12,464.4 | 71,856.9 | 17.3% | 26.1 min |

**Mean 12,695 CU(s)/day** (min 11,138, max 15,339, sd 1,305). Excluding 09-03, which ran short:
**12,166 CU(s)/day**. The run is remarkably consistent — a ±5% band once warm.

| SKU | Daily budget CU(s) | FUAM steady state |
|---|--:|--:|
| **F2** | 172,800 | **7.35%** |
| **F4** | 345,600 | 3.67% |
| **F8** | 691,200 | 1.84% |
| F16 | 1,382,400 | 0.92% |
| F32 | 2,764,800 | 0.46% |
| **F64** | 5,529,600 | **0.23%** |

3.53 CU-hours/day ⇒ **$0.63/day, $19.04/month, $232/year** at East US $0.18/CU-hour. Against an F2
provisioned 24×7 ($259.20/month) that is 7.3% of the bill; against an F64 ($8,294/month), 0.2%.

FUAM consumed **16.1% of all CU in this tenant** over the sample — though the tenant is quiet
(79,080 CU(s)/day mean), so read that as "FUAM is significant relative to a small workload", not as a
general figure.

### Two corrections to the July analysis

1. **The prediction was ~2× too low.** Steady state is 12,695 CU(s)/day, not the 5,700–6,900 predicted.
2. **"Deployment period inflation" was largely wrong.** July's mean was 61,122 ÷ 5 = **12,224 CU(s)/day**
   — within 4% of the measured steady state of 12,695. What the deployment period actually changed was
   *variance*, not level: July swung from 5,732 to 23,116, while the scheduled runs hold 11,138–15,339.
   The honest summary is that FUAM costs about 12,000–13,000 CU(s)/day whenever it is doing a full pass,
   and July looked erratic only because the passes were manual and irregular.

Composition has shifted, though. In July, Lakehouse OneLake operations led at 35.1% with notebooks at
32.1% — the signature of an initial bulk load. In steady state notebooks dominate:

| Kind | CU(s)/day | Share |
|---|--:|--:|
| SynapseNotebook (19 items) | 8,543 | 70.2% |
| Pipeline (15 items) | 2,791 | 22.9% |
| Lakehouse (2 items) | 832 | 6.8% |

### Where the cost actually sits, by module

Rolled up using the deployed folder as the module name (09-04 → 09-08):

| Module | CU(s)/day | Share |
|---|--:|--:|
| **Capacity Metrics** | **3,397** | **27.9%** |
| Inventory | 1,593 | 13.1% |
| Workspaces | 1,224 | 10.1% |
| Activities | 966 | 7.9% |
| (root — orchestrator + Lakehouse) | 905 | 7.4% |
| Capacity Refreshables | 669 | 5.5% |
| Others | 557 | 4.6% |
| Capacities | 539 | 4.4% |
| Git Connections | 515 | 4.2% |
| Tenant Settings | 491 | 4.0% |
| WidelyShared | 433 | 3.6% |
| Active Items | 411 | 3.4% |
| Tags | 243 | 2.0% |
| Domains | 223 | 1.8% |

Single most expensive notebooks: `01_Transfer_Incremental_Inventory_Unit` (1,573/day),
`01_Transfer_CapacityMetricData_Timepoints_Unit` (1,383/day),
`03_Transfer_CapacityMetricData_ItemOperation_Unit` (1,015/day).

**The Capacity Metrics module alone is 27.9% of FUAM's cost** — and it is the module that duplicates
what the Metrics App already shows for 14 days. Its value is history beyond that window and SQL access;
if neither is needed, dropping it takes FUAM from 7.04% to **5.07% of an F2**.

Two levers, measured rather than estimated:

| Change | CU(s)/day | F2 | F8 | $/month |
|---|--:|--:|--:|--:|
| Daily (as configured) | 12,166 | 7.04% | 1.76% | $18.25 |
| Every 2 days | 6,083 | 3.52% | 0.88% | $9.12 |
| Every 3 days | 4,055 | 2.35% | 0.59% | $6.08 |
| Weekly | 1,738 | 1.01% | 0.25% | $2.61 |
| Daily, minus Capacity Metrics | 8,769 | 5.07% | 1.27% | $13.15 |

For comparison, Workspace Monitoring's always-on core measured ~2,110 CU(s)/day ≈ **1.2% of an F2**
(`D:\Copilot\PBIEmbeddedMonitoring\docs\architecture.md`). So a daily FUAM costs about **6× Workspace
Monitoring**, not the 3–11× range estimated from the July data.

**Recommendation.** On F16 and above FUAM is noise (<1%) — run it daily. On an F8 it is 1.8%, still
comfortable. On an **F2 or F4** it is 7.4% / 3.7% of the entire daily budget before any user workload,
which is real money on a small capacity: run it every 2–3 days, or drop the Capacity Metrics module if
the 14-day Metrics App window is enough.

### Method note — Direct Lake served a stale frame

The first pass of this analysis reported no FUAM data after 2026-09-03 and looked like a broken
pipeline. It was not: `FUAM_Core_SM` is Direct Lake and served an **old frame**. Its refresh history
shows no framing since 2026-07-14, yet re-running the identical query minutes later returned data
through 09-09. The Lakehouse **SQL analytics endpoint was staler still**, showing nothing after
2026-07-15 — its metadata sync lags the Delta tables.

So three views of the same Delta table disagreed at the same instant. When FUAM appears to have stopped
collecting, re-query before concluding anything, and confirm against `MAX([Date])` on the fact table
rather than a filtered slice.

The takeaway for a small capacity: FUAM is **not free on an F2** — budget several percent of the daily
budget before any user workload, and prefer a daily schedule over frequent runs. On F64+ it is noise.

### How the steady-state measurement was set up (2026-09-01)

To get a real steady-state number the dormant deployment was repaired and scheduled:

- Workspace renamed `EmbeddedMon-Accelerator` → **`FUAM`** (matches `deployment_config.yaml`).
- Three placeholder notebooks repaired from the repo (see §3b).
- `Load_FUAM_Data_E2E` scheduled **daily at 05:00 UTC** (schedule `d4f85d37-db7c-44dc-89ee-3ba69d4e6587`,
  30-day window). It had **no schedule and no run history** before this.

To repeat the measurement, query `capacity_metrics_by_item_by_operation_by_day` filtered to
`WorkspaceId = "5CFFBECD-9608-494B-984E-46BF4F774E12"`, group by `[Date]`, and keep only days holding
exactly one scheduled run. Exclude 2026-09-01 and 09-02 (manual runs, catch-up after seven weeks
dormant, and the §3g spike) and the current day, which is always partial. Results are in the
steady-state section above.

Note FUAM reports its own capacity metrics one day in arrears, and its extract window follows the
Metrics App's 14-day limit.

## 2c. What FUAM actually depends on (2026-09-09)

Verified by reading the deployed pipeline parameters and notebook source, not from documentation.

**Only the Capacity Metrics module needs the Metrics App.** `Load_Capacity_Metrics_E2E` declares:

```
metric_workspace     = 42fe4109-…   (the Capacity Metrics App workspace)
metric_dataset       = a57adbf4-…   (its semantic model)
metric_days_in_scope = 2
```

and its three notebooks call `fabric.evaluate_dax(workspace=metric_workspace, dataset=metric_dataset, …)`.
So FUAM's capacity data is re-extracted from the semantic model Microsoft documents as unsupported
(§1). The notebook carries five schema variants — `dax_query_v53 / v47 / v44 / v40 / v37` — and probes
each in turn to detect the installed version, which is why FUAM's changelog pins to app versions.

**The other ~72% of FUAM does not touch it.** Inventory, Workspaces, Activities, Tenant Settings, Git
Connections, Domains, Tags and WidelyShared read Fabric/Power BI admin REST APIs. Evidence: in July,
Capacity Metrics was empty while FUAM still loaded 199 workspaces, 91 reports, 86 models and 8,477
activities.

Failure handling differs by where it breaks, and one path is quiet:

| Failure | Code | Effect |
|---|---|---|
| No schema version matches | `raise Exception(...)` | notebook fails |
| Connects, cannot list capacities | `notebookutils.notebook.exit(...)` | **exits cleanly — pipeline still reports Completed** |

The second path is how a green pipeline ends up with no capacity data, which is what the July
deployment looked like.

**Capacity requirements are two separate things.**

- **FUAM itself requires a Fabric capacity** — Spark notebooks, pipelines, Lakehouse and Direct Lake
  models are all capacity workloads. That is the 12,695 CU(s)/day measured above.
- **The Metrics App workspace does not need dedicated capacity for FUAM to read it.** FUAM uses SemPy
  `evaluate_dax` → `executeQueries`, not XMLA. Confirmed directly: XMLA against that workspace was
  refused with *"does not have permission to call the Discover method"* (XMLA requires dedicated
  capacity) while `executeQueries` against the same model succeeded.

The Metrics App's own prerequisites still apply — installed by a **capacity admin**, and it reports
**F-SKUs only**. July's empty capacity metrics were not a capacity-assignment problem: the trial
capacity was not exposed and both F2s were suspended, so there was nothing to report.

**Consequence for the cost decision.** Dropping the Capacity Metrics module (27.9% of FUAM's cost)
leaves FUAM working as a governance and inventory tool and removes the only part that depends on an
unsupported, version-pinned schema. Cost and fragility come off together.

## 3. Existing solutions
| Tool | Owner | Stars | What it is |
|---|---|---|---|
| **FUAM** `microsoft/fabric-toolbox/monitoring/fabric-unified-admin-monitoring` | MS CAT team, **not supported** | 894 | Tenant monitoring. Ingests Metrics App via DAX into a Lakehouse, beats the 14/30-day cap. Monthly releases. Added a Semantic Model Optimization module (BPA + VertiPaq on top CU consumers). |
| **BI-Pixie-Skills** `DataChant/BI-Pixie-Skills/plugins/fabric-capacity` | Community | 0 | `capacity-model-guide.md` — best schema map of the Metrics App + Chargeback models anywhere, incl. the `MPARAMETER` pattern. 15 ready `.dax` files. `run_dax.py` runner. Works on a **Pro** licence. |
| **fabric-architecture-review** `microsoft/fabric-architecture-review` | Microsoft official | 14 | Python collector; auto-discovers the Metrics App dataset, fires DAX probes via `executeQueries`, incl. `INFO.VIEW.TABLES()`. JSON output. |
| **semantic-link-labs** `microsoft/semantic-link-labs` | Microsoft (M. Kovalsky) | 571 | Capacity CRUD + **surge protection rule read/write**. Does *not* query the Metrics App model. |
| **fabric-dw-query-capacity-correlation** `mariyaali/…` | Community | 0 | PBIP joining capacity utilization to Warehouse Query Insights — "which query caused the spike?" |
| **Rayfin capacity governance** `bradcoles-dev/rayfin-fabric-capacity-metrics` | Community | 0 | Replacement UI on Eventhouse with alert rules. Alerting backend **cannot deploy** — Fabric rejects Functions. |
| **Fabric Cost Analysis** `microsoft/fabric-toolbox/monitoring/fabric-cost-analysis` | MS | — | Azure spend (FOCUS), not CU. Complement to FUAM. |

Deprecated: `RuiRomano/pbimonitor` (260★) — README redirects to FUAM.
Obsolete: `RuiRomano/pbipremiumcapacitymetricsquery` — targets the pre-Fabric Premium schema.

## 3b. Deployment defects found by the parity check (2026-09-01)

Compared the live deployment against `microsoft/fabric-toolbox` at version **2026.6.1** (the deployed
version, and still the repo's current one):

| Check | Result |
|---|---|
| Repo items present in workspace | 51 / 51 |
| DataPipelines, normalised JSON with GUIDs masked | **17 / 17 identical** |
| Notebooks, code cells only | **21 / 24 identical** |
| Workspace folders | **0 of 18 created** — items are flat (fixed, see §3d) |
| Extra items | 3, all from the July repair |

**Three notebooks had been created but never filled** — they contained only the
`# Welcome to your new notebook` placeholder. Their item description read `Created by fab`, whereas
every correctly deployed notebook reads `Imported from fab`:

| Notebook | Was | Repaired to |
|---|--:|--:|
| `Init_FUAM_Lakehouse_Tables` | 75 chars | 64,483 |
| `01_FUAM_Lakehouse_Backup` | 75 chars | 2,314 |
| `02_FUAM_Lakehouse_Optimization` | 75 chars | 663 |

This is almost certainly the root cause of the July incident ("Lakehouse lacks optional columns
required by the fixed FUAM TMDL"): `Init_FUAM_Lakehouse_Tables` builds the schema and was empty. The
Lakehouse has 61 tables today only because the July repair added a compensating `Ensure_FUAM_Tables`
notebook. Both Maintenance notebooks were also empty, so backup and optimization silently did nothing.

Repaired by pushing repo content via
`POST /v1/workspaces/{ws}/items/{id}/updateDefinition?updateMetadata=false` with a base64 `ipynb` part.
`Init_FUAM_Lakehouse_Tables` carries no lakehouse metadata by design — it binds by name through
`%%configure { "defaultLakehouse": { "name": "FUAM_Lakehouse" } }`; the other two were repointed at this
workspace's `FUAM_Lakehouse` id.

**Check any FUAM deployment for this.** A notebook whose description is `Created by fab` rather than
`Imported from fab`, or whose definition is ~75 characters, was never populated. The deploying pipeline
reports success either way.

## 3c. Repo vs deployment: comparison method

- Notebook definitions come back from `getDefinition` as a **long-running operation** (202 + `Location`,
  poll to `Succeeded`, then `GET {location}/result`). Pipelines return 200 inline. Treating notebooks
  like pipelines yields an empty body and a false "DIFF".
- Repo notebooks are `notebook-content.ipynb`, not `.py`; request `?format=ipynb` to compare like for like.
- Compare **code cells only** — ipynb metadata, `known_lakehouses` and per-deployment GUIDs differ by design.
- Mask GUIDs before diffing pipelines, otherwise all 17 appear different.
- In PowerShell 5.1, `Invoke-WebRequest` inside a `-File` script can throw
  `NullReferenceException` on these endpoints; `Invoke-RestMethod` is reliable, except when you need
  response headers for the LRO `Location`, where `-UseBasicParsing` is required.

## 3d. Workspace folders (2026-09-02)

The deployment had **no folders** — all 63 items sat flat. `deployment_config.yaml` defines the intended
layout under its `folders:` key, so it was rebuilt from that: **17 folders, 56 of 63 items filed.**

Created with `POST /v1/workspaces/{ws}/folders`, then each item moved with
`POST /v1/workspaces/{ws}/items/{itemId}/move` and body `{"targetFolderId": "..."}`. Moving an item does
not change its id, so pipeline references and the schedule survive untouched (verified afterwards).

| Folder | Items | Folder | Items |
|---|--:|---|--:|
| Reporting | 10 | Domains | 2 |
| Maintenance | 6 | Git Connections | 2 |
| Capacity Metrics | 4 | Inventory | 2 |
| Tenant Settings | 4 | Optimization Module | 2 |
| WidelyShared | 4 | Tags | 2 |
| Others | 4 | Workspaces | 2 |
| Activities | 3 | Capacity Refreshables | 2 |
| Deployment | 3 | Capacities | 2 |
| Active Items | 2 | | |

Three points worth knowing:

- **The config lists `Optimization Module` twice** — once holding `00_Run_Optimization_Module_for_SM_Unit`
  and again holding `01_Run_Optimization_Module_for_SM_Unit` plus `Load_Optimization_Module_E2E`. Only the
  `01_` notebook is deployed, so the `00_` reference is dead. Merge the two entries; creating both raises
  a duplicate-name error.
- **SQL endpoints follow their lakehouse automatically.** Moving `FUAM_Config_Lakehouse` also moved its
  `SQLEndpoint`, which is why Deployment shows 3 items where the config lists 2 (likewise Maintenance 6/5,
  Others 4/3). 53 explicit moves + 3 inherited = 56.
- **`POST .../move` throttles at HTTP 429** after roughly 50 rapid calls, and PowerShell surfaces it as an
  empty error string. Retry with backoff and read the status code, or the moves look like silent failures.

Seven items remain at root, all correctly: `Load_FUAM_Data_E2E` (the master orchestrator) and
`FUAM_Lakehouse` + its SQL endpoint are not in any config folder; `FabricWorkspaceMonitoring`
(KQLDashboard) is not a FUAM item; and `Align_FUAM_Model_Schema`, `Ensure_FUAM_Tables`,
`Export_FUAM_Evidence` are the July repair's own additions, so leaving them at root keeps the
non-standard items visible.

## 3e. Retiring the July repair notebooks (2026-09-02)

The three non-repo notebooks were reviewed once `Init_FUAM_Lakehouse_Tables` was restored:

| Notebook | Purpose | Outcome |
|---|---|---|
| `Ensure_FUAM_Tables` | Read `table_definitions.snappy.parquet`, `CREATE TABLE IF NOT EXISTS` per table | **Deleted** — duplicates the restored `Init_FUAM_Lakehouse_Tables` |
| `Export_FUAM_Evidence` | Row-count 7 tables, write a CSV to `Files/evidence/` | **Deleted** — diagnostic only |
| `Align_FUAM_Model_Schema` | Add the optional columns the fixed TMDL expects; normalise `workspaces.tags` | **Kept** as a break-glass recovery tool |

Evidence for deletion, all checked rather than assumed:

- **No pipeline references any of them** — all 17 pipeline definitions were scanned for both the display
  names and the item GUIDs. They were orphaned, never part of the scheduled flow.
- **The schema fix has held.** All ten optional columns (`sensitivityLabel.labelId`, `tags`,
  `upstreamDataflows`, `connectionDetails.*`, `directQueryRefreshSchedule.*`) are present and survived two
  full pipeline runs, so `Align` has nothing outstanding to repair.
- **Two were already broken by the rename.** `Ensure_FUAM_Tables` and `Export_FUAM_Evidence` hardcoded
  `ws_name = "EmbeddedMon-Accelerator"` and built `abfss://` paths from it. `Align` used GUIDs, so it
  still worked.

`Align_FUAM_Model_Schema` is worth keeping: it is defensive (preflight for case-insensitive column
collisions, Delta version guard against concurrent writes, row-count check after the change) and it is
idempotent — the `overwrite` branch that rewrites `workspaces`/`capacities` is skipped when the `tags`
column already has the expected `ArrayType<Struct>`. It is the only remedy if those columns regress.

It was made portable: cell 0 previously hardcoded this workspace's and lakehouse's GUIDs, and now
resolves both at run time.

```python
ctx = notebookutils.runtime.context
workspace_id = ctx.get("currentWorkspaceId")
lakehouse_id = notebookutils.lakehouse.get("FUAM_Lakehouse")["id"]   # falls back to defaultLakehouseId
```

Both calls were verified in this workspace before the edit by running a throwaway notebook that wrote
its result to OneLake — `currentWorkspaceId`, `defaultLakehouseId` and `lakehouse.get(name)["id"]` all
returned the correct values. Notebook stdout is **not** retrievable from
`GET .../jobs/instances/{id}` (it returns status only, and the snapshot endpoints 404), so writing the
result to OneLake Files and reading it back over the DFS API is the practical way to get output from an
API-triggered notebook run.

Workspace is now **61 items**: 51 repo items, 4 SQL endpoints, the KQL dashboard, and the single
retained repair notebook. Archived copies of all three notebooks are in the session workspace under
`files/fuam-repair-notebooks/`.

## 3f. FUAM reports hide deleted workspaces (2026-09-02)

Found while capturing a side-by-side diagnosis walkthrough: FUAM's **Item Outlier Analysis** page
rendered an empty table for 2026-07-04, a day whose capacity chart showed a 5,520% CU peak.

The data is not missing. A direct DAX query against
`capacity_metrics_by_item_by_operation_by_day` returns **17 item/operation rows and 250,761 CU(s)** for
that date, topped by an `LlmPlugin` **AI Query at 13,177 CU(s)** over 18 operations — a genuine high-CU
event on an F2.

The table is empty because **100% of that day's CU belongs to workspaces since deleted**, and
`FUAM_Core_Report` carries a report-level filter named *Is Capacity Deleted* on
`capacities[fuam_deleted]` (confirmed by reading the report definition: 7 `fuam_deleted` references
across `report.json` and two visuals). In this tenant **146 of 295 workspaces** are flagged deleted,
nearly all short-lived `autorunner_*` automation workspaces. For comparison, 0% of 2026-09-01's CU maps
to a deleted workspace, so that day renders normally.

Consequence for anyone evaluating FUAM: if the workload creates and destroys workspaces — CI runs,
per-customer provisioning, ephemeral test environments — **the long history is retained in the Lakehouse
but invisible in the reports.** Recovering it requires querying the tables directly, which is what the
Lakehouse SQL endpoint and `executeQueries` are for. This strengthens rather than weakens the case for
FUAM's queryable storage, but it means the reports alone understate historical consumption.

Method note: `SUMMARIZECOLUMNS` over `workspaces` and the metrics fact collapses to a single total
because the relationship is inactive (see §3d). Group by `[WorkspaceId]` in the fact and join to the
dimension outside DAX.

## 3g. Generating a reproducible CU spike for testing (2026-09-03)

Built to produce a traceable high-CU event on demand. Method: fire many **unique** pure-CPU DAX queries
at one semantic model through the Power BI `executeQueries` REST API.

```dax
EVALUATE ROW("x", SUMX(GENERATESERIES(1, 25000000), SQRT([Value]) * SIN([Value])))
```

Two failures worth knowing, both hit on the first attempts:

- **Identical queries are cached.** The first burst reused one query string; the engine returned a
  cached result in ~2 s instead of ~9 s and burned almost no CU. Vary a literal per query
  (`GENERATESERIES(i+1, ROWS+i)`) or the load test measures nothing.
- **`executeQueries` is capped at 120 requests per minute per user.** A burst of 240 returned exactly
  120 successes and 120 HTTP 429s. Back off and retry, or stay under the cap. This confirms the
  documented limit in §2.

Result on an FTL64: 564 queries / 4,158 query-seconds over ~12 minutes produced **peak utilization
204.65%**, one timepoint at **201.74% of base capacity** from interactive alone, and **419% cumulative
carryforward** with 2.1 minutes to burn down — without tripping throttling, because bursting absorbed it.

### What the diagnosis walkthrough showed

- The Health page still read **Healthy** at 3× normal load with zero rejections. The signal was the
  one-hour average (26.50%) against the seven-day baseline (8.01%), plus P95 interactive delay at 72.
- On the **14-day** item list the offending model sat fourth and looked unremarkable. Only after
  filtering the ribbon chart to a single day did it become the clear top consumer at 58,588 CU(s),
  3× the next item. Filtering to the day is the step that makes the item list diagnostic.
- Exceeding 100% of base capacity is **not** the same as being throttled; the throttling tab showed the
  10-minute interactive percentage stopping near 90% of its threshold.
- FUAM recorded 58,348.8 CU against the Metrics App's 58,588.4 (0.4% apart) — but only because its
  05:00 UTC run happened to fall 11 minutes after the burst. An hour later and it would have been a day
  behind.

## 3h. Metrics App semantic model — verified table list (2026-09-03)

The research above flagged that Metrics App table names were community-sourced. They are now verified:
`executeQueries` against the Metrics App model (`Fabric Capacity Metrics`) returns **111 tables**,
including `Capacities`, `Items`, `Metrics By Item And Operation`, `Metrics By Item Operation And Hour`,
`Timepoint Interactive Detail`, `Timepoint Background Detail`, `Timepoint Interactive Summary`,
`Usage Summary By Capacities (Last 1 hour / 24 hours / 7 days)`, `Surge Protection By Day`,
`Items Throttled`, `System Events` and `Storage By Workspaces And Day`.

`Timepoint Interactive Detail` carries `Total CU (s)`, `Timepoint CU (s)`, `% of base capacity`,
`Throttling (s)`, `User`, `Operation`, `Operation Id`, `Status` and `Billing type` — the per-operation
grain the report shows.

**Caveat that matters:** most of these tables are DirectQuery and return **empty** over `executeQueries`
unless the model's `MPARAMETER` values (`CapacitiesList`, `RegionName`, `TimePoint`) are injected in the
query. Schema introspection via `INFO.VIEW.TABLES()` works without them; data does not. This is the
`MPARAMETER` pattern documented in `DataChant/BI-Pixie-Skills`. Use the report UI, or inject the
parameters, rather than assuming the model is empty.

## 4. MCP / agent integration

| Server | Official | Stars | Reaches CU data? |
|---|---|---|---|
| **FabricIQ** `microsoft/skills-for-fabric` (= local `powerbi-remote`) | Yes | 1,083 | **Indirect — `ExecuteQuery` runs DAX against any model, incl. Metrics App** |
| **fabric-rti-mcp** `microsoft/fabric-rti-mcp` | Yes | 130 | **Yes, with pipeline — `kusto_query` over the Eventhouse** |
| Fabric Core MCP (remote) `microsoft/mcp` | Yes | 3,630 | No — `list_capacities` returns SKU/state only |
| Fabric MCP (VS Code local) `microsoft/mcp` | Yes | 3,630 | No admin/capacity tools |
| Azure MCP `microsoft/mcp` | Yes | 3,630 | No — ARM returns config only |
| `bablulawrence/ms-fabric-mcp-server` | No | 4 | Indirect via `execute_dax_query`. Dev-only, has destructive tools. |
| `Augustab/microsoft_fabric_mcp` | No | 25 | No — `list_compute_usage` is an in-progress job list, not CU |
| `datumnova/ms-fabric-mcp` | No | 16 | No |

`augmentedivan/fabric-mcp-server` is Daniel Miessler's AI "Fabric" — unrelated.

Also: a **Fabric Data Agent can be published as an MCP endpoint**
(`learn.microsoft.com/fabric/data-science/data-agent-mcp-server`, preview), giving a natural-language
surface over a semantic model.

## 5. Microsoft's own troubleshooting workflow

`learn.microsoft.com/fabric/enterprise/throttling` — fully manual, no wizard:
Health page → Compute page throttling charts + System events → Utilization chart → right-click
drill-through to Timepoint → sort operations by Total CU(s) → Timepoint Item Detail.
Burndown table gives "minutes to burndown".

Ways to stop throttling: temporarily raise the SKU, pause/resume (clears carry-forward), or enable
Capacity Overage (preview, pays 3× to avoid throttling).

Documented Metrics App limits: ~10–15 min latency; **14-day** compute history; 30-day storage; dimensions
refresh once daily at midnight; Timepoint tables cap at **100,000 records**; no built-in alerting;
sampling may occur on export; sovereign clouds do not populate throttling charts.

Surge protection is GA (F-SKU only) at both capacity and workspace level; workspace checks run every
5 minutes; autoscale-billed Spark is excluded.

## 6. Recommended architecture

1. **Now, zero infrastructure** — DAX against the Metrics App using the FabricIQ `ExecuteQuery` MCP tool
   already configured locally as `powerbi-remote`. Gets the existing 14 days. Unsupported, so treat the
   schema as volatile and pin/verify queries.
2. **Durable** — deploy the `fpm-capacity-events` Jumpstart accelerator (Eventstream → Eventhouse), then
   point `microsoft/fabric-rti-mcp` at it so an agent can run KQL. Supported, unlimited retention, but
   only from the day you turn it on.
3. **Full tenant monitoring** — install FUAM.

Borrow from `DataChant/BI-Pixie-Skills` regardless: the model guide and the 15 DAX files remove most of
the discovery work for either path.

## Open questions

- Whether the Metrics App exposes a stable internal timepoint endpoint worth wrapping (unverified).
- Admin Monitoring Workspace (preview) — Learn does not enumerate its capacity reports.
- Metrics App semantic model table names are community-sourced; verify against a live tenant with
  `INFO.VIEW.TABLES()` or `$SYSTEM.TMSCHEMA_TABLES` before relying on them. **(Resolved — see §3h.)**
- ~~FUAM's steady-state cost on a daily schedule is still unmeasured.~~ **Measured 2026-09-09 over six
  scheduled days: 12,695 CU(s)/day, 7.35% of an F2, 0.23% of an F64. See §2b.**
- Whether FUAM's cost scales with tenant size. This sample is one quiet tenant (315 workspaces,
  ~79,000 CU(s)/day). The Inventory and Workspaces modules are 23% of FUAM's cost and both scale with
  item count, so a larger tenant should cost proportionally more — unverified.

## Verified access notes (2026-09-01)

- `powerbi-design` MCP (`list_workspaces`, `list_reports`) reaches the **demo tenant**
  (`MngEnvMCAP777813`); the retired `powerbi-remote`/FabricIQ was bound to **msit** and could not see it.
- `powerbi-design-get_semantic_model_schema` failed on every model tried (`FUAM_Core_SM`, `FUAM_Item_SM`
  and the Metrics App) with the same `DatasetExecuteQueriesError` / `AnalysisServicesErrorCode
  3239575574`, while hand-built `executeQueries` DAX against the same models worked — so the failure is
  in that tool's introspection query, not the models.
- **XMLA needs dedicated capacity.** `powerbi-modeling-mcp ConnectFabric` to the Metrics App workspace
  failed with "does not have permission to call the Discover method" because that workspace is Pro, not
  on capacity. The REST `executeQueries` path has no such requirement.
- Working pattern used here: `az account get-access-token --resource https://analysis.windows.net/powerbi/api`
  then `POST /v1.0/myorg/groups/{ws}/datasets/{ds}/executeQueries`. Semantic model *names* differ from
  report names (`FUAM_Core_Report` → `FUAM_Core_SM`); list them via `/groups/{ws}/datasets`.
- In FUAM's model the `workspaces` table has no active relationship to
  `capacity_metrics_by_item_by_operation_by_day`, so `SUMMARIZECOLUMNS` over both collapses to one
  total. Group by `[WorkspaceId]` and map names separately.
