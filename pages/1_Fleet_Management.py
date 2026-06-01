import streamlit as st
import pandas as pd
from db import init_supabase, check_password

st.set_page_config(page_title="Fleet Management Dashboard", page_icon="🚐", layout="wide")
check_password()
st.title("Fleet Management Dashboard")

supabase = init_supabase()

st.info("Make sure you have run the `supabase_setup.sql` script in your Supabase SQL editor and updated the `SUPABASE_KEY` in `db.py`.")

try:
    # Manage Vans
    st.header("Manage Fleet")
    fleet_res = supabase.table('fleet').select('*').execute()
    df_fleet = pd.DataFrame(fleet_res.data)
    
    if not df_fleet.empty:
        def safe_van_int(v):
            try:
                return int(v)
            except:
                return 9999
        df_fleet['van_sort'] = df_fleet['van_number'].apply(safe_van_int)
        df_fleet = df_fleet.sort_values('van_sort').drop(columns=['van_sort']).reset_index(drop=True)
        st.write("Edit the fleet details below. Add tags by typing them in the tags column (e.g. `['island_pass']`). Note that tags is a list.")
        edited_fleet = st.data_editor(df_fleet, num_rows="dynamic", use_container_width=True, key="fleet_editor")
        if st.button("Save Fleet Changes"):
            for idx, row in edited_fleet.iterrows():
                record = row.dropna().to_dict()
                # Simple parsing for tags if entered as string in UI
                if 'tags' in record and isinstance(record['tags'], str):
                    try:
                        import ast
                        record['tags'] = ast.literal_eval(record['tags'])
                    except:
                        pass
                if 'van_number' in record and record['van_number']:
                    supabase.table('fleet').upsert(record).execute()
            st.success("Fleet changes saved!")
    else:
        st.warning("Fleet table is empty. Please run the SQL setup script.")
        
    # Manage Drivers
    st.header("Manage Drivers")
    drivers_res = supabase.table('drivers').select('*').execute()
    df_drivers = pd.DataFrame(drivers_res.data)
    
    if not df_drivers.empty:
        edited_drivers = st.data_editor(df_drivers, num_rows="dynamic", use_container_width=True, key="drivers_editor")
        if st.button("Save Driver Changes"):
            for idx, row in edited_drivers.iterrows():
                record = row.dropna().to_dict()
                if 'driver_name' in record and record['driver_name']:
                    supabase.table('drivers').upsert(record).execute()
                    
            # Handle deletions
            original_names = set(df_drivers['driver_name'].dropna())
            current_names = set(edited_drivers['driver_name'].dropna())
            removed_names = original_names - current_names
            
            for name in removed_names:
                supabase.table('drivers').delete().eq('driver_name', name).execute()
                
            st.success("Driver changes saved!")
    else:
        st.info("No drivers in DB yet. Add rows below to restrict drivers.")
        new_df = pd.DataFrame(columns=["driver_name", "vehicle_restriction", "is_safe"])
        edited_drivers = st.data_editor(new_df, num_rows="dynamic", use_container_width=True, key="drivers_editor_new")
        if st.button("Save Driver Changes"):
            for idx, row in edited_drivers.iterrows():
                record = row.dropna().to_dict()
                if 'driver_name' in record and record['driver_name']:
                    supabase.table('drivers').upsert(record).execute()
            st.success("Driver changes saved! Refresh page to see them.")

except Exception as e:
    st.error(f"Failed to connect to Supabase or tables do not exist. Error: {e}")
