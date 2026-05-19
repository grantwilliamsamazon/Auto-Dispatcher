import pandas as pd

wave_data = {
    "waves": [
        {
            "wave_number": 3,
            "staging_time": "10:45 AM",
            "lanes": {"4": 7, "3": 7, "2": 1}
        },
        {
            "wave_number": 4,
            "staging_time": "11:05 AM",
            "lanes": {"4": 7, "3": 7, "2": 1}
        }
    ]
}

df = pd.DataFrame({
    "route_id": ["CX" + str(i) for i in range(1, 20)],
    "wave_time": ["10:45 AM"] * 10 + ["11:05 AM"] * 9,
    "van": [""] * 19,
    "lane": [""] * 19
})

for wave in wave_data["waves"]:
    w_time = str(wave.get("staging_time", "")).lower().replace("am", "").replace("pm", "").strip()
    
    def match_time(val):
        return str(val).lower().replace("am", "").replace("pm", "").strip() == w_time
        
    wave_routes = df[df["wave_time"].apply(match_time)].index
    print(f"Wave {wave['wave_number']} w_time: '{w_time}' matched {len(wave_routes)} routes")
    
    lanes = wave.get("lanes", {})
    route_idx = 0
    for lane_name, cap in lanes.items():
        for _ in range(cap):
            if route_idx < len(wave_routes):
                df.at[wave_routes[route_idx], "lane"] = str(lane_name)
                route_idx += 1

print(df)
