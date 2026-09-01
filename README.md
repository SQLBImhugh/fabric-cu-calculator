# Microsoft Fabric CU Calculator

A lightweight, **static single-page web app** that estimates Microsoft Fabric **Capacity Unit (CU)**
consumption, CU-hours, cost, smoothing load, and throttling exposure for both **interactive** and
**background** operations. All math runs client-side — no backend, no build step.

🔗 **Live app:** https://sqlbimhugh.github.io/fabric-cu-calculator/

## Features

- **SKU picker** (F2 → F2048) with live base-CU / CU-hours-per-day / CU(s)-per-timepoint stats
- **Interactive vs Background** smoothing models (5–64 min vs 24 h / 2,880 timepoints)
- **Estimators:** direct CU-seconds / CU-hours, Copilot (tokens), Cosmos DB (RU/s), Spark (CU × time), Data Factory pipeline
- **Outputs:** effective CU-hours, cost, per-timepoint load %, throttling status, and a step-by-step formula trace
- **Region-aware pricing** (64 Azure regions, USD/CU-hour, overridable)
- **Glossary** of 18 Fabric CU terms with inline tooltips linked to Microsoft Learn
- Dark UI, SKU reference table

## Formulas

```
effective_CU_hours   = Total_CU(s) / 3600
cost                 = effective_CU_hours × price_per_CU_hour
interactive per-tp   = Total_CU(s) / N         (N = 10…128 timepoints = 5…64 min)
background  per-tp   = Total_CU(s) / 2880       (24 h)
load %               = per_timepoint_CU(s) / (base_CU × 30)
```

See [`Fabric-CU-Consumption-Reference.md`](./Fabric-CU-Consumption-Reference.md) for the full concept +
formula reference, sourced from Microsoft Learn.

## Run locally

Just open `index.html` in a browser — it's fully self-contained.

## Hosting on GitHub Pages

This repo is served from the `main` branch root. To replicate:
`Settings → Pages → Build and deployment → Source: Deploy from a branch → main / (root)`.

---

*Pricing figures are illustrative; confirm regional rates on the
[Fabric pricing page](https://azure.microsoft.com/pricing/details/microsoft-fabric/).*
