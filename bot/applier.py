from __future__ import annotations

import time
from datetime import datetime

from .utils import DATA_DIR, human_delay


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


class Applier:
    """Applies to approved jobs and sends the configured follow-up chat message.

    All waiting that touches the user's account uses human_delay() (3-5s,
    re-rolled each time). Scraping has no such delay.
    """

    def __init__(self, session, cfg: dict, storage, log, prompt=None, stop_check=None):
        self.session = session
        self.cfg = cfg
        self.storage = storage
        self.log = log
        self.prompt = prompt
        self.stop_check = stop_check
        self.message = cfg.get("follow_up_message", "").strip()

    def _ask(self, message: str) -> None:
        if callable(self.prompt):
            self.prompt(message)
        else:
            try:
                input(message)
            except (EOFError, RuntimeError):
                self.log.warning("No console to ask manually: %s", message)

    # ------------------------------------------------------------------ public
    def run(self, jobs: list[dict]) -> int:
        limits = self.cfg.get("limits", {})
        max_apps = int(limits.get("max_applications_per_run", 10))
        max_failures = int(limits.get("max_consecutive_failures", 3))
        completed = 0
        failures = 0

        for job in jobs:
            if callable(self.stop_check) and self.stop_check():
                self.log.warning("Stopped by user")
                break
            if completed >= max_apps:
                self.log.info("Reached max_applications_per_run (%d)", max_apps)
                break
            if failures >= max_failures:
                self.log.warning("Stopping after %d consecutive failures", failures)
                break

            self.log.info("[%d/%d] %s", completed + 1, len(jobs), job.get("title", ""))
            if self._apply_one(job):
                completed += 1
                failures = 0
            else:
                failures += 1

            if completed < max_apps and failures < max_failures:
                human_delay(self.cfg)  # randomized pause before the next job

        return completed

    # ----------------------------------------------------------------- private
    def _apply_one(self, job: dict) -> bool:
        page = self.session.page
        try:
            human_delay(self.cfg)
            page.goto(job["url"], wait_until="domcontentloaded", timeout=60_000)
            # Wait for React to hydrate before touching the apply button.
            # Login is verified once at startup - do NOT re-check per job,
            # because the SSR shell always contains a "Sign In" button.
            try:
                page.wait_for_selector("#J_apply", timeout=15_000)
            except Exception:
                self.log.warning("Login may have expired - re-authenticating")
                if not self.session.login(force=True):
                    self.log.error("Login failed - skipping %s", job["job_id"])
                    self.storage.update(
                        job["job_id"], status="failed", error="login required"
                    )
                    return False
                page.goto(job["url"], wait_until="domcontentloaded", timeout=60_000)
                page.wait_for_selector("#J_apply", timeout=15_000)

            apply = page.locator("#J_apply")
            if apply.count() == 0:
                apply = page.get_by_text("Send application message", exact=False)
            apply.first.click(timeout=20_000)
            page.wait_for_timeout(800)

            self.storage.update(job["job_id"], status="applied", applied_at=_now())
            self.log.info("Application submitted for %s", job["job_id"])

            # Sometimes the site accepts the application but never opens the
            # chat panel. Nothing more can be done automatically in that case.
            if not self._wait_for_chat():
                self.log.warning(
                    "Chat panel did not open for %s - application submitted only",
                    job["job_id"],
                )
                self._debug_log(job, "CHAT: not available after apply")
                return True

            # Step 2: send the resume (the "Send Resume" button appears after
            # the application is submitted).
            resume_sent = self.submit_resume(job)
            if resume_sent:
                self.storage.update(job["job_id"], resumed_at=_now())

            # The "More" popup can stay open over the composer after the
            # resume click - dismiss it before typing the follow-up message.
            self._dismiss_popups()

            # Step 3: send the follow-up chat message.
            human_delay(self.cfg)
            sent = self._send_follow_up(job)
            if sent:
                self.storage.update(job["job_id"], status="chatted", chatted_at=_now())
            return True

        except Exception as exc:
            self.log.exception("Failed to process %s: %s", job["job_id"], exc)
            self.storage.update(job["job_id"], status="failed", error=str(exc)[:500])
            return False

    RESUME_LABELS = (
        "Send resume",
        "Send Resume",
        "Submit Resume",
        "Send my resume",
        "发送简历",
        "提交简历",
    )

    def _chat_scope(self):
        page = self.session.page
        dialog = page.locator("[role='dialog']").last
        return dialog if dialog.count() and dialog.is_visible() else page

    def _wait_for_chat(self, timeout_seconds: float = 8.0) -> bool:
        page = self.session.page
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            if self._find_message_input(self._chat_scope()) is not None:
                return True
            page.wait_for_timeout(300)
        return False

    def _find_resume_item(self, scope):
        """Find the 'Send resume' menu item (it may be hidden inside 'More')."""
        for role in ("menuitem", "button", "option", "menuitemcheckbox"):
            for label in self.RESUME_LABELS:
                item = scope.get_by_role(role, name=label, exact=False)
                if item.count() and item.last.is_visible():
                    return item.last

        # Generic elements: the item is often a plain div/li, not a button.
        candidates = scope.locator("div, li, span, p, a, button")
        for index in range(min(candidates.count(), 400)):
            item = candidates.nth(index)
            try:
                if not item.is_visible():
                    continue
                # Prefer the innermost element, not the popup container that
                # merely contains the matching text.
                if item.evaluate("el => el.children.length > 0"):
                    continue
                text = (item.inner_text() or "").strip()
            except Exception:
                continue
            lower = text.lower()
            if not text or len(text) > 60:
                continue
            if "resume" not in lower and "简历" not in text:
                continue
            # Skip system messages and the "View Attached Resume" card.
            if any(word in lower for word in ("sent", "attach", "view", "查看", "已发送")):
                continue
            return item
        return None

    def _find_more_chip(self, scope):
        for label in ("More", "More ▾", "更多"):
            chip = scope.get_by_role("button", name=label, exact=False)
            if chip.count() and chip.last.is_visible():
                return chip.last
            chip = scope.get_by_text(label, exact=False)
            if chip.count() and chip.last.is_visible():
                return chip.last
        return None

    def _resume_already_sent(self, job: dict) -> bool:
        """True if this chat already shows the resume-sent system message."""
        try:
            row = self.storage.conn.execute(
                "SELECT resumed_at FROM jobs WHERE job_id = ?", (job["job_id"],)
            ).fetchone()
            if row and row[0]:
                return True
        except Exception:
            pass
        try:
            text = self._chat_scope().inner_text(timeout=2_000) or ""
            if (
                "Your resume has been sent to the recruiter" in text
                or "resume has been sent" in text.lower()
                or "履歷已發送" in text
                or "简历已发送" in text
            ):
                return True
        except Exception:
            pass
        return False

    def submit_resume(self, job: dict) -> bool:
        """Hover 'More' to reveal 'Send resume', then click it (unless already sent)."""
        page = self.session.page
        scope = self._chat_scope()

        if self._resume_already_sent(job):
            self.log.info("Resume already sent for %s - skipping", job["job_id"])
            self._debug_log(job, "RESUME: already sent, skipping")
            return True

        item = self._find_resume_item(scope)
        how = "already visible"
        if item is None:
            more = self._find_more_chip(scope)
            if more is not None:
                try:
                    more.hover(timeout=5_000)
                    page.wait_for_timeout(400)
                except Exception:
                    pass
                item = self._find_resume_item(scope)
                how = "after hovering More"
            if item is None and more is not None:
                # Physical hover is unreliable in headless mode; dispatch the
                # same mouse events the site listens for.
                try:
                    page.evaluate(
                        """el => {
                            el.dispatchEvent(new MouseEvent('mouseenter', {bubbles: true}));
                            el.dispatchEvent(new MouseEvent('mouseover', {bubbles: true}));
                        }""",
                        more.element_handle(),
                    )
                    page.wait_for_timeout(400)
                except Exception:
                    pass
                item = self._find_resume_item(scope)
                how = "after JS hover on More"
            if item is None and more is not None:
                try:
                    more.click(timeout=5_000)
                    page.wait_for_timeout(400)
                except Exception:
                    pass
                item = self._find_resume_item(scope)
                how = "after clicking More"

        if item is None:
            self.log.warning("No 'Send resume' item found for %s - continuing without it", job["job_id"])
            self._debug_log(job, "RESUME ITEM: NOT FOUND - skipped")
            self._debug_dump_chat(job, scope)
            return False

        try:
            text = (item.inner_text() or "").strip()
        except Exception:
            text = "Send resume"
        human_delay(self.cfg)
        self.log.info("Clicking resume item '%s' (%s) for %s", text, how, job["job_id"])
        self._debug_log(job, f"RESUME ITEM ({how}): {text}")
        # JS click: moving the mouse toward the popup can close the "More"
        # menu before a normal Playwright click lands.
        try:
            page.evaluate("el => el.click()", item.element_handle())
        except Exception:
            try:
                item.click(timeout=8_000, force=True)
            except Exception as exc:
                self.log.warning("Could not click resume item: %s", exc)
        page.wait_for_timeout(700)
        self._complete_resume_dialog(job)
        self._handle_confirmation(job)
        return True

    def _complete_resume_dialog(self, job: dict) -> None:
        """Handle the 'Select a resume' dialog that follows 'Send resume'."""
        page = self.session.page
        dialog = None
        for _ in range(20):
            candidate = page.locator("[role='dialog']").last
            if candidate.count() and candidate.is_visible():
                text = candidate.inner_text(timeout=2_000)
                if "Select a resume" in text or "选择简历" in text:
                    dialog = candidate
                    break
            page.wait_for_timeout(200)
        if dialog is None:
            return

        self.log.info("Resume selection dialog opened for %s", job["job_id"])
        self._debug_log(job, "RESUME DIALOG: " + dialog.inner_text(timeout=3_000)[:500].replace("\n", " | "))

        # Pick the configured CV, or the first one listed.
        wanted = (self.cfg.get("resume_filename_contains") or "").strip().lower()
        options = []
        elements = dialog.locator("div, li, span, p, label")
        for index in range(min(elements.count(), 300)):
            element = elements.nth(index)
            try:
                if not element.is_visible():
                    continue
                if element.evaluate("el => el.children.length > 0"):
                    continue
                text = (element.inner_text() or "").strip()
            except Exception:
                continue
            if ".pdf" in text.lower() or ".doc" in text.lower() or ".docx" in text.lower():
                options.append((text, element))

        if not options:
            self.log.warning("No resume file options found for %s", job["job_id"])
        else:
            choice = next(((t, e) for t, e in options if wanted and wanted in t.lower()), options[0])
            human_delay(self.cfg)
            self.log.info("Selecting resume '%s' for %s", choice[0], job["job_id"])
            self._debug_log(job, f"RESUME SELECTED: {choice[0]}")
            try:
                page.evaluate("el => el.click()", choice[1].element_handle())
            except Exception:
                try:
                    choice[1].click(timeout=8_000)
                except Exception as exc:
                    self.log.warning("Could not select resume: %s", exc)

        # Confirm with the dialog's own "Send" button.
        human_delay(self.cfg)
        send = dialog.get_by_role("button", name="Send", exact=True)
        if send.count() and send.last.is_visible():
            try:
                page.evaluate("el => el.click()", send.last.element_handle())
            except Exception:
                send.last.click(timeout=8_000)
            self.log.info("Resume sent from selection dialog for %s", job["job_id"])
            self._debug_log(job, "RESUME DIALOG SEND: clicked")
        else:
            self.log.warning("No Send button in resume dialog for %s", job["job_id"])
            self._debug_log(job, "RESUME DIALOG SEND: NOT FOUND - skipped")
            self._debug_dump_chat(job, dialog)
        page.wait_for_timeout(700)

    def _debug_dump_chat(self, job: dict, scope) -> None:
        try:
            texts = []
            controls = scope.locator("button, [role='menuitem'], li, div")
            for index in range(min(controls.count(), 250)):
                element = controls.nth(index)
                if not element.is_visible():
                    continue
                text = (element.inner_text() or "").strip()
                if text and len(text) <= 60 and text not in texts:
                    texts.append(text)
            self._debug_log(job, "CHAT CONTROLS: " + " | ".join(texts[:80]))
        except Exception:
            pass

    def _dismiss_popups(self) -> None:
        """Close any hover menu left open over the chat composer."""
        page = self.session.page
        try:
            page.keyboard.press("Escape")
        except Exception:
            pass
        try:
            page.mouse.move(2, 2)
        except Exception:
            pass
        page.wait_for_timeout(250)

    def _handle_confirmation(self, job: dict) -> None:
        page = self.session.page
        dialog = page.locator("[role='dialog']").last
        scope = dialog if dialog.count() and dialog.is_visible() else page
        for label in ("Agree", "Confirm", "OK", "Yes", "确定", "確認"):
            try:
                button = scope.get_by_role("button", name=label, exact=True)
                if button.count() and button.last.is_visible() and button.last.is_enabled():
                    self.log.info("Confirming '%s'", label)
                    self._debug_log(job, f"CONFIRM BUTTON: {label}")
                    button.last.click(timeout=10_000)
                    page.wait_for_timeout(500)
                    return
            except Exception:
                continue

    def _debug_log(self, job: dict, message: str) -> None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        with open(DATA_DIR / "apply_debug.log", "a", encoding="utf-8") as f:
            f.write(f"[{job['job_id']}] {message}\n")

    @staticmethod
    def _input_text(locator) -> str:
        try:
            return locator.input_value() or ""
        except Exception:
            try:
                return locator.inner_text() or ""
            except Exception:
                return ""

    def _message_appeared(self, scope) -> bool:
        try:
            return self.message in (scope.inner_text(timeout=2_000) or "")
        except Exception:
            return False

    def _send_follow_up(self, job: dict) -> bool:
        """Type the follow-up message, click the composer's exact 'Send', and verify."""
        page = self.session.page
        for attempt in range(2):
            dialog = page.locator("[role='dialog']").last
            scope = dialog if dialog.count() and dialog.is_visible() else page
            message_input = self._find_message_input(scope)
            if message_input is None:
                message_input = self._find_message_input(page)
                if message_input is not None:
                    scope = page
            if message_input is None:
                self.log.warning("No chat input found for %s - skipping message", job["job_id"])
                self._debug_log(job, "CHAT INPUT: NOT FOUND - skipped")
                return False

            if attempt == 0:
                self._debug_snapshot(job, scope)
            message_input.fill("")
            if attempt == 0:
                message_input.fill(self.message)
            else:
                # Some custom editors ignore fill(); use real keystrokes.
                message_input.type(self.message, delay=15)
            page.wait_for_timeout(250)

            typed = self.message in self._input_text(message_input)
            if not typed:
                self._debug_log(job, f"CHAT SEND attempt {attempt + 1}: text not in input after fill")

            send_button = self._find_send_button(scope, message_input)
            if send_button is None:
                send_button = self._find_send_button(page, message_input)
            human_delay(self.cfg)
            if send_button is not None:
                try:
                    name = (send_button.inner_text() or "Send").strip()
                except Exception:
                    name = "Send"
                self._debug_log(job, f"CHAT SEND BUTTON (attempt {attempt + 1}): {name}")
                try:
                    page.evaluate("el => el.click()", send_button.element_handle())
                except Exception:
                    try:
                        send_button.click(timeout=8_000)
                    except Exception:
                        message_input.press("Enter")
            else:
                self._debug_log(job, f"CHAT SEND BUTTON (attempt {attempt + 1}): none - Enter")
                message_input.press("Enter")

            page.wait_for_timeout(800)
            appeared = self._message_appeared(scope)
            cleared = not self._input_text(message_input).strip()
            self._debug_log(
                job,
                f"CHAT SEND attempt {attempt + 1}: typed={typed} appeared={appeared} cleared={cleared}",
            )
            # The site clears the composer when it accepts a message. That is
            # the reliable signal - chat text may be virtualised and invisible
            # to inner_text(). Never retry once the composer has been cleared,
            # or the same message gets sent twice.
            if typed and cleared:
                self.log.info("Follow-up message sent for %s", job["job_id"])
                self._debug_log(job, "CHAT SEND RESULT: sent")
                return True
            if attempt == 0:
                self.log.warning("Message not verified - retrying with keystrokes")

        self.log.warning("Follow-up message could not be verified for %s - continuing", job["job_id"])
        self._debug_log(job, "CHAT SEND RESULT: unverified - continuing")
        return False

    @staticmethod
    def _find_message_input(scope):
        selectors = (
            "textarea",
            "[contenteditable='true']",
            "input[placeholder*='essage' i]",
            "input[placeholder*='訊息' i]",
            "input[placeholder*='消息' i]",
            "input[placeholder*='Message' i]",
        )
        for selector in selectors:
            locator = scope.locator(selector)
            if locator.count():
                candidate = locator.last
                if candidate.is_visible():
                    return candidate
        return None

    SEND_NAMES = ("send", "发送", "送出", "submit")

    @staticmethod
    def _is_send_button(button) -> bool:
        try:
            if not button.is_visible() or not button.is_enabled():
                return False
            text = (button.inner_text() or "").strip().lower()
            label = (button.get_attribute("aria-label") or "").strip().lower()
        except Exception:
            return False
        if text and text not in Applier.SEND_NAMES:
            return False
        if label and label not in Applier.SEND_NAMES:
            return False
        return bool(text or label)

    @staticmethod
    def _find_send_button(scope, message_input=None):
        """Find the composer's Send button - never 'Send contact'/'Send resume'."""
        scopes = []
        if message_input is not None:
            try:
                container = message_input.locator("xpath=ancestor::div[3]")
                if container.count():
                    scopes.append(container.first)
            except Exception:
                pass
        scopes.append(scope)

        for current in scopes:
            for name in ("Send", "发送", "送出", "Submit"):
                locator = current.get_by_role("button", name=name, exact=True)
                for index in range(locator.count()):
                    button = locator.nth(index)
                    if Applier._is_send_button(button):
                        return button
            elements = current.locator("button, div, span, a")
            for index in range(min(elements.count(), 400)):
                element = elements.nth(index)
                try:
                    if not element.is_visible() or element.evaluate("el => el.children.length > 0"):
                        continue
                    text = (element.inner_text() or "").strip().lower()
                except Exception:
                    continue
                if text in Applier.SEND_NAMES:
                    return element
        return None

    def _debug_snapshot(self, job: dict, scope) -> None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        path = DATA_DIR / "apply_debug.log"
        try:
            text = scope.inner_text(timeout=3_000)[:3000]
            inputs = [
                element.get_attribute("placeholder") or ""
                for element in scope.locator("input, textarea").all()[:20]
            ]
            with open(path, "a", encoding="utf-8") as f:
                f.write(f"\n=== {job['job_id']} {_now()} ===\n{text}\nINPUTS: {inputs}\n")
        except Exception:
            pass
