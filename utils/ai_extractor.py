import json
import streamlit as st
from google import genai
from google.genai import types
from PIL import Image
import os

def extract_wave_schedule(image_file):
    """
    Uses Gemini 2.5 Flash to extract BVIS waves from the whiteboard image.
    Ignores LUMA waves.
    Returns JSON mapping: Wave Number, Staging Time, target lanes, total capacity per lane.
    """
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key or api_key == "YOUR_GEMINI_KEY":
        st.error("Gemini API Key missing. Cannot extract schedule.")
        return None
        
    img = Image.open(image_file)
    client = genai.Client()
    
    prompt = """
    You are an AI assistant helping an Amazon delivery dispatcher. You are looking at an image of a whiteboard showing "Load Out Placement" or wave schedules.
    Extract the schedule ONLY for the "BVIS" waves. Ignore any waves or rows that say "LUMA".
    
    Return the data as a strict JSON object matching this exact structure:
    {
      "waves": [
        {
          "wave_number": "Integer (e.g., 3)",
          "staging_time": "String (e.g., '10:45 am')",
          "lanes": {
             "Lane Name/Number (e.g., '4')": "Integer capacity (e.g., 7)",
             "Another Lane": "Integer capacity"
          }
        }
      ]
    }
    
    For the lanes, the column headers are usually "Lane 4", "Lane 3", etc. Use just the number or name as the key (e.g., "4", "3").
    The cells contain the capacity and the company (e.g., "7 BVIS"). Extract only the integer for the capacity.
    Do NOT wrap the response in markdown code blocks, return raw JSON.
    """
    
    try:
        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=[prompt, img],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
            )
        )
        
        raw_text = response.text.strip()
        
        # Remove markdown formatting if present
        if raw_text.startswith("```json"):
            raw_text = raw_text[7:]
        elif raw_text.startswith("```"):
            raw_text = raw_text[3:]
        if raw_text.endswith("```"):
            raw_text = raw_text[:-3]
            
        raw_text = raw_text.strip()
        
        # Fix common JSON errors like trailing commas
        import re
        raw_text = re.sub(r',\s*}', '}', raw_text)
        raw_text = re.sub(r',\s*\]', ']', raw_text)
        
        data = json.loads(raw_text)
        st.success("Successfully extracted wave schedule!")
        return data
    except Exception as e:
        st.error(f"Error during extraction: {e}")
        # Show what failed to help debug
        if 'raw_text' in locals():
            with st.expander("Show raw AI response"):
                st.code(raw_text)
        return None
