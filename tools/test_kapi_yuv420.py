#!/usr/bin/env python3
"""Run the combined HDMI YUV420 and DVI RGB8 KAPI fixture."""
from pathlib import Path
import runpy

runpy.run_path(str(Path(__file__).with_name("test_kapi_dvi_rgb.py")), run_name="__main__")
