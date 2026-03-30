"""
scrape_google_maps.py
---------------------
Scrapes Google Maps search results using Playwright (Chromium).
No API key or account required — 100% free.

Entry point:
    run_scraper(query: str, target_count: int = 50) -> list[dict]

Each dict contains:
    name, phone, address, website, rating, review_count, maps_link
    (missing fields are None, never omitted)

Anti-detection:
    - German locale / Europe/Berlin timezone / Stuttgart geolocation
    - Human-like random delays between every action
    - Session cookies persisted in .tmp/browser_state.json
    - navigator.webdriver masked via JS injection
"""

import json
import logging
import os
import random
import re
import time
import urllib.parse
from pathlib import Path

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

logger = logging.getLogger(__name__)

BROWSER_STATE_PATH = Path(".tmp/browser_state.json")
STEALTH_SCRIPT = """
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
window.chrome = { runtime: {} };
Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
Object.defineProperty(navigator, 'languages', {get: () => ['de-DE', 'de', 'en']});
"""


def _random_delay(low: float = 1.0, high: float = 2.5) -> None:
    time.sleep(random.uniform(low, high))


def _build_search_url(query: str) -> str:
    encoded = urllib.parse.quote_plus(query)
    return f"https://www.google.com/maps/search/{encoded}"


def _scroll_results_feed(page, target_count: int) -> None:
    """
    Scrolls the Google Maps results sidebar until target_count cards are visible
    or results are exhausted (3 consecutive scroll-stalls).
    """
    stall_count = 0
    previous_count = 0

    for _ in range(200):  # safety cap
        try:
            page.evaluate(
                "document.querySelector('div[role=\"feed\"]').scrollTop += 3000"
            )
        except Exception:
            # Feed not yet rendered — wait and retry
            _random_delay(1.5, 2.5)
            continue

        _random_delay(2.5, 4.0)

        cards = page.query_selector_all('a[href*="/maps/place/"]')
        current_count = len(cards)
        logger.debug("Sichtbare Karten: %d / %d", current_count, target_count)

        if current_count >= target_count:
            break

        if current_count == previous_count:
            stall_count += 1
            if stall_count >= 6:
                logger.info(
                    "Keine weiteren Ergebnisse. Gefunden: %d (Ziel war %d)",
                    current_count, target_count
                )
                break
        else:
            stall_count = 0

        previous_count = current_count

        # Google sometimes renders an explicit end-of-results marker
        end_marker = page.query_selector("span.HlvSq")
        if end_marker:
            logger.info("Ende der Ergebnisliste erreicht (%d Einträge).", current_count)
            break


def _extract_text(page, selector: str) -> str | None:
    try:
        el = page.query_selector(selector)
        return el.inner_text().strip() if el else None
    except Exception:
        return None


def _extract_aria_label(page, selector: str, strip_prefix: str = "") -> str | None:
    try:
        el = page.query_selector(selector)
        if not el:
            return None
        label = el.get_attribute("aria-label") or ""
        if strip_prefix and label.startswith(strip_prefix):
            label = label[len(strip_prefix):]
        return label.strip() or None
    except Exception:
        return None


def _extract_href(page, selector: str) -> str | None:
    try:
        el = page.query_selector(selector)
        return el.get_attribute("href") if el else None
    except Exception:
        return None


def _parse_rating(raw: str | None) -> float | None:
    if not raw:
        return None
    try:
        return float(raw.replace(",", "."))
    except ValueError:
        return None


def _parse_review_count(raw: str | None) -> int | None:
    if not raw:
        return None
    digits = "".join(c for c in raw if c.isdigit())
    return int(digits) if digits else None


