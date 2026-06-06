# DSP Auto-Dispatch Engine

## Overview
The DSP Auto-Dispatch Engine is a Streamlit-based web application designed to automate and streamline the dispatch operations for Amazon Delivery Service Partners (DSPs). By intelligently integrating route data, wave schedules, fleet constraints, and driver profiles, the system automatically generates optimal vehicle assignments, digital whiteboard exports, and stamped PDF pick sheets. 

This repository serves as the central codebase for the dispatch automation logic, UI, and integrations.

## Core Features
1. **Data Ingestion**: 
   - Upload Cortex Roster (Excel format) for driver assignments.
   - Upload Route Pick Sheets (PDF) to parse exact package, bag, and overflow volumes per route using PyMuPDF.
   - Upload Wave Schedule (Image) and extract the staging times and lane capacities automatically using Google's Gemini Vision AI.
2. **Constraint-Satisfaction Dispatch Algorithm**: 
   - Automatically matches drivers to appropriate vans based on complex rules:
     - **Driver Profiles**: Vehicle-specific restrictions (e.g., "Mercedes only", "no Ford"), and safety ratings (`is_safe`).
     - **Van Capabilities**: Special tags (e.g., `new_van`, `no_camera`, `island_pass`), drive train (AWD/FWD for rural routes), and size class (Standard vs. Large).
     - **Route Tags**: Supports rapid tagging for Island and Rural routes, strictly ensuring the correct van type is assigned.
3. **Interactive Resolution & Overrides**:
   - Built-in conflict resolution UI for when multiple drivers are listed on a single route.
   - "Quick Swap" interface for dispatchers to safely manually override and swap vans/drivers without losing route data.
4. **Export & Output**:
   - Generates a styled digital whiteboard table (color-coded by wave time and van status) for easy copy-pasting.
   - Automatically stamps the blank Route Sheets (PDF) with the assigned Driver Name, Van Number, and Lane, and optionally sorts the pages chronologically by Wave Time.
5. **Database Integration**:
   - Powered by Supabase to persistently manage the DSP's `fleet` (status, tags, make, drive train) and `drivers` (restrictions, safety ratings).

## Project Architecture & Key Files

### Frontend / UI (`app.py` & `pages/`)
- **`app.py`**: The main entry point for the Streamlit app. It orchestrates the 6-step dispatch workflow: (1) Upload, (2) Wave Configuration, (3) Route Tagging, (4) Dispatch Assignments, (5) Review & Override, and (6) PDF Generation. It holds the session state and logic for conflict resolution.
- **`pages/1_Fleet_Management.py`**: An administrative dashboard for managing the active fleet and driver profiles. Data is directly synchronized with the connected Supabase database.

### Backend / Core Utilities (`utils/`)
- **`utils/dispatch_logic.py`**: Contains the `run_dispatch_algorithm()` function. This is the heart of the engine, containing all logic for scoring and populating available vans (`pop_best_van()`), strictly enforcing restrictions (`passes_make_restriction()`), and properly grouping assignments into wave times and lanes.
- **`utils/pdf_parser.py`**: Contains PyMuPDF (`fitz`) logic for extracting text from route sheets via regex (`extract_pdf_data()`) and precisely stamping assignments back onto the PDF (`generate_stamped_pdf()`).
- **`utils/ai_extractor.py`**: Handles communication with the Gemini 2.5 Flash API to perform OCR and structure extraction on the whiteboard wave schedule images (`extract_wave_schedule()`).

### Configuration & Database (`db.py` & `supabase_setup.sql`)
- **`db.py`**: Initializes connection clients for Supabase and Google Gemini using Streamlit secrets. Also provides the simple `check_password()` authentication wrapper.
- **`supabase_setup.sql`**: The database schema required for the Supabase project, defining the `fleet` and `drivers` tables.

## Technology Stack
- **Framework**: [Streamlit](https://streamlit.io/)
- **Database**: [Supabase](https://supabase.com/) (PostgreSQL)
- **AI / Vision**: [Google GenAI (Gemini 2.5 Flash)](https://ai.google.dev/)
- **PDF Processing**: [PyMuPDF (fitz)](https://pymupdf.readthedocs.io/en/latest/)
- **Data Manipulation**: [Pandas](https://pandas.pydata.org/)

## Future AI Conversations & Feature Development
When planning future features or modifications, consider the following design patterns established in this codebase:
- **State Management**: The app relies heavily on `st.session_state` to pass DataFrames (e.g., `routes_df`, `assignments`) between the sequential UI steps in `app.py`. Ensure state is properly initialized or popped when re-running extraction.
- **Algorithm Constraints**: Any new constraints (e.g., new types of vans, new driver certifications) should be added to the `is_van_compatible()`, `passes_make_restriction()`, and the `pop_best_van` priority cascade in `utils/dispatch_logic.py`.
- **Database Schema**: If the algorithm requires new permanent state (e.g., driver seniority), it must be added to the `supabase_setup.sql` schema and handled in the data editor upsert logic within `pages/1_Fleet_Management.py`.
- **Secrets Management**: Third-party API keys (Supabase, Gemini) must route through `st.secrets` and `db.py`. Do not hardcode credentials in the feature logic.
