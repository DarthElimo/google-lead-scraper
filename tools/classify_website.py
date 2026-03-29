"""
classify_website.py
-------------------
Checks a website URL and classifies its quality as one of:
  - "Keine Website"    → no URL, unreachable, or effectively empty
  - "Einfache Website" → exists but very basic/thin
  - "Vorhanden"        → proper website with real content

Used by run_lead_scraper.py to enrich each lead record.
"""

import logging
import re

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "de-DE,de;q=0.9,en;q=0.8",
}


def _ensure_scheme(url: str) -> str:
    if not url.startswith(("http://", "https://")):
        return "https://" + url
    return url


def _fetch_page(url: str, timeout: int = 8) -> tuple[str | None, int | None]:
    """
    Fetches a URL and returns (html_content, status_code).
    Returns (None, None) on any connection error.
    Retries once with verify=False if SSL fails (common for small German business sites).
    """
    url = _ensure_scheme(url)
    try:
        resp = requests.get(url, headers=HEADERS, timeout=timeout, allow_redirects=True)
        return resp.text, resp.status_code
    except requests.exceptions.SSLError:
        try:
            resp = requests.get(
                url, headers=HEADERS, timeout=timeout,
                allow_redirects=True, verify=False
            )
            return resp.text, resp.status_code
        except Exception:
            return None, None
    except Exception as e:
        logger.debug("Fetch failed for %s: %s", url, e)
        return None, None


def _analyze_html(html: str) -> str:
    """
    Scores the HTML content and returns a classification string.
    """
    soup = BeautifulSoup(html, "lxml")

    # Modern SPA detection — React/Vue sites render empty server-side
    module_scripts = soup.find_all("script", {"type": "module"})
    if len(module_scripts) >= 2:
        return "Vorhanden"

    score = 0

    # Word count (body text only)
    body = soup.find("body")
    body_text = body.get_text(separator=" ", strip=True) if body else ""
    word_count = len(body_text.split())
    if word_count >= 300:
        score += 3
    elif word_count >= 100:
        score += 1

    # Navigation
    has_nav = bool(soup.find("nav"))
    if not has_nav:
        # Check for a <ul> with 3+ <li> children as a fallback navigation indicator
        for ul in soup.find_all("ul"):
            if len(ul.find_all("li", recursive=False)) >= 3:
                has_nav = True
                break
    if has_nav:
        score += 2

    # Heading depth (distinct heading levels used)
    heading_levels = {tag.name for tag in soup.find_all(["h1", "h2", "h3"])}
    if len(heading_levels) >= 2:
        score += 2

    # Multiple content sections
    sections = soup.find_all(["section", "article"])
    if sections:
        score += 2
    else:
        # Fallback: count divs with meaningful text content
        content_divs = [
            div for div in soup.find_all("div")
            if len(div.get_text(strip=True).split()) > 20
        ]
        if len(content_divs) >= 3:
            score += 2

    # Contact form
    if soup.find("form"):
        score += 1

    # External stylesheets or scripts (indicates a real build, not a hand-coded single file)
    external_assets = 0
    for tag in soup.find_all(["link", "script"]):
        src = tag.get("href") or tag.get("src") or ""
        if src.startswith("http") or src.startswith("//"):
            external_assets += 1
    if external_assets >= 2:
        score += 1

    # Meta description
    if soup.find("meta", {"name": re.compile("description", re.I)}):
        score += 1

    logger.debug("Website score: %d (words=%d)", score, word_count)

    if score >= 5:
        return "Vorhanden"
    elif score >= 2:
        return "Einfache Website"
    else:
        return "Keine Website"


def classify_website(url: str | None) -> str:
    """
    Main entry point. Takes a URL (or None) and returns one of:
      "Keine Website" | "Einfache Website" | "Vorhanden"
    """
    if not url or not url.strip():
        return "Keine Website"

    # PDF or other non-HTML content
    if url.lower().endswith(".pdf"):
        return "Einfache Website"

    html, status = _fetch_page(url)

    if html is None or status is None:
        logger.debug("Unreachable: %s", url)
        return "Keine Website"

    if status >= 400:
        logger.debug("HTTP %d for: %s", status, url)
        return "Keine Website"

    result = _analyze_html(html)
    logger.debug("%s → %s", url, result)
    return result
