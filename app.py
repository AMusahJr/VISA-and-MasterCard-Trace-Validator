import streamlit as st
import re
import json
import pandas as pd

# Load Ghana ISO8583 spec JSON
with open("iso8583_ghana_only.json") as f:
    spec = json.load(f)
data_elements = spec["data_elements"]

# Regex patterns
fld_pattern = re.compile(r"FLD\s+\((\d+)\)\s+\((?:\d+|LLVAR)\)\s+\[(.*?)\]")
nested_start_pattern = re.compile(r"FLD\s+\((\d+)\)\s+\((\d+)\)")
nested_line_pattern = re.compile(r"\((.*?)\).*?:\s+\[(.*?)\]")

def detect_scheme(fields):
    if "126" in fields:
        return "Mastercard"
    return "Visa"

def validate_field(field_num, length, value, mti, scheme):
    rule = data_elements.get(field_num)
    if not rule:
        return None
    usage = rule.get("Usage", {})
    if usage.get("all") == "M" or usage.get(mti) == "M":
        if not value:
            return f"Missing mandatory field {field_num}"

    # DE 42
    if field_num == "42":
        if not value.strip():
            return f"Missing mandatory field {field_num}"
        return None

    # DE 22
    if field_num == "22":
        if not value or not value.isdigit():
            return "Invalid format: expected numeric"
        if len(value) not in (3, 4):
            return f"Invalid length: expected 3 or 4, got {len(value)}"
        return None

    # DE 100 — Ghana spec (numeric LLVAR)
    if field_num == "100":
        if not value.strip():
            return "Missing mandatory field 100"
        if not value.isdigit():
            return "Invalid format: expected numeric"
        if len(value) < 1 or len(value) > 11:
            return f"Invalid length: expected 1–11, got {len(value)}"
        return None

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
            if field_num == "126" and mti not in ["0210", "0110", "0430"]:
                continue
            mandatory.append(field_num)
    return mandatory

st.title("ISO8583 Trace File Validator (Visa vs Mastercard aware, Ghana DE100)")

uploaded_files = st.file_uploader("Upload one or more trace files", accept_multiple_files=True)

if uploaded_files:
    for uploaded_file in uploaded_files:
        st.subheader(f"Results for {uploaded_file.name}")

        messages = []
        current_message = None
        nested_field = None
        nested_data = {}

        for line_num, line in enumerate(uploaded_file, 1):
            try:
                line = line.decode("utf-8")
            except UnicodeDecodeError:
                line = line.decode("latin-1")
            line = line.strip()

            # Start of new message
            if "M.T.I" in line:
                mti_match = re.search(r"\[(\d+)\]", line)
                if mti_match:
                    current_mti = mti_match.group(1)
                    current_message = {"mti": current_mti, "fields": {}}
                    current_message["fields"]["MTI"] = current_mti
                    messages.append(current_message)
                    nested_field = None
                    nested_data = {}
                continue

            if current_message:
                # Nested field start
                fld_match = nested_start_pattern.search(line)
                if fld_match:
                    nested_field = str(int(fld_match.group(1)))
                    nested_data = {}
                    current_message["fields"][nested_field] = nested_data
                    continue

                # Nested line
                if nested_field and line.startswith(">"):
                    tag_match = nested_line_pattern.search(line)
                    if tag_match:
                        tag, value = tag_match.groups()
                        nested_data[tag.strip()] = value.strip()
                    continue

                # Reset nested field
                if "FLD" in line and not line.startswith(">"):
                    nested_field = None

                # Regular field
                match = fld_pattern.search(line)
                if match:
                    field_num, length, value = match.groups()
                    normalized = str(int(field_num))
                    current_message["fields"][normalized] = value.strip()
                else:
                    continue

        # MTI counts
        mti_counts = {}
        for msg in messages:
            mti = msg["mti"]
            mti_counts[mti] = mti_counts.get(mti, 0) + 1

        st.write("### MTI Counts in File")
        df_counts = pd.DataFrame(list(mti_counts.items()), columns=["MTI", "Count"])
        st.dataframe(df_counts)

        mti_options = sorted(mti_counts.keys())
        selected_mtis = st.multiselect("Select one or more MTIs to view", mti_options, default=mti_options)
        filtered_messages = [msg for msg in messages if msg["mti"] in selected_mtis]

        total_mtis = 0
        mtis_with_errors = 0
        mtis_clean = 0

        for i, msg in enumerate(filtered_messages, 1):
            mti = msg["mti"]
            field_values = msg["fields"]

            if mti in ["0800", "0810", "0820"]:
                continue

            scheme = detect_scheme(field_values)

            total_mtis += 1
            st.write(f"### Message {i} (MTI {mti}, Scheme {scheme}) Validation")
            mandatory_fields = get_mandatory_fields(mti)
            mandatory_data = []
            passed_count, failed_count = 0, 0
            available_count, missing_count = 0, 0
            errors = []

            for f in mandatory_fields:
                value = field_values.get(f)

                if isinstance(value, dict):
                    # Show actual tag=value pairs
                    display_value = "; ".join([f"{k}={v}" for k, v in value.items()])
                    mandatory_data.append({
                        "Field": f"DE {f}",
                        "Value": display_value,
                        "Validation": "✅ Nested field captured"
                    })
                    passed_count += 1
                    available_count += 1

                elif value:
                    available_count += 1
                    issue = validate_field(f, str(len(value)), value, mti, scheme)

                    # Special case: DE 100 — always show actual numeric LLVAR
                    if f == "100":
                        mandatory_data.append({
                            "Field": "DE 100",
                            "Value": value,  # e.g. "300302" from trace file
                            "Validation": "✅ Passed" if not issue else f"❌ {issue}"
                        })
                        if not issue:
                            passed_count += 1
                        else:
                            failed_count += 1
                            errors.append({"Field": f, "Value": value, "Issue": issue})
                    else:
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
                            errors.append({"Field": f, "Value": value, "Issue": issue})

                else:
                    missing_count += 1
                    mandatory_data.append({
                        "Field": f"DE {f}",
                        "Value": "❌ Missing",
                        "Validation": "❌ Missing mandatory field"
                    })
                    failed_count += 1
                    errors.append({
                        "Field": f,
                        "Value": "❌ Missing",
                        "Issue": "Missing mandatory field"
                    })

            st.info(
                f"Summary for Message {i} (MTI {mti}, Scheme {scheme}): {len(mandatory_fields)} mandatory fields — "
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

        # --- Global summary for filtered MTIs ---
        st.write("---")
        st.success(
            f"Global Summary (Filtered): {total_mtis} transactional messages — "
            f"{mtis_clean} clean, {mtis_with_errors} with errors"
        )
		