import streamlit as st
import pandas as pd
from google import genai
from google.genai import types
import fitz  # PyMuPDF
import io
import json
import datetime
import os

# --- Configuration & Setup ---
st.set_page_config(page_title="DSP Auto-Dispatch Engine", page_icon="🚐", layout="wide")

st.markdown("""
<style>
    /* Hide Streamlit default branding */
    #MainMenu {visibility: hidden;}
    footer {visibility: hidden;}
    
    /* Expanders & Cards */
    .streamlit-expanderHeader {
        font-weight: 600;
    }
    
    /* Buttons */
    .stButton>button {
        background-color: #3b82f6;
        color: white;
        border-radius: 8px;
        border: none;
        padding: 10px 24px;
        font-weight: 600;
        transition: all 0.2s ease;
    }
    .stButton>button:hover {
        background-color: #2563eb;
        color: white;
    }
</style>
""", unsafe_allow_html=True)

from db import init_supabase, init_gemini, check_password

check_password()

GEMINI_API_KEY = init_gemini()
supabase = init_supabase()

if not GEMINI_API_KEY or GEMINI_API_KEY == "YOUR_GEMINI_KEY":
    st.warning("Please update YOUR_GEMINI_KEY in db.py to use AI extraction.")


# --- Core Logic Stubs ---

from utils.pdf_parser import extract_pdf_data, generate_stamped_pdf
from utils.ai_extractor import extract_wave_schedule
from utils.dispatch_logic import run_dispatch_algorithm


# --- Streamlit UI ---

st.title("🚐 DSP Auto-Dispatch Engine")
st.markdown("""
Welcome to the Auto-Dispatch Engine. Upload your daily files below to automatically generate route assignments
and stamped pick sheets based on fleet constraints and wave schedules.
""")

# 1. File Uploads
st.header("1. Upload Daily Files")
col1, col2, col3 = st.columns(3)

with col1:
    roster_file = st.file_uploader("Cortex Roster (Excel)", type=["xlsx"])
with col2:
    schedule_img = st.file_uploader("Wave Schedule (Image)", type=["png", "jpg", "jpeg"])
with col3:
    route_pdf = st.file_uploader("Route Pick Sheets (PDF)", type=["pdf"])

if roster_file and route_pdf:
    if st.button("Extract Route Data"):
        with st.spinner("Extracting PDF and Excel data..."):
            # Clear any old state
            st.session_state.pop("driver_conflicts", None)
            st.session_state.pop("unresolved_routes_df", None)
            st.session_state.pop("routes_df", None)
            st.session_state.pop("assignments", None)
            
            res_df = extract_pdf_data(route_pdf, roster_file)
            if res_df is not None:
                st.session_state["routes_df"] = res_df
                
        if "debug_pdf_text" in st.session_state:
            with st.expander("PDF Extraction Debug (Click to view raw text)"):
                st.text("If Overflow or Bags say 0, copy some of this text and send it to me so I can see how PyMuPDF reads your file:")
                st.text(st.session_state["debug_pdf_text"])

if st.session_state.get("show_conflict_success"):
    st.success("✅ Conflicts resolved! You can now proceed to Route Tagging.")
    st.session_state["show_conflict_success"] = False

# Conflict Resolution UI
if "driver_conflicts" in st.session_state:
    st.error("⚠️ Multiple drivers found for the same route.")
    st.write("Please select the correct driver for each conflicted route:")
    
    with st.form("conflict_resolution_form"):
        resolved_drivers = {}
        for r_id, drivers_list in st.session_state["driver_conflicts"].items():
            # User must explicitly confirm even if one is empty
            selected = st.selectbox(f"Select actual driver for Route **{r_id}**:", drivers_list, key=f"conflict_{r_id}")
            resolved_drivers[r_id] = selected
            
        if st.form_submit_button("Confirm Drivers"):
            df = st.session_state["unresolved_routes_df"].copy()
            
            # Update driver name for conflicted routes
            for route, resolved_driver in resolved_drivers.items():
                df.loc[df['route_id'] == route, 'driver'] = resolved_driver
                
            # Deduplicate just in case
            filtered_df = df.drop_duplicates(subset=["route_id"]).copy()
            
            st.session_state["routes_df"] = filtered_df
            del st.session_state["driver_conflicts"]
            del st.session_state["unresolved_routes_df"]
            st.session_state["show_conflict_success"] = True
            st.rerun()

