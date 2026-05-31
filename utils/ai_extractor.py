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
        data = json.loads(response.text)
        st.success("Successfully extracted wave schedule!")
        return data
    except Exception as e:
        st.error(f"Error during extraction: {e}")
        return None
