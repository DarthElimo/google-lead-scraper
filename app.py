"""
app.py
------
Flask web server for the Lead Scraper UI.
Runs as a macOS background service (launchd) — always available at http://localhost:5001

Start:  python app.py
Access: http://localhost:5001  (save as browser bookmark)
"""

import json
import logging
import os
import random
import re
import sys
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path

from flask import Flask, jsonify, render_template, request, send_file

# Add tools/ to path so we can import the scraper modules
sys.path.insert(0, str(Path(__file__).parent / "tools"))

app = Flask(__name__)

# In-memory job store — resets on server restart, which is fine
jobs: dict[str, dict] = {}
jobs_lock = threading.Lock()

TMP_DIR = Path(".tmp")
TMP_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(str(TMP_DIR / "server.log"), encoding="utf-8"),
    ],
)
logger = logging.getLogger(__name__)


# ── Routes ────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/start", methods=["POST"])
def start_job():
    data = request.get_json(force=True)
    queries = [q.strip() for q in data.get("queries", []) if q.strip()]
    count = max(5, min(500, int(data.get("count", 50))))
    website_check = bool(data.get("website_check", True))

    if not queries:
        return jsonify({"error": "Bitte mindestens einen Suchbegriff eingeben."}), 400

    job_id = str(uuid.uuid4())[:8]
    with jobs_lock:
        jobs[job_id] = {
            "status": "running",
            "progress": 0,
            "step": "Wird vorbereitet…",
            "log": [],
            "record_count": 0,
            "output_path": None,
            "error": None,
            "queries": queries,
            "count": count,
            "website_check": website_check,
        }

    thread = threading.Thread(
        target=_run_job,
        args=(job_id, queries, count, website_check),
        daemon=True,
    )
    thread.start()
    logger.info("Job %s gestartet: %s | count=%d | website_check=%s", job_id, queries, count, website_check)
    return jsonify({"job_id": job_id})


@app.route("/status/<job_id>")
def job_status(job_id):
    with jobs_lock:
        job = jobs.get(job_id)
    if job is None:
        return jsonify({"error": "Job nicht gefunden"}), 404
    return jsonify(job)


@app.route("/download/<job_id>")
def download_file(job_id):
    with jobs_lock:
        job = jobs.get(job_id)

    if not job or not job.get("output_path"):
        return "Datei nicht gefunden.", 404

    path = Path(job["output_path"])
    if not path.exists():
        return "Datei nicht mehr vorhanden.", 404

    return send_file(
        str(path.resolve()),
        as_attachment=True,
        download_name=path.name,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


# ── Job runner ────────────────────────────────────────────────────────────────

def _log(job_id: str, message: str) -> None:
    timestamp = datetime.now().strftime("%H:%M:%S")
    line = f"{timestamp}  {message}"
    with jobs_lock:
        jobs[job_id]["log"].append(line)
    logger.info("[%s] %s", job_id, message)


def _set(job_id: str, **kwargs) -> None:
    with jobs_lock:
        jobs[job_id].update(kwargs)


def _run_job(job_id: str, queries: list[str], count: int, website_check: bool) -> None:
    try:
        from scrape_google_maps import run_scraper
        from classify_website import classify_website
        from export_to_excel import export

        all_records: list[dict] = []
        n_queries = len(queries)
        scrape_progress_per_query = 60 // n_queries  # scraping = 0-60%

        # ── Phase 1: Scraping ────────────────────────────────────────────────
        for i, query in enumerate(queries):
            base_progress = i * scrape_progress_per_query
            _set(job_id, step=f"Google Maps: {query!r}", progress=base_progress)
            _log(job_id, f"Suche: {query} (Ziel: {count} Ergebnisse)")

            try:
                records = run_scraper(query, target_count=count)
            except RuntimeError as e:
                _log(job_id, f"FEHLER: {e}")
                _set(job_id, status="error", error=str(e))
                return
            except Exception as e:
                _log(job_id, f"Unerwarteter Fehler bei '{query}': {e}")
                records = []

            if not records:
                _log(job_id, f"Keine Ergebnisse fuer '{query}'.")
            else:
                all_records.extend(records)
                _log(job_id, f"{len(records)} Datensätze gefunden.")

            _set(job_id,
                 progress=base_progress + scrape_progress_per_query,
                 record_count=len(all_records))

            # Pause between queries
            if i < n_queries - 1:
                pause = random.uniform(8, 14)
                _log(job_id, f"Pause {pause:.0f}s vor nächstem Suchbegriff…")
                time.sleep(pause)

        if not all_records:
            _set(job_id, status="error", error="Keine Leads gefunden. Suchbegriff oder Internetverbindung prüfen.")
            return

        # ── Phase 2: Website classification ─────────────────────────────────
        if website_check:
            _set(job_id, step="Website-Check läuft…", progress=60)
            _log(job_id, f"Klassifiziere {len(all_records)} Websites…")
            total = len(all_records)
            for idx, record in enumerate(all_records):
                status_str = classify_website(record.get("website"))
                record["website_status"] = status_str
                progress = 60 + int((idx + 1) / total * 30)  # 60-90%
                _set(job_id, progress=progress)
                if (idx + 1) % 10 == 0 or idx == total - 1:
                    _log(job_id, f"Website-Check: {idx + 1}/{total}")
                time.sleep(random.uniform(0.5, 1.2))
        else:
            for record in all_records:
                record["website_status"] = "Nicht geprüft"

        # ── Deduplication + sort ─────────────────────────────────────────────
        _set(job_id, step="Aufbereitung…", progress=90)
        seen: set[str] = set()
        deduped = []
        for r in all_records:
            phone_key = re.sub(r"\D", "", r.get("phone") or "")
            if phone_key and phone_key in seen:
                continue
            if phone_key:
                seen.add(phone_key)
            deduped.append(r)

        deduped.sort(key=lambda r: r.get("review_count") or 0, reverse=True)
        removed = len(all_records) - len(deduped)
        if removed:
            _log(job_id, f"Dedupliziert: {removed} Duplikate entfernt.")

        # ── Phase 3: Export ──────────────────────────────────────────────────
        _set(job_id, step="Excel wird erstellt…", progress=92)
        slug = re.sub(r"[^\w]+", "_", queries[0])[:35].strip("_").lower()
        date_str = datetime.now().strftime("%Y-%m-%d")
        output_path = str(TMP_DIR / f"leads_{slug}_{date_str}.xlsx")

        _log(job_id, f"Exportiere {len(deduped)} Leads nach Excel…")
        saved = export(deduped, output_path)
        _log(job_id, f"Fertig. Datei: {Path(saved).name}")

        _set(job_id,
             status="done",
             progress=100,
             step="Fertig",
             record_count=len(deduped),
             output_path=saved)

    except Exception as e:
        logger.exception("Job %s: unbehandelter Fehler", job_id)
        _set(job_id, status="error", error=f"Interner Fehler: {e}")


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5001))
    host = "0.0.0.0" if "PORT" in os.environ else "127.0.0.1"
    logger.info("Lead Scraper läuft auf http://%s:%d", host, port)
    app.run(
        host=host,
        port=port,
        debug=False,
        use_reloader=False,
    )
