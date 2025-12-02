import streamlit as st
import re
import json
import pandas as pd

# Load Ghana ISO8583 spec JSON
with open("iso8583_ghana_only.json") as f:
    spec = json.load(f)
data_elements = spec["data_elements"]

# Regex to capture FLD lines from trace file
fld_pattern = re.compile(r"FLD\s+\((\d+)\)\s+\((\d+)\)\s+\[(.*?)\]")

def validate_field(field_num, length, value, mti):
    rule = data_elements.get(field_num)
    if not rule:
        return None

    usage = rule.get("Usage", {})
    if usage.get("all") == "M" or usage.get(mti) == "M":
        if not value:
            return f"Missing mandatory field {field_num}"

    expected_length = rule["Length"]
    if expected_length.isdigit():
        if len(value) != int(expected_length):
            return f"Invalid length: expected {expected_length}, got {len(value)}"

    fmt = rule["Format"]
    if fmt == "n" and not value.isdigit():
        return f"Invalid format: expected numeric"
    if fmt == "an" and not value.isalnum():
        return f"Invalid format: expected alphanumeric"

    if field_num == "39":
        if value not in ["00", "01", "02"]:
            return f"Invalid response code: {value}"

    return None

def get_mandatory_fields(mti):
    mandatory = []
    for field_num, rule in data_elements.items():
        usage = rule.get("Usage", {})
        if usage.get("all") == "M" or usage.get(mti) == "M":
            mandatory.append(field_num)
    return mandatory

st.title("ISO8583 Trace File Validator (Ghana Profile)")

uploaded_files = st.file_uploader(
    "Upload one or more trace files",
    accept_multiple_files=True
)

if uploaded_files:
    for uploaded_file in uploaded_files:
        st.subheader(f"Results for {uploaded_file.name}")
        errors = []
        field_values = {}
        mti = None

        # Decode lines with fallback encoding
        for line_num, line in enumerate(uploaded_file, 1):
            try:
                line = line.decode("utf-8")
            except UnicodeDecodeError:
                line = line.decode("latin-1")  # fallback for Windows-encoded files

            if "M.T.I" in line:
                mti_match = re.search(r"\[(\d+)\]", line)
                if mti_match:
                    mti = mti_match.group(1)

            match = fld_pattern.search(line)
            if match:
                field_num, length, value = match.groups()
                field_values[field_num] = value.strip()
                error = validate_field(field_num, length, value.strip(), mti)
                if error:
                    errors.append({
                        "Line": line_num,
                        "Field": field_num,
                        "Value": value.strip(),
                        "Issue": error
                    })

        if mti:
            st.write(f"Detected MTI: `{mti}`")
            mandatory_fields = get_mandatory_fields(mti)
            mandatory_data = []
            passed_count, failed_count = 0, 0

            for f in mandatory_fields:
                value = field_values.get(f)
                if value:
                    issue = validate_field(f, str(len(value)), value, mti)
                    if not issue:
                        mandatory_data.append({
                            "Field": f"DE {f}",
                            "Value": value,
                            "Validation": "✅ Passed"
                        })
                        passed_count += 1
                    else:
                        mandatory_data.append({
                            "Field": f"DE {f}",
                            "Value": value,
                            "Validation": f"❌ {issue}"
                        })
                        failed_count += 1
                else:
                    mandatory_data.append({
                        "Field": f"DE {f}",
                        "Value": "❌ Missing",
                        "Validation": "❌ Missing mandatory field"
                    })
                    failed_count += 1

            # Summary panel
            st.info(f"Summary: {len(mandatory_fields)} mandatory fields checked — "
                    f"{passed_count} passed, {failed_count} failed")

            # Mandatory fields table with color highlights (using Styler.map)
            df_mandatory = pd.DataFrame(mandatory_data)

            def highlight_validation(val):
                if "✅" in val:
                    return "background-color: #d4edda; color: #155724"  # green
                else:
                    return "background-color: #f8d7da; color: #721c24"  # red

            st.write("Mandatory Fields and Validation Status:")
            st.dataframe(df_mandatory.style.map(highlight_validation, subset=["Validation"]))

            # CSV download for mandatory fields
            csv = df_mandatory.to_csv(index=False).encode("utf-8")
            st.download_button(
                label="Download Mandatory Field Report as CSV",
                data=csv,
                file_name=f"{uploaded_file.name}_mandatory_fields.csv",
                mime="text/csv"
            )

        if errors:
            st.write("Detailed Validation Errors:")
            df_errors = pd.DataFrame(errors)
            st.dataframe(df_errors)

            # CSV download for errors
            csv_errors = df_errors.to_csv(index=False).encode("utf-8")
            st.download_button(
                label="Download Error Report as CSV",
                data=csv_errors,
                file_name=f"{uploaded_file.name}_errors.csv",
                mime="text/csv"
            )
        else:
            st.success("No validation errors found ✅")