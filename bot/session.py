from __future__ import annotations

import json
import os
import uuid
from pathlib import Path

from dotenv import load_dotenv
from playwright.sync_api import sync_playwright

from .utils import ROOT, setup_logging

BASE = "https://www.offertoday.com"
HOME = f"{BASE}/en/"
AUTH_PROBE_URL = f"{BASE}/en/applied-history"
ACCOUNT_CACHE_KEY = "OTD_C_USER_ACCOUNT_CACHE"


class Session:
    """Owns the Playwright browser, login state and the shared API context."""

    def __init__(self, cfg: dict, state_path: Path, prompt=None):
        self.cfg = cfg
        self.state_path = Path(state_path)
        self.prompt = prompt
        self.log = setup_logging()
        self.pw = None
        self.browser = None
        self.context = None
        self.page = None
        self._verified = False

    # ------------------------------------------------------------------ setup
    def start(self) -> None:
        self.pw = sync_playwright().start()
        browser_cfg = self.cfg.get("browser", {})
        kwargs = {"headless": browser_cfg.get("headless", False)}
        if browser_cfg.get("slow_mo"):
            kwargs["slow_mo"] = browser_cfg["slow_mo"]
        self.browser = self.pw.chromium.launch(**kwargs)

        context_kwargs = {"viewport": {"width": 1440, "height": 900}}
        if self.state_path.exists():
            context_kwargs["storage_state"] = str(self.state_path)
            self.log.info("Restored session from %s", self.state_path.name)
        self.context = self.browser.new_context(**context_kwargs)
        self.page = self.context.new_page()
        self.page.goto(HOME, wait_until="domcontentloaded", timeout=60_000)
        self.wait_ready()
        self.log.info("Browser ready: %s", self.page.url)

    def wait_ready(self, timeout: int = 4_000) -> None:
        try:
            self.page.wait_for_load_state("networkidle", timeout=timeout)
        except Exception:
            pass
        self.page.wait_for_timeout(400)

    def save_state(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.context.storage_state(path=str(self.state_path))
        self.log.info("Session saved to %s", self.state_path)

    def _ask(self, message: str) -> None:
        if callable(self.prompt):
            self.prompt(message)
        else:
            input(message)

    def close(self) -> None:
        if self.browser:
            self.browser.close()
        if self.pw:
            self.pw.stop()

    # ------------------------------------------------------------------ login
    def is_logged_in(self) -> bool:
        """Authoritative check: a logged-out visit to /applied-history redirects home."""
        try:
            self.page.goto(AUTH_PROBE_URL, wait_until="domcontentloaded", timeout=30_000)
            self.wait_ready()
            stays = self.page.url.startswith(AUTH_PROBE_URL)
            sign_in = self.page.get_by_role("button", name="Sign In", exact=True)
            no_button = sign_in.count() == 0 or not sign_in.first.is_visible()
            self._verified = stays and no_button
            return self._verified
        except Exception:
            self._verified = False
            return False

    def login(self, email: str | None = None, password: str | None = None, force: bool = False) -> bool:
        if not force and self.is_logged_in():
            self.log.info("Already logged in")
            return True

        load_dotenv(ROOT / ".env")
        email = email or os.getenv("OFFERTODAY_EMAIL")
        password = password or os.getenv("OFFERTODAY_PASSWORD")
        if not email or not password:
            self.log.error("Missing credentials - set OFFERTODAY_EMAIL/PASSWORD in .env")
            return False

        try:
            self.page.goto(HOME, wait_until="domcontentloaded", timeout=60_000)
            self.wait_ready()
            # force=True may be called while the session is still valid.
            if self.is_logged_in():
                self.log.info("Already logged in")
                return True

            clicked = self.page.evaluate(
                """() => {
                    const b = [...document.querySelectorAll('button')]
                        .find(x => (x.textContent || '').trim() === 'Sign In');
                    if (b) { b.click(); return true; }
                    return false;
                }"""
            )
            if not clicked:
                self.page.get_by_role("button", name="Sign In", exact=True).first.click(timeout=15_000)

            dialog = self.page.locator("[role='dialog']").last
            dialog.wait_for(state="visible", timeout=15_000)
            dialog.get_by_placeholder("Email or Phone Number").fill(email)

            terms = dialog.locator("input[type='checkbox']").first
            if terms.count() and not terms.is_checked():
                terms.check()

            toggle = dialog.get_by_text("Login with Password", exact=False)
            if toggle.count():
                toggle.first.click()
                self.page.wait_for_timeout(300)

            dialog.locator("input[type='password']").first.fill(password)
            dialog.get_by_role("button", name="Sign In", exact=True).last.click(timeout=15_000)

            for _ in range(30):
                if not dialog.count() or not dialog.is_visible():
                    break
                self.page.wait_for_timeout(500)
            self.wait_ready()

            if not self.is_logged_in():
                self.log.warning("Auto-login did not complete (captcha or verification?).")
                self._ask("Finish logging in in the browser window, then press Continue here: ")
                if not self.is_logged_in():
                    return False

            self.save_state()
            self.log.info("Login successful")
            return True

        except Exception as exc:
            self.log.exception("Login failed: %s", exc)
            self._ask("Log in manually in the browser window, then press Continue here: ")
            if self.is_logged_in():
                self.save_state()
                return True
            return False

    def recover_session(self, hard: bool = False) -> None:
        if hard:
            self.log.warning("Re-authenticating ...")
            self.login(force=True)
        else:
            self.log.warning("Session expired - refreshing page ...")
            self.page.reload(wait_until="domcontentloaded", timeout=60_000)
            self.wait_ready()

    # ------------------------------------------------------------------- HTTP
    def api_post(self, path: str, payload: dict, referer: str | None = None):
        # Logged-in requests require the CSRF token the site stores in a cookie.
        csrf = next(
            (c["value"] for c in self.context.cookies()
             if c["name"].lower() == "csrf-token"),
            "",
        )
        headers = {
            "accept": "application/json, text/plain, */*",
            "api-language": "en_HK",
            "content-type": "application/json;charset=UTF-8",
            "csrf-token": csrf,
            "x-requested-with": "XMLHttpRequest",
            "origin": BASE,
            "referer": referer or HOME,
            "traceid": "F-" + uuid.uuid4().hex[:20],
            "user-agent": self.page.evaluate("navigator.userAgent"),
        }
        return self.page.request.post(
            BASE + path,
            data=json.dumps(payload),
            headers=headers,
            timeout=30_000,
        )
