import streamlit as st
import re
import json
import pandas as pd

# Load Ghana ISO8583 spec JSON
with open("iso8583_ghana_only.json") as f:
    spec = json.load(f)
data_elements = spec["data_elements"]

# Regex patterns
fld_pattern = re.compile(r"FLD\s*\((\d+)\)\s*(?::|\s*)\s*\((\d+|LLVAR)\)\s*(?::|\s*)\s*\[(.*?)\]")
nested_start_pattern = re.compile(r"FLD\s*\((\d+)\)\s*(?::|\s*)\s*\((\d+|LLVAR)\)")
nested_line_pattern = re.compile(r"\((.*?)\).*?(?::|\s*)\s*\[(.*?)\]")

def detect_scheme(fields):
    """Detect whether the trace belongs to Visa or Mastercard."""
    if "126" in fields:
        return "Mastercard"
    return "Visa"

def validate_field(field_num, length, value, mti, scheme, field_values=None):
    rule = data_elements.get(field_num)
    if not rule:
        return None
    usage = rule.get("Usage", {})
    if usage.get("all") == "M" or usage.get(mti) == "M":
        if not value:
            return f"Missing mandatory field {field_num}"

    # Special case: DE 42 — Card Acceptor ID
    if field_num == "42":
        if not value.strip():
            return f"Missing mandatory field {field_num}"
        return None

    # Special case: DE 12 — Local Transaction Time (hhmmss)
    if field_num == "12":
        clean_value = re.sub(r"\D", "", value)[-6:]  # take last 6 digits
        if not clean_value.isdigit() or len(clean_value) != 6:
            return f"Invalid length: expected 6, got {len(clean_value)} (raw {value})"
        return None

    # Special case: DE 13 — Local Transaction Date (MMDD)
    if field_num == "13":
        clean_value = re.sub(r"\D", "", value)[-4:]  # take last 4 digits
        if not clean_value.isdigit() or len(clean_value) != 4:
            return f"Invalid length: expected 4, got {len(clean_value)} (raw {value})"
        return None

    # Special case: DE 22 — POS Entry Mode (accept 3 or 4 digits)
    if field_num == "22":
        clean_value = value.strip()[:4]  # take first 3–4 digits
        if not clean_value.isdigit() or len(clean_value) not in (3, 4):
            return f"Invalid length: expected 3 or 4, got {len(clean_value)} (raw {value})"
        return None

    # Special case: DE 25 — POS Condition Code (2 digits, pad if needed)
    if field_num == "25":
        clean_value = value.strip()[:2]  # take first 2 digits
        if len(clean_value) == 1:
            clean_value = clean_value.zfill(2)
        if not clean_value.isdigit() or len(clean_value) != 2:
            return f"Invalid format/length: expected 2 digits, got {value}"
        return None

    # Special case: DE 38 — Authorization Identification Response
    if field_num == "38":
        if scheme == "Visa":
            rc = None
            if field_values and "39" in field_values:
                rc = field_values["39"]
            # Visa: mandatory only if approved response
            if mti in ["0210", "0230", "0430"] and rc == "00":
                if not value or len(value) != 6 or not value.isalnum():
                    return f"Invalid DE 38 for Visa: must be 6 alphanumeric chars in approved responses (raw {value})"
            # Declines may omit DE 38 → no error
            return None
        elif scheme == "Mastercard":
            # Mastercard: mandatory in all responses
            if not value or len(value) != 6:
                return f"Invalid DE 38 for Mastercard: must be 6 chars (raw {value})"
            if not value.isalnum():
                return f"Invalid DE 38 for Mastercard: must be alphanumeric/numeric (raw {value})"
            return None

    # Special case: DE 100 — Receiving Institution Identification Code
    if field_num == "100":
        if not value.strip():
            return "Missing mandatory field 100"
        if not value.isalnum():
            return "Invalid format: expected alphanumeric"
        if len(value) > 15:
            return f"Invalid length: expected up to 15, got {len(value)}"
        return None

    # Generic validation
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

def get_mandatory_fields(mti, scheme, field_values=None):
    mandatory = []
    for field_num, rule in data_elements.items():
        usage = rule.get("Usage", {})
        if usage.get("all") == "M" or usage.get(mti) == "M":
            # Special rule: DE 126 only mandatory for Mastercard response MTIs
            if field_num == "126" and mti not in ["0210", "0110", "0430"]:
                continue

            # Special rule: DE 38 (Authorization Identification Response)
            if field_num == "38":
                if scheme == "Visa":
                    # Visa: mandatory only if approved response (RC=00)
                    rc = field_values.get("39") if field_values else None
                    if rc != "00":
                        continue  # skip DE 38 for Visa declines
                elif scheme == "Mastercard":
                    # Mastercard: mandatory in all responses
                    pass  # always include
            mandatory.append(field_num)
    return mandatory
st.title("VISA and MasterCard Trace Validator")

uploaded_files = st.file_uploader("Upload one or more trace files", accept_multiple_files=True)

if uploaded_files:
    for uploaded_file in uploaded_files:
        # Display only (no key allowed here)
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

            # If we are inside a message, capture fields
            if current_message:
                # Nested field start
                if "FLD (055)" in line or "FLD (062)" in line or "FLD (063)" in line:
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

                # Reset nested field when next FLD starts
                if "FLD" in line and not line.startswith(">"):
                    nested_field = None

                # Regular field
                match = fld_pattern.search(line)
                if match:
                    field_num, length, value = match.groups()
                    normalized = str(int(field_num))
                    current_message["fields"][normalized] = value.strip()

        # MTI counts
        mti_counts = {}
        for msg in messages:
            mti = msg["mti"]
            mti_counts[mti] = mti_counts.get(mti, 0) + 1

        st.write("### MTI Counts in File")
        df_counts = pd.DataFrame(list(mti_counts.items()), columns=["MTI", "Count"])
        st.dataframe(df_counts, key=f"counts_{uploaded_file.name}")

        # Multi-select filter (widget → needs key)
        mti_options = sorted(mti_counts.keys())
        selected_mtis = st.multiselect(
            "Select one or more MTIs to view",
            mti_options,
            default=mti_options,
            key=f"mtiselect_{uploaded_file.name}"
        )

        filtered_messages = [msg for msg in messages if msg["mti"] in selected_mtis]

        # Validation phase
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
            mandatory_fields = get_mandatory_fields(mti, scheme, field_values)
            mandatory_fields = [f for f in mandatory_fields if f in field_values]
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
                    issue = validate_field(f, str(len(value)), value, mti, scheme)
                    if not issue:
                        mandatory_data.append({"Field": f"DE {f}", "Value": value, "Validation": "✅ Passed"})
                        passed_count += 1
                    else:
                        mandatory_data.append({"Field": f"DE {f}", "Value": value, "Validation": f"❌ {issue}"})
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

            st.dataframe(
                df_mandatory.style.map(highlight_validation, subset=["Validation"]),
                key=f"mandatory_{uploaded_file.name}_{i}"
            )

        # --- Global summary for filtered MTIs ---
        st.write("---")
        st.success(
            f"Global Summary (Filtered): {total_mtis} transactional messages — "
            f"{mtis_clean} clean, {mtis_with_errors} with errors"
        )