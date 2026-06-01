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

def check_password():
    if "authenticated" not in st.session_state:
        st.session_state["authenticated"] = False

    if not st.session_state["authenticated"]:
        st.title("🔒 Access Restricted")
        passcode = st.text_input("Enter Passcode:", type="password")
        if st.button("Submit"):
            if passcode == "111020":
                st.session_state["authenticated"] = True
                st.rerun()
            else:
                st.error("Incorrect passcode.")
        st.stop()
