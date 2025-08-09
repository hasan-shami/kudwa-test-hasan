import pandas as pd
import json
import re
from datetime import datetime

def detect_file_type(json_data):
    if "data" in json_data and isinstance(json_data["data"], dict):
        if "Header" in json_data["data"]:
            return "quickbooks"
    if "data" in json_data and isinstance(json_data["data"], list):
        if "rootfi_id" in json_data["data"][0]:
            return "rootfi"
    return "unknown"


def _parse_quickbooks(data):
    # Extract period metadata
    cols = data['Columns']['Column']
    periods = []
    for col in cols:
        md = {m['Name']: m['Value'] for m in col.get('MetaData', [])}
        periods.append({
            'key': md.get('ColKey'),
            'start': md.get('StartDate'),
            'end': md.get('EndDate')
        })

    # Recursively traverse rows
    entries = []
    def recurse_rows(section):
        for row in section.get('Rows', {}).get('Row', []):
            rtype = row.get('type')
            if rtype == 'Data':
                name_cell = row['ColData'][0]
                base = {
                    'account': name_cell['value'],
                    'account_id': name_cell.get('id')
                }
                for i, cell in enumerate(row['ColData'][1:len(periods)+1]):
                    val = cell.get('value')
                    entries.append({
                        **base,
                        'period_key': periods[i]['key'],
                        'period_start': periods[i]['start'],
                        'period_end': periods[i]['end'],
                        'value': float(val) if val not in (None, '') else None
                    })
            elif rtype == 'Section':
                recurse_rows(row)
    recurse_rows(data)

    return pd.DataFrame(entries)


def _parse_rootfi(records):
    # Flatten nested line items with category context
    entries = []
    def dfs(items, category, period_start, period_end, path):
        for item in items:
            base = {
                'category': category,
                'subcategories': ' > '.join(path) if path else None,
                'name': item.get('name'),
                'account_id': item.get('account_id'),
                'period_start': period_start,
                'period_end': period_end,
                'value': item.get('value')
            }
            entries.append(base)
            # Recurse into deeper line_items
            if item.get('line_items'):
                dfs(item['line_items'], category, period_start, period_end, path + [item.get('name')])

    for rec in records:
        ps = rec.get('period_start')
        pe = rec.get('period_end')
        for cat_key in ['revenue', 'cost_of_goods_sold', 'operating_expenses',
                        'non_operating_revenue', 'non_operating_expenses']:
            dfs(rec.get(cat_key, []), cat_key, ps, pe, [])

    return pd.DataFrame(entries)


def parse_financial_file(source):
    """
    Dynamically parses a financial JSON file (QuickBooks-style or Rootfi-style) without hardcoding keys.

    Args:
        source (str or dict): Path to JSON file or already-loaded JSON dict.

    Returns:
        pandas.DataFrame: A normalized table of financial entries.
    """
    # Load JSON
    if isinstance(source, str):
        with open(source, 'r', encoding='utf-8') as f:
            data = json.load(f)
    else:
        data = source

    # Detect format
    root = data.get('data')
    if isinstance(root, dict) and 'Header' in root:
        return _parse_quickbooks(root)
    elif isinstance(root, list):
        return _parse_rootfi(root)
    else:
        raise ValueError("Unrecognized financial data format")

def map_account_to_category(acct):
    category_df = pd.read_csv('local/df_cat.csv')
    category_map = category_df.to_dict(orient='list')

    # 1) guard against nulls
    if not isinstance(acct, str):
        return "unknown"
    # 2) find the first prefix that matches
    for cat, patterns in category_map.items():
        for pat in patterns:
            if acct.startswith(f"{pat}_"):
                return cat
    # 3) fallback: anything with "_expense"
    if "_expense" in acct:
        return "operating_expenses"
    return "unknown"

# which sections to walk and their natural sign (expenses negative if you want signed sums)
SECTION_SPECS = {
    "revenue": +1,
    "cost_of_goods_sold": -1,
    "operating_expenses": -1,
    "non_operating_revenue": +1,
    "non_operating_expenses": -1,
    "taxes": -1,
}

def _to_float(x):
    try:
        return float(x) if x is not None else 0.0
    except Exception:
        return 0.0

def _parse_dates(rec):
    # prefer explicit period_start/period_end
    start = rec.get("period_start")
    end   = rec.get("period_end")
    if not (start and end):
        pid = rec.get("platform_id") or ""
        m = re.match(r"(\d{4}-\d{2}-\d{2})_(\d{4}-\d{2}-\d{2})", pid)
        if m:
            start, end = m.group(1), m.group(2)
    return start, end

