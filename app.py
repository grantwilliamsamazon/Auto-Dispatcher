import streamlit as st
import pandas as pd
import google.generativeai as genai
import fitz  # PyMuPDF
import io
import json
import datetime
from db import init_supabase, init_gemini
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

GEMINI_API_KEY = init_gemini()
supabase = init_supabase()

if not GEMINI_API_KEY or GEMINI_API_KEY == "YOUR_GEMINI_KEY":
    st.warning("Please update YOUR_GEMINI_KEY in db.py to use AI extraction.")


# --- Core Logic Stubs ---

def extract_pdf_data(route_sheet_pdf, cortex_excel):
    """
    Extracts route volumes and Amazon-Assigned Wave Times.
    
    1. Uses PyMuPDF (fitz) to read the blank Route Pick Sheets (PDF).
       Extracts each Route ID (e.g., CX-10) and its Volume (Packages/Stops).
    2. Reads the Cortex Roster (Excel) using pandas to pull driver assignments.
    3. Merges data into a unified DataFrame.
    """
    import re
    
    # 1. Excel Parsing
    df_excel = pd.read_excel(cortex_excel)
    if 'Route code' in df_excel.columns and 'Driver name' in df_excel.columns:
        df_excel = df_excel[['Route code', 'Driver name']].copy()
        df_excel = df_excel.rename(columns={'Route code': 'route_id', 'Driver name': 'driver'})
    else:
        st.error("Excel missing 'Route code' or 'Driver name' columns.")
        df_excel = pd.DataFrame(columns=['route_id', 'driver'])

    # 2. PDF Parsing
    doc = fitz.open(stream=route_sheet_pdf.read(), filetype="pdf")
    pdf_data = []
    
    for page_num in range(len(doc)):
        page = doc[page_num]
        text = page.get_text("text")
        lines = [line.strip() for line in text.split('\n') if line.strip()]
        
        if len(lines) < 5:
            continue
            
        route_id = lines[1]
        wave_line = lines[3]
        
        # Extract wave time (e.g., 10:45 AM)
        time_match = re.search(r'(\d{1,2}:\d{2}\s*[APM]{2})', wave_line, re.IGNORECASE)
        wave_time = time_match.group(1).upper() if time_match else "Unknown"
        
        bags = 0
        overflow = 0
        packages = 0
        
        if page_num == 0:
            st.session_state["debug_pdf_text"] = text
            
        text_lower = text.lower()
        b_match = re.search(r'(\d+)\D{0,15}?bags?', text_lower)
        if b_match: bags = int(b_match.group(1))
        
        # Try before: catches "overflow", "oversize", "overow", "overﬂow" (unicode ligature), "over ow", etc.
        o_match = re.search(r'(\d+)\D{0,15}?(?:over.{0,3}ow|over\s*size)', text_lower)
        if o_match: 
            overflow = int(o_match.group(1))
        else:
            # Try after
            o_match_after = re.search(r'(?:over.{0,3}ow|over\s*size)\D{0,15}?(\d+)', text_lower)
            if o_match_after:
                overflow = int(o_match_after.group(1))
        
        for i, line in enumerate(lines):
            if "Total Packages" in line and i + 1 < len(lines):
                try:
                    packages = int(lines[i+1])
                except ValueError:
                    pass
                break
                
        pdf_data.append({
            "route_id": route_id,
            "packages": packages,
            "bags": bags,
            "overflow": overflow,
            "wave_time": wave_time,
            "page_num": page_num
        })
        
    df_pdf = pd.DataFrame(pdf_data)
    
    # Group by route_id so multi-page routes have a list of pages, ensuring 1 row per route
    if not df_pdf.empty:
        df_pdf = df_pdf.groupby('route_id').agg({
            'packages': 'max',
            'bags': 'max',
            'overflow': 'max',
            'wave_time': 'first',
            'page_num': lambda x: list(x)
        }).reset_index()
    
    # Reset file pointer for later use (like stamping)
    route_sheet_pdf.seek(0)
    
    # 3. Merge Data
    if not df_pdf.empty:
        df_merged = pd.merge(df_pdf, df_excel, on="route_id", how="left")
        # clean up exact duplicate rows from Excel, ignoring the unhashable page_num list
        df_merged = df_merged.drop_duplicates(subset=[c for c in df_merged.columns if c != 'page_num'])
        # Fill missing drivers with empty string
        df_merged['driver'] = df_merged['driver'].fillna('')
        
        # Check for multiple drivers on a single route (pipe-separated or duplicate rows)
        conflicts = {}
        for route_id, group in df_merged.groupby("route_id"):
            all_drivers = []
            for d in group['driver']:
                d_str = str(d).strip()
                if not d_str:
                    continue
                if '|' in d_str:
                    all_drivers.extend([x.strip() for x in d_str.split('|') if x.strip()])
                else:
                    all_drivers.append(d_str)
                    
            unique_drivers = list(set(all_drivers))
            if len(unique_drivers) > 1:
                conflicts[route_id] = unique_drivers
                
        if conflicts:
            st.session_state["driver_conflicts"] = conflicts
            st.session_state["unresolved_routes_df"] = df_merged
            st.warning("Driver conflicts detected! Please resolve them below.")
            return None
            
    else:
        df_merged = pd.DataFrame(columns=['route_id', 'driver', 'packages', 'wave_time', 'page_num'])
        
    st.success(f"Successfully extracted {len(df_merged)} routes!")
    return df_merged

