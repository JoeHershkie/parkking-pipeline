import pandas as pd
import ast
import sys

# 1. Load the massive CSV into memory
df = pd.read_csv('toronto_raw_parking_dump.csv')

# 2. Fix the filter: Use .str.contains() to catch things like "Schedule 13: No Parking"
# We also ignore capitalization (case=False) just to be safe.
active_rules = df[
    (df['Latest_Action'] != 'Repealed') &
    (df['scheduleName'].str.contains('No Parking', case=False, na=False))
].copy()

# SAFETY CHECK: Stop the script if the filters removed everything
if active_rules.empty:
    print("Wait! The filters removed all rows. Check the exact text in your CSV columns.")
    sys.exit()

# 3. Create a helper function to unpack the nested 'ByLaw_Table' data
def extract_bylaw_data(cell_data):
    try:
        data_list = ast.literal_eval(cell_data)
        return {item['key']: item['value'] for item in data_list if 'key' in item and 'value' in item}
    except:
        return {}

print(f"Success! Found {len(active_rules)} active parking rules.")
print("Unpacking nested data... (this might take a few seconds)")

# 4. A safer, faster way to turn the dictionaries into a DataFrame
unpacked_list = active_rules['ByLaw_Table'].apply(extract_bylaw_data).tolist()
unpacked_data = pd.DataFrame(unpacked_list, index=active_rules.index)

# 5. Filter by the specific columns we actually want
columns_to_keep = ['Highway', 'Side', 'Between', 'Prohibited Times and/or Days']

# (We use .reindex instead of just brackets to avoid errors if a column happens to be entirely missing)
clean_data = unpacked_data.reindex(columns=columns_to_keep)

# 6. Save to a brand new, tiny CSV file
clean_data.to_csv('clean_parking_targets.csv', index=False)
print("Done! Clean CSV created.")