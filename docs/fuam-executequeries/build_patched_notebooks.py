"""Produce the patched FUAM notebooks for the pull request.

For each of the three capacity-metrics notebooks:
  1. insert a new code cell defining the XMLA-with-REST-fallback helper
  2. repoint every fabric.evaluate_dax call at that helper

The DAX strings, version probing, column renames and downstream writes are all
left untouched.
"""
import base64, copy, io, json, os, re, subprocess

REPO = "microsoft/fabric-toolbox"
BASE = "monitoring/fabric-unified-admin-monitoring/src"
NBS = [
    "01_Transfer_CapacityMetricData_Timepoints_Unit",
    "02_Transfer_CapacityMetricData_ItemKind_Unit",
    "03_Transfer_CapacityMetricData_ItemOperation_Unit",
]
OUT = r"D:\Copilot\CUCalculator\_pr\src"
os.makedirs(OUT, exist_ok=True)

HELPER_MD = """#### Capacity Metrics connection helper

`fabric.evaluate_dax` retrieves data over XMLA, which requires the Capacity Metrics app to sit on a P or F capacity with the XMLA endpoint enabled. The helper below keeps that as the primary path and falls back to the Power BI REST `executeQueries` endpoint when XMLA is unavailable, so a Capacity Metrics app hosted in a Pro workspace can still be read.
"""

HELPER_CODE = '''import re
import time

import pandas as pd

# executeQueries caps a result at 100,000 rows OR 1,000,000 values, whichever
# binds first, and enforces them by silently truncating: it returns HTTP 200
# with no warning. XMLA has no such cap, so the fallback must detect truncation
# rather than write an incomplete day of capacity metrics.
_MAX_ROWS = 100_000
_MAX_VALUES = 1_000_000

# A real date or time carries one of these; a key such as '20260908' does not.
_DATE_SEPARATOR = re.compile(r"[-/:]")


def _coerce_datetime_columns(df):
    """Restore datetime dtypes that the JSON round-trip flattened to strings.

    XMLA returns date columns as datetime64[ns]; executeQueries returns them as
    strings. These frames are handed to spark.createDataFrame and appended to a
    Delta table, so leaving them as strings would write a conflicting column
    type.

    A column is converted only when every non-null value parses AND contains a
    date or time separator. The separator test matters: DateKey is the string
    '20260908', which pandas parses happily as a date but which the Capacity
    Metrics app returns as a string over XMLA too -- converting it breaks the
    Delta append with DELTA_FAILED_TO_MERGE_FIELDS. GUID columns fail the
    all-values-parse test and are left alone.
    """
    for column in df.columns:
        if df[column].dtype != object:
            continue
        non_null = df[column].dropna()
        if non_null.empty:
            continue
        if not non_null.astype(str).str.contains(_DATE_SEPARATOR).all():
            continue
        parsed = pd.to_datetime(df[column], errors="coerce")
        if parsed.notna().sum() == df[column].notna().sum():
            df[column] = parsed
    return df


def _evaluate_dax_rest(workspace, dataset, dax_string, max_retries=5):
    """Execute a DAX query through the Power BI REST executeQueries endpoint.

    Requires the 'Dataset Execute Queries REST API' tenant setting and Build
    permission on the semantic model, but no XMLA endpoint -- so it works when
    the Capacity Metrics app sits in a Pro workspace.
    """
    client = fabric.FabricRestClient()
    path = f"/v1.0/myorg/groups/{workspace}/datasets/{dataset}/executeQueries"
    payload = {"queries": [{"query": dax_string}],
               "serializerSettings": {"includeNulls": True}}

    for attempt in range(max_retries):
        response = client.post(path, json=payload)
        if response.status_code == 200:
            break
        # executeQueries allows 120 requests per minute per identity
        if response.status_code in (429, 503) and attempt < max_retries - 1:
            time.sleep(20 * (attempt + 1))
            continue
        response.raise_for_status()
    else:
        raise RuntimeError(f"executeQueries did not succeed after {max_retries} attempts")

    rows = response.json()["results"][0]["tables"][0]["rows"]
    if not rows:
        return pd.DataFrame()

    # Preserve the column order the DAX projection produced: the notebooks
    # rename columns positionally.
    columns = list(rows[0].keys())
    df = pd.DataFrame(rows, columns=columns)

    value_cap_rows = _MAX_VALUES // max(len(columns), 1)
    if len(df) in (_MAX_ROWS, value_cap_rows):
        raise RuntimeError(
            f"executeQueries returned {len(df):,} rows x {len(columns)} columns, "
            f"exactly the service limit (row cap {_MAX_ROWS:,}, value cap "
            f"{value_cap_rows:,} rows at this width), so the result is probably "
            f"truncated. Narrow the query or enable the XMLA endpoint, which has "
            f"no such cap.")

    return _coerce_datetime_columns(df)


def evaluate_dax_compat(workspace, dataset, dax_string):
    """Run a DAX query, preferring XMLA and falling back to REST."""
    try:
        return fabric.evaluate_dax(workspace=workspace, dataset=dataset,
                                   dax_string=dax_string)
    except Exception as xmla_error:
        print(f"INFO: XMLA query failed ({type(xmla_error).__name__}); "
              f"retrying over the REST executeQueries endpoint.")
        return _evaluate_dax_rest(workspace, dataset, dax_string)
'''


