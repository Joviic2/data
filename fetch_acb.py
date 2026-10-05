#!/usr/bin/env python3
"""Dedicated ACB entry point for the API-Sports league scraper.

Writes data/acb/site.json in the format consumed by index.html. The shared
scraper handles the API response and cache; this entry point keeps ACB
separate so its schedule and boxscore refresh can run independently.
"""
import os
import runpy
from pathlib import Path

os.environ["LEAGUES"] = "acb"
runpy.run_path(str(Path(__file__).with_name("fetch_apisports.py")), run_name="__main__")

