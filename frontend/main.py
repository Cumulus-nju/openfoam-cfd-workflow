"""
UrbanWind CFD — Entry Point

Quick launch:
    python -m frontend.main

Or after packaging:
    UrbanWindFrontend.exe
"""
import sys
from pathlib import Path

# Ensure project root is in path
sys.path.insert(0, str(Path(__file__).parent.parent))

from frontend.app import main

if __name__ == "__main__":
    main()
