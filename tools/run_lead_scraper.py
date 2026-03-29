"""
run_lead_scraper.py
-------------------
CLI entry point for the lead generation tool.
Orchestrates scraping, website classification, deduplication, and Excel export.

Usage examples:
    # Single query, 75 results
    python tools/run_lead_scraper.py --query "Heizung Sanitär Stuttgart" --count 75

    # Multiple queries merged into one Excel file
    python tools/run_lead_scraper.py \\
        --query "Heizung Stuttgart" "Elektriker Stuttgart" "Maler Stuttgart" \\
        --count 50

    # Skip website check (faster run)
    python tools/run_lead_scraper.py --query "Sanitär Stuttgart" --count 100 --no-website-check

    # Custom output path
    python tools/run_lead_scraper.py --query "Heizung Stuttgart" --output leads/meine_leads.xlsx
"""

import argparse
import json
import logging
import os
import random
import re
import sys
import time
from datetime import datetime
from pathlib import Path

# Add tools/ to path so this script can be run from any directory
sys.path.insert(0, str(Path(__file__).parent))

from classify_website import classify_website
from export_to_excel import export
from scrape_google_maps import run_scraper

TMP_DIR = Path(".tmp")


def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    log_file = TMP_DIR / "scraper.log"

    handlers = [
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(str(log_file), encoding="utf-8"),
    ]
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
        handlers=handlers,
    )
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("playwright").setLevel(logging.WARNING)


def _auto_output_path(queries: list[str]) -> str:
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    slug = re.sub(r"[^\w]+", "_", queries[0])[:40].strip("_").lower()
    date = datetime.now().strftime("%Y-%m-%d")
    return str(TMP_DIR / f"leads_{slug}_{date}.xlsx")


def _normalize_phone(phone: str | None) -> str | None:
    """Strip all non-digit characters for deduplication comparison."""
    if not phone:
        return None
    digits = re.sub(r"\D", "", phone)
    return digits if digits else None


def _deduplicate(records: list[dict]) -> list[dict]:
    """
    Remove duplicate businesses by normalized phone number.
    Records without a phone are kept (can't deduplicate them).
    """
    seen_phones: set[str] = set()
    deduped = []
    for record in records:
        phone_key = _normalize_phone(record.get("phone"))
        if phone_key:
            if phone_key in seen_phones:
                continue
            seen_phones.add(phone_key)
        deduped.append(record)
    return deduped


def _classify_websites(records: list[dict], logger) -> list[dict]:
    """Classify each record's website in-place. Adds 'website_status' field."""
    total = len(records)
    for i, record in enumerate(records):
        url = record.get("website")
        status = classify_website(url)
        record["website_status"] = status

        if (i + 1) % 10 == 0 or i == total - 1:
            logger.info("  Website-Check: %d/%d", i + 1, total)

        # Polite delay between requests
        time.sleep(random.uniform(0.5, 1.5))

    return records


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Lead-Scraper: Lokale Betriebe aus Google Maps → Excel"
    )
    parser.add_argument(
        "--query", "-q",
        nargs="+",
        required=True,
        metavar="SUCHBEGRIFF",
        help='Suchbegriff(e), z.B. "Heizung Stuttgart" "Elektriker Stuttgart"',
    )
    parser.add_argument(
        "--count", "-n",
        type=int,
        default=50,
        metavar="ANZAHL",
        help="Anzahl der Ergebnisse pro Suchbegriff (Standard: 50)",
    )
    parser.add_argument(
        "--output", "-o",
        metavar="DATEI",
        help="Ausgabepfad für die Excel-Datei (Standard: .tmp/leads_*.xlsx)",
    )
    parser.add_argument(
        "--no-website-check",
        action="store_true",
        help="Website-Klassifizierung überspringen (schneller, ~40%% weniger Zeit)",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Ausführliche Logging-Ausgabe (DEBUG-Level)",
    )
    args = parser.parse_args()

    _setup_logging(args.verbose)
    log = logging.getLogger(__name__)

    output_path = args.output or _auto_output_path(args.query)
    all_records: list[dict] = []

    log.info("=" * 60)
    log.info("LEAD SCRAPER — %s", datetime.now().strftime("%d.%m.%Y %H:%M"))
    log.info("Suchbegriffe: %s", ", ".join(args.query))
    log.info("Ziel pro Begriff: %d | Website-Check: %s", args.count, not args.no_website_check)
    log.info("=" * 60)

    for idx, query in enumerate(args.query):
        log.info("[%d/%d] Scrape: '%s'", idx + 1, len(args.query), query)

        try:
            records = run_scraper(query, target_count=args.count)
        except RuntimeError as e:
            log.error("Abbruch bei '%s': %s", query, e)
            continue
        except Exception as e:
            log.error("Unerwarteter Fehler bei '%s': %s", query, e)
            continue

        if not records:
            log.warning("  Keine Ergebnisse für '%s'.", query)
        else:
            log.info("  %d Datensätze gefunden.", len(records))

            if not args.no_website_check:
                log.info("  Prüfe Websites...")
                records = _classify_websites(records, log)
            else:
                for r in records:
                    r["website_status"] = "Nicht geprüft"

            all_records.extend(records)

        # Pause between queries to avoid triggering rate limits
        if idx < len(args.query) - 1:
            pause = random.uniform(8, 15)
            log.info("  Pause %.0fs vor nächstem Suchbegriff...", pause)
            time.sleep(pause)

    if not all_records:
        log.error("Keine Leads gesammelt. Prüfe den Suchbegriff und deine Internetverbindung.")
        sys.exit(1)

    # Deduplicate
    before = len(all_records)
    all_records = _deduplicate(all_records)
    after = len(all_records)
    if before != after:
        log.info("Dedupliziert: %d → %d Einträge (%d Duplikate entfernt)", before, after, before - after)

    # Sort by review_count descending (most-reviewed = bigger / more established business)
    all_records.sort(key=lambda r: r.get("review_count") or 0, reverse=True)

    # Export to Excel
    log.info("Exportiere %d Leads nach: %s", len(all_records), output_path)
    try:
        saved_path = export(all_records, output_path)
        log.info("=" * 60)
        print(f"\n✓ Fertig. {len(all_records)} Leads gespeichert unter:\n  {saved_path}\n")
    except PermissionError as e:
        log.error("%s", e)
        # Fallback: JSON
        fallback = TMP_DIR / f"leads_fallback_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        fallback.parent.mkdir(parents=True, exist_ok=True)
        with open(fallback, "w", encoding="utf-8") as f:
            json.dump(all_records, f, ensure_ascii=False, indent=2)
        log.info("Fallback-Datei gespeichert: %s", fallback)
        sys.exit(1)


if __name__ == "__main__":
    main()
