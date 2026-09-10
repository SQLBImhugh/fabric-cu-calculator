"""Drop-in replacement for fabric.evaluate_dax in FUAM's capacity-metrics notebooks.

Why
---
FUAM reads the Capacity Metrics app with `fabric.evaluate_dax`, which Microsoft
documents as retrieving data over XMLA:

    "Data is retrieved using XMLA and therefore requires at least XMLA read-only
     to be enabled"
    -- learn.microsoft.com/fabric/data-science/read-write-power-bi-python

XMLA endpoints exist only on P/F capacities, which is why
`How_to_deploy_FUAM.md` requires:

    "Fabric Capacity Metrics app (workspace) with attached P or F-capacity with
     enabled XMLA endpoint (at least 'Read')"

The Power BI REST `executeQueries` endpoint runs the same DAX with no XMLA
endpoint and no capacity on the hosting workspace, so a Pro-hosted Capacity
Metrics app becomes readable.

Compatibility
-------------
FUAM renames the returned frame positionally, e.g.

    capacity_df = fabric.evaluate_dax(...)
    capacity_df.columns = ['CapacityId', 'TimePoint', ...]

so a replacement only has to preserve column ORDER and COUNT, not names.
Verified against a live Capacity Metrics app (v53) -- see README.md beside this
file for the measured evidence.

Usage
-----
Define this once per notebook, then replace

    fabric.evaluate_dax(workspace=metric_workspace, dataset=metric_dataset, dax_string=q)

with

    evaluate_dax_compat(metric_workspace, metric_dataset, q)
"""

import time

import pandas as pd
import sempy.fabric as fabric


def _evaluate_dax_rest(workspace: str, dataset: str, dax_string: str,
                       max_retries: int = 5) -> pd.DataFrame:
    """Execute a DAX query through the Power BI REST executeQueries endpoint.

    Requires the 'Dataset Execute Queries REST API' tenant setting and Build
    permission on the semantic model. Needs no XMLA endpoint, so it works when
    the Capacity Metrics app sits in a Pro workspace.

    Limits (per Microsoft): 100,000 rows or 1,000,000 values per query, 15 MB
    per response, and 120 requests per minute per user or service principal.
    FUAM issues one query per capacity per day, which keeps results far below
    the row and value caps; the request cap is handled by the retry below.
    """
    client = fabric.FabricRestClient()
    path = f"/v1.0/myorg/groups/{workspace}/datasets/{dataset}/executeQueries"
    payload = {
        "queries": [{"query": dax_string}],
        "serializerSettings": {"includeNulls": True},
    }

    for attempt in range(max_retries):
        response = client.post(path, json=payload)
        if response.status_code == 200:
            break
        # 429 Too Many Requests / 503 Service Unavailable -> back off and retry
        if response.status_code in (429, 503) and attempt < max_retries - 1:
            time.sleep(20 * (attempt + 1))
            continue
        response.raise_for_status()
    else:
        raise RuntimeError(
            f"executeQueries did not succeed after {max_retries} attempts")

    rows = response.json()["results"][0]["tables"][0]["rows"]
    if not rows:
        return pd.DataFrame()

    # Preserve the column order produced by the DAX projection. FUAM relies on
    # position, so this must not be sorted or otherwise reordered.
    return pd.DataFrame(rows, columns=list(rows[0].keys()))


def evaluate_dax_compat(workspace: str, dataset: str, dax_string: str) -> pd.DataFrame:
    """Run a DAX query, preferring XMLA and falling back to REST.

    Keeps the existing XMLA path as-is so behaviour is unchanged wherever it
    already works, and only falls back when XMLA is unavailable -- for example
    a Capacity Metrics app hosted in a Pro workspace, or a capacity whose XMLA
    endpoint is off.
    """
    try:
        return fabric.evaluate_dax(
            workspace=workspace, dataset=dataset, dax_string=dax_string)
    except Exception as xmla_error:
        print(f"INFO: XMLA query failed ({type(xmla_error).__name__}); "
              f"retrying over the REST executeQueries endpoint.")
        return _evaluate_dax_rest(workspace, dataset, dax_string)
