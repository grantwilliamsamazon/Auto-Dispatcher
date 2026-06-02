import pandas as pd
import datetime
import streamlit as st

def run_dispatch_algorithm(routes_df, wave_data, tags, available_vans, drivers):
    """
    The core constraint-satisfaction Auto-Assign logic.
    """
    df = routes_df.copy()
    
    # Create a local copy of available_vans to avoid mutating the caller's list
    vans = [dict(v) for v in available_vans]
    
    # Sort available vans numerically so they are assigned in order (1, 2, 3...)
    def get_van_sort_key(v):
        try:
            return int(v['van_number'])
        except:
            return 999
    vans.sort(key=get_van_sort_key)
    
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
        # Quick exit if driver name is empty/missing to avoid matching empty strings/NaN
        if not excel_driver_name or pd.isna(excel_driver_name):
            return None
        excel_norm = normalize_name(excel_driver_name)
        if excel_norm in ["", "nan", "none", "null"]:
            return None
            
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

    def clean_van_44_restriction(restriction_str):
        if not restriction_str:
            return "", False
        res_str = str(restriction_str).strip().lower()
        keywords = ["van_44", "ram_44", "van 44", "ram 44", "44"]
        is_van_44_approved = False
        for kw in keywords:
            if kw in res_str:
                is_van_44_approved = True
                res_str = res_str.replace(kw, "")
        # Clean up commas, spaces, etc.
        res_str = res_str.replace(",", " ").strip()
        return res_str, is_van_44_approved

    def passes_make_restriction(v, driver_restriction):
        make = str(v.get("make", "")).strip().lower()
        van_num = str(v.get("van_number", "")).strip()
        v_tags = v.get("tags", [])
        
        # 1. Determine if the van is Van 44 (or tagged as such)
        is_van_44 = (van_num == "44") or any(
            t in v_tags for t in ["van_44", "ram_44", "van 44", "ram 44"]
        )
        
        # 2. Extract Van 44 approval and clean driver restriction
        res_str, is_van_44_approved = clean_van_44_restriction(driver_restriction)
        
        # 3. If it is Van 44, the driver MUST be approved
        if is_van_44 and not is_van_44_approved:
            return False
            
        # 4. If the van is a Mercedes, the driver MUST have a Mercedes restriction
        if make == "mercedes":
            return res_str == "mercedes"
            
        # 5. If the driver has a Mercedes restriction, they can ONLY drive a Mercedes
        if res_str == "mercedes":
            return make == "mercedes"
            
        # 6. Apply remaining make restrictions
        if res_str == "":
            return True
        if res_str.startswith("no "):
            return make != res_str[3:].strip()
        return make == res_str

    def is_van_compatible(v, row, driver_record):
        # 1. Check driver restriction
        restriction = driver_record.get('vehicle_restriction', None) if driver_record else None
        if not passes_make_restriction(v, restriction):
            return False
                
        # 2. Check driver safety for new vans
        is_safe = driver_record.get('is_safe', False) if driver_record else False
        v_tags = v.get("tags", [])
        if "new_van" in v_tags and not is_safe:
            return False
            
        # 3. Check route specific requirements
        route_id = row['route_id']
        if route_id in island_routes:
            if "island_pass" not in v_tags:
                return False
        if route_id in rural_routes:
            if v.get("drive_train") not in ['FWD', 'AWD']:
                return False
                
        return True
    
    def pop_best_van(req_func, driver, prefer_func=None):
        driver_record = get_driver_match(driver)
        restriction = driver_record.get('vehicle_restriction', None) if driver_record else None
        is_safe = driver_record.get('is_safe', False) if driver_record else False
        
        def passes_restriction(v):
            return passes_make_restriction(v, restriction)

        # Helper to search vans
        def search_vans(enforce_prefer, allow_no_camera, allow_new_van):
            for i, v in enumerate(vans):
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
        if idx != -1: return vans.pop(idx)
        
        # Pass 2: Drop prefer (avoid no_camera, avoid new_van)
        if prefer_func:
            idx = search_vans(enforce_prefer=False, allow_no_camera=False, allow_new_van=False)
            if idx != -1: return vans.pop(idx)
            
        # Pass 3: Allow no_camera, avoid new_van, keep prefer
        idx = search_vans(enforce_prefer=True, allow_no_camera=True, allow_new_van=False)
        if idx != -1: return vans.pop(idx)
        
        # Pass 4: Allow no_camera, avoid new_van, drop prefer
        idx = search_vans(enforce_prefer=False, allow_no_camera=True, allow_new_van=False)
        if idx != -1: return vans.pop(idx)

        # Pass 5: Allow new_van, keep prefer
        idx = search_vans(enforce_prefer=True, allow_no_camera=True, allow_new_van=True)
        if idx != -1: return vans.pop(idx)
        
        # Pass 6: Allow new_van, drop prefer
        idx = search_vans(enforce_prefer=False, allow_no_camera=True, allow_new_van=True)
        if idx != -1: return vans.pop(idx)
        
        # Fallback 1: The absolute last resort, drop driver restrictions but KEEP req_func!
        # This prevents an AWD-required route from getting a RWD van.
        for i, v in enumerate(vans):
            if req_func and not req_func(v): continue
            v_tags = v.get("tags", [])
            if "new_van" in v_tags and not is_safe: continue
            return vans.pop(i)
            
        return None

    # Pre-calculate heavy routes globally
    # Only force Heavy Routes if we actually plan to use Large vans (excluding bad ones if we have enough buffer)
    def get_van_tier(v):
        tags = v.get("tags", [])
        if "new_van" in tags: return 2
        if "no_camera" in tags: return 1
        return 0
        
    def get_van_sort_key_for_count(v):
        num = 999
        try: num = int(v['van_number'])
        except: pass
        return (get_van_tier(v), num)
        
    sorted_vans_for_count = sorted(vans, key=get_van_sort_key_for_count)
    vans_to_use = sorted_vans_for_count[:len(df)]
    large_van_count = sum(1 for v in vans_to_use if v.get("size_class") == "Large")
    
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
            # PREFER Large vans, but do not strictly require them, so we don't force assignment to bad vans.
            van = pop_best_van(
                req_func=lambda v: True, 
                driver=row['driver'],
                prefer_func=lambda v: v.get("size_class") == 'Large'
            )
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

    # 5. Resolve greedy-assignment conflicts where safe drivers took standard vans,
    # leaving unsafe drivers with only new/restricted vans.
    unassigned_indices = df[df["van"] == ""].index.tolist()
    if unassigned_indices and vans:
        all_vans_lookup = {v["van_number"]: v for v in available_vans}
        for idx_unassigned in unassigned_indices:
            row_unassigned = df.loc[idx_unassigned]
            driver_unassigned = row_unassigned['driver']
            driver_unassigned_record = get_driver_match(driver_unassigned)
            
            for i_v, v_avail in enumerate(vans):
                swap_found = False
                for idx_assigned, row_assigned in df[df["van"] != ""].iterrows():
                    v_assigned_num = row_assigned["van"]
                    v_assigned = all_vans_lookup.get(v_assigned_num)
                    if not v_assigned:
                        continue
                        
                    driver_assigned = row_assigned['driver']
                    driver_assigned_record = get_driver_match(driver_assigned)
                    
                    if is_van_compatible(v_avail, row_assigned, driver_assigned_record) and \
                       is_van_compatible(v_assigned, row_unassigned, driver_unassigned_record):
                        # Perform swap!
                        df.at[idx_assigned, "van"] = v_avail["van_number"]
                        df.at[idx_unassigned, "van"] = v_assigned["van_number"]
                        
                        # Remove v_avail from the available pool
                        vans.pop(i_v)
                        swap_found = True
                        break
                if swap_found:
                    break
            
    df = df.drop(columns=['parsed_time'])
            
    # Phase 3: Immutable Wave & Lane Parking
    if "waves" in wave_data and len(wave_data["waves"]) > 0:
        debug_logs = []
        for wave in wave_data["waves"]:
            # Make the matching robust (ignoring AM/PM, spaces, and leading zeros)
            raw_time = wave.get("staging_time", "")
            
            def clean_time(t_str):
                cleaned = str(t_str).lower().replace("am", "").replace("pm", "").replace(".", "").strip()
                if cleaned.startswith("0") and len(cleaned) > 1:
                    cleaned = cleaned[1:]
                return cleaned
                
            w_time = clean_time(raw_time)
            
            # Find routes assigned to this exact wave time
            def match_time(val):
                return clean_time(val) == w_time
                
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
