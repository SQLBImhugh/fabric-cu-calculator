# Microsoft Fabric — Capacity Unit (CU) Consumption, Usage & Billing Reference

> A comprehensive reference for how Microsoft Fabric measures, smooths, and bills compute as
> **Capacity Units (CUs)**. Part 1 covers concepts + the calculation formulas (interactive and
> background CU‑hour math). Part 2 is the service‑by‑service consumption breakdown.
>
> All facts sourced from Microsoft Learn (links inline). Pricing numbers shown (e.g. `$0.18/CU‑hour`)
> are **illustrative** — actual rates are regional; see the [Fabric pricing page](https://azure.microsoft.com/pricing/details/microsoft-fabric/).

---

## TL;DR cheat‑sheet

| Quantity | Formula |
| --- | --- |
| CU‑hours from CU‑seconds | `CU_hours = CU_seconds / 3600` |
| CU‑seconds from CU‑hours | `CU_seconds = CU_hours × 3600` |
| Capacity supply (per hour) | `base_CU` CU‑hours/hour  *(F64 → 64)* |
| Capacity supply (per day) | `base_CU × 24` CU‑hours/day  *(F64 → 1,536)* |
| Capacity supply (per 30‑s timepoint) | `base_CU × 30` CU‑seconds  *(F64 → 1,920)* |
| Capacity supply (per hour, in CU‑s) | `base_CU × 3600` CU‑seconds  *(F64 → 230,400)* |
| Utilization % at a timepoint | `smoothed_CU(s)_in_timepoint / (base_CU × 30)` |
| Cost of a job | `effective_CU_hours × price_per_CU_hour` |
| **Interactive** per‑timepoint load | `Total_CU(s) / N`, where `N` = 10…128 timepoints (5–64 min) |
| **Background** per‑timepoint load | `Total_CU(s) / 2880` (spread evenly over 24 h) |

---

# PART 1 — CONCEPTS & CALCULATIONS

## 1.1 What is a Capacity Unit (CU)?

A **Capacity Unit (CU)** is Fabric's unit of compute power. When you buy a Fabric capacity (an **F SKU**),
you get a fixed number of CUs that are **shared across every Fabric workload** (Power BI, Warehouse,
Spark, Data Factory, Real‑Time Intelligence, OneLake, Copilot, etc.). Billing is **consumption‑based**:
you pay for the capacity, and every operation draws from that shared CU pool.
([Licenses](https://learn.microsoft.com/fabric/enterprise/licenses), [Buy a subscription](https://learn.microsoft.com/fabric/enterprise/buy-subscription))

* **F SKUs** (Azure) — pay‑as‑you‑go, billed **per second with a 1‑minute minimum**, can pause/resume, scale up/down. Recommended.
* **P SKUs** (Power BI Premium) — legacy, being retired in favor of F SKUs.

### SKU → CU reference table

| SKU | Capacity Units (CUs) | Power BI SKU | PBI v‑cores | CU‑seconds / 30‑s timepoint | CU‑hours / hour | CU‑hours / day |
| --- | --- | --- | --- | --- | --- | --- |
| F2    | 2     | –        | 0.25 | 60     | 2     | 48     |
| F4    | 4     | –        | 0.5  | 120    | 4     | 96     |
| F8    | 8     | EM/A1    | 1    | 240    | 8     | 192    |
| F16   | 16    | EM2/A2   | 2    | 480    | 16    | 384    |
| F32   | 32    | EM3/A3   | 4    | 960    | 32    | 768    |
| F64   | 64    | P1/A4    | 8    | 1,920  | 64    | 1,536  |
| Trial | 64    | –        | 8    | 1,920  | 64    | 1,536  |
| F128  | 128   | P2/A5    | 16   | 3,840  | 128   | 3,072  |
| F256  | 256   | P3/A6    | 32   | 7,680  | 256   | 6,144  |
| F512  | 512   | P4/A7    | 64   | 15,360 | 512   | 12,288 |
| F1024 | 1024  | P5/A8    | 128  | 30,720 | 1,024 | 24,576 |
| F2048 | 2048  | –        | 256  | 61,440 | 2,048 | 49,152 |

Sources: [Licenses – core building blocks](https://learn.microsoft.com/fabric/enterprise/licenses#core-building-blocks),
[Plan your capacity size](https://learn.microsoft.com/fabric/enterprise/plan-capacity) (30‑second CU use = `CUs × 30`),
[Capacity overage – limits](https://learn.microsoft.com/fabric/enterprise/capacity-overage-overview#capacity-overage-limits) (CU‑hours/day).

> **Key identity:** A SKU's CU number **is** its CU‑hours‑per‑hour rate. An F64 supplies 64 CU‑hours
> every hour = 1,536 CU‑hours/day = 1,920 CU‑seconds in every 30‑second timepoint.

## 1.2 The unit zoo — CU, CU(s), CU‑hour

| Term | Meaning |
| --- | --- |
| **CU** | Instantaneous compute power (a rate). F64 = 64 CUs available at any instant. |
| **CU‑second `CU(s)`** | 1 CU sustained for 1 second. The atomic unit the Metrics App reports. |
| **CU‑hour** | 1 CU sustained for 1 hour = **3,600 CU‑seconds**. The unit Azure bills on. |
| **Timepoint** | A fixed **30‑second** evaluation window. There are **2,880** timepoints in 24 hours. |

Conversions:

```
CU_hours   = CU_seconds / 3600
CU_seconds = CU_hours   × 3600
```

([Throttling – smoothing](https://learn.microsoft.com/fabric/enterprise/throttling#smoothing),
[Metrics app calculations](https://learn.microsoft.com/fabric/enterprise/metrics-app-calculations))

## 1.3 Bursting and smoothing (why CU‑hours ≠ wall‑clock)

* **Bursting** — Fabric lets an operation temporarily use **more** compute than the SKU provisions so it
  finishes fast. A small capacity can therefore run a large job. The CU(s) consumed reflect the *bursted*
  compute, not the SKU ceiling. ([Bursting](https://learn.microsoft.com/fabric/enterprise/throttling#bursting))
* **Smoothing** — To avoid penalizing that burst, Fabric **averages (smooths) the consumed CU(s) across
  future 30‑second timepoints**. Smoothed usage is paid for by *future capacity* (the CUs available in
  upcoming timepoints). This is why the Metrics App shows usage attributed to timepoints *after* a job ran.
  ([Smoothing](https://learn.microsoft.com/fabric/enterprise/throttling#smoothing))

> **Important for cost math:** The **`Total CU(s)`** the Metrics App reports for an operation **already
> accounts for the operation's duration and bursting**. To get cost you simply divide by 3,600 — the
> wall‑clock duration is *not* needed (and must not be multiplied in again).
> ([Pricing scenario note](https://learn.microsoft.com/fabric/data-factory/pricing-scenario-load-1-tb-parquet-to-data-warehouse))

## 1.4 Interactive vs. Background operations

Every operation is classified as **interactive** or **background**. The classification only changes **how
the consumed CU(s) are smoothed** — not the total CU(s) consumed.
([Fabric operations](https://learn.microsoft.com/fabric/enterprise/fabric-operations))

| | Interactive | Background |
| --- | --- | --- |
| Typical work | Ad‑hoc queries, report/visual rendering, modeling, UI actions, Copilot prompts*(*see note)* | Scheduled/long jobs: Spark, pipelines, dataflows, warehouse queries, OneLake transactions, Copilot |
| Smoothing window | **Min 5 min, up to 64 min** depending on CU(s) consumed | **24 hours** (fixed) |
| Smoothing timepoints `N` | **10 → 128** | **2,880** |
| Throttling exposure | Hits the 10‑min / 60‑min thresholds quickly | Smoothed thin; only the 24‑h threshold |

> *Note:* Many "AI/Copilot" operations are classified **background** so their cost spreads over 24 h, even
> though the user experience is interactive. Always verify a service's classification in §Part 2.

## 1.5 Core formulas

### (a) Capacity supply

```
supply_per_timepoint (CU‑s) = base_CU × 30
supply_per_hour      (CU‑s) = base_CU × 3600
supply_per_hour      (CU‑h) = base_CU
supply_per_day       (CU‑h) = base_CU × 24
```

### (b) Effective CU‑hours & cost of any job (the number you bill on)

```
effective_CU_hours = Total_CU(s) / 3600
job_cost           = effective_CU_hours × price_per_CU_hour      # regional PAYG or reservation rate
```

### (c) Utilization % at a timepoint

```
utilization%_at_timepoint = smoothed_CU(s)_assigned_to_timepoint / (base_CU × 30)
                          = smoothed_CUs / base_CU                # the Metrics App "% of base"
```

When `utilization% > 100%` the capacity is **overloaded** and begins throttling / carryforward.
([Metrics app calculations](https://learn.microsoft.com/fabric/enterprise/metrics-app-calculations),
[Timepoint summary – % of base](https://learn.microsoft.com/fabric/enterprise/metrics-app-timepoint-summary-page))

---

### (d) INTERACTIVE job — CU‑hour calculation

An interactive operation's `Total_CU(s)` is smoothed over **N timepoints**, where N depends on how much
the operation consumed:

```
effective_CU_hours      = Total_CU(s) / 3600

N_timepoints            = clamp( chosen by Fabric , 10 , 128 )     # 10 = 5 min, 128 = 64 min
per_timepoint_CU(s)     = Total_CU(s) / N_timepoints
per_timepoint_load%     = per_timepoint_CU(s) / (base_CU × 30)
```

* **Lower bound:** 5 min = 300 s = **10 timepoints**.
* **Upper bound:** 64 min = 3,840 s = **128 timepoints**.
* Fabric increases N (toward 64 min) the more CU(s) the operation consumed; the exact selection function
  is adaptive and not publicly specified — use 10–128 as the bracket.
  ([Smoothing](https://learn.microsoft.com/fabric/enterprise/throttling#smoothing))

**Worked example** — a report query consumes **Total = 600 CU(s)** on an **F64**, smoothed over the 5‑minute minimum:

```
effective_CU_hours  = 600 / 3600                = 0.167 CU‑hours
N                   = 10  (5‑min minimum)
per_timepoint_CU(s) = 600 / 10                  = 60 CU(s)/timepoint
per_timepoint_load% = 60 / (64 × 30) = 60/1920  = 3.13% of the F64 per timepoint
cost @ $0.18/CU‑h   = 0.167 × 0.18              ≈ $0.03
```

---

### (e) BACKGROUND job — CU‑hour calculation

A background operation's `Total_CU(s)` is spread **evenly over the full 24‑hour window = 2,880 timepoints**:

```
effective_CU_hours      = Total_CU(s) / 3600
per_timepoint_CU(s)     = Total_CU(s) / 2880
per_timepoint_load%     = per_timepoint_CU(s) / (base_CU × 30)
```

**Worked example** — a Spark notebook consumes **Total = 282,240 CU(s)** on an **F64**:

```
effective_CU_hours  = 282,240 / 3600            = 78.4 CU‑hours
per_timepoint_CU(s) = 282,240 / 2880            = 98 CU(s)/timepoint
per_timepoint_load% = 98 / 1920                 = 5.1% of the F64 per timepoint (held for 24 h)
cost @ $0.18/CU‑h   = 78.4 × 0.18               ≈ $14.11
```

(The 282,240 CU(s) / $14.11 figure is the real Metrics‑App result from the
[load‑1‑TB‑CSV‑to‑Lakehouse pricing scenario](https://learn.microsoft.com/fabric/data-factory/pricing-scenario-load-1-tb-csv-to-lakehouse-table).)

---

## 1.6 Throttling, carryforward & overage (what happens above 100%)

Fabric evaluates **future smoothed usage** against three thresholds. Each tab in the Metrics App
**Throttling** visual maps to a future‑time window:
([Metrics app calculations](https://learn.microsoft.com/fabric/enterprise/metrics-app-calculations),
[Throttling policy](https://learn.microsoft.com/fabric/enterprise/throttling))

| Throttling stage | Threshold (future usage ≥ 100% of …) | Effect |
| --- | --- | --- |
| **Interactive Delay** | next **10 minutes** | 20‑second delay added to interactive requests |
| **Interactive Rejection** | next **60 minutes** | Interactive requests rejected (UI error); background still runs |
| **Background Rejection** | next **24 hours** | **All** requests rejected (interactive + background) |

* **Carryforward (overage):** consumption above 100% in a timepoint is **carried forward** ("Add") and
  **burned down** in later timepoints when usage drops below 100%. Only **billable** operations count.
  ([Compute page – Overages](https://learn.microsoft.com/fabric/enterprise/metrics-app-compute-page))
* **Background rejection of 250%** means you used **2.5×** your daily (24‑h) capacity allowance.
* **Capacity Overage (preview):** lets the capacity bill the overage instead of throttling.
  Overage is billed at **3× the pay‑as‑you‑go rate**. Estimate:
  `overage_cost ≈ 3 × price_per_CU_hour × configured_CU_hour_limit`. Keep the limit **below ⅓ of daily
  CU‑hours** (above that, scaling up the SKU is cheaper).
  ([Capacity overage](https://learn.microsoft.com/fabric/enterprise/capacity-overage-overview),
  [Enable overage](https://learn.microsoft.com/fabric/enterprise/enable-capacity-overage))
* **Surge protection:** admins can trigger **background rejection earlier** (custom reject/recover
  thresholds on the 24‑h background %), and cap per‑workspace CU spend in a rolling 24‑h window.
  ([Surge protection](https://learn.microsoft.com/fabric/enterprise/surge-protection))

---

# PART 2 — SERVICE‑BY‑SERVICE BREAKDOWN

> For each workload: how it's classified, what the consumption is measured in, and the published rate(s).
> The **CU(s) → CU‑hours → cost** math from Part 1 applies uniformly on top of these rates.

## 2.1 Power BI

* **Classification:** mostly **interactive** (queries, visual & report rendering, modeling); some background (e.g. scheduled refresh).
* **Meter:** `Power BI Usage CU`. ([Azure bill – invoice meters](https://learn.microsoft.com/fabric/enterprise/azure-billing#invoice-meters))
* **Paginated reports capacity‑planning formulas**
  ([Paginated capacity planning](https://learn.microsoft.com/power-bi/paginated-reports/paginated-capacity-planning)):

  ```
  max_concurrent_report_renders = (capacity_units × 3.75) / report_CPU_seconds
  max_SKU_users                 = max_concurrent_report_renders / 0.05      # 5% concurrency assumption
  ```

## 2.2 Data Warehouse & SQL analytics endpoint

* **Classification:** **background** for most Warehouse operations (to benefit from 24‑h smoothing).
  **Exceptions are interactive:** modeling operations (create measure, visualize) and creating/updating
  Power BI semantic models/reports → follow **Interactive Rejection**. DMVs are background.
  ([Smoothing & throttling in Warehouse](https://learn.microsoft.com/fabric/data-warehouse/compute-capacity-smoothing-throttling),
  [Warehouse usage reporting](https://learn.microsoft.com/fabric/data-warehouse/usage-reporting))
* **Measured in:** CU(s) per query/operation (reported in the Metrics App timepoint drill‑through).
* In‑flight operations are **never throttled mid‑run**; throttling applies to the *next* operation after smoothing.

## 2.3 Apache Spark (Notebooks, Spark Job Definitions, Lakehouse jobs)

* **Classification:** **background** (24‑h smoothing).
  ([Spark billing & utilization](https://learn.microsoft.com/fabric/data-engineering/billing-capacity-management-for-spark))
* **Core mapping:** **1 CU = 2 Spark VCores.** Spark VCores are shared across Spark items in the capacity.
  ([Spark concurrency & queueing](https://learn.microsoft.com/fabric/data-engineering/spark-job-concurrency-and-queueing))
* **Bursting / queueing:** jobs admit against available VCores (FIFO queue; **24‑h queue expiration**). If the
  capacity is throttled, new Spark jobs are **rejected** (HTTP 430), not queued.
* **Autoscale Billing (optional):** Spark usage billed separately under the
  **`Autoscale for Spark Capacity Usage CU`** meter; the Metrics App has a dedicated *Autoscale compute for
  Spark* page (billable vs non‑billable CU(s) per 1‑minute period, with a Max‑CU‑per‑minute line).
  ([Autoscale Spark page](https://learn.microsoft.com/fabric/enterprise/metrics-app-feature-autoscale-page))
* **Base meter:** `Spark Memory Optimized Capacity Usage CU`.

## 2.4 Data Factory — Pipelines, Dataflows Gen2, Copy Job

Serverless/elastic; you pay for the operations you author. ([Data Factory pricing](https://learn.microsoft.com/fabric/data-factory/pricing-overview))

**Pipelines** ([Pipelines pricing](https://learn.microsoft.com/fabric/data-factory/pricing-pipelines)):

| Engine | Rate |
| --- | --- |
| **Data movement** (Copy activity) | **1.5 CU‑hours** per intelligent‑throughput‑optimization unit, for the copy duration |
| **Data orchestration** (non‑copy activity runs) | **0.0056 CU‑hours** per activity run |
| **SSIS** (Invoke SSIS package) | **1.35 CU‑hours per vCore** of SSIS uptime |

**Copy Job** ([Copy job pricing scenario](https://learn.microsoft.com/fabric/data-factory/pricing-scenario-copy-job)):

| Pattern | Rate |
| --- | --- |
| Full copy | **1.5 CU‑hours** per intelligent‑throughput‑optimization unit |
| Incremental copy | **3 CU‑hours** per intelligent‑throughput‑optimization unit |

**Dataflow Gen2:** **Standard Compute** + **High Scale Compute** meters.
**Worked example (pipeline Copy):** throughput unit = 4, duration ≈ 11 min →
`4 × 1.5 × (11/60) = 1.1 CU‑hours = 3,960 CU(s)` → at `$0.18/CU‑h` ≈ **$0.20**.
([Parquet→Warehouse scenario](https://learn.microsoft.com/fabric/data-factory/pricing-scenario-load-1-tb-parquet-to-data-warehouse))

> Pipelines that trigger Notebooks/Dataflows incur **those items' consumption too** — sum them.

## 2.5 Real‑Time Intelligence (Eventhouse / KQL, Eventstream, Activator)

* **Eventhouse / KQL Database:** `Eventhouse UpTime` (background) measures time the Eventhouse is active;
  plus query/ingestion operations. See [KQL Database consumption](https://learn.microsoft.com/fabric/real-time-intelligence/kql-database-consumption).
* **Eventstream:** long‑running by design — instead of throttling new operations, Fabric throttles the
  CU(s) allocated to keeping the stream open. ([Throttling – Fabric‑specific behavior](https://learn.microsoft.com/fabric/enterprise/throttling))
* **Activator** ([Activator capacity usage](https://learn.microsoft.com/fabric/real-time-intelligence/data-activator/activator-capacity-usage)):
  1. **Rule uptime / hour** — flat hourly charge while a rule is active.
  2. **Event ingestion** — accrues as real‑time events are processed.
  3. **Event computations** — cost of evaluating each event against rule conditions.
  4. **Storage** — events retained 30 days in Fabric.
* **Meters:** `Real‑Time Intelligence – Event Operations`, `Real‑Time Intelligence – Event Listener & Alert`.

## 2.6 OneLake (storage + transactions)

* **Classification:** transaction compute is **background**.
  ([OneLake compute & storage](https://learn.microsoft.com/fabric/onelake/onelake-capacity-consumption),
  [Fabric operations – OneLake](https://learn.microsoft.com/fabric/enterprise/fabric-operations))
* **Two cost components:**
  1. **Storage** — per‑GB‑per‑month (pay‑as‑you‑go), tier‑dependent (Hot > Cool > Cold).
  2. **Transactions** — per‑operation **CU(s)** for Read / Write / Iterative / Other, charged per 4 MB,
     per 10,000 operations; cheapest on Hot, most expensive on Cold.
     Cool/Cold add a **per‑GB data‑retrieval** CU fee and **early‑deletion penalties** (30‑day Cool / 90‑day Cold min retention).
     ([OneLake storage tiers](https://learn.microsoft.com/fabric/onelake/onelake-storage-tiers))
* **Meters:** `OneLake Read/Write/Iterative/Other Operations Capacity Usage CU` (+ `… via API`, `… BCDR`).
  As of May 2026 the Metrics App reports OneLake compute at the **workspace level** under a single *OneLake* item.

## 2.7 Copilot & AI

* **Classification:** **background** (consumption spread over 24 h, so prompts "stack" but don't spike).
  ([How Copilot works – cost](https://learn.microsoft.com/fabric/fundamentals/how-copilot-works))
* **Metered by tokens** (~1,000 tokens ≈ 750 words), at different input/output rates
  ([Copilot consumption](https://learn.microsoft.com/fabric/fundamentals/copilot-fabric-consumption)):

  | Token type | Rate |
  | --- | --- |
  | Input prompt | **100 CU‑seconds per 1,000 tokens** |
  | Output completion | **400 CU‑seconds per 1,000 tokens** |

  ```
  copilot_CU_seconds = (input_tokens/1000 × 100) + (output_tokens/1000 × 400)
  copilot_CU_hours   = copilot_CU_seconds / 3600
  ```
* Shown in the Metrics App under operation **`Copilot in Fabric`**. ML models use `ML Model Endpoint Capacity Usage CU`.

## 2.8 SQL database in Fabric

* **Classification:** **background** (`SQL database in Microsoft Fabric` meter).
  ([SQL DB usage reporting](https://learn.microsoft.com/fabric/database/sql/usage-reporting))
* **vCore ↔ CU conversion:** `1 CU = 0.383 DB vCores`, i.e. `1 DB vCore = 2.611 CU`. (F64 → 24.512 SQL vCores.)
* **Billing nuance:** compute billed per active second **plus a 15‑minute online keep‑alive** after activity;
  storage billed continuously. Cap peaks via a **max‑vCore** setting.

## 2.9 Cosmos DB in Fabric

* **Classification:** **background** (bills the **highest autoscale throughput consumed per hour**, to avoid
  any single hour triggering throttling). ([Cosmos DB billing](https://learn.microsoft.com/fabric/database/cosmos-db/billing-usage))
* **RU/s ↔ CU/hr conversion:**

  ```
  100 RU/s = 0.067 CU/hr      1 RU/s = 0.00067 CU/hr      1 CU/hr ≈ 1,500 RU/s
  ```
* Containers autoscale 10%→100% of provisioned max (default max 5,000 RU/s; 1,000–50,000 via SDK).

## 2.10 Fabric Apps / SQL‑in‑app / GraphQL

* Fabric apps consume capacity for their **SQL database**, **GraphQL API**, and **OneLake** operations;
  several platform features add no separate charge. ([Fabric Apps pricing](https://learn.microsoft.com/fabric/apps/pricing))

---

## Appendix A — End‑to‑end calculation recipe

1. **Read `Total CU(s)`** for the operation/item from the Metrics App (Timepoint → Item Detail page).
   This already includes duration + bursting — don't multiply by runtime again.
2. **Convert:** `effective_CU_hours = Total_CU(s) / 3600`.
3. **Cost:** `effective_CU_hours × price_per_CU_hour` (regional PAYG, e.g. `$0.18`; reservation is cheaper; overage = 3×).
4. **Per‑timepoint load (for throttling analysis):**
   * Interactive → `Total_CU(s) / N`, `N ∈ [10,128]`.
   * Background → `Total_CU(s) / 2880`.
5. **Utilization %:** `per_timepoint_CU(s) / (base_CU × 30)`.
6. **Compare to thresholds:** 10‑min (interactive delay) / 60‑min (interactive rejection) / 24‑h (background rejection).

## Appendix B — Metrics App glossary

| Field | Meaning |
| --- | --- |
| **Total CU(s)** | Total CU‑seconds the operation consumed (duration + burst already included). |
| **Timepoint CU(s)** | CU‑seconds attributed to *this* 30‑s timepoint after smoothing. |
| **Throttling (s)** | Seconds of throttle applied to the operation due to prior‑timepoint overload. |
| **% of base** | `smoothed_CUs / base_CUs` for the timepoint. |
| **Duration (s)** | Wall‑clock runtime. Does **not** affect throttling and is **not** used in cost math. |
| **Smoothing start/end** | When an (interactive) operation's smoothing window begins/ends. |
| **Billing type** | Billable vs Non‑billable. CU(s) of **failed** ops still count toward overload. |

## Appendix C — Source documents (Microsoft Learn)

* Throttling policy / smoothing / bursting — https://learn.microsoft.com/fabric/enterprise/throttling
* Metrics app calculations — https://learn.microsoft.com/fabric/enterprise/metrics-app-calculations
* Compute page — https://learn.microsoft.com/fabric/enterprise/metrics-app-compute-page
* Timepoint / item‑detail / summary pages — https://learn.microsoft.com/fabric/enterprise/metrics-app-timepoint-page
* Fabric operations (interactive vs background, per‑experience) — https://learn.microsoft.com/fabric/enterprise/fabric-operations
* Licenses / SKU table — https://learn.microsoft.com/fabric/enterprise/licenses
* Plan your capacity size — https://learn.microsoft.com/fabric/enterprise/plan-capacity
* Capacity overage — https://learn.microsoft.com/fabric/enterprise/capacity-overage-overview · https://learn.microsoft.com/fabric/enterprise/enable-capacity-overage
* Surge protection — https://learn.microsoft.com/fabric/enterprise/surge-protection
* Understand your Azure bill — https://learn.microsoft.com/fabric/enterprise/azure-billing
* Warehouse smoothing/throttling & usage — https://learn.microsoft.com/fabric/data-warehouse/compute-capacity-smoothing-throttling · https://learn.microsoft.com/fabric/data-warehouse/usage-reporting
* Spark billing & concurrency — https://learn.microsoft.com/fabric/data-engineering/billing-capacity-management-for-spark · https://learn.microsoft.com/fabric/data-engineering/spark-job-concurrency-and-queueing
* Data Factory pricing — https://learn.microsoft.com/fabric/data-factory/pricing-overview · /pricing-pipelines · /pricing-scenario-copy-job
* OneLake consumption / tiers — https://learn.microsoft.com/fabric/onelake/onelake-capacity-consumption · /onelake-storage-tiers
* Copilot consumption — https://learn.microsoft.com/fabric/fundamentals/copilot-fabric-consumption · /how-copilot-works
* SQL database / Cosmos DB billing — https://learn.microsoft.com/fabric/database/sql/usage-reporting · https://learn.microsoft.com/fabric/database/cosmos-db/billing-usage
* Activator / RTI — https://learn.microsoft.com/fabric/real-time-intelligence/data-activator/activator-capacity-usage
* Paginated reports capacity planning — https://learn.microsoft.com/power-bi/paginated-reports/paginated-capacity-planning

---

*Compiled from Microsoft Learn via the Microsoft Learn MCP. Pricing figures are illustrative; confirm
regional rates on the [Fabric pricing page](https://azure.microsoft.com/pricing/details/microsoft-fabric/).*
