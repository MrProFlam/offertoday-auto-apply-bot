# OfferToday auto-apply bot

Personal-use bot for `offertoday.com`. It scrapes **part-time jobs across all
categories**, shows you a preview for approval, applies using your existing profile
CV. For each approved job the bot applies, clicks **Send Resume** (skipping it if
the chat already shows that your resume was sent), then sends your follow-up chat
message. Randomized **3–5 second** pauses are used only during applying/chatting;
scraping is not artificially delayed.

> **Terms of Service:** bulk automated applying/messaging is likely against
> OfferToday's ToS and may get your account banned. Use the review gate, keep the
> per-run cap low, and run a dry run first. You are responsible for how you use it.

## Setup

```powershell
cd "C:\Users\USERNAME\\Documents\Default Project\offertoday-bot"
pip install -r requirements.txt
python -m playwright install chromium
```

1. Copy `.env.example` to `.env` and enter your OfferToday email/phone and password.
2. Edit `config.yaml`:
   - `search.employment_types: [2]` — part-time only; `job_function_codes: []` means all categories
   - `search.max_pages: 5` — about 50 part-time jobs per run
   - `filters` — keywords, salary floor, locations
   - `follow_up_message` — your chat template
   - `limits.max_applications_per_run` — max applications **per run** (not per day); raise it or use `--force` to apply more

## Usage

```powershell
# First run: log in and save the session
python main.py --login-only

# Verify scraping + matching without sending anything
python main.py --dry-run

# Real run: scrape, preview, approve, apply + chat
python main.py

# Skip the approval prompt (still respects the per-run cap)
python main.py --all

# Ignore the per-run cap for one run
python main.py --force
```

At the approval prompt you can enter `1,3,5`, `(1,2)`, `1-5`, or `all`.

If the bot cannot find the "Send resume" button or the chat box, it logs the reason
and moves to the next job — no manual action is needed during apply/chat. Only
login or a captcha may still require you to intervene in the browser.

## Desktop app (EXE)

Run `build.bat` to produce **`dist\OfferTodayBot.exe`** (single file, ~55 MB).
On first run the EXE creates **`Desktop\OffertodayBOT`** with `config.yaml`,
`.env`, and `data/`. That Desktop folder is where the app always reads and saves
settings, credentials, and job status. If old `config.yaml`/`.env`/`data` files are
found beside the EXE, they are copied into the Desktop folder on first run.

### Fresh computer
1. Copy only `OfferTodayBot.exe` anywhere and double-click it.
2. The Desktop folder is created automatically and Chromium is **downloaded
   automatically** on first launch (needs internet, ~280–440 MB, a few minutes).
   If it fails, click **Install browser** in the Run tab to retry.
3. Enter your credentials in the **Account** tab — do **not** copy your `.env` file
   to another machine.
4. Click **Scrape jobs**, select jobs (or **Select all**), then **Apply selected**.

The app has three tabs:

- **Run** — click **Scrape jobs**, then tick the jobs you want (or use **Select all**
  / **Clear**) and click **Apply selected**. **Dry run** scrapes without sending
  anything. The activity log shows progress. **Stop** finishes the current job
  and halts.
- **Config** — edit filters, delay range, per-run cap, and the follow-up message,
  then **Save config**.
- **Account** — enter your email/phone and password, **Save credentials**, and
  **Test login**.

The first run may take 5–10 seconds to unpack. The Playwright Chromium browser is
**not bundled**; the app uses your existing install in `%LOCALAPPDATA%\ms-playwright`
and logs a warning there if it is missing. The original `python main.py` CLI still
works exactly as before.

## Files created at runtime (in `data/`, gitignored)

- `session.json` — saved login cookies
- `bot.db` — every job seen and its status (prevents duplicate applications)
- `preview.csv` — full list from the last preview
- `apply_debug.log` — dialog text/fields seen during applying, useful for fixing selectors

## Notes

- The scraper uses the site's own JSON endpoint (`/wapi/geek/recommend/search/list`).
- Selectors for the apply/chat UI are isolated in `bot/applier.py`. If OfferToday
  changes its UI, that is the file to update (the debug log helps).
- If login ever requires a one-time code, the bot pauses and asks you to enter it
  manually in the browser window.
