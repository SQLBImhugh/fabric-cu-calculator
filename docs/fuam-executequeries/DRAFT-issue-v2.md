# Draft issue for microsoft/fabric-toolbox — NOT POSTED

Scope: documentation only. No code change proposed.

---

**Title:**

`FUAM runs fine with the Capacity Metrics app in a Pro workspace — deploy docs say it is unsupported`

**Labels:** `documentation`, `fuam`

---

**Body:**

## Summary

`How_to_deploy_FUAM.md` requires the Capacity Metrics app's workspace to have a P or F capacity with
XMLA enabled, and states that Pro shared workspaces are not supported. Microsoft's own install
guidance for that app recommends the opposite. I tested the configuration the docs call unsupported
and FUAM ran end to end without modification.

## The contradiction

Microsoft Learn, [Install the Microsoft Fabric capacity metrics app](https://learn.microsoft.com/fabric/enterprise/metrics-app-install)
(page updated 2026-02-17):

> To avoid throttling due to capacity overutilization, install the app in a workspace with a **Pro license**.

FUAM, [`how-to/How_to_deploy_FUAM.md`](https://github.com/microsoft/fabric-toolbox/blob/main/monitoring/fabric-unified-admin-monitoring/how-to/How_to_deploy_FUAM.md):

> Fabric Capacity Metrics app (workspace) **with attached P or F-capacity** with **enabled XMLA endpoint** (at least 'Read')

> A Power BI or Fabric capacity (PPU, Pro 'shared' workspaces are not supported)

## What I tested

A fresh Capacity Metrics app (v53), installed with default settings, in a workspace confirmed shared
before anything was run:

```
GET /v1.0/myorg/admin/groups?$filter=id eq '<workspace>'
  name                  : Microsoft Fabric Capacity Metrics
  isOnDedicatedCapacity : False
  capacityId            : (empty)
```

FUAM was pointed at it by changing only the `metric_workspace` and `metric_dataset` parameters. No
code was modified.

**Version detection.** This is the step that fails when XMLA is unavailable, producing the error
reported in #418 and #436 (*"Capacity Metrics data structure is not compatible or connection to
capacity metrics is not possible"*), because every probe runs through `fabric.evaluate_dax`:

| Probe | Result |
|---|---|
| v53 | succeeded |
| v47 | succeeded |
| v40 | succeeded |
| version selected | **v53** |

**Data query.** FUAM's v53 query shape (`MPARAMETER 'CapacitiesList'` plus the two `TREATAS`
filters), one capacity, one day, run from a Fabric notebook:

| Transport | Rows |
|---|--:|
| `fabric.evaluate_dax` (XMLA) | **2,880** |
| `POST .../executeQueries` (REST) | 2,880 |

**Full pipeline.** `Load_Capacity_Metrics_E2E` completed and merged into all three gold tables:

| Gold table | Delta operation | Rows after |
|---|---|--:|
| `capacity_metrics_by_timepoint` | MERGE | 81,656 |
| `capacity_metrics_by_item_kind_by_day` | MERGE | 1,457 |
| `capacity_metrics_by_item_by_operation_by_day` | MERGE | 8,472 |

## Suggested change

Scope the capacity requirement to FUAM's own workspace, which genuinely needs one, and soften the
Metrics App workspace line. Something like: the Metrics App workspace does not appear to require its
own capacity, though XMLA read on shared capacity is not documented by Microsoft, so P/F capacity
remains the supported configuration.

I would rather you chose the wording than propose a PR, since only a maintainer can decide whether to
describe a behaviour Microsoft has not documented.

## Two caveats I want to be explicit about

1. **This is undocumented behaviour, not a documented capability.** The Power BI service description
   feature table still lists *"XMLA endpoint read/write connectivity — Power BI Pro: No"*. I found no
   Learn page announcing a change. So the correct conclusion is "the prerequisite is stricter than the
   code requires", not "Pro is supported".
2. **One tenant, one app version (v53), a capacity-admin identity.** Others may see different
   behaviour.

## A note for anyone who reaches for `executeQueries`

The obvious-looking fix, if XMLA ever is blocked, is to swap `fabric.evaluate_dax` for the REST
`executeQueries` endpoint, which is documented as Pro-compatible. I built and validated that change
before establishing that it was unnecessary. It has a failure mode worth recording:

**`executeQueries` truncates silently.** No error, no warning, no flag in the response.

| Requested | Returned | HTTP status |
|---|---|---|
| 150,000 rows × 1 column | **exactly 100,000 rows** | 200 |
| 95,000 rows × 12 columns (1,140,000 values) | **83,333 rows** (999,996 values) | 200 |
| 95,000 rows × 10 columns (950,000 values) | 95,000 rows | 200 |

XMLA has no equivalent cap. For `capacity_metrics_by_item_by_operation_by_day` — the only one of the
three queries that scales with tenant size — the value cap binds at roughly 58,800 rows at 17 columns.
Reachable on a large estate, and the failure mode is a silently short day of capacity metrics rather
than an error. Any move to that endpoint would need a guard that raises when a result lands exactly on
either limit.

## Environment

- FUAM deployed from `main`; Capacity Metrics app v53
- Measured 2026-09-10

---

## Notes to self (not part of the issue body)

- All quotes verified verbatim from primary sources on 2026-09-10: the Learn page fetched directly,
  `How_to_deploy_FUAM.md` read from `raw.githubusercontent.com` at `main`.
- Trap that nearly produced the opposite conclusion: the `*_silver` staging tables read 0 rows after a
  successful run, because the notebooks end with `DELETE FROM <silver_table>` once the merge to gold
  succeeds. Combined with `notebookutils.notebook.exit()` making failures report `Completed`, the only
  reliable check is Delta history on the **gold** tables.
- #372 / #417 / #418 / #436 are version-compatibility problems, referenced only for the shared error
  string. #640 (high CU consumption) deliberately left alone — different measurement, wrong thread.
- Full investigation trail, including the withdrawn transport swap, is in
  `docs/fuam-executequeries/README.md` in this repo.
