from __future__ import annotations


TERMINAL_STATUSES = {"applied", "chatted", "skipped"}


def filter_jobs(jobs: list[dict], cfg: dict, known: dict[str, str]) -> list[dict]:
    """Apply configured criteria and return ranked matches."""
    rules = cfg.get("filters", {})
    include = [k.lower() for k in rules.get("keywords_include", []) if k]
    exclude = [k.lower() for k in rules.get("keywords_exclude", []) if k]
    locations = [loc.lower() for loc in rules.get("locations", []) if loc]
    min_salary = float(rules.get("min_salary_hkd", 0) or 0)
    include_unknown = bool(rules.get("include_unknown_salary", False))
    require_part_time = 2 in cfg.get("search", {}).get("employment_types", [])

    matched: list[dict] = []
    for job in jobs:
        # Failed jobs stay retryable; already-applied ones are skipped.
        if known.get(job["job_id"]) in TERMINAL_STATUSES:
            continue

        # Safety net: the API already filters by employment type, but double-check.
        if require_part_time:
            employment = (job.get("employment") or "").lower()
            if employment and "part time" not in employment:
                continue

        haystack = f"{job.get('title','')} {job.get('description','')}".lower()
        location = job.get("location", "").lower()

        if include and not any(word in haystack for word in include):
            continue
        if any(word in haystack for word in exclude):
            continue
        if locations and not any(loc in location for loc in locations):
            continue
        if min_salary > 0:
            salary = job.get("salary_min")
            if salary is None:
                if not include_unknown:
                    continue
            elif salary < min_salary:
                continue

        score = sum(1 for word in include if word in haystack)
        job["score"] = score
        matched.append(job)

    matched.sort(key=lambda j: (-j["score"], j.get("title", "")))
    return matched
