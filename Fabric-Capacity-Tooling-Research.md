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
(`D:\Copilot\PBIEmbeddedMonitoring\docs\architecture.md`). So FUAM costs roughly **3–11× Workspace
Monitoring** depending on the day.

**Read these as deployment-period numbers, not steady state.** July 9–10 include the initial full
extraction and a repair session where the pipeline was re-run repeatedly by hand; the deployment was
never put on a schedule and has been dormant since (last model refresh 2026-07-14). A production FUAM
running one scheduled pipeline pass per day should sit nearer the July 11/14 figures
(~5,700–6,900 CU(s)/day ≈ 3.3–4.0% of an F2). FUAM was 0.8% of all tenant CU in the window.

The takeaway for a small capacity: FUAM is **not free on an F2** — budget several percent of the daily
budget before any user workload, and prefer a daily schedule over frequent runs. On F64+ it is noise.

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
  `INFO.VIEW.TABLES()` or `$SYSTEM.TMSCHEMA_TABLES` before relying on them.
- FUAM's steady-state cost on a daily schedule is still unmeasured — the figures in §2b cover a
  deployment and repair period. Re-run the query in §2b after FUAM has run scheduled for a week.

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
