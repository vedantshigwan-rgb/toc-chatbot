import csv
import json
import os


def extract_json_from_csv(csv_file_path, output_json_path):
    """Reads a CSV file, extracts JSON objects from the 'JSON object' column,

    and writes them into a valid JSON file.
    """
    json_list = []

    if not os.path.exists(csv_file_path):
        print(f"Error: The file '{csv_file_path}' does not exist.")
        return

    print(f"Processing '{csv_file_path}'...")

    with open(csv_file_path, mode="r", encoding="utf-8") as file:
        # Use csv.DictReader to automatically map columns by their header names
        reader = csv.DictReader(file)

        # Clean up column names just in case there are hidden spaces
        reader.fieldnames = [
            field.strip() for field in reader.fieldnames if field
        ]

        # Target column name
        target_column = "JSON object"

        if target_column not in reader.fieldnames:
            print(
                f"Error: Could not find '{target_column}' column. Available columns: {reader.fieldnames}"
            )
            return

        for row_idx, row in enumerate(reader, start=1):
            raw_json_str = row.get(target_column)

            if raw_json_str:
                try:
                    # Convert the string snippet into an actual Python dictionary/object
                    json_data = json.loads(raw_json_str.strip())
                    json_list.append(json_data)
                except json.JSONDecodeError:
                    print(
                        f"Warning: Row {row_idx} contains invalid JSON. Skipping."
                    )

    # Save the extracted array of JSON objects into the new file
    with open(output_json_path, mode="w", encoding="utf-8") as out_file:
        # indent=4 makes the output file pretty and human-readable
        json.dump(json_list, out_file, indent=4)

    print(
        f"Success! Extracted {len(json_list)} JSON records to '{output_json_path}'\n"
    )


# ==========================================
# HOW TO RUN FOR MULTIPLE FILES
# ==========================================
if __name__ == "__main__":
    # Example 1: Single File execution
    # Just change these paths whenever you want to process a new file
    input_csv = r"C:\Users\Tanvi.Limaye\Downloads\TOC_DATA_SCHEMA_ADW - buyerwise trolley status.csv"  #<- change path here
    output_json = r"D:\buyer_lookup_project\redis_final\buyerwise_trolley.json"   #change path here 

    # Uncomment the line below to run a single file:
    extract_json_from_csv(input_csv, output_json)        # if u are only retrive the data from one file then uncomment this other leave it as it is 

    # Example 2: Batch processing a list of files automatically
    files_to_process = [ 
        ("file1.csv", "output1.json"),             # this is for batch running of multiple files at one time 
        ("file2.csv", "output2.json"),
        # Add more pairs here...
    ]

    for csv_path, json_path in files_to_process:
        # Double check if the CSV actually exists before trying to run it
        if os.path.exists(csv_path):
            extract_json_from_csv(csv_path, json_path)