# 2. AI Schedule Extraction & Lane Configuration
st.header("2. Wave & Lane Configuration")
if schedule_img:
    if st.button("Extract Wave Schedule"):
        with st.spinner("Analyzing lane assignment image..."):
            wave_data = extract_wave_schedule(schedule_img)
            st.session_state["wave_data"] = wave_data
            
if "wave_data" in st.session_state and st.session_state["wave_data"] is not None:
    st.subheader("Confirm Lane Capacities")
    st.write("Adjust the lane capacities identified by the AI below:")
    
    wave_data = st.session_state["wave_data"]
    for w_idx, wave in enumerate(wave_data.get("waves", [])):
        st.markdown(f"**Wave {wave.get('wave_number')}**")
        
        # User can edit staging time in case the AI missed it
        current_time = wave.get("staging_time", "")
        new_time = st.text_input(f"Staging Time", value=current_time, key=f"time_{w_idx}")
        st.session_state["wave_data"]["waves"][w_idx]["staging_time"] = new_time
        
        lanes = wave.get("lanes", {})
        if lanes:
            cols = st.columns(len(lanes))
            new_lanes = {}
            for idx, (lane_name, capacity) in enumerate(lanes.items()):
                with cols[idx]:
                    new_cap = st.number_input(f"Lane {lane_name} Capacity", min_value=0, value=capacity, key=f"lane_{w_idx}_{lane_name}")
                    new_lanes[str(lane_name)] = new_cap
            st.session_state["wave_data"]["waves"][w_idx]["lanes"] = new_lanes
        else:
            st.warning("No lanes detected for this wave.")

# 3. Rapid Tagging UI
st.header("3. Route Tagging")

if "routes_df" in st.session_state:
    actual_routes = st.session_state["routes_df"]["route_id"].tolist()
else:
    actual_routes = []
    st.info("Extract Route Data above to load routes for tagging.")

island_routes = st.multiselect("Select Island Routes (Requires Pass)", actual_routes)
rural_north_routes = st.multiselect("Select Rural North Routes (Requires AWD/FWD)", [r for r in actual_routes if r not in island_routes])
rural_west_routes = st.multiselect("Select Rural West Routes (Requires AWD/FWD)", [r for r in actual_routes if r not in island_routes and r not in rural_north_routes])

rural_routes = rural_north_routes + rural_west_routes

