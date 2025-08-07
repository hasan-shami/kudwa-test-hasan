from data_manager import parse_financial_file
import pandas as pd



df1 = parse_financial_file('local/data_set_1.json')
df2 = parse_financial_file('local/data_set_2.json')
df = pd.concat([df1, df2], ignore_index=True)