from __future__ import annotations

import os
import queue
import threading
import tkinter as tk
from tkinter import messagebox, scrolledtext, ttk

import yaml

from .browser_setup import browsers_installed, install_browser
from .runtime import ENV_PATH, DATA_DIR, ROOT, is_frozen
from .session import Session
from .storage import Storage
from .utils import load_config, setup_logging
from main import apply_selection, scrape_jobs

CONFIG_FIELDS = [
    ("Keyword", "search.keyword", "str"),
    ("Max pages", "search.max_pages", "int"),
    ("Page size", "search.page_size", "int"),
    ("Include keywords (comma separated)", "filters.keywords_include", "list"),
    ("Exclude keywords (comma separated)", "filters.keywords_exclude", "list"),
    ("Minimum salary HKD", "filters.min_salary_hkd", "int"),
    ("Locations (comma separated)", "filters.locations", "list"),
    ("Max applications per run", "limits.max_applications_per_run", "int"),
    ("Random delay min (seconds)", "delays.apply_action.0", "float"),
    ("Random delay max (seconds)", "delays.apply_action.1", "float"),
    ("Resume file contains", "resume_filename_contains", "str"),
    ("Follow-up message", "follow_up_message", "text"),
    ("Manual assist for unknown steps", "assist_on_unknown_dialog", "bool"),
    ("Run browser hidden", "browser.headless", "bool"),
]


def _get_path(cfg: dict, path: str):
    current = cfg
    for part in path.split("."):
        current = current[int(part)] if part.isdigit() else current[part]
    return current


def _set_path(cfg: dict, path: str, value) -> None:
    parts = path.split(".")
    current = cfg
    for part in parts[:-1]:
        current = current[int(part)] if part.isdigit() else current[part]
    last = parts[-1]
    if last.isdigit():
        current[int(last)] = value
    else:
        current[last] = value