def extract_wave_schedule(image_file):
    """
    Uses Gemini 2.5 Flash to extract BVIS waves from the whiteboard image.
    Ignores LUMA waves.
    Returns JSON mapping: Wave Number, Staging Time, target lanes, total capacity per lane.
    """
    if not GEMINI_API_KEY:
        st.error("Gemini API Key missing. Cannot extract schedule.")
        return None
        
    from PIL import Image
    
    img = Image.open(image_file)
    model = genai.GenerativeModel('gemini-2.5-flash')
    
    prompt = """
    You are an AI assistant helping an Amazon delivery dispatcher. You are looking at an image of a whiteboard showing "Load Out Placement" or wave schedules.
    Extract the schedule ONLY for the "BVIS" waves. Ignore any waves or rows that say "LUMA".
    
    Return the data as a strict JSON object matching this exact structure:
    {
      "waves": [
        {
          "wave_number": "Integer (e.g., 3)",
          "staging_time": "String (e.g., '10:45 am')",
          "lanes": {
             "Lane Name/Number (e.g., '4')": "Integer capacity (e.g., 7)",
             "Another Lane": "Integer capacity"
          }
        }
      ]
    }
    
    For the lanes, the column headers are usually "Lane 4", "Lane 3", etc. Use just the number or name as the key (e.g., "4", "3").
    The cells contain the capacity and the company (e.g., "7 BVIS"). Extract only the integer for the capacity.
    Do NOT wrap the response in markdown code blocks, return raw JSON.
    """
    
    try:
        response = model.generate_content(
            [prompt, img],
            generation_config=genai.GenerationConfig(
                response_mime_type="application/json",
            )
        )
        data = json.loads(response.text)
        st.success("Successfully extracted wave schedule!")
        return data
    except Exception as e:
        st.error(f"Error during extraction: {e}")
        return None

