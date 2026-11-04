"""Command-line entry point for Hindi Anime Zone metadata searches."""

from __future__ import annotations

import argparse
import sys
import unittest

from scraper import HindiAnimeZoneScraper, format_result


def run_self_tests() -> int:
    suite = unittest.defaultTestLoader.discover("tests")
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Search Hindi Anime Zone for anime page metadata (no media links)."
    )
    parser.add_argument("title", nargs="?", help="Anime title, e.g. Solo Leveling")
    parser.add_argument("--max-results", type=int, default=5, help="Maximum detail pages to return")
    parser.add_argument(
        "--catalogue-pages",
        type=int,
        default=None,
        help="Maximum catalogue pages to inspect (default is set in config.py)",
    )
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="Run offline parser tests without contacting the website",
    )
    args = parser.parse_args()

    if args.self_test:
        return run_self_tests()
    if not args.title:
        parser.error("provide an anime title or use --self-test")

    scraper_args = {}
    if args.catalogue_pages is not None:
        scraper_args["max_catalogue_pages"] = max(1, args.catalogue_pages)

    try:
        with HindiAnimeZoneScraper(**scraper_args) as scraper:
            results = scraper.search(args.title, max_results=args.max_results)
            if not results:
                print(f"No matching anime pages were verified for: {args.title}")
                if scraper.last_errors:
                    print("\nDetails:")
                    for error in scraper.last_errors:
                        print(f"- {error}")
                return 1
            for index, result in enumerate(results):
                if index:
                    print("\n" + "=" * 64 + "\n")
                print(format_result(result))
            if scraper.last_errors:
                print("\nWarnings:")
                for error in scraper.last_errors:
                    print(f"- {error}")
        return 0
    except KeyboardInterrupt:
        print("\nSearch cancelled.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