# 4. Auto-Assign
st.header("4. Dispatch Assignments")
if st.button("Run Auto-Assign Algorithm"):
    if "routes_df" in st.session_state:
        routes_df = st.session_state["routes_df"]
        
        # Step 2: Fetch DB state
        try:
            fleet_res = supabase.table('fleet').select('*').eq('status', 'active').execute()
            available_vans = fleet_res.data
            
            drivers_res = supabase.table('drivers').select('*').execute()
            drivers = drivers_res.data
        except Exception as e:
            st.error(f"Failed to fetch data from Supabase. Ensure tables are created and key is set in db.py. Error: {e}")
            available_vans = []
            drivers = []
            
        # Step 2.5: Auto-add new drivers
        def normalize_name(name):
            return " ".join(str(name).lower().replace("-", " ").strip().split())

        def driver_exists(excel_driver_name):
            excel_norm = normalize_name(excel_driver_name)
            # Fuzzy match
            db_names = [normalize_name(d['driver_name']) for d in drivers]
            import difflib
            matches = difflib.get_close_matches(excel_norm, db_names, n=1, cutoff=0.5)
            if matches: return True
            
            # Substring Match Fallback
            for db_name in db_names:
                if db_name and (db_name in excel_norm or excel_norm in db_name): return True
            return False

        new_drivers_added = []
        for excel_driver in routes_df['driver'].unique():
            if not str(excel_driver).strip(): continue
            if not driver_exists(excel_driver):
                new_name = str(excel_driver).strip()
                try:
                    supabase.table('drivers').insert({'driver_name': new_name, 'vehicle_restriction': ''}).execute()
                    drivers.append({'driver_name': new_name, 'vehicle_restriction': ''})
                    new_drivers_added.append(new_name)
                except Exception as e:
                    st.error(f"Failed to add new driver {new_name}: {e}")
                    
        if new_drivers_added:
            st.success(f"Automatically added {len(new_drivers_added)} new driver(s) to the database: {', '.join(new_drivers_added)}")
        
        # Step 3: Run algorithm
        tags = {"island": island_routes, "rural": rural_routes}
        wave_config = st.session_state.get("wave_data", {})
        
        assignments = run_dispatch_algorithm(routes_df, wave_config, tags, available_vans, drivers)
        
        # Sort assignments by integer van number
        def safe_van_int(v):
            try:
                return int(v)
            except:
                return 9999
        assignments['van_sort'] = assignments['van'].apply(safe_van_int)
        assignments = assignments.sort_values('van_sort').drop(columns=['van_sort']).reset_index(drop=True)
        
        # Add route count for confirmation
        assignments['route_count'] = assignments.index + 1
        
        st.session_state["assignments"] = assignments
    else:
        st.warning("Please extract route data first.")

