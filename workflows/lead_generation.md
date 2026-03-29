# Workflow: Lead Generation — Lokale Handwerksbetriebe

## Ziel

Automatisch lokale Betriebe (Heizung, Sanitär, Elektriker, Maler etc.) aus Google Maps scrapen und als strukturierte Excel-Datei für Cold Calling aufbereiten.

**Output:** `.tmp/leads_*.xlsx` mit Spalten:
`Name | Telefon | Adresse | Website | Rating | Bewertungen | Maps Link | Website Status`

---

## Einmalige Einrichtung

```bash
# 1. Abhängigkeiten installieren
pip install -r requirements.txt

# 2. Chromium Browser herunterladen (nur einmal nötig)
playwright install chromium
```

---

## Ausführung

### Einzelne Suche

```bash
python tools/run_lead_scraper.py --query "Heizung Sanitär Stuttgart" --count 100
```

### Mehrere Branchen gleichzeitig (werden zu einer Datei zusammengeführt)

```bash
python tools/run_lead_scraper.py \
  --query "Heizung Stuttgart" "Sanitär Stuttgart" "Elektriker Stuttgart" \
  --count 200
```

### Schneller Run ohne Website-Check (~40% schneller)

```bash
python tools/run_lead_scraper.py --query "Heizung Stuttgart" --count 500 --no-website-check
```

### Eigener Ausgabepfad

```bash
python tools/run_lead_scraper.py \
  --query "Heizung Stuttgart" \
  --count 100 \
  --output leads/kampagne_april.xlsx
```

### Alle Optionen

| Flag | Standard | Beschreibung |
|------|----------|-------------|
| `--query` / `-q` | — | Suchbegriff(e), mind. 1 erforderlich |
| `--count` / `-n` | 50 | Ergebnisse pro Suchbegriff |
| `--output` / `-o` | `.tmp/leads_*.xlsx` | Ausgabedatei |
| `--no-website-check` | aus | Website-Klassifizierung überspringen |
| `--verbose` / `-v` | aus | Detailliertes Logging |

---

## Ergebnisse verstehen

### Website Status — Was bedeutet das für den Vertrieb?

| Status | Bedeutung | Strategie |
|--------|-----------|-----------|
| `Keine Website` | Betrieb hat keine oder unerreichbare Website | **Top-Priorität**: Direktes Angebot für Website + Digitalisierung |
| `Einfache Website` | Sehr rudimentäre, alte oder inhaltsarme Seite | **Hohe Priorität**: Website-Redesign, SEO, Online-Präsenz aufbauen |
| `Vorhanden` | Funktionierende, moderne Website | Geringere Priorität; andere Dienstleistungen pitchen |
| `Nicht geprüft` | `--no-website-check` war aktiv | Manuell prüfen bei Interesse |

### Excel-Filter-Empfehlung

1. AutoFilter auf Spalte **Website Status** → nur `Keine Website` zeigen → beste Leads oben
2. Zusätzlich nach **Bewertungen** sortieren (viele Bewertungen = etablierter Betrieb = Budget vorhanden)
3. Betriebe ohne Telefonnummer herausfiltern

---

## Bekannte Probleme & Lösungen

### Google CAPTCHA

**Symptom:** Script bricht ab mit „CAPTCHA erkannt"

**Lösung:**
1. Warte 30–60 Minuten
2. Lösche die gespeicherte Session: `rm .tmp/browser_state.json`
3. Starte erneut

**Hinweis:** Bei wöchentlicher Nutzung mit ~500 Leads sehr unwahrscheinlich.

---

### 0 Ergebnisse

**Symptom:** „Keine Ergebnisse für '...'"

**Mögliche Ursachen:**
- Suchbegriff zu spezifisch oder unbekannte Schreibweise
- Google Maps hat für diese Kombination keine Einträge

**Lösung:**
- Allgemeinerer Begriff versuchen: `"Klempner Stuttgart"` statt `"Klempnermeister Stuttgart Mitte"`
- Stadt variieren: `"Stuttgart"` → `"Stuttgart Vaihingen"`, `"Leinfelden-Echterdingen"` usw.

---

### Langsamer Run

**Symptom:** 500 Leads dauern sehr lange (>90 Minuten)

**Lösung:** `--no-website-check` verwenden. Website-Klassifizierung macht ~40% der Laufzeit aus. Kann später manuell für Top-Leads nachgeholt werden.

---

### Excel-Datei kann nicht gespeichert werden

**Symptom:** „Kann Datei nicht speichern — ist sie in Excel geöffnet?"

**Lösung:** Excel schließen, dann Script erneut ausführen. Die Daten gehen nicht verloren (JSON-Fallback unter `.tmp/leads_fallback_*.json`).

---

### Selektoren nicht mehr gültig (nach Google Maps Update)

**Symptom:** Alle Felder außer `maps_link` sind leer (`None`)

**Ursache:** Google Maps ändert CSS-Klassen 2–3x pro Jahr.

**Lösung:**
1. Öffne Google Maps im Browser, suche nach einem Betrieb
2. Öffne DevTools (F12) → Inspector
3. Identifiziere die neuen Selektoren für Name, Adresse, Telefon, Website, Rating
4. Aktualisiere `tools/scrape_google_maps.py` (Zeile ~95–105, Funktion `_extract_card_data`)
5. Notiere die Änderung hier im Abschnitt „Wartungshistorie"

---

## Laufzeit-Schätzung

| Leads | Ohne Website-Check | Mit Website-Check |
|-------|-------------------|-------------------|
| 50 | ~10 Min | ~18 Min |
| 100 | ~18 Min | ~35 Min |
| 300 | ~50 Min | ~90 Min |
| 500 | ~80 Min | ~150 Min |

*Zeiten variieren je nach Internet-Geschwindigkeit und Google-Antwortzeit.*

---

## Logs

Alle Ausgaben werden nach `.tmp/scraper.log` geschrieben.
Bei Problemen: Log-Datei prüfen für vollständige Fehlermeldungen.

---

## Wartungshistorie

| Datum | Änderung |
|-------|----------|
| 2026-03-29 | Initiale Version erstellt |

