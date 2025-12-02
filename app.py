import streamlit as st
import re
import json
import pandas as pd

# Load Ghana ISO8583 spec JSON
with open("iso8583_ghana_only.json") as f:
    spec = json.load(f)
data_elements = spec["data_elements"]

fld_pattern = re.compile(r"FLD\s+\((\d+)\)\s+\((\d+)\)\s+\[(.*?)\]")
nested_start_pattern = re.compile(r"FLD\s+\((\d+)\)\s+\((\d+)\)")
nested_line_pattern = re.compile(r"\((.*?)\).*?:\s+\[(.*?)\]")

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

st.title("ISO8583 Trace File Validator (Nested Field Support)")

uploaded_files = st.file_uploader("Upload one or more trace files", accept_multiple_files=True)

if uploaded_files:
    for uploaded_file in uploaded_files:
        st.subheader(f"Results for {uploaded_file.name}")
        mtis = {}
        current_mti = None
        nested_field = None
        nested_data = {}

        for line_num, line in enumerate(uploaded_file, 1):
            try:
                line = line.decode("utf-8")
            except UnicodeDecodeError:
                line = line.decode("latin-1")

            if "M.T.I" in line:
                mti_match = re.search(r"\[(\d+)\]", line)
                if mti_match:
                    current_mti = mti_match.group(1)
                    if current_mti not in mtis:
                        mtis[current_mti] = {}

            # Start of nested field
            if "FLD (055)" in line or "FLD (062)" in line or "FLD (063)" in line:
                fld_match = nested_start_pattern.search(line)
                if fld_match and current_mti:
                    nested_field = fld_match.group(1)
                    nested_data = {}
                    mtis[current_mti][nested_field] = nested_data
                continue

            # Nested line
            if nested_field and line.strip().startswith(">"):
                tag_match = nested_line_pattern.search(line)
                if tag_match:
                    tag, value = tag_match.groups()
                    nested_data[tag.strip()] = value.strip()
                continue

            # Reset nested field
            if "FLD" in line and not line.strip().startswith(">"):
                nested_field = None

            # Regular field
            match = fld_pattern.search(line)
            if match and current_mti:
                field_num, length, value = match.groups()
                mtis[current_mti][field_num] = value.strip()

        # 🔍 Show raw MTI and field dump
        st.write("### Raw MTI and Field Dump")
        for mti, fields in mtis.items():
            st.write(f"**MTI {mti}** → {len(fields)} fields captured")
            st.json(fields)

        # Validation phase
        total_mtis = 0
        mtis_with_errors = 0
        mtis_clean = 0

        for mti, field_values in mtis.items():
            if mti in ["0800", "0810", "0820"]:
                continue

            total_mtis += 1
            st.write(f"### MTI {mti} Validation")
            mandatory_fields = get_mandatory_fields(mti)
            mandatory_data = []
            passed_count, failed_count = 0, 0
            available_count, missing_count = 0, 0
            errors = []

            for f in mandatory_fields:
                value = field_values.get(f)
                if isinstance(value, dict):
                    display_value = f"{len(value)} nested items"
                    mandatory_data.append({"Field": f"DE {f}", "Value": display_value, "Validation": "✅ Nested field captured"})
                    passed_count += 1
                    available_count += 1
                elif value:
                    available_count += 1
                    issue = validate_field(f, str(len(value)), value, mti)
                    if not issue:
                        mandatory_data.append({"Field": f"DE {f}", "Value": value, "Validation": "✅ Passed"})
                        passed_count += 1
                    else:
                        mandatory_data.append({"Field": f"DE {f}", "Value": value, "Validation": f"❌ {issue}"})
                        failed_count += 1
                        errors.append({"Field": f, "Value": value, "Issue": issue})
                else:
                    missing_count += 1
                    mandatory_data.append({"Field": f"DE {f}", "Value": "❌ Missing", "Validation": "❌ Missing mandatory field"})
                    failed_count += 1
                    errors.append({"Field": f, "Value": "❌ Missing", "Issue": "Missing mandatory field"})

            st.info(
                f"Summary for MTI {mti}: {len(mandatory_fields)} mandatory fields — "
                f"{available_count} available, {missing_count} missing; "
                f"{passed_count} passed, {failed_count} failed"
            )

            if failed_count > 0:
                mtis_with_errors += 1
            else:
                mtis_clean += 1

            df_mandatory = pd.DataFrame(mandatory_data)

            def highlight_validation(val):
                if "✅" in val:
                    return "background-color: #d4edda; color: #155724"
                else:
                    return "background-color: #f8d7da; color: #721c24"

            st.dataframe(df_mandatory.style.map(highlight_validation, subset=["Validation"]))

        st.write("---")
        st.success(
            f"Global Summary: File contained {total_mtis} MTIs — "
            f"{mtis_clean} clean, {mtis_with_errors} with errors"
        )