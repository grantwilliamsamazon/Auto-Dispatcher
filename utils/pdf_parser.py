import pandas as pd
import fitz  # PyMuPDF
import re
import io
import streamlit as st
import datetime

def extract_pdf_data(route_sheet_pdf, cortex_excel):
    """
    Extracts route volumes and Amazon-Assigned Wave Times.
    
    1. Uses PyMuPDF (fitz) to read the blank Route Pick Sheets (PDF).
       Extracts each Route ID (e.g., CX-10) and its Volume (Packages/Stops).
    2. Reads the Cortex Roster (Excel) using pandas to pull driver assignments.
    3. Merges data into a unified DataFrame.
    """
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
        
        # Extract wave time (e.g., 10:45 AM or 10:45 a.m.)
        time_match = re.search(r'(\d{1,2}:\d{2}\s*(?:[ap]\.?\s*m\.?))', wave_line, re.IGNORECASE)
        wave_time = time_match.group(1).upper().replace(".", "") if time_match else "Unknown"
        
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
