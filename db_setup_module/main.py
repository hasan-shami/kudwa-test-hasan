from data_manager import parse_financial_file, map_account_to_category, flatten_rootfi
import pandas as pd
import json



df1 = parse_financial_file('local/data_set_1.json')
df2 = parse_financial_file('local/data_set_2.json')
with open('local/data_set_2.json') as f:
    payload = json.load(f)

records = payload["data"] if isinstance(payload, dict) and "data" in payload else payload

df_rootfi = flatten_rootfi(records, value_mode="leaf")
df1["category"] = df1["account"].apply(map_account_to_category) # for quickbooks files flow

df = pd.concat([df1, df2], ignore_index=True)