def run_dispatch_algorithm(routes_df, wave_data, tags, available_vans, drivers):
    """
    The core constraint-satisfaction Auto-Assign logic.
    """
    df = routes_df.copy()
    
    # Sort available vans numerically so they are assigned in order (1, 2, 3...)
    def get_van_sort_key(v):
        try:
            return int(v['van_number'])
        except:
            return 999
    available_vans.sort(key=get_van_sort_key)
    
    def parse_time(time_str):
        try:
            return pd.to_datetime(time_str, format='%I:%M %p').time()
        except:
            try:
                return pd.to_datetime(time_str).time()
            except:
                return datetime.time(0, 0)
                
    df['parsed_time'] = df['wave_time'].apply(parse_time)
    df = df.sort_values(by='parsed_time').reset_index(drop=True)
    
    df["van"] = ""
    df["lane"] = ""
    
    island_routes = tags.get("island", [])
    rural_routes = tags.get("rural", [])
    
    def normalize_name(name):
        return " ".join(str(name).lower().replace("-", " ").strip().split())

    def get_driver_match(excel_driver_name):
        excel_norm = normalize_name(excel_driver_name)
        excel_tokens = set(excel_norm.split())
        
        # 1. Exact match (normalized)
        for d in drivers:
            db_norm = normalize_name(d['driver_name'])
            if db_norm == excel_norm:
                return d
                
        # 2. Fuzzy match (handle Jaquez- cody e -> cody eagle)
        import difflib
        db_names = [normalize_name(d['driver_name']) for d in drivers]
        matches = difflib.get_close_matches(excel_norm, db_names, n=1, cutoff=0.5)
        if matches:
            match_name = matches[0]
            for d in drivers:
                if normalize_name(d['driver_name']) == match_name:
                    return d
                    
        # 3. Partial substring match as fallback
        for d in drivers:
            db_norm = normalize_name(d['driver_name'])
            if db_norm and (db_norm in excel_norm or excel_norm in db_norm):
                return d
                
        return None
    
    def pop_best_van(req_func, driver, prefer_func=None):
        unassigned_count = len(df[df["van"] == ""])
        buffer_count = len(available_vans) - unassigned_count
        
        driver_record = get_driver_match(driver)
        restriction = driver_record.get('vehicle_restriction', None) if driver_record else None
        is_safe = driver_record.get('is_safe', False) if driver_record else False
        
        def passes_restriction(v):
            if not restriction or str(restriction).strip() == "":
                return True
            res_str = str(restriction).strip().lower()
            make = str(v.get("make", "")).lower()
            if res_str.startswith("no "):
                return make != res_str[3:].strip()
            return make == res_str

        # Helper to search vans
        def search_vans(enforce_buffer, enforce_prefer):
            for i, v in enumerate(available_vans):
                if req_func and not req_func(v): continue
                if enforce_prefer and prefer_func and not prefer_func(v): continue
                if not passes_restriction(v): continue
                
                v_tags = v.get("tags", [])
                if "new_van" in v_tags and not is_safe: continue
                if enforce_buffer and "no_camera" in v_tags and buffer_count > 0:
                    continue
                return i
            return -1

        # Pass 1: Strict (buffer + preference)
        idx = search_vans(enforce_buffer=True, enforce_prefer=True)
        if idx != -1:
            if "no_camera" in available_vans[idx].get("tags", []):
                buffer_count -= 1
            return available_vans.pop(idx)
            
        # Pass 2: Drop preference, keep buffer
        if prefer_func:
            idx = search_vans(enforce_buffer=True, enforce_prefer=False)
            if idx != -1:
                if "no_camera" in available_vans[idx].get("tags", []):
                    buffer_count -= 1
                return available_vans.pop(idx)
                
        # Pass 3: Drop buffer, keep preference
        idx = search_vans(enforce_buffer=False, enforce_prefer=True)
        if idx != -1: return available_vans.pop(idx)
        
        # Pass 4: Drop buffer and preference
        idx = search_vans(enforce_buffer=False, enforce_prefer=False)
        if idx != -1: return available_vans.pop(idx)
        
        # Third pass fallback: Drop req_func (like Heavy route requirement) but KEEP driver restriction!
        # This prevents a 'No ford' driver from being forced into a Ford just because it's a Heavy route.
        for i, v in enumerate(available_vans):
            if not passes_restriction(v): continue
            v_tags = v.get("tags", [])
            if "new_van" in v_tags and not is_safe: continue
            return available_vans.pop(i)
            
        # Fourth pass fallback: The absolute last resort, drop driver restrictions.
        for i, v in enumerate(available_vans):
            v_tags = v.get("tags", [])
            if "new_van" in v_tags and not is_safe: continue
            return available_vans.pop(i)
            
        return None

    # Pre-calculate heavy routes globally
    large_van_count = sum(1 for v in available_vans if v.get("size_class") == "Large")
    df_sorted_vol = df.sort_values(by=["overflow", "packages"], ascending=[False, False])
    heavy_routes = df_sorted_vol['route_id'].head(large_van_count).tolist()

    # Process wave by wave to maintain contiguous van assignments
    unique_waves = sorted(df['parsed_time'].unique())
    
    for wave in unique_waves:
        # 1. Island Routes in this wave
        for idx, row in df[(df['parsed_time'] == wave) & (df['route_id'].isin(island_routes)) & (df["van"] == "")].iterrows():
            van = pop_best_van(lambda v: "island_pass" in v.get("tags", []), row['driver'])
            if van: df.at[idx, "van"] = van["van_number"]
                    
        # 2. Rural/Dirt Routes in this wave
        for idx, row in df[(df['parsed_time'] == wave) & (df['route_id'].isin(rural_routes)) & (df["van"] == "")].iterrows():
            # Exclude RWD -> Require FWD or AWD, but PREFER Standard to save Large vans for Heavy routes
            van = pop_best_van(
                req_func=lambda v: v.get("drive_train") in ['FWD', 'AWD'], 
                driver=row['driver'],
                prefer_func=lambda v: v.get("size_class") == "Standard"
            )
            if van: df.at[idx, "van"] = van["van_number"]
                
        # 3. Heavy Routes in this wave
        for idx, row in df[(df['parsed_time'] == wave) & (df['route_id'].isin(heavy_routes)) & (df["van"] == "")].iterrows():
            van = pop_best_van(lambda v: v.get("size_class") == 'Large', row['driver'])
            if van: df.at[idx, "van"] = van["van_number"]
                
        # 4. The Leftovers in this wave
        for idx, row in df[(df['parsed_time'] == wave) & (df["van"] == "")].iterrows():
            # Prefer Standard to avoid eating up Large vans
            van = pop_best_van(
                req_func=lambda v: True, 
                driver=row['driver'],
                prefer_func=lambda v: v.get("size_class") == "Standard"
            )
            if van: df.at[idx, "van"] = van["van_number"]
            
    df = df.drop(columns=['parsed_time'])
            
    # Phase 3: Immutable Wave & Lane Parking
    if "waves" in wave_data and len(wave_data["waves"]) > 0:
        debug_logs = []
        for wave in wave_data["waves"]:
            # Make the matching robust (ignoring AM/PM and spaces)
            raw_time = wave.get("staging_time", "")
            w_time = str(raw_time).lower().replace("am", "").replace("pm", "").strip()
            
            # Find routes assigned to this exact wave time
            def match_time(val):
                return str(val).lower().replace("am", "").replace("pm", "").strip() == w_time
                
            wave_routes = df[df["wave_time"].apply(match_time)].index
            debug_logs.append(f"Wave {wave.get('wave_number')} (Time: '{raw_time}' -> '{w_time}') matched {len(wave_routes)} routes")
            
            lanes = wave.get("lanes", {})
            route_idx = 0
            
            # Fill lanes based on user-confirmed capacity
            for lane_name, cap in lanes.items():
                for _ in range(int(cap)):
                    if route_idx < len(wave_routes):
                        df.at[wave_routes[route_idx], "lane"] = str(lane_name)
                        route_idx += 1
                        
        st.info("Lane Assignment Debug: " + " | ".join(debug_logs))
    else:
        st.warning("⚠️ No Wave/Lane data was found! Did you forget to click 'Extract Wave Schedule' in Step 2 before running the algorithm?")
        
    st.success("Auto-Assign Algorithm Completed Successfully!")
    return df

