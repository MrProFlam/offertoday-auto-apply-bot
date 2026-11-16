from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

BROWSER_PREFIXES = ("chromium-", "chromium_headless_shell-")


def browser_path() -> Path:
    custom = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    if custom and custom != "0":
        return Path(custom)
    local = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(local) / "ms-playwright"


def browsers_installed() -> bool:
    path = browser_path()
    if not path.is_dir():
        return False
    return any(item.name.startswith(BROWSER_PREFIXES) for item in path.iterdir())


def install_browser(log) -> bool:
    """Download Playwright's Chromium using the CLI bundled inside the EXE."""
    try:
        from playwright._impl._driver import compute_driver_executable

        node, cli = compute_driver_executable()
    except Exception as exc:
        log(f"Could not find the bundled Playwright driver: {exc}")
        return False

    env = os.environ.copy()
    env["PLAYWRIGHT_BROWSERS_PATH"] = str(browser_path())
    command = [str(node), str(cli), "install", "chromium"]
    log("Downloading Chromium for Playwright - this can take a few minutes...")

    options = {
        "env": env,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.STDOUT,
        "text": True,
        "bufsize": 1,
    }
    if sys.platform == "win32":
        options["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)

    try:
        process = subprocess.Popen(command, **options)
        for line in process.stdout:
            text = line.rstrip()
            if text:
                log(text[:300])
        code = process.wait()
    except Exception as exc:
        log(f"Browser install error: {exc}")
        return False

    if code == 0:
        log("Chromium installed successfully. You can now scrape and apply.")
        return True
    log(f"Browser install failed (exit code {code}). Check your internet connection and retry.")
    return False
