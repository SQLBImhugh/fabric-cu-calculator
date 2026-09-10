# Draft issue for microsoft/fabric-toolbox — NOT POSTED

Scope: the documentation contradiction only. No code change proposed.

---

**Title:**

`FUAM docs: Capacity Metrics app prerequisite (P/F capacity + XMLA) contradicts Microsoft's own install guidance (Pro workspace)`

**Labels:** `documentation`, `fuam`

---

**Body:**

## Summary

FUAM's deployment prerequisites and Microsoft's install guidance for the Capacity Metrics app give
opposite instructions about where that app's workspace should live. A user who follows the Microsoft
guidance cannot satisfy the FUAM prerequisite.

## The two statements

Microsoft Learn, [Install the Microsoft Fabric capacity metrics app](https://learn.microsoft.com/fabric/enterprise/metrics-app-install)
(page updated 2026-02-17):

> To avoid throttling due to capacity overutilization, install the app in a workspace with a **Pro license**.

and, under Prerequisites:

> A Power BI license (Pro, Premium Per User, or a Power BI individual trial) to access and use the app

FUAM, [`how-to/How_to_deploy_FUAM.md`](https://github.com/microsoft/fabric-toolbox/blob/main/monitoring/fabric-unified-admin-monitoring/how-to/How_to_deploy_FUAM.md):

> Fabric Capacity Metrics app (workspace) **with attached P or F-capacity** with **enabled XMLA endpoint** (at least 'Read')

and:

> A Power BI or Fabric capacity (PPU, Pro 'shared' workspaces are not supported)

## Question

Which is authoritative for the **Capacity Metrics app's** workspace specifically?

I ask because the FUAM line is ambiguous between two readings, and they have different consequences
for anyone deploying:

1. **FUAM as a whole** needs a P/F capacity — which is clearly true, since FUAM's own notebooks,
   pipelines, Lakehouse and Direct Lake models are all capacity workloads. Nobody disputes this.
2. **The Metrics App's workspace** additionally needs its own P/F capacity with XMLA enabled — which
   is what the prerequisite literally says, and which conflicts with the Learn guidance above.

If the intent is (1), the prerequisite could be reworded to scope the capacity requirement to the
FUAM workspace and say what is actually required of the Metrics App workspace. If the intent is (2),
it would help to say why, since it means deliberately ignoring Microsoft's throttling advice for that
app.

## Note for anyone who reaches for `executeQueries` as the workaround

The obvious-looking fix is to swap `fabric.evaluate_dax` (XMLA) for the Power BI REST
`executeQueries` endpoint, which is documented as working on Pro. I built and validated that change
before concluding it is a bad idea, and the reason is worth recording here so nobody else repeats it:

**`executeQueries` truncates silently.** It does not error, warn, or flag the result.

| Requested | Returned | HTTP status |
|---|---|---|
| 150,000 rows × 1 column | **exactly 100,000 rows** | 200 |
| 95,000 rows × 12 columns (1,140,000 values) | **83,333 rows** (999,996 values) | 200 |
| 95,000 rows × 10 columns (950,000 values) | 95,000 rows | 200 |

XMLA has no equivalent cap. For the `capacity_metrics_by_item_by_operation_by_day` query — the only
one of the three that scales with tenant size — the value cap binds at roughly 58,800 rows at 17
columns. That is far above what a small tenant produces, but it is reachable on a large estate, and
the failure mode is a silently short day of capacity metrics rather than an error. Any move to that
endpoint would need a guard that raises when a result lands exactly on either limit.

(Aside, offered only so the observation is on record and not as an argument: in one tenant,
`fabric.evaluate_dax` did successfully return 150,000 rows from a model in a workspace the admin API
reported as `isOnDedicatedCapacity: False`. XMLA read on shared capacity is undocumented — the Power
BI service description feature table still lists *"XMLA endpoint read/write connectivity — Power BI
Pro: No"* — so I would not treat that as something FUAM should rely on. It does suggest the
prerequisite may be stricter than the code strictly requires, which is part of why the wording
question above is worth settling.)

## Environment

- FUAM deployed from `main`, Capacity Metrics app v53
- Measurements taken 2026-09-10

---

## Notes to self (not part of the issue body)

- Both quotes verified verbatim from primary sources on 2026-09-10: the Learn page was fetched
  directly, and `How_to_deploy_FUAM.md` was read from `raw.githubusercontent.com` at `main`.
- Related existing issues, checked but deliberately **not** referenced in the body because they are
  version-compatibility problems rather than transport problems: #372, #417, #418, #436.
- #640 (FUAM high CU consumption) deliberately left alone — different measurement, wrong thread.
- Full investigation trail, including the withdrawn transport swap and the validated helper, is in
  `docs/fuam-executequeries/README.md` in this repo.
