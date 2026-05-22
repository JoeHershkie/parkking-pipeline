import pandas as pd
import re

from paths import data_path

# 1. Load your clean data
df = pd.read_csv(data_path('clean_parking_targets.csv'))

# --- THE REGEX RULEBOOK ---

# Rule 1: The Perfect Offset (e.g., "St. Clair and a point 55 metres north")
regex_perfect_offset = re.compile(
    r'^(?P<start_intersection>.*?) and a point (?:approximately )?(?P<distance>\d+(?:\.\d+)?) metres (?P<direction>north|south|east|west)$',
    re.IGNORECASE
)

# Rule 2: Intersection to Offset (e.g., "Wychwood Ave and a point 61 metres west of Bathurst St")
regex_intersect_to_offset = re.compile(
    r'^(?P<start_intersection>.*?) and a point (?:approximately )?(?P<distance>\d+(?:\.\d+)?) metres (?P<direction>north|south|east|west) of (?P<offset_intersection>.*?)$',
    re.IGNORECASE
)

# Rule 3: Offset to Intersection (e.g., "A point 30.5 metres north of Heath St and Glen Elm Ave")
regex_offset_to_intersect = re.compile(
    r'^A point (?:approximately )?(?P<distance>\d+(?:\.\d+)?) metres (?P<direction>north|south|east|west) of (?P<start_intersection>.*?) and (?P<end_intersection>.*?)$',
    re.IGNORECASE
)

# Rule 4: The Relative Extension (e.g., "A point 40m north of Lowther Ave and a point 26m further north")
# Note: Catches both "further [dir]" and "[dir] thereof"
regex_relative_extension = re.compile(
    r'^A point (?P<dist1>\d+(?:\.\d+)?) metres (?P<dir1>north|south|east|west) of (?P<start_intersection>.*?) and a point (?P<dist2>\d+(?:\.\d+)?) metres (?:further (?:north|south|east|west)|(?:north|south|east|west) thereof)$',
    re.IGNORECASE
)

# Rule 5: The Perfect Block (e.g., "Appleton Avenue and Brock Street")
regex_block = re.compile(
    r'^(?P<start_intersection>.*?) and (?P<end_intersection>.*?)$',
    re.IGNORECASE
)

# Rule 6: Intersection to Relative Extension (e.g., "Dovercourt Road and a point 32 metres further west")
regex_intersect_extension = re.compile(
    r'^(?P<start_intersection>.*?) and a point (?:approximately )?(?P<distance>\d+(?:\.\d+)?) metres further (?P<direction>north|south|east|west)$',
    re.IGNORECASE
)

# Rule 7: Entire Length
regex_entire_length = re.compile(
    r'^Entire length$',
    re.IGNORECASE
)

# --- THE PARSER ---
def parse_strict_regex(text):
    if pd.isna(text): return None
    text = str(text).strip()

    # Try Rule 1
    m1 = regex_perfect_offset.match(text)
    if m1: return {**m1.groupdict(), 'rule_type': 'perfect_offset'}

    # Try Rule 2
    m2 = regex_intersect_to_offset.match(text)
    if m2: return {**m2.groupdict(), 'rule_type': 'intersect_to_offset'}

    # Try Rule 4 FIRST (The highly specific double-distance rule)
    m4 = regex_relative_extension.match(text)
    if m4: return {**m4.groupdict(), 'rule_type': 'relative_extension'}

    # Try Rule 3 SECOND (The looser catch-all)
    m3 = regex_offset_to_intersect.match(text)
    if m3: return {**m3.groupdict(), 'rule_type': 'offset_to_intersect'}

    # Try Rule 5 (Only if no distance measurements are mentioned)
    if "point" not in text.lower() and "metres" not in text.lower():
        m5 = regex_block.match(text)
        if m5: return {**m5.groupdict(), 'rule_type': 'block'}

    # Try Rule 6
    m6 = regex_intersect_extension.match(text)
    if m6: return {**m6.groupdict(), 'rule_type': 'intersect_extension'}

    # Try Rule 7
    m7 = regex_entire_length.match(text)
    if m7: return {'rule_type': 'entire_length'}

    # If it fails all tests, return None (Send to LLM)
    return None


# --- EXECUTION ---
print("Running expanded Regex pass...")
df['parsed_data'] = df['Between'].apply(parse_strict_regex)

regex_success_df = df[df['parsed_data'].notnull()].copy()
llm_queue_df = df[df['parsed_data'].isnull()].copy()

total_rows = len(df)
success_count = len(regex_success_df)
fail_count = len(llm_queue_df)

print(f"Total Rows: {total_rows}")
print(f"Regex Solved: {success_count} ({round((success_count / total_rows) * 100, 1)}%)")
print(f"Sent to LLM: {fail_count} ({round((fail_count / total_rows) * 100, 1)}%)")

llm_queue_df.to_csv(data_path('llm_processing_queue.csv'), index=False)
regex_success_df.to_csv(data_path('parsed_successes.csv'), index=False)