def _record_meta(rec):
    # Bring forward scalar metadata automatically (no hardcoding)
    keep = {}
    for k, v in rec.items():
        if isinstance(v, (str, int, float, bool)) or v is None:
            keep[k] = v
    # Normalize dates
    ps, pe = _parse_dates(rec)
    keep["period_start"] = ps
    keep["period_end"]   = pe
    # Helpful normalized columns
    if pe:
        keep["period_end_date"] = pe
        try:
            dt = datetime.fromisoformat(pe)
            keep["period_year"]  = dt.year
            keep["period_month"] = dt.month
        except Exception:
            pass
    return keep

def _node_ids_map(node):
    # grab every id-ish key: 'id' or '*_id' (case-insensitive)
    ids = {}
    for k, v in node.items():
        kl = k.lower()
        if kl == "id" or kl.endswith("_id"):
            if v not in (None, ""):
                ids[k] = str(v)
    return ids

def _primary_element_id(ids_map):
    # sensible priority without hardcoding to a single field
    for pref in ("account_id", "accountId", "element_id", "id"):
        if pref in ids_map and ids_map[pref]:
            return ids_map[pref]
    # otherwise first available
    return next(iter(ids_map.values()), None)

def flatten_rootfi(records: list, value_mode: str = "leaf") -> pd.DataFrame:
    """
    Flatten Rootfi P&L-style JSON with full metadata & ids.

    value_mode:
      'raw'  -> use node's reported value
      'leaf' -> use only leaves (parents contribute 0)
      'net'  -> parent contribution = reported - sum(children reported)
    """
    assert value_mode in {"raw", "leaf", "net"}
    rows = []

    def walk(section, item, sign, parent_uid, path, depth, period_meta):
        name = (item.get("name") or "").strip()
        children = item.get("line_items") or []
        has_children = len(children) > 0

        # Values
        reported = _to_float(item.get("value", 0))
        child_sum = sum(_to_float(ch.get("value", 0)) for ch in children)
        net_contrib = reported - child_sum
        if value_mode == "raw":
            value_use = reported
        elif value_mode == "leaf":
            value_use = reported if not has_children else 0.0
        else:  # net
            value_use = net_contrib

        # IDs (dynamic)
        ids_map = _node_ids_map(item)
        element_id = _primary_element_id(ids_map)

        # Node uid: prefer element_id; else section+path+name
        node_uid = element_id or f"{section}:{path}/{name}".strip("/")

        row = {
            **period_meta,                     # all record-level meta incl. dates
            "section": section,
            "name": name,
            "node_uid": node_uid,
            "parent_uid": parent_uid,
            "path": f"{path}/{name}" if path else name,
            "depth": depth,
            "has_children": has_children,
            "children_reported_sum": child_sum,
            "agg_matches_children": abs(net_contrib) < 1e-9,
            "reported_value": reported,
            "value_use": value_use,
            "signed_value_use": value_use * sign,
            "element_id": element_id,
            "node_ids": ids_map,              # keep the full id dict too
        }
        # also break out each id key as its own column (DataFrame will align)
        for k, v in ids_map.items():
            row[f"id__{k}"] = v

        rows.append(row)

        for ch in children:
            walk(section, ch, sign, node_uid, row["path"], depth + 1, period_meta)

    for rec in records:
        period_meta = _record_meta(rec)
        for section, sign in SECTION_SPECS.items():
            for top in rec.get(section) or []:
                walk(section, top, sign, parent_uid=None, path="", depth=0, period_meta=period_meta)

    df = pd.DataFrame(rows)
    # Optional: make parsed datetime columns
    for c in ("period_start", "period_end", "period_end_date"):
        if c in df.columns:
            df[c] = pd.to_datetime(df[c], errors="coerce")
    return df


def process_data(df1):
    df1['value'] = df1['value'].abs()
    df1 = df1.dropna(subset='period_end')
    df1['period_start'] = pd.to_datetime(df1['period_start'])
    df1['period_end'] = pd.to_datetime(df1['period_end'])

    # canonical period month
    df1["period_month"] = df1["period_start"].dt.to_period("M").astype(str)  # e.g., "2020-01"
    df1 = df1.drop('period_key', axis=1)
    # handy dims (optional)
    df1["year"] = df1["period_start"].dt.year.astype("Int64")
    df1["month"] = df1["period_start"].dt.month.astype("Int64")
    df1["quarter"] = df1["period_start"].dt.quarter.astype("Int64")

    df1['account'] = df1['account'].str.replace(r'_\d+$', '', regex=True)

    return df1

def write_to_sql(df):
    import sqlite3
    con = sqlite3.connect("../data.db")

    df.to_sql("data", con, if_exists="replace",index=False)

def read_from_sqlite(query):
    import sqlite3
    con = sqlite3.connect("../data.db")

    df_from_db = pd.read_sql(query, con)

    return df_from_db
