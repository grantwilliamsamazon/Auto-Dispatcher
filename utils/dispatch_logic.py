import pandas as pd
import datetime
import streamlit as st

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
        def search_vans(enforce_prefer, allow_no_camera, allow_new_van):
            for i, v in enumerate(available_vans):
                if req_func and not req_func(v): continue
                if enforce_prefer and prefer_func and not prefer_func(v): continue
                if not passes_restriction(v): continue
                
                v_tags = v.get("tags", [])
                
                # If it's a new van and we aren't allowing them yet, skip
                if "new_van" in v_tags:
                    if not is_safe: continue # Never allow unsafe drivers in new vans
                    if not allow_new_van: continue
                    
                # If it has no camera and we aren't allowing them yet, skip
                if "no_camera" in v_tags:
                    if not allow_no_camera: continue
                    
                return i
            return -1

        # Pass 1: Strict (prefer match, avoid no_camera, avoid new_van)
        idx = search_vans(enforce_prefer=True, allow_no_camera=False, allow_new_van=False)
        if idx != -1: return available_vans.pop(idx)
        
        # Pass 2: Drop prefer (avoid no_camera, avoid new_van)
        if prefer_func:
            idx = search_vans(enforce_prefer=False, allow_no_camera=False, allow_new_van=False)
            if idx != -1: return available_vans.pop(idx)
            
        # Pass 3: Allow no_camera, avoid new_van, keep prefer
        idx = search_vans(enforce_prefer=True, allow_no_camera=True, allow_new_van=False)
        if idx != -1: return available_vans.pop(idx)
        
        # Pass 4: Allow no_camera, avoid new_van, drop prefer
        idx = search_vans(enforce_prefer=False, allow_no_camera=True, allow_new_van=False)
        if idx != -1: return available_vans.pop(idx)

        # Pass 5: Allow new_van, keep prefer
        idx = search_vans(enforce_prefer=True, allow_no_camera=True, allow_new_van=True)
        if idx != -1: return available_vans.pop(idx)
        
        # Pass 6: Allow new_van, drop prefer
        idx = search_vans(enforce_prefer=False, allow_no_camera=True, allow_new_van=True)
        if idx != -1: return available_vans.pop(idx)
        
        # Fallback 1: Drop req_func (like Heavy route requirement) but KEEP driver restriction!
        # This prevents a 'No ford' driver from being forced into a Ford just because it's a Heavy route.
        for i, v in enumerate(available_vans):
            if not passes_restriction(v): continue
            v_tags = v.get("tags", [])
            if "new_van" in v_tags and not is_safe: continue
            return available_vans.pop(i)
            
        # Fallback 2: The absolute last resort, drop driver restrictions.
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