def _extract_card_data(page, cards: list) -> list[dict]:
    """
    Clicks each result card, waits for the detail panel, extracts fields.
    Per-card errors are logged and skipped — never fatal.
    """
    results = []

    for i, card in enumerate(cards):
        try:
            _random_delay(0.4, 1.0)
            card.click()

            try:
                page.wait_for_selector("h1.DUwDvf", timeout=6000)
            except PlaywrightTimeoutError:
                logger.warning("Karte %d: Detail-Panel nicht geladen, übersprungen.", i + 1)
                results.append(_empty_record())
                continue

            _random_delay(0.3, 0.8)

            name = _extract_text(page, "h1.DUwDvf")
            address = _extract_aria_label(page, 'button[data-item-id="address"]', "Adresse: ")
            phone = _extract_aria_label(page, 'button[data-item-id^="phone:tel:"]', "Telefon: ")
            website = _extract_href(page, 'a[data-item-id="authority"]')
            maps_link = page.url

            # Rating from aria-label on the star span, e.g. "4,6 Sterne"
            rating_raw = None
            star = page.query_selector("span.ceNzKf")
            if star:
                lbl = star.get_attribute("aria-label") or ""
                m = re.search(r"([\d,]+)\s*Sterne?", lbl)
                if m:
                    rating_raw = m.group(1)
            # Fallback: text of first MW4etd in detail panel
            if not rating_raw:
                mains = page.query_selector_all('div[role="main"]')
                detail = mains[1] if len(mains) > 1 else (mains[0] if mains else None)
                if detail:
                    el = detail.query_selector("span.MW4etd")
                    if el:
                        rating_raw = el.inner_text().strip()

            record = {
                "name": name,
                "phone": phone,
                "address": address,
                "website": website,
                "rating": _parse_rating(rating_raw),
                "review_count": None,
                "maps_link": maps_link,
            }

            logger.debug(
                "[%d/%d] %s | rating=%s | %s",
                i + 1, len(cards),
                name or "—", rating_raw, phone or "—"
            )
            results.append(record)

        except Exception as e:
            logger.warning("Karte %d: Fehler beim Extrahieren: %s", i + 1, e)
            results.append(_empty_record())

    return results



def _extract_from_sidebar(card) -> dict:
    """
    Reads business data directly from a sidebar card element — no click required.
    Phone and website are extracted if Google has surfaced them as chips in the card;
    otherwise they remain None and will be fetched by clicking in the hybrid function.
    """
    # Name
    name_el = card.query_selector("div.qBF1Pd")
    name = name_el.inner_text().strip() if name_el else None
    if not name:
        name = (card.get_attribute("aria-label") or "").strip() or None

    # Rating
    rating_el = card.query_selector("span.MW4etd")
    rating_raw = rating_el.inner_text().strip() if rating_el else None

    # Address / area snippet
    address = None
    info_el = card.query_selector("div.W4Efsd")
    if info_el:
        parts = [p.strip() for p in info_el.inner_text().split("·") if p.strip()]
        address = parts[-1] if parts else None

    # Phone — tel: link in card if Google surfaced it
    phone = None
    tel_el = card.query_selector('a[href^="tel:"]')
    if tel_el:
        phone = (tel_el.get_attribute("href") or "").replace("tel:", "").strip() or None

    # Website — non-Google external href in card
    website = None
    for a in card.query_selector_all("a[href]"):
        href = a.get_attribute("href") or ""
        if href.startswith("http") and "google.com" not in href and "maps.app" not in href:
            website = href
            break

    return {
        "name": name,
        "phone": phone,
        "address": address,
        "website": website,
        "rating": _parse_rating(rating_raw),
        "review_count": None,
        "maps_link": card.get_attribute("href"),
    }


def _extract_card_data_hybrid(page, cards: list) -> list[dict]:
    """
    Hybrid extraction: read sidebar first (no click), then click only if
    phone OR website is missing. Significantly faster than clicking every card.
    """
    results = []

    for i, card in enumerate(cards):
        try:
            record = _extract_from_sidebar(card)

            needs_click = not record["phone"] or not record["website"]
            if needs_click:
                _random_delay(0.3, 0.7)
                card.click()
                try:
                    page.wait_for_selector("h1.DUwDvf", timeout=6000)
                except PlaywrightTimeoutError:
                    logger.warning("Karte %d: Detail-Panel nicht geladen, übersprungen.", i + 1)
                    results.append(record)
                    continue
                _random_delay(0.2, 0.5)

                if not record["phone"]:
                    record["phone"] = _extract_aria_label(
                        page, 'button[data-item-id^="phone:tel:"]', "Telefon: "
                    )
                if not record["website"]:
                    record["website"] = _extract_href(page, 'a[data-item-id="authority"]')
                record["maps_link"] = page.url

            logger.debug(
                "[%d/%d] %s | phone=%s | website=%s | clicked=%s",
                i + 1, len(cards),
                record["name"] or "—",
                "✓" if record["phone"] else "—",
                "✓" if record["website"] else "—",
                "ja" if needs_click else "nein",
            )
            results.append(record)

        except Exception as e:
            logger.warning("Karte %d: Fehler beim Extrahieren: %s", i + 1, e)
            results.append(_empty_record())

    return results


