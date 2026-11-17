# streamlit cloud starts the app from app.py, so this just runs frontend.py
# (all the actual pages are in frontend.py)
import runpy
from pathlib import Path

runpy.run_path(str(Path(__file__).with_name("frontend.py")), run_name="__main__")
