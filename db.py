import streamlit as st
from supabase import create_client, Client
import os

def init_supabase() -> Client:
    url = st.secrets.get("SUPABASE_URL", "")
    key = st.secrets.get("SUPABASE_KEY", "")
    return create_client(url, key)

def init_gemini():
    api_key = st.secrets.get("GEMINI_API_KEY", "")
    if api_key and api_key != "YOUR_GEMINI_KEY":
        os.environ["GEMINI_API_KEY"] = api_key
    return api_key