def _empty_record() -> dict:
    return {
        "name": None,
        "phone": None,
        "address": None,
        "website": None,
        "rating": None,
        "review_count": None,
        "maps_link": None,
    }


def run_scraper(query: str, target_count: int = 50) -> list[dict]:
    """
    Main entry point. Opens Google Maps, searches for query,
    scrolls until target_count results are visible, extracts data.

    Returns a list of dicts. Missing fields are None.
    Raises RuntimeError if a CAPTCHA is detected.
    """
    BROWSER_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    storage_state = str(BROWSER_STATE_PATH) if BROWSER_STATE_PATH.exists() else None

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--disable-infobars",
                "--no-first-run",
                "--no-default-browser-check",
                "--lang=de-DE,de",
            ],
        )

        context_kwargs = dict(
            locale="de-DE",
            timezone_id="Europe/Berlin",
            viewport={"width": 1366, "height": 768},
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            geolocation={"latitude": 48.7758, "longitude": 9.1829},
            permissions=["geolocation"],
        )
        if storage_state:
            context_kwargs["storage_state"] = storage_state

        context = browser.new_context(**context_kwargs)
        context.add_init_script(STEALTH_SCRIPT)
        page = context.new_page()

        try:
            url = _build_search_url(query)
            logger.info("Öffne Google Maps: %s", url)

            # Retry logic for the initial page load
            for attempt in range(3):
                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=20000)
                    break
                except PlaywrightTimeoutError:
                    if attempt == 2:
                        raise
                    logger.warning("Timeout beim Laden (Versuch %d/3), warte...", attempt + 1)
                    _random_delay(8, 12)

            _random_delay(2.0, 3.5)

            # Google Consent Banner (consent.google.com)
            if "consent.google.com" in page.url:
                logger.info("Google-Zustimmungsbanner erkannt — akzeptiere Cookies…")
                try:
                    page.click('button[aria-label="Alle akzeptieren"]', timeout=6000)
                    page.wait_for_url("**/maps/**", timeout=15000)
                    _random_delay(2.0, 3.5)
                    logger.info("Consent akzeptiert, weiter zu Maps: %s", page.url[:80])
                except PlaywrightTimeoutError:
                    logger.warning("Consent-Button konnte nicht geklickt werden.")

            # CAPTCHA detection
            if page.query_selector('iframe[title*="reCAPTCHA"]') or \
               page.query_selector("#recaptcha"):
                raise RuntimeError(
                    "CAPTCHA erkannt — Google hat diese Session blockiert.\n"
                    "Lösung: Warte 30-60 Minuten, dann erneut versuchen.\n"
                    "Oder: Lösche .tmp/browser_state.json und starte neu."
                )

            # Wait for the results feed
            try:
                page.wait_for_selector('div[role="feed"]', timeout=12000)
            except PlaywrightTimeoutError:
                logger.warning(
                    "Ergebnisliste nicht geladen. "
                    "Möglicherweise 0 Treffer für diesen Suchbegriff."
                )
                return []

            _random_delay(1.0, 2.0)

            # Scroll to load enough results
            _scroll_results_feed(page, target_count)

            # Collect all visible result cards
            cards = page.query_selector_all('a[href*="/maps/place/"]')
            cards = cards[:target_count]  # cap at requested amount
            logger.info("%d Karten gefunden, extrahiere Daten...", len(cards))

            if not cards:
                return []

            results = _extract_card_data_hybrid(page, cards)

            # Persist session cookies for next run
            context.storage_state(path=str(BROWSER_STATE_PATH))
            logger.debug("Browser-Session gespeichert: %s", BROWSER_STATE_PATH)

            return results

        finally:
            context.close()
            browser.close()
