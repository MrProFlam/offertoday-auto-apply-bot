from __future__ import annotations

import argparse

from bot.applier import Applier
from bot.filters import filter_jobs
from bot.reviewer import review
from bot.scraper import Scraper
from bot.session import Session
from bot.storage import Storage
from bot.utils import DATA_DIR, load_config, setup_logging


def scrape_jobs(cfg: dict, session: Session, storage: Storage, log) -> list[dict]:
    """Scrape and filter jobs. Shared by the CLI and the GUI."""
    jobs = Scraper(session, cfg, log).scrape()
    log.info("Scraped %d jobs", len(jobs))
    storage.save_jobs(jobs)
    matches = filter_jobs(jobs, cfg, storage.known_jobs())
    log.info("Matched %d jobs after filtering", len(matches))
    return matches


def apply_selection(
    cfg: dict,
    session: Session,
    storage: Storage,
    selected: list[dict],
    log,
    force: bool = False,
    prompt=None,
    stop_check=None,
) -> int:
    """Apply to selected jobs. Shared by the CLI and the GUI."""
    if not selected:
        log.info("Nothing selected - no applications sent")
        return 0

    cap = int(cfg.get("limits", {}).get("max_applications_per_run", 10))
    if force:
        log.warning("--force: ignoring per-run cap of %d", cap)
    elif len(selected) > cap:
        log.warning(
            "Per-run cap is %d but %d selected - applying first %d (use --force to override)",
            cap,
            len(selected),
            cap,
        )
        selected = selected[:cap]

    applier = Applier(session, cfg, storage, log, prompt=prompt, stop_check=stop_check)
    applied = applier.run(selected)
    log.info("Done. Applied to %d job(s). Status: %s", applied, storage.status_counts())
    return applied


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="OfferToday auto-apply bot")
    parser.add_argument("--dry-run", action="store_true", help="scrape + preview only; send nothing")
    parser.add_argument("--login-only", action="store_true", help="log in and save the session, then exit")
    parser.add_argument("--all", action="store_true", help="skip the approval prompt (still respects the per-run cap)")
    parser.add_argument("--force", action="store_true", help="ignore the per-run cap for this run")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    cfg = load_config()
    log = setup_logging()
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    session = Session(cfg, DATA_DIR / "session.json")
    storage = Storage(DATA_DIR / "bot.db")
    try:
        session.start()

        if args.login_only:
            session.login()
            return
        if not args.dry_run and not session.login():
            log.error("Cannot continue without logging in")
            return

        matches = scrape_jobs(cfg, session, storage, log)
        if not matches:
            return

        selected = matches if args.all else review(matches)
        if args.dry_run:
            log.info("Dry run - no applications sent")
            return

        apply_selection(cfg, session, storage, selected, log, force=args.force)
    finally:
        session.close()
        storage.close()


if __name__ == "__main__":
    main()
