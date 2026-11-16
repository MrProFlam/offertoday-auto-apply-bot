from __future__ import annotations

from .utils import parse_salary_min

BASE = "https://www.offertoday.com"
SEARCH_API = "/wapi/geek/recommend/search/list"


class Scraper:
    """Collects job listings through OfferToday's own JSON search endpoint.

    No artificial delays here - only Playwright's normal wait-for-load on
    optional detail pages.
    """

    def __init__(self, session, cfg: dict, log):
        self.session = session
        self.cfg = cfg
        self.log = log

    def scrape(self) -> list[dict]:
        search = self.cfg.get("search", {})
        functions = search.get("job_function_codes") or [None]
        employment_types = search.get("employment_types", [])
        page_size = int(search.get("page_size", 10))
        max_pages = int(search.get("max_pages", 5))
        referer = search.get("search_url") or f"{BASE}/en/search"
        found: dict[str, dict] = {}
        session_id: str | None = None

        for function_code in functions:
            for page in range(1, max_pages + 1):
                body = None
                for attempt in range(3):
                    payload = {
                        "keyword": search.get("keyword", ""),
                        "page": page,
                        "salaryType": 0,
                        "publishTime": "",
                        "experiences": [],
                        "educationLevels": [],
                        "benefits": [],
                        "workPermits": [],
                        "employmentTypes": employment_types,
                        "rcdType": 7,
                        "pageSize": page_size,
                        "jobFunctionCodes": [function_code] if function_code else [],
                        "industries": [],
                        "subDistrictCodes": [],
                    }
                    # The site's frontend threads sessionId through every page.
                    if session_id:
                        payload["sessionId"] = session_id

                    try:
                        response = self.session.api_post(SEARCH_API, payload, referer=referer)
                    except Exception as exc:
                        self.log.error("Search request failed (page %s): %s", page, exc)
                        break
                    if response.status != 200:
                        self.log.error("Search HTTP %s on page %s", response.status, page)
                        break
                    try:
                        body = response.json()
                    except Exception:
                        self.log.error("Search returned non-JSON on page %s", page)
                        break

                    code = body.get("code")
                    if code == 0:
                        break

                    message = str(body.get("msg") or "")
                    self.log.warning("Search API error on page %s (attempt %s): %s", page, attempt + 1, message)
                    if "login" in message.lower() or code in (401, 403):
                        self.session.recover_session(hard=attempt >= 1)
                        continue
                    # Some other error: don't hammer the API.
                    break

                if not body or body.get("code") != 0:
                    self.log.error("Giving up on page %s", page)
                    break

                data = body.get("data") or {}
                session_id = data.get("sessionId") or session_id
                items = data.get("resultList") or []
                if not items:
                    break

                for item in items:
                    job = self._normalise(item)
                    if job["job_id"]:
                        found[job["job_id"]] = job

                self.log.info(
                    "Page %s: %d jobs (function %s)", page, len(items), function_code or "all"
                )

                # The API caps each response at 10 results and reports hasMore.
                has_more = data.get("hasMore")
                total = data.get("total") or data.get("totalCount") or data.get("count")
                if has_more is False:
                    break
                if total and page * page_size >= int(total):
                    break
                if has_more is None and len(items) < page_size:
                    break

        jobs = list(found.values())
        if search.get("fetch_job_details"):
            self._fetch_details(jobs)
        return jobs

    @staticmethod
    def _normalise(item: dict) -> dict:
        job_id = item.get("jobId") or ""
        salary = item.get("salaryDesc") or ""
        return {
            "job_id": job_id,
            "title": (item.get("jobName") or "").strip(),
            "company": item.get("brandName") or item.get("companyName") or "",
            "salary": salary,
            "salary_min": parse_salary_min(salary),
            "location": item.get("locationDesc") or "",
            "experience": item.get("experience") or "",
            "education": item.get("educationDesc") or "",
            "employment": (item.get("employType") or {}).get("name", ""),
            "boss_name": item.get("bossName") or "",
            "boss_title": item.get("bossTitle") or "",
            "posted": item.get("rcdReason") or "",
            "url": f"{BASE}/en/job/{job_id}",
            "description": "",
        }

    def _fetch_details(self, jobs: list[dict]) -> None:
        self.log.info("Fetching %d job detail pages ...", len(jobs))
        page = self.session.page
        for job in jobs:
            if not job["job_id"]:
                continue
            try:
                page.goto(job["url"], wait_until="domcontentloaded", timeout=30_000)
                page.wait_for_selector("#J_apply", timeout=10_000)
                job["description"] = page.locator("body").inner_text(timeout=10_000)
            except Exception as exc:
                self.log.warning("Could not read detail for %s: %s", job["job_id"], exc)