def _scrollable(parent):
    canvas = tk.Canvas(parent, highlightthickness=0)
    scrollbar = ttk.Scrollbar(parent, orient="vertical", command=canvas.yview)
    inner = ttk.Frame(canvas)
    inner.bind("<Configure>", lambda event: canvas.configure(scrollregion=canvas.bbox("all")))
    canvas.create_window((0, 0), window=inner, anchor="nw")
    canvas.pack(side="left", fill="both", expand=True)
    scrollbar.pack(side="right", fill="y")
    canvas.configure(yscrollcommand=scrollbar.set)
    return inner


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("OfferToday Auto Apply Bot")
        self.geometry("1080x760")
        self.minsize(900, 600)

        self.log_queue: queue.Queue = queue.Queue()
        self.ui_queue: queue.Queue = queue.Queue()
        self.log = setup_logging(log_queue=self.log_queue)

        self.cfg = load_config()
        self.session: Session | None = None
        self.storage: Storage | None = None
        self.matches: list[dict] = []
        self.checks: dict[str, bool] = {}
        self.running = False
        self.stop_event = threading.Event()
        self.browser_ready = browsers_installed()
        self.installing = False

        self._build_tabs()
        self._load_config_form()
        self._load_account_form()
        self.log.info("App files: %s", ROOT)
        self._prepare_browser()
        self.after(150, self._poll)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ------------------------------------------------------------------ build
    def _build_tabs(self) -> None:
        notebook = ttk.Notebook(self)
        notebook.pack(fill="both", expand=True, padx=8, pady=8)

        run_tab = ttk.Frame(notebook)
        config_tab = ttk.Frame(notebook)
        account_tab = ttk.Frame(notebook)
        notebook.add(run_tab, text="Run")
        notebook.add(config_tab, text="Config")
        notebook.add(account_tab, text="Account")

        self._build_run_tab(run_tab)
        self._build_config_tab(config_tab)
        self._build_account_tab(account_tab)

    def _build_run_tab(self, parent) -> None:
        controls = ttk.Frame(parent)
        controls.pack(fill="x", pady=(0, 6))
        self.scrape_btn = ttk.Button(controls, text="Scrape jobs", command=lambda: self._start("scrape"))
        self.select_all_btn = ttk.Button(controls, text="Select all", command=self._select_all, state="disabled")
        self.clear_btn = ttk.Button(controls, text="Clear", command=self._clear_selection, state="disabled")
        self.apply_btn = ttk.Button(controls, text="Apply selected", command=self._apply_selected)
        self.dry_btn = ttk.Button(controls, text="Dry run", command=lambda: self._start("dryrun"))
        self.stop_btn = ttk.Button(controls, text="Stop", command=self._stop, state="disabled")
        self.browser_btn = ttk.Button(controls, text="Install browser", command=self._prepare_browser)
        for button in (self.scrape_btn, self.select_all_btn, self.clear_btn, self.apply_btn, self.dry_btn, self.stop_btn, self.browser_btn):
            button.pack(side="left", padx=(0, 6))
        self.status_var = tk.StringVar(value="Ready")
        ttk.Label(controls, textvariable=self.status_var).pack(side="right")

        table_frame = ttk.Frame(parent)
        table_frame.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(
            table_frame,
            columns=("sel", "title", "company", "salary", "location"),
            show="headings",
            selectmode="none",
            height=14,
        )
        for column, heading, width in (
            ("sel", "", 38),
            ("title", "Job title", 340),
            ("company", "Company", 190),
            ("salary", "Salary", 110),
            ("location", "Location", 220),
        ):
            self.tree.heading(column, text=heading)
            self.tree.column(column, width=width, anchor="w")
        self.tree.pack(side="left", fill="both", expand=True)
        table_scroll = ttk.Scrollbar(table_frame, orient="vertical", command=self.tree.yview)
        table_scroll.pack(side="right", fill="y")
        self.tree.configure(yscrollcommand=table_scroll.set)
        self.tree.bind("<Button-1>", self._toggle_check)

        log_frame = ttk.LabelFrame(parent, text="Activity log")
        log_frame.pack(fill="both", expand=True, pady=(8, 0))
        self.log_text = scrolledtext.ScrolledText(log_frame, height=12, state="disabled", wrap="word")
        self.log_text.pack(fill="both", expand=True, padx=4, pady=4)

    def _build_config_tab(self, parent) -> None:
        inner = _scrollable(parent)
        self.config_vars: dict[str, tk.Variable] = {}
        for row, (label, path, kind) in enumerate(CONFIG_FIELDS):
            ttk.Label(inner, text=label).grid(row=row, column=0, sticky="w", padx=8, pady=4)
            if kind == "bool":
                variable = tk.BooleanVar()
                widget = ttk.Checkbutton(inner, variable=variable)
            elif kind == "text":
                variable = tk.StringVar()
                widget = tk.Text(inner, height=4, width=60)
                self.config_vars[path] = widget
                widget.grid(row=row, column=1, sticky="we", padx=8, pady=4)
                continue
            else:
                variable = tk.StringVar()
                widget = ttk.Entry(inner, textvariable=variable, width=58)
            self.config_vars[path] = variable
            widget.grid(row=row, column=1, sticky="we", padx=8, pady=4)
        inner.columnconfigure(1, weight=1)
        buttons = ttk.Frame(inner)
        buttons.grid(row=len(CONFIG_FIELDS), column=0, columnspan=2, pady=12)
        ttk.Button(buttons, text="Save config", command=self._save_config).pack(side="left", padx=4)
        ttk.Button(buttons, text="Reload", command=self._load_config_form).pack(side="left", padx=4)
        ttk.Label(
            inner,
            text="Part-time only and no category filter are always enabled.",
            foreground="#666666",
        ).grid(row=len(CONFIG_FIELDS) + 1, column=0, columnspan=2, padx=8)

    def _build_account_tab(self, parent) -> None:
        inner = ttk.Frame(parent)
        inner.pack(fill="both", expand=True, padx=16, pady=16)
        ttk.Label(inner, text="OfferToday account", font=("TkDefaultFont", 12, "bold")).grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 14)
        )
        ttk.Label(inner, text="Email or phone").grid(row=1, column=0, sticky="w", pady=6)
        self.email_var = tk.StringVar()
        ttk.Entry(inner, textvariable=self.email_var, width=44).grid(row=1, column=1, sticky="w", pady=6)
        ttk.Label(inner, text="Password").grid(row=2, column=0, sticky="w", pady=6)
        self.password_var = tk.StringVar()
        ttk.Entry(inner, textvariable=self.password_var, show="*", width=44).grid(row=2, column=1, sticky="w", pady=6)
        buttons = ttk.Frame(inner)
        buttons.grid(row=3, column=0, columnspan=2, pady=14)
        ttk.Button(buttons, text="Save credentials", command=self._save_account).pack(side="left", padx=4)
        ttk.Button(buttons, text="Test login", command=self._test_login).pack(side="left", padx=4)
        self.account_status = tk.StringVar(value="Not tested")
        ttk.Label(inner, textvariable=self.account_status).grid(row=4, column=0, columnspan=2, sticky="w")
        ttk.Label(
            inner,
            text="Credentials are stored in a local .env file next to this app.\n"
            "The app does not send them anywhere except OfferToday.",
            foreground="#666666",
            justify="left",
        ).grid(row=5, column=0, columnspan=2, sticky="w", pady=(20, 0))

    # ------------------------------------------------------------------ config
    def _load_config_form(self) -> None:
        cfg = load_config()
        self.cfg = cfg
        for label, path, kind in CONFIG_FIELDS:
            if path not in self.config_vars:
                continue
            value = _get_path(cfg, path)
            widget = self.config_vars[path]
            if kind == "bool":
                widget.set(bool(value))
            elif kind == "text":
                widget.delete("1.0", "end")
                widget.insert("1.0", str(value))
            elif kind == "list":
                widget.set(", ".join(str(item) for item in value))
            else:
                widget.set(str(value))

    def _save_config(self) -> None:
        cfg = load_config()
        try:
            for label, path, kind in CONFIG_FIELDS:
                widget = self.config_vars[path]
                if kind == "bool":
                    value = bool(widget.get())
                elif kind == "text":
                    value = widget.get("1.0", "end").strip()
                elif kind == "list":
                    value = [item.strip() for item in widget.get().split(",") if item.strip()]
                elif kind == "int":
                    value = int(widget.get())
                elif kind == "float":
                    value = float(widget.get())
                else:
                    value = widget.get()
                _set_path(cfg, path, value)
            from .runtime import CONFIG_PATH

            CONFIG_PATH.write_text(yaml.safe_dump(cfg, sort_keys=False, allow_unicode=True), encoding="utf-8")
            self.cfg = cfg
            messagebox.showinfo("Config", "Configuration saved.")
        except Exception as exc:
            messagebox.showerror("Config", f"Could not save config: {exc}")

    def _load_account_form(self) -> None:
        values = {}
        if ENV_PATH.exists():
            for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
                if "=" in line and not line.strip().startswith("#"):
                    key, value = line.split("=", 1)
                    values[key.strip()] = value.strip()
        self.email_var.set(values.get("OFFERTODAY_EMAIL", ""))
        self.password_var.set(values.get("OFFERTODAY_PASSWORD", ""))

    def _save_account(self) -> None:
        try:
            values = {}
            if ENV_PATH.exists():
                for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
                    if "=" in line and not line.strip().startswith("#"):
                        key, value = line.split("=", 1)
                        values[key.strip()] = value.strip()
            values["OFFERTODAY_EMAIL"] = self.email_var.get().strip()
            values["OFFERTODAY_PASSWORD"] = self.password_var.get().strip()
            ENV_PATH.write_text(
                "\n".join(f"{key}={value}" for key, value in values.items()) + "\n",
                encoding="utf-8",
            )
            self.account_status.set("Credentials saved")
        except Exception as exc:
            messagebox.showerror("Account", f"Could not save credentials: {exc}")

    def _test_login(self) -> None:
        if self.running:
            return
        threading.Thread(target=self._test_login_worker, daemon=True).start()

    def _test_login_worker(self) -> None:
        try:
            cfg = load_config()
            session = Session(cfg, DATA_DIR / "session.json", prompt=self._ask)
            session.start()
            ok = session.login()
            self.ui_queue.put(("status", "Login OK" if ok else "Login failed"))
            self.ui_queue.put(("account_status", "Logged in" if ok else "Login failed"))
            session.close()
        except Exception as exc:
            self.log.exception("Login test failed: %s", exc)
            self.ui_queue.put(("account_status", "Login failed"))

    # ------------------------------------------------------------------ actions
    def _start(self, action: str) -> None:
        if not self.browser_ready:
            messagebox.showinfo("Browser required", "Chromium is not ready yet. Click Install browser first.")
            return
        if self.running:
            messagebox.showinfo("Busy", "A task is already running.")
            return
        self.stop_event.clear()
        self.running = True
        self.status_var.set("Working...")
        self.scrape_btn.configure(state="disabled")
        self.select_all_btn.configure(state="disabled")
        self.clear_btn.configure(state="disabled")
        self.dry_btn.configure(state="disabled")
        self.apply_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")
        threading.Thread(target=self._worker, args=(action, None), daemon=True).start()

    def _apply_selected(self) -> None:
        if not self.browser_ready:
            messagebox.showinfo("Browser required", "Chromium is not ready yet. Click Install browser first.")
            return
        if self.running:
            return
        selected = [self.matches[int(iid)] for iid, checked in self.checks.items() if checked and int(iid) < len(self.matches)]
        if not selected:
            messagebox.showinfo("No selection", "Tick the jobs you want to apply to first.")
            return
        self.stop_event.clear()
        self.running = True
        self.status_var.set(f"Applying to {len(selected)} job(s)...")
        self.scrape_btn.configure(state="disabled")
        self.select_all_btn.configure(state="disabled")
        self.clear_btn.configure(state="disabled")
        self.dry_btn.configure(state="disabled")
        self.apply_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")
        threading.Thread(target=self._worker, args=("apply", selected), daemon=True).start()

    def _stop(self) -> None:
        self.stop_event.set()
        self.status_var.set("Stopping after the current job...")

    def _worker(self, action: str, payload) -> None:
        # Playwright's sync API must be created and used in one thread, so each
        # action gets a fresh session (login state is restored from session.json).
        session = None
        storage = None
        try:
            cfg = load_config()
            self.cfg = cfg
            session = Session(cfg, DATA_DIR / "session.json", prompt=self._ask)
            session.start()
            storage = Storage(DATA_DIR / "bot.db")
            if not session.is_logged_in() and not session.login():
                self.ui_queue.put(("status", "Login failed"))
                return
            if action in ("scrape", "dryrun"):
                matches = scrape_jobs(cfg, session, storage, self.log)
                self.ui_queue.put(("matches", matches))
                self.ui_queue.put(("status", "Scrape complete" if action == "scrape" else "Dry run complete"))
            elif action == "apply":
                apply_selection(
                    cfg,
                    session,
                    storage,
                    payload or [],
                    self.log,
                    prompt=self._ask,
                    stop_check=self.stop_event.is_set,
                )
                self.ui_queue.put(("status", "Apply complete"))
        except Exception as exc:
            self.log.exception("Error: %s", exc)
            self.ui_queue.put(("status", "Error - see log"))
        finally:
            if session is not None:
                session.close()
            if storage is not None:
                storage.close()
            self.ui_queue.put(("done", None))

    # ------------------------------------------------------------------ UI sync
    def _set_all_checks(self, checked: bool) -> None:
        for item in self.tree.get_children():
            self.checks[item] = checked
            self.tree.set(item, "sel", "☑" if checked else "☐")

    def _select_all(self) -> None:
        self._set_all_checks(True)
        self.status_var.set(f"Selected all {len(self.matches)} job(s)")

    def _clear_selection(self) -> None:
        self._set_all_checks(False)
        self.status_var.set("Selection cleared")

    def _toggle_check(self, event) -> None:
        if self.tree.identify_region(event.x, event.y) != "cell":
            return
        if self.tree.identify_column(event.x) != "#1":
            return
        item = self.tree.identify_row(event.y)
        if not item:
            return
        checked = not self.checks.get(item, False)
        self.checks[item] = checked
        self.tree.set(item, "sel", "☑" if checked else "☐")

    def _show_matches(self, matches: list[dict]) -> None:
        self.matches = matches
        for item in self.tree.get_children():
            self.tree.delete(item)
        self.checks = {}
        for index, job in enumerate(matches):
            iid = str(index)
            self.tree.insert(
                "",
                "end",
                iid=iid,
                values=("☐", job.get("title", ""), job.get("company", ""), job.get("salary", ""), job.get("location", "")),
            )
            self.checks[iid] = False
        self._set_buttons_for_browser(self.browser_ready)

    def _ask(self, message: str) -> None:
        event = threading.Event()
        self.ui_queue.put(("ask", (message, event)))
        event.wait()

    def _show_ask(self, payload) -> None:
        message, event = payload
        dialog = tk.Toplevel(self)
        dialog.title("Action needed")
        dialog.transient(self)
        ttk.Label(dialog, text=message, wraplength=420).pack(padx=16, pady=16)
        ttk.Button(dialog, text="Continue", command=lambda: (event.set(), dialog.destroy())).pack(pady=(0, 16))
        dialog.grab_set()

    def _poll(self) -> None:
        while not self.log_queue.empty():
            record = self.log_queue.get_nowait()
            text = record.getMessage() if hasattr(record, "getMessage") else str(record)
            self.log_text.configure(state="normal")
            self.log_text.insert("end", text + "\n")
            self.log_text.see("end")
            self.log_text.configure(state="disabled")
        while not self.ui_queue.empty():
            kind, payload = self.ui_queue.get_nowait()
            if kind == "matches":
                self._show_matches(payload)
            elif kind == "status":
                self.status_var.set(payload)
            elif kind == "account_status":
                self.account_status.set(payload)
            elif kind == "ask":
                self._show_ask(payload)
            elif kind == "browser_ready":
                ok, present = payload
                self.installing = False
                self.browser_ready = present
                self._set_buttons_for_browser(present)
                self.status_var.set("Browser ready" if present else "Browser install failed - retry")
                if not present:
                    messagebox.showwarning(
                        "Browser install",
                        "Chromium could not be installed. Check your internet connection "
                        "and click 'Install browser' to retry.",
                    )
            elif kind == "done":
                self.running = False
                self._set_buttons_for_browser(self.browser_ready)
                self.stop_btn.configure(state="disabled")
        self.after(150, self._poll)

    def _set_buttons_for_browser(self, ready: bool) -> None:
        state = "normal" if ready else "disabled"
        self.scrape_btn.configure(state=state)
        self.dry_btn.configure(state=state)
        have_jobs = ready and bool(self.matches)
        for button in (self.apply_btn, self.select_all_btn, self.clear_btn):
            button.configure(state="normal" if have_jobs else "disabled")
        self.browser_btn.configure(state="disabled" if self.installing else "normal")

    def _prepare_browser(self) -> None:
        if self.running or self.installing:
            return
        if browsers_installed():
            self.browser_ready = True
            self._set_buttons_for_browser(True)
            self.status_var.set("Browser ready")
            return
        self.installing = True
        self.browser_ready = False
        self._set_buttons_for_browser(False)
        self.status_var.set("Installing browser...")
        self.log.warning("Chromium not found - downloading it automatically (one-time setup).")
        threading.Thread(target=self._install_browser_worker, daemon=True).start()

    def _install_browser_worker(self) -> None:
        ok = install_browser(self.log.info)
        self.ui_queue.put(("browser_ready", (ok, browsers_installed())))

    def _on_close(self) -> None:
        if self.running and not messagebox.askokcancel("Quit", "A task is running. Quit anyway?"):
            return
        self.destroy()
