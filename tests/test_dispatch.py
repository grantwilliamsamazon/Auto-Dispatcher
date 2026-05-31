import pytest
import pandas as pd
import random
from utils.dispatch_logic import run_dispatch_algorithm

def test_run_dispatch_algorithm_full_scale():
    # Set a seed so the test is deterministic
    random.seed(42)
    
    # --- 1. GENERATE 45 VANS ---
    available_vans = []
    for i in range(1, 46):
        # Let's make every 5th van a Large van, every 7th van an AWD, etc.
        size_class = "Large" if i % 5 == 0 else "Standard"
        drive_train = "AWD" if i % 7 == 0 else ("FWD" if i % 2 == 0 else "RWD")
        tags = []
        if i == 1: tags.append("island_pass")
        if i == 2: tags.append("island_pass")
        if i % 10 == 0: tags.append("no_camera")
        
        available_vans.append({
            "van_number": str(i),
            "make": "Ford" if i % 3 == 0 else "Dodge",
            "size_class": size_class,
            "drive_train": drive_train,
            "tags": tags
        })
        
    # --- 2. GENERATE 35 ROUTES ACROSS 3 WAVES ---
    waves_config = [
        {"time": "9:45 AM", "count": 12, "lanes": {"1": 6, "2": 6}},
        {"time": "10:15 AM", "count": 12, "lanes": {"3": 6, "4": 6}},
        {"time": "10:45 AM", "count": 11, "lanes": {"5": 6, "6": 5}},
    ]
    
    routes_data = []
    drivers = []
    
    route_counter = 1
    for w_idx, w_conf in enumerate(waves_config):
        for _ in range(w_conf["count"]):
            route_id = f"CX-{route_counter}"
            driver_name = f"Driver {route_counter}"
            
            # Make some routes heavy, some light
            packages = random.randint(100, 400)
            bags = packages // 15
            overflow = packages // 10
            
            routes_data.append({
                "route_id": route_id,
                "driver": driver_name,
                "packages": packages,
                "bags": bags,
                "overflow": overflow,
                "wave_time": w_conf["time"]
            })
            
            # Driver Restrictions
            restriction = ""
            if route_counter % 8 == 0: restriction = "No Ford"
            if route_counter % 11 == 0: restriction = "Dodge"
            
            drivers.append({
                "driver_name": driver_name,
                "vehicle_restriction": restriction,
                "is_safe": True
            })
            
            route_counter += 1
            
    routes_df = pd.DataFrame(routes_data)
    
    # Determine tags
    island_routes = ["CX-1", "CX-2"]
    rural_routes = ["CX-5", "CX-15", "CX-25", "CX-30"]
    tags = {"island": island_routes, "rural": rural_routes}
    
    wave_data = {
        "waves": [
            {"wave_number": 1, "staging_time": "9:45 AM", "lanes": {"1": 6, "2": 6}},
            {"wave_number": 2, "staging_time": "10:15 AM", "lanes": {"3": 6, "4": 6}},
            {"wave_number": 3, "staging_time": "10:45 AM", "lanes": {"5": 6, "6": 5}}
        ]
    }
    
    # Save original vans for validation since algorithm might mutate the list
    import copy
    original_vans = copy.deepcopy(available_vans)
    
    # --- 3. RUN ALGORITHM ---
    result_df = run_dispatch_algorithm(routes_df, wave_data, tags, available_vans, drivers)
    
    # --- 4. VALIDATION ---
    assert len(result_df) == 35, "Should have processed 35 routes"
    assert not result_df["van"].isnull().any(), "All routes should have a van"
    
    # Verify Island Routes
    island_results = result_df[result_df["route_id"].isin(island_routes)]
    for _, row in island_results.iterrows():
        # Find the van in original_vans
        van_str = str(row["van"]).split(".")[0] # handle float .0 if pandas inferred it
        van = next((v for v in original_vans if v["van_number"] == van_str), None)
        assert van is not None, f"Could not find van '{van_str}' in original_vans"
        assert "island_pass" in van["tags"], f"Route {row['route_id']} did not get an island pass van"
        
    # Verify Rural Routes
    rural_results = result_df[result_df["route_id"].isin(rural_routes)]
    for _, row in rural_results.iterrows():
        van_str = str(row["van"]).split(".")[0]
        van = next((v for v in original_vans if v["van_number"] == van_str), None)
        assert van["drive_train"] in ["AWD", "FWD"], f"Rural route {row['route_id']} got {van['drive_train']}"
        
    # Verify "No Ford" Restriction
    no_ford_drivers = [d["driver_name"] for d in drivers if d["vehicle_restriction"] == "No Ford"]
    no_ford_results = result_df[result_df["driver"].isin(no_ford_drivers)]
    for _, row in no_ford_results.iterrows():
        van_str = str(row["van"]).split(".")[0]
        van = next((v for v in original_vans if v["van_number"] == van_str), None)
        assert van["make"].lower() != "ford", f"Driver {row['driver']} with No Ford restriction got a Ford"
