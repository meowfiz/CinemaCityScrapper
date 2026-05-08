from __future__ import annotations

import argparse
from pathlib import Path

from rich.console import Console
from rich.table import Table

from scraper import MovieShowings, run_scrape


def print_table(items: list[MovieShowings]) -> None:
    console = Console()
    table = Table(title="Filmy z timetable + oceny Filmweb (posortowane)")
    table.add_column("Tytuł", overflow="fold")
    table.add_column("Ocena", justify="right")
    table.add_column("Godziny", overflow="fold")
    table.add_column("Filmweb", overflow="fold")

    for it in items:
        rating = "-" if it.rating_filmweb is None else f"{it.rating_filmweb:.1f}"
        table.add_row(it.title, rating, ", ".join(it.showtimes), it.filmweb_url or "-")

    console.print(table)


def main() -> int:
    ap = argparse.ArgumentParser(description="Scraper repertuaru -> oceny Filmweb -> sortowanie.")
    ap.add_argument("--url", required=True, help="Link do strony repertuaru (tam gdzie są godziny seansów).")
    ap.add_argument("--out", default="results", help="Prefix plików wyjściowych (bez rozszerzenia).")
    ap.add_argument("--timeout-ms", type=int, default=45_000, help="Timeout renderowania strony (ms).")
    ap.add_argument("--dump-html", default=None, help="Zapisz wyrenderowany HTML do pliku (debug).")
    args = ap.parse_args()

    console = Console()
    try:
        items, json_path, csv_path = run_scrape(
            url=args.url,
            out_prefix=Path(args.out),
            timeout_ms=args.timeout_ms,
            dump_html=Path(args.dump_html) if args.dump_html else None,
            progress_cb=lambda msg: console.print(f"[bold]{msg}[/bold]"),
        )
    except Exception as e:
        console.print(f"[red]{e}[/red]")
        return 2

    print_table(items)
    console.print(f"Zapisano: [bold]{json_path}[/bold], [bold]{csv_path}[/bold]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