def gh_file(path):
    out = subprocess.run(["gh", "api", f"repos/{REPO}/contents/{path}", "--jq", ".content"],
                         capture_output=True, text=True, shell=True)
    if out.returncode != 0:
        raise RuntimeError(out.stderr[:300])
    return base64.b64decode(re.sub(r"\s", "", out.stdout)).decode("utf-8")


for nb in NBS:
    raw = gh_file(f"{BASE}/{nb}.Notebook/notebook-content.ipynb")
    doc = json.loads(raw)

    before = sum(len(re.findall(r"fabric\.evaluate_dax\(", "".join(c["source"])))
                 for c in doc["cells"])

    # 1. repoint the calls
    for cell in doc["cells"]:
        cell["source"] = [s.replace("fabric.evaluate_dax(", "evaluate_dax_compat(")
                          for s in cell["source"]]

    after = sum(len(re.findall(r"fabric\.evaluate_dax\(", "".join(c["source"])))
                for c in doc["cells"])
    compat = sum(len(re.findall(r"evaluate_dax_compat\(", "".join(c["source"])))
                 for c in doc["cells"])

    # 2. insert the helper: a markdown cell plus a code cell, matching the
    #    structure and metadata of the notebook's existing cells
    md_template = next(c for c in doc["cells"] if c["cell_type"] == "markdown")
    code_template = next(c for c in doc["cells"] if c["cell_type"] == "code")
    existing_ids = {c.get("id") for c in doc["cells"]}

    def new_id(seed):
        candidate = seed
        n = 0
        while candidate in existing_ids:
            n += 1
            candidate = f"{seed}{n}"
        existing_ids.add(candidate)
        return candidate

    md_cell = copy.deepcopy(md_template)
    md_cell["id"] = new_id("fuam-rest-fallback-md")
    md_cell["source"] = [HELPER_MD]

    code_cell = copy.deepcopy(code_template)
    code_cell["id"] = new_id("fuam-rest-fallback-code")
    code_cell["source"] = [HELPER_CODE]
    if "outputs" in code_cell:
        code_cell["outputs"] = []
    if "execution_count" in code_cell:
        code_cell["execution_count"] = None

    insert_at = 1 if doc["cells"] and doc["cells"][0].get("cell_type") == "markdown" else 0
    doc["cells"][insert_at:insert_at] = [md_cell, code_cell]

    dest = os.path.join(OUT, f"{nb}.Notebook")
    os.makedirs(dest, exist_ok=True)
    io.open(os.path.join(dest, "notebook-content.ipynb"), "w", encoding="utf-8").write(
        json.dumps(doc, indent=1, ensure_ascii=False))

    ids = [c.get("id") for c in doc["cells"]]
    print(f"{nb}")
    print(f"   fabric.evaluate_dax  {before} -> {after}")
    print(f"   evaluate_dax_compat  {compat}")
    print(f"   cells {len(doc['cells'])}, unique ids: {len(set(ids)) == len(ids)}")
    print(f"   code cell metadata:  {json.dumps(code_cell.get('metadata', {}))[:90]}")
