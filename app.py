import streamlit as st
import pandas as pd
import gspread
from google.oauth2.service_account import Credentials
import streamlit_authenticator as stauth

st.set_page_config(page_title="Current IT Needs", layout="wide")

# CSS for narrower sidebar
st.markdown("""
    <style>
        [data-testid="stSidebar"] {
            min-width: 220px !important;
            max-width: 220px !important;
        }
    </style>
""", unsafe_allow_html=True)

# ==========================================
# --- AUTHENTICATION GATEWAY ---
# ==========================================
try:
    # Convert st.secrets to a standard dict so the authenticator can mutate tracking values safely
    credentials = dict(st.secrets["auth"]["credentials"].to_dict())
    cookie = dict(st.secrets["auth"]["cookie"].to_dict())

    authenticator = stauth.Authenticate(
        credentials,
        cookie["name"],
        cookie["key"],
        cookie["expiry_days"]
    )
except KeyError:
    st.error("Authentication secrets are missing. Please add them to your Streamlit Cloud Secrets.")
    st.stop()

# Render the login widget
authenticator.login()

if st.session_state["authentication_status"] is False:
    st.error("Username/password is incorrect")
elif st.session_state["authentication_status"] is None:
    st.warning("Please enter your username and password")
elif st.session_state["authentication_status"]:
    
    # Render the logout button in the sidebar
    authenticator.logout("Logout", "sidebar")
    st.sidebar.divider()

    # ==========================================
    # --- APP LOGIC (Only runs if logged in) ---
    # ==========================================

    # 1. AUTHENTICATION (Google Sheets)
    @st.cache_resource
    def get_google_client():
        scope = [
            'https://www.googleapis.com/auth/spreadsheets',
            'https://www.googleapis.com/auth/drive'
        ]
        creds = Credentials.from_service_account_info(st.secrets["gcp_service_account"], scopes=scope)
        return gspread.authorize(creds)

    @st.cache_resource(ttl=600)
    def get_workbook():
        client = get_google_client()
        return client.open_by_key(st.secrets["sheet_key"])

    # --- Cached Sidebar List ---
    @st.cache_data(show_spinner=False, ttl=600)
    def get_all_sheet_titles():
        wb = get_workbook()
        worksheets = wb.worksheets()
        return [ws.title for ws in worksheets if ws.title not in ["App_Notes", "App_Reference_Tables"]]

    # 2. DATA FETCHING
    @st.cache_data(show_spinner=False, ttl=600)
    def get_sheet_data(sheet_title):
        wb = get_workbook()
        sheet = wb.worksheet(sheet_title)
        
        raw_data = sheet.get_all_values()
        
        if not raw_data:
            return pd.DataFrame()
            
        headers = raw_data[0]
        rows = raw_data[1:]
        
        df = pd.DataFrame(rows, columns=headers)
        df = df.loc[:, df.columns != '']
        
        if len(df.columns) > 0:
            df['_gs_row'] = range(2, len(df) + 2)
            
            visible_cols = [col for col in df.columns if col != '_gs_row']
            empty_mask = df[visible_cols].isin(['', 'FALSE', 'False', 'false', False, None]).all(axis=1)
            df = df[~empty_mask]
            
            first_col = headers[0]
            df[first_col] = pd.to_numeric(df[first_col], errors='coerce').fillna(0).astype(int)
            df = df.sort_values(by=first_col, ascending=True)
            
            df = df.reset_index(drop=True)
        
        return df

    @st.cache_data(show_spinner=False, ttl=600)
    def get_tab_notes():
        try:
            sheet = get_workbook().worksheet("App_Notes")
            raw_data = sheet.get_all_values()
            if len(raw_data) > 1:
                return {row[0]: row[1] for row in raw_data[1:]}
        except Exception:
            pass
        return {}

    @st.cache_data(show_spinner=False, ttl=600)
    def get_reference_table(tab_name):
        try:
            sheet = get_workbook().worksheet("App_Reference_Tables")
            raw_data = sheet.get_all_values()
            
            if len(raw_data) > 0:
                headers = raw_data[0]
                while len(headers) < 7:
                    headers.append(f"Col {len(headers)-1}")
                    
                data_cols = headers[2:7]
                
                if len(raw_data) > 1:
                    tab_rows = [row for row in raw_data[1:] if row[0] == tab_name]
                    if len(tab_rows) > 0:
                        df = pd.DataFrame(tab_rows, columns=headers)
                        if len(df) > 2:
                            df = df.head(2)
                        df = df[data_cols].copy()
                        
                        df.iloc[0, 0] = "List Manager (Primary)"
                        if len(df) > 1:
                            df.iloc[1, 0] = "List Manager (Alternate)"
                            
                        return df, True
                        
                blank_data = [
                    ["List Manager (Primary)", "", "", "", ""],
                    ["List Manager (Alternate)", "", "", "", ""]
                ]
                return pd.DataFrame(blank_data, columns=data_cols), False
                
        except Exception:
            pass
        
        fallback_cols = ['Col 1', 'Col 2', 'Col 3', 'Col 4', 'Col 5']
        blank_data = [
            ["List Manager (Primary)", "", "", "", ""],
            ["List Manager (Alternate)", "", "", "", ""]
        ]
        return pd.DataFrame(blank_data, columns=fallback_cols), False

    # 3. SAVE LOGIC
    def save_reference_table(tab_name, edited_df):
        try:
            sheet = get_workbook().worksheet("App_Reference_Tables")
            raw_data = sheet.get_all_values()
            
            if len(raw_data) > 0:
                header = raw_data[0]
                other_rows = [header] + [row for row in raw_data[1:] if row[0] != tab_name]
                sheet.clear()
                sheet.update(other_rows)
                
            new_rows = []
            for idx, row in edited_df.iterrows():
                new_rows.append([tab_name, str(idx + 1)] + [str(row[c]) for c in edited_df.columns])
                
            if new_rows:
                sheet.append_rows(new_rows)
                
            get_reference_table.clear()
        except Exception as e:
            st.error(f"Failed to save reference table. Ensure 'App_Reference_Tables' tab exists. Error: {e}")

    def save_tab_note(tab_name):
        new_note = st.session_state[f"note_input_{tab_name}"]
        try:
            sheet = get_workbook().worksheet("App_Notes")
            col_values = sheet.col_values(1)
            
            if tab_name in col_values:
                row_idx = col_values.index(tab_name) + 1
                sheet.update_cell(row_idx, 2, new_note)
            else:
                sheet.append_row([tab_name, new_note])
                
            get_tab_notes.clear()
        except Exception as e:
            st.error(f"Failed to save note: {e}")

    def process_updates(sheet_title, changes, force_cascade=False):
        wb = get_workbook()
        sheet = wb.worksheet(sheet_title)
        
        df = get_sheet_data(sheet_title)
        headers = sheet.row_values(1)
        prio_col = headers[0]
        
        requested_priorities = []
        
        if changes.get("edited_rows"):
            for row_idx, edit_dict in changes["edited_rows"].items():
                if prio_col in edit_dict:
                    val = edit_dict[prio_col]
                    if val is not None and str(val).strip() != "":
                        gs_row = int(df.at[int(row_idx), '_gs_row'])
                        requested_priorities.append((gs_row, int(val)))
                    
        if changes.get("added_rows"):
            for added_row in changes["added_rows"]:
                if prio_col in added_row:
                    val = added_row[prio_col]
                    if val is not None and str(val).strip() != "":
                        requested_priorities.append(('new', int(val)))
                    
        existing_prios = df[prio_col].tolist()
        collision = False
        for req in requested_priorities:
            gs_row, req_val = req
            if req_val in existing_prios:
                matches = df[df[prio_col] == req_val]
                if gs_row != 'new':
                    matches = matches[matches['_gs_row'] != gs_row]
                if not matches.empty:
                    collision = True
                    break

        if collision and not force_cascade:
            st.session_state.pending_conflict = {'title': sheet_title, 'changes': changes}
            return

        cell_updates = {} 
        
        for req in requested_priorities:
            req_gs_row, req_val = req
            conflict_val = req_val
            
            for idx, row in df.iterrows():
                r_gs_row = int(row['_gs_row'])
                if r_gs_row == req_gs_row: 
                    continue 
                
                current_prio = int(row[prio_col])
                
                if (r_gs_row, 1) in cell_updates:
                    current_prio = cell_updates[(r_gs_row, 1)]
                    
                if current_prio == conflict_val:
                    conflict_val += 1
                    cell_updates[(r_gs_row, 1)] = conflict_val
                elif current_prio > conflict_val:
                    break 

        if changes.get("edited_rows"):
            for row_idx, edit_dict in changes["edited_rows"].items():
                gs_row = int(df.at[int(row_idx), '_gs_row'])
                for col_name, new_val in edit_dict.items():
                    if col_name in headers:
                        col_idx = headers.index(col_name) + 1
                        clean_val = new_val if new_val is not None else ""
                        cell_updates[(gs_row, col_idx)] = clean_val

        if cell_updates:
            final_updates = [gspread.Cell(row=r, col=c, value=v) for (r, c), v in cell_updates.items()]
            sheet.update_cells(final_updates)
            
        if changes.get("added_rows"):
            new_rows = []
            for added_row in changes["added_rows"]:
                row_data = [added_row.get(col, "") if added_row.get(col) is not None else "" for col in headers if col != '']
                new_rows.append(row_data)
            sheet.append_rows(new_rows)
            
        if changes.get("deleted_rows"):
            gs_rows_to_delete = [int(df.at[int(idx), '_gs_row']) for idx in changes["deleted_rows"]]
            for gs_row in sorted(gs_rows_to_delete, reverse=True):
                sheet.delete_rows(gs_row)
                
        st.session_state.grid_reset_trigger = st.session_state.get("grid_reset_trigger", 0) + 1
        get_sheet_data.clear()
        if "pending_conflict" in st.session_state:
            del st.session_state.pending_conflict

    def trigger_updates(sheet_title, current_editor_key):
        changes = st.session_state.get(current_editor_key, {})
        if changes:
            process_updates(sheet_title, changes, force_cascade=False)

    # 4. CONFLICT RESOLUTION DIALOG
    @st.dialog("Priority Collision Detected")
    def confirm_conflict_dialog():
        st.warning("The priority number you assigned is already in use by another item.")
        st.write("Do you want to insert it here and automatically shift all lower-priority items down by 1?")
        
        col1, col2 = st.columns(2)
        with col1:
            if st.button("Yes, Insert and Shift", use_container_width=True):
                data = st.session_state.pending_conflict
                process_updates(data['title'], data['changes'], force_cascade=True)
                st.rerun()
        with col2:
            if st.button("Cancel", type="secondary", use_container_width=True):
                del st.session_state.pending_conflict
                get_sheet_data.clear() 
                st.rerun()

    # 5. FRONTEND UI
    if "grid_reset_trigger" not in st.session_state:
        st.session_state.grid_reset_trigger = 0

    try:
        sheet_titles = get_all_sheet_titles()
    except Exception as e:
        st.error(f"Failed to connect to Google Sheets. Verify your secrets.toml file. Error: {e}")
        st.stop()

    st.sidebar.title("Navigation")
    selected_sheet = st.sidebar.radio("Select a Sheet:", sheet_titles)
    st.title(f"Current IT Needs - {selected_sheet}")

    # --- RENDER DYNAMIC REFERENCE TABLE ---
    ref_df, _ = get_reference_table(selected_sheet)

    ref_editor_key = f"ref_editor_{selected_sheet}"
    edited_ref_df = st.data_editor(
        ref_df,
        key=ref_editor_key,
        num_rows="fixed",
        use_container_width=True,
        hide_index=True
    )
    if st.button("💾 Save Role Info.", key=f"save_ref_{selected_sheet}"):
        save_reference_table(selected_sheet, edited_ref_df)
        st.rerun()

    # --- RENDER SAVED IT NOTES ---
    all_notes = get_tab_notes()
    current_note = all_notes.get(selected_sheet, "")

    st.text_area(
        "IT Notes", 
        value=current_note,
        key=f"note_input_{selected_sheet}", 
        height=100, 
        placeholder="Enter notes for this tab here. Click outside the box to save...",
        on_change=save_tab_note,
        args=(selected_sheet,)
    )

    st.divider() 

    if "pending_conflict" in st.session_state:
        confirm_conflict_dialog()
        st.stop()

    def display_grid(title):
        df = get_sheet_data(title)
        
        if len(df.columns) == 0:
            st.info(f"The '{title}' tab is missing headers in Row 1. Add some headers in Row 1 of Google Sheets to get started.")
            return

        first_col = df.columns[0]
        
        # --- DATA ENTRY FORM ---
        st.subheader("Add New Item")
        st.markdown("Use this form to seamlessly add new tasks to the queue.")
        
        with st.form(key=f"add_form_{title}", clear_on_submit=True):
            form_cols_list = []
            for c in df.columns:
                c_lower = c.strip().lower()
                if c_lower == "_gs_row" or "completed" in c_lower or "staff" in c_lower:
                    continue
                form_cols_list.append(c)
            
            width_ratios = []
            for i in range(len(form_cols_list)):
                if i == 0:
                    width_ratios.append(1)
                elif i == 1:
                    width_ratios.append(4)
                else:
                    width_ratios.append(2)
                    
            form_cols = st.columns(width_ratios)
            
            new_row_data = {}
            for i, col_name in enumerate(form_cols_list):
                with form_cols[i]:
                    if col_name == first_col:
                        new_row_data[col_name] = st.number_input(col_name, min_value=1, step=1, value=1)
                    else:
                        new_row_data[col_name] = st.text_input(col_name, value="")
                        
            submit_btn = st.form_submit_button("Add to Queue", type="primary")
            
            if submit_btn:
                for k, v in new_row_data.items():
                    if isinstance(v, bool):
                        new_row_data[k] = "TRUE" if v else "FALSE"
                    else:
                        new_row_data[k] = str(v)
                
                form_changes = {"added_rows": [new_row_data]}
                process_updates(title, form_changes, force_cascade=False)
                st.rerun()

        st.divider()

        # --- RENDER MAIN DATA GRID ---
        st.subheader("Queue")
        
        col_config = {"_gs_row": None}
        
        for i, col_name in enumerate(df.columns):
            if col_name == "_gs_row":
                continue
                
            if i == 0:
                col_config[col_name] = st.column_config.NumberColumn(
                    col_name, help="Priority number. 1 is highest priority.", step=1, min_value=1, width=80
                )
            elif i == 2:
                if col_name == "Completed" or "completed" in col_name.strip().lower():
                    col_config[col_name] = st.column_config.CheckboxColumn(col_name, default=False, width=110)
                else:
                    col_config[col_name] = st.column_config.Column(col_name, width=110)
            else:
                if col_name == "Completed" or "completed" in col_name.strip().lower():
                    col_config[col_name] = st.column_config.CheckboxColumn(col_name, default=False, width="large")
                else:
                    col_config[col_name] = st.column_config.Column(col_name, width="large")
        
        editor_key = f"editor_{title}_{st.session_state.grid_reset_trigger}"
        
        st.data_editor(
            df,
            key=editor_key,
            num_rows="dynamic",
            column_config=col_config,
            use_container_width=True, 
            hide_index=True
        )
        
        changes = st.session_state.get(editor_key, {})
        disable_save = False

        if changes.get("added_rows"):
            st.error("⚠️ **Please use the 'Add New Item' form above** to create new rows. The table's `+` button is disabled for new entries to prevent priority math errors.")
            disable_save = True
        else:
            has_changes = bool(changes.get("edited_rows") or changes.get("deleted_rows"))
            prio_edits = sum(1 for edit_dict in changes.get("edited_rows", {}).values() if first_col in edit_dict)

            disable_save = not has_changes

            if prio_edits > 1:
                st.error("⚠️ **Multiple Priority Changes Detected!**\n\nPlease process one priority change at a time. Click **🔄 Refresh Data** to clear edits.")
                disable_save = True
            elif prio_edits == 1:
                st.warning("💡 **Pending Priority Change:** Please click **💾 Save Changes** before editing other priority numbers.")

        col1, col2 = st.columns([1, 6])
        with col1:
            if st.button("💾 Save Changes", type="primary", disabled=disable_save, use_container_width=True):
                trigger_updates(title, editor_key)
                st.rerun()
        with col2:
            if st.button("🔄 Refresh Data", use_container_width=True):
                if editor_key in st.session_state:
                    del st.session_state[editor_key]
                st.session_state.grid_reset_trigger += 1
                get_sheet_data.clear()
                get_tab_notes.clear()
                get_reference_table.clear()
                get_all_sheet_titles.clear()
                st.rerun()

    display_grid(selected_sheet)