def generate_stamped_pdf(pdf_file, assignments_df, sort_by_wave=False):
    """
    Stamps the assigned Driver Name, Van #, and Lane onto the respective pages of the Route Sheets PDF.
    Optionally sorts the pages chronologically by Wave Time.
    """
    try:
        # Reset file pointer and load bytes into PyMuPDF
        pdf_file.seek(0)
        pdf_bytes = pdf_file.read()
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        
        for idx, row in assignments_df.iterrows():
            pages = row.get("page_num", [])
            if not isinstance(pages, list):
                if pd.isna(pages): continue
                pages = [int(pages)]
                
            route_id = str(row.get("route_id", ""))
            driver = str(row.get("driver", "UNASSIGNED"))
            if driver == "nan" or driver.strip() == "": driver = "UNASSIGNED"
            
            van = str(row.get("van", "UNASSIGNED"))
            if van == "nan" or van.strip() == "": van = "UNASSIGNED"
            
            lane = str(row.get("lane", "UNASSIGNED"))
            if lane == "nan" or lane.strip() == "": lane = "UNASSIGNED"
            
            for p in pages:
                page_num = int(p)
                if page_num < 0 or page_num >= len(doc):
                    continue
                    
                page = doc[page_num]
                
                # Find the route ID text to anchor our Y-coordinate
                rects = page.search_for(route_id)
                if rects:
                    # Use the first occurrence of the Route ID, add ~20 points for padding below it
                    base_y = rects[0].y1 + 10 
                else:
                    base_y = 150 # Fallback Y coordinate if search fails
                    
                # Define exact regions based on user's image request
                # Red color (1, 0, 0), fontsize 20
                text_color = (1, 0, 0)
                font_size = 20
                
                # 1. Driver Name (Left aligned)
                name_rect = fitz.Rect(50, base_y, 250, base_y + 40)
                page.insert_textbox(name_rect, driver, fontsize=font_size, color=text_color, fontname="helv")
                
                # 2. Van # (Center aligned)
                van_rect = fitz.Rect(260, base_y, 400, base_y + 40)
                page.insert_textbox(van_rect, f"Van: {van}", fontsize=font_size, color=text_color, fontname="helv")
                
                # 3. Lane (Right aligned)
                lane_rect = fitz.Rect(410, base_y, 580, base_y + 40)
                page.insert_textbox(lane_rect, f"Lane: {lane}", fontsize=font_size, color=text_color, fontname="helv")
            
        if sort_by_wave:
            def get_time_val(time_str):
                try:
                    return datetime.datetime.strptime(str(time_str).strip(), "%I:%M %p").time()
                except:
                    return datetime.time(0,0)
            
            temp_df = assignments_df.copy()
            temp_df['time_val'] = temp_df['wave_time'].apply(get_time_val)
            temp_df = temp_df.sort_values(by='time_val')
            
            pno_list = []
            for pages in temp_df['page_num']:
                if not isinstance(pages, list):
                    if not pd.isna(pages):
                        pages = [pages]
                    else:
                        pages = []
                for p in pages:
                    if int(p) >= 0 and int(p) < len(doc):
                        pno_list.append(int(p))
                        
            # Add any extra pages (like cover sheets) that weren't in the assignments
            for i in range(len(doc)):
                if i not in pno_list:
                    pno_list.append(i)
                    
            doc.select(pno_list)
            
        out_pdf = io.BytesIO()
        doc.save(out_pdf)
        
        st.success("PDF Stamped successfully!")
        return out_pdf.getvalue()
        
    except Exception as e:
        st.error(f"Error stamping PDF: {str(e)}")
        return None


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
                            # Swap with the first occupant found
                            df_assign.at[driver_idx, 'van'] = target_van
                            df_assign.at[occupied_idx[0], 'van'] = current_van
                            st.success(f"Swapped Vans: {driver_to_move} is now in {target_van}. Previous occupant moved to {current_van}.")
                        else:
                            # Just move
                            df_assign.at[driver_idx, 'van'] = target_van
                            st.success(f"Moved {driver_to_move} to Van {target_van}.")
                            
                        st.session_state["assignments"] = df_assign
                        st.rerun()
                    else:
                        st.info("Driver is already in that van.")

    col_order = ["route_count", "route_id", "van", "driver", "lane", "wave_time", "packages", "bags", "overflow"]
    edited_df = st.data_editor(st.session_state["assignments"], num_rows="dynamic", use_container_width=True, column_order=col_order)
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
