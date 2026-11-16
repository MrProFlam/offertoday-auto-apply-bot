from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

import yaml


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def desktop_dir() -> Path:
    """Resolve the real Desktop, including OneDrive-redirected desktops."""
    home = Path.home()
    for candidate in (home / "Desktop", home / "OneDrive" / "Desktop"):
        if candidate.is_dir():
            return candidate
    return home / "Desktop"


def exe_dir() -> Path:
    return Path(sys.executable).resolve().parent


def app_dir() -> Path:
    """Where config/.env/data live: Desktop\\OffertodayBOT for the EXE."""
    if is_frozen():
        return desktop_dir() / "OffertodayBOT"
    return Path(__file__).resolve().parent.parent


ROOT = app_dir()
CONFIG_PATH = ROOT / "config.yaml"
DATA_DIR = ROOT / "data"
ENV_PATH = ROOT / ".env"
ENV_EXAMPLE_PATH = ROOT / ".env.example"

DEFAULT_CONFIG = {
    "browser": {"headless": False, "slow_mo": 0},
    "search": {
        "base_url": "https://www.offertoday.com",
        "search_url": "https://www.offertoday.com/en/recommend/jobs-Part-Time/t2",
        "job_function_codes": [],
        "employment_types": [2],
        "keyword": "",
        "page_size": 10,
        "max_pages": 5,
        "fetch_job_details": False,
    },
    "filters": {
        "keywords_include": [],
        "keywords_exclude": [],
        "min_salary_hkd": 0,
        "include_unknown_salary": False,
        "locations": [],
    },
    "limits": {"max_applications_per_run": 10, "max_consecutive_failures": 3},
    "delays": {"apply_action": [3.0, 5.0], "speed_multiplier": 1.0},
    "resume_filename_contains": "",
    "follow_up_message": "Hi, I'm interested in this role. I'd be happy to chat - thank you!",
    "assist_on_unknown_dialog": True,
}

ENV_TEMPLATE = (
    "# Enter your OfferToday credentials in the app's Account tab, or here.\n"
    "OFFERTODAY_EMAIL=\n"
    "OFFERTODAY_PASSWORD=\n"
)


def _migrate_from_exe_dir() -> None:
    """Copy an existing config/.env/data from beside the EXE on first run."""
    source = exe_dir()
    if source.resolve() == ROOT.resolve():
        return
    for name in ("config.yaml", ".env"):
        src, dst = source / name, ROOT / name
        if src.exists() and not dst.exists():
            shutil.copy2(src, dst)
    src_data = source / "data"
    if src_data.is_dir():
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        for item in src_data.iterdir():
            if item.is_file() and not (DATA_DIR / item.name).exists():
                shutil.copy2(item, DATA_DIR / item.name)


def ensure_defaults() -> None:
    """Create the Desktop app folder and its files on first run."""
    ROOT.mkdir(parents=True, exist_ok=True)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if is_frozen():
        _migrate_from_exe_dir()
    if not CONFIG_PATH.exists():
        CONFIG_PATH.write_text(
            yaml.safe_dump(DEFAULT_CONFIG, sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )
    if not ENV_EXAMPLE_PATH.exists():
        ENV_EXAMPLE_PATH.write_text(ENV_TEMPLATE, encoding="utf-8")
    if not ENV_PATH.exists():
        ENV_PATH.write_text(ENV_TEMPLATE, encoding="utf-8")
