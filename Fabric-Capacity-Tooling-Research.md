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