# 5. Review & Edit
if "assignments" in st.session_state:
    st.subheader("Review & Manual Override")
    
    df_assign = st.session_state["assignments"].copy()
    
    with st.expander("🔄 Quick Swap / Reassign Vans", expanded=False):
        st.write("Use this tool to safely move a driver to a different van or swap two vans without losing route data.")
        qs_col1, qs_col2, qs_col3 = st.columns([2, 2, 1])
        
        # Unique valid drivers
        valid_drivers = sorted(df_assign[df_assign['driver'].str.strip().astype(bool)]['driver'].unique())
        
        with qs_col1:
            driver_to_move = st.selectbox("Select Driver to Move", options=valid_drivers, key="qs_driver")
            
        with qs_col2:
            # All possible vans 1-45
            all_vans_str = [str(i) for i in range(1, 46)]
            target_van = st.selectbox("Select Target Van", options=all_vans_str, key="qs_van")
            
        with qs_col3:
            st.write("") # spacing
            st.write("")
            if st.button("Swap / Move Van"):
                if driver_to_move:
                    driver_idx = df_assign[df_assign['driver'] == driver_to_move].index[0]
                    current_van = str(df_assign.at[driver_idx, 'van']).strip()
                    
                    if current_van != target_van:
                        occupied_idx = df_assign[df_assign['van'].astype(str).str.strip() == target_van].index
                        
                        if not occupied_idx.empty:
                            # Swap with all occupants found
                            df_assign.at[driver_idx, 'van'] = target_van
                            df_assign.loc[occupied_idx, 'van'] = current_van
                            st.success(f"Swapped Vans: {driver_to_move} is now in {target_van}. Previous occupant(s) moved to {current_van}.")
                        else:
                            # Just move
                            df_assign.at[driver_idx, 'van'] = target_van
                            st.success(f"Moved {driver_to_move} to Van {target_van}.")
                            
                        st.session_state["assignments"] = df_assign
                        st.rerun()
                    else:
                        st.info("Driver is already in that van.")

    col_order = ["route_count", "route_id", "van", "driver", "lane", "wave_time", "packages", "bags", "overflow"]
    edited_df = st.data_editor(st.session_state["assignments"], num_rows="dynamic", width="stretch", column_order=col_order)
    st.session_state["assignments"] = edited_df
    
    st.subheader("Whiteboard Export")
    st.write("Highlight and copy the table below to paste directly into your digital whiteboard! It is sorted by Van 1-45.")
    
    # Fetch all vans to get statuses (including inactive ones) for the fleet notes
    try:
        all_fleet_res = supabase.table('fleet').select('*').execute()
        fleet_dict = {str(v['van_number']): v.get('status', '') for v in all_fleet_res.data}
    except:
        fleet_dict = {}

    def get_location(r_id):
        if r_id in rural_north_routes:
            return "Rural North"
        elif r_id in rural_west_routes:
            return "Rural West"
        else:
            return "Central Brunswick"
            
    wb_data = []
    for i in range(1, 46):
        van_str = str(i)
        assigned = edited_df[edited_df['van'] == van_str]
        
        status_note = fleet_dict.get(van_str, "")
        if status_note.lower() == "active":
            status_note = ""
            
        if not assigned.empty:
            driver = assigned.iloc[0].get('driver', '')
            cx = assigned.iloc[0].get('route_id', '')
            location = get_location(cx)
            wave = assigned.iloc[0].get('wave_time', '')
            lane = assigned.iloc[0].get('lane', '')
            pkgs = assigned.iloc[0].get('packages', '')
            bags_ct = assigned.iloc[0].get('bags', '')
            over_ct = assigned.iloc[0].get('overflow', '')
        else:
            driver = cx = location = wave = lane = pkgs = bags_ct = over_ct = "\xa0"
            
        wb_data.append({
            "Name": driver if str(driver).strip() else "\xa0",
            "CX": cx if str(cx).strip() else "\xa0",
            "Location": location if str(location).strip() else "\xa0",
            "Wave Times": wave if str(wave).strip() else "\xa0",
            "Lane": lane if str(lane).strip() else "\xa0",
            "Packages": pkgs if str(pkgs).strip() else "\xa0",
            "Bags": bags_ct if str(bags_ct).strip() else "\xa0",
            "Overflow": over_ct if str(over_ct).strip() else "\xa0",
            "Returned": "\xa0",
            "Fleet Note": status_note if status_note else "\xa0"
        })
        
    wb_df = pd.DataFrame(wb_data)
    
    # Get unique wave times, excluding empty
    unique_waves = sorted([w for w in wb_df['Wave Times'].unique() if str(w).strip()])
    wave_colors = ['#e6f2ff', '#e6ffe6', '#fff2e6', '#ffe6e6', '#f2e6ff', '#e6ffff'] # Light pastel colors
    wave_color_map = {wave: wave_colors[i % len(wave_colors)] for i, wave in enumerate(unique_waves)}

    def style_digital_board(row):
        # 1. Vehicles with damage/issues (Fleet Note is not empty)
        fleet_note = str(row['Fleet Note']).strip()
        if fleet_note:
            return ['background-color: #ffcccc; color: black;'] * len(row) # Light Red
            
        # 2. Different wave times have different colors
        wave = str(row['Wave Times']).strip()
        if wave and wave in wave_color_map:
            color = wave_color_map[wave]
            return [f'background-color: {color}; color: black;'] * len(row)
            
        # 3. Unused rows explicitly set to white
        return ['background-color: #ffffff; color: black;'] * len(row)

    # Apply styles and set HTML table attributes so borders copy nicely
    styled_df = wb_df.style.apply(style_digital_board, axis=1)
    styled_df = styled_df.set_table_attributes('border="1" style="border-collapse: collapse; text-align: center; width: 100%; font-family: sans-serif;"')
    
    st.markdown(styled_df.hide(axis="index").to_html(), unsafe_allow_html=True)
    
    # 6. PDF Generation
    sort_by_wave = st.checkbox("Sort printed sheets by Wave Time", value=True)
    if st.button("Generate Stamped PDF"):
        stamped_pdf = generate_stamped_pdf(route_pdf, edited_df, sort_by_wave=sort_by_wave)
        if stamped_pdf is not None:
            st.session_state["stamped_pdf"] = stamped_pdf
            
    if "stamped_pdf" in st.session_state:
        st.download_button(
            label="Download Stamped Route Sheets",
            data=st.session_state["stamped_pdf"],
            file_name="stamped_route_sheets.pdf",
            mime="application/pdf"
        )
