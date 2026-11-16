import sys


def main() -> None:
    import os

    from bot.runtime import DATA_DIR, ensure_defaults, is_frozen

    # Playwright's browsers live in the user profile, not inside the EXE.
    if is_frozen():
        local = os.environ.get("LOCALAPPDATA")
        if local:
            os.environ.setdefault(
                "PLAYWRIGHT_BROWSERS_PATH", os.path.join(local, "ms-playwright")
            )

    ensure_defaults()

    # Hidden diagnostic: python gui_main.py --self-test
    if "--self-test" in sys.argv:
        result = "unknown"
        try:
            from bot.session import Session
            from bot.utils import load_config

            session = Session(load_config(), DATA_DIR / "session.json")
            session.start()
            result = f"OK: {session.page.url}"
            session.close()
        except Exception as exc:
            result = f"FAIL: {exc}"
        (DATA_DIR / "selftest.txt").write_text(result, encoding="utf-8")
        return

    from bot.gui import App

    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
