from __future__ import annotations

import csv
import re

from rich.console import Console
from rich.table import Table

from .utils import DATA_DIR


def review(jobs: list[dict]) -> list[dict]:
    """Show a preview, export CSV, and return only the jobs the user approves."""
    if not jobs:
        return []

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    csv_path = DATA_DIR / "preview.csv"
    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["number", "title", "company", "salary", "location", "score", "url"],
        )
        writer.writeheader()
        for number, job in enumerate(jobs, 1):
            writer.writerow(
                {
                    "number": number,
                    "title": job.get("title", ""),
                    "company": job.get("company", ""),
                    "salary": job.get("salary", ""),
                    "location": job.get("location", ""),
                    "score": job.get("score", 0),
                    "url": job.get("url", ""),
                }
            )

    console = Console()
    table = Table(title="Matching OfferToday jobs", show_lines=True)
    table.add_column("#", style="cyan", justify="right", no_wrap=True)
    table.add_column("Title", style="bold", max_width=42)
    table.add_column("Company", max_width=24)
    table.add_column("Salary", max_width=14)
    table.add_column("Location", max_width=20)
    for number, job in enumerate(jobs, 1):
        table.add_row(
            str(number),
            job.get("title", ""),
            job.get("company", ""),
            job.get("salary", ""),
            job.get("location", ""),
        )
    console.print(table)
    console.print(f"[dim]Full list saved to {csv_path}[/dim]")

    answer = console.input(
        "Enter job numbers to apply (e.g. 1,3,5 or (1,2) or 1-5), 'all' for all, or 'q' to quit: "
    ).strip().lower()
    if answer in ("q", "quit", ""):
        console.print("[dim]Nothing selected.[/dim]")
        return []
    if answer in ("all", "a", "*"):
        console.print(f"[green]Selected all {len(jobs)} jobs.[/green]")
        return jobs

    # Accept (1,2), [1,2], 1,2, 1 2 3, 1-5, 1 to 5 ...
    numbers: list[int] = []

    def add_range(match: re.Match) -> str:
        start, end = int(match.group(1)), int(match.group(2))
        if start > end:
            start, end = end, start
        numbers.extend(range(start, end + 1))
        return " "

    rest = re.sub(r"(\d+)\s*(?:-|–|to|\.\.)\s*(\d+)", add_range, answer)
    numbers.extend(int(value) for value in re.findall(r"\d+", rest))

    selected: list[dict] = []
    seen: set[int] = set()
    for number in numbers:
        if 1 <= number <= len(jobs) and number not in seen:
            seen.add(number)
            selected.append(jobs[number - 1])

    if selected:
        console.print(f"[green]Selected {len(selected)} job(s).[/green]")
    else:
        console.print("[yellow]No valid job numbers found - nothing selected.[/yellow]")
    return selected
