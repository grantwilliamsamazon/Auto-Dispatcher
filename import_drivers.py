import pandas as pd
from db import init_supabase
import re

def clean_name(name):
    # Remove extra spaces
    return re.sub(r'\s+', ' ', str(name)).strip()

def run():
    print("Initializing Supabase...")
    supabase = init_supabase()
    
    print("Reading AssociateData.csv...")
    df = pd.read_csv('AssociateData.csv')
    
    # Filter to only ACTIVE associates
    active_df = df[df['Status'].str.upper() == 'ACTIVE']
    
    names = active_df['Name and ID'].apply(clean_name).tolist()
    print(f"Found {len(names)} active drivers.")
    
    success_count = 0
    for name in names:
        if not name or name.lower() == 'nan':
            continue
        try:
            supabase.table('drivers').upsert({'driver_name': name}).execute()
            success_count += 1
        except Exception as e:
            print(f"Error inserting {name}: {e}")
            
    print(f"Successfully inserted {success_count} drivers into the database.")

if __name__ == "__main__":
    run()
