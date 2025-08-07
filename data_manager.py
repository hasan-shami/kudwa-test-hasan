import pandas as pd
import json

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