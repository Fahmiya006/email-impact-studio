"""Run the whole pipeline in order: python src/run_all.py"""
import runpy
from pathlib import Path

here = Path(__file__).parent
import sys
sys.path.insert(0, str(here))
for script in ["01_ab_test.py", "02_observational.py", "03_uplift_policy.py", "04_build_dashboard.py", "05_export_model.py"]:
    print(f"\n===== {script} =====")
    runpy.run_path(str(here / script), run_name="__main__")
