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

import re
import time

import pandas as pd
import sempy.fabric as fabric

# executeQueries caps a result at 100,000 rows OR 1,000,000 values, whichever
# binds first -- and it enforces them by SILENTLY TRUNCATING, returning HTTP 200
# with no warning. Measured 2026-09-10: a request for 150,000 rows returned
# exactly 100,000, and a request for 1,140,000 values returned 999,996. XMLA has
# no such cap, so a fallback must detect truncation or it will quietly write
# incomplete capacity metrics.
_MAX_ROWS = 100_000
_MAX_VALUES = 1_000_000

# A real date or time carries one of these; a key such as '20260908' does not.
_DATE_SEPARATOR = re.compile(r"[-/:]")


def _evaluate_dax_rest(workspace: str, dataset: str, dax_string: str,
                       max_retries: int = 5) -> pd.DataFrame:
    """Execute a DAX query through the Power BI REST executeQueries endpoint.

    Requires the 'Dataset Execute Queries REST API' tenant setting and Build
    permission on the semantic model. Needs no XMLA endpoint, so it works when
    the Capacity Metrics app sits in a Pro workspace.

    Raises if the response looks truncated, because a short result is worse than
    a failure: it would be written to the lakehouse as though it were complete.
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
    columns = list(rows[0].keys())
    df = pd.DataFrame(rows, columns=columns)

    # Truncation guard. The service returns exactly the cap, so landing on it is
    # indistinguishable from a genuine result of that size -- treat both as
    # suspect rather than risk writing a partial day.
    value_cap_rows = _MAX_VALUES // max(len(columns), 1)
    if len(df) in (_MAX_ROWS, value_cap_rows):
        raise RuntimeError(
            f"executeQueries returned {len(df):,} rows x {len(columns)} columns, "
            f"which is exactly the service limit (row cap {_MAX_ROWS:,}, "
            f"value cap {value_cap_rows:,} rows at this width). The result is "
            f"probably truncated. Narrow the query -- for example by splitting "
            f"the day or filtering by item kind -- or use the XMLA path, which "
            f"has no such cap.")

    return _coerce_datetime_columns(df)


def _coerce_datetime_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Restore datetime dtypes that the JSON round-trip flattened to strings.

    XMLA returns date columns as datetime64[ns]; the REST endpoint returns them
    as strings (in this tenant as '9/8/2026 12:00:00 AM'). FUAM feeds the frame
    straight into spark.createDataFrame and appends to a Delta table, so leaving
    them as strings would write a conflicting column type -- TimePoint and Date
    in particular.

    A column is converted only when every non-null value parses AND contains a
    date or time separator. That second test matters: FUAM's DateKey column is
    the string '20260908', which pandas will happily parse as a date, but which
    the Metrics App returns as a string over XMLA too. Converting it would break
    the Delta append with DELTA_FAILED_TO_MERGE_FIELDS. GUID columns
    (CapacityId, ItemId, WorkspaceId) fail the all-values-parse test and are
    likewise left alone.
    """
    for column in df.columns:
        if df[column].dtype != object:
            continue
        non_null = df[column].dropna()
        if non_null.empty:
            continue
        # a bare digit run such as '20260908' is a key, not a date
        if not non_null.astype(str).str.contains(_DATE_SEPARATOR).all():
            continue
        parsed = pd.to_datetime(df[column], errors="coerce")
        if parsed.notna().sum() == df[column].notna().sum():
            df[column] = parsed
    return df


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
