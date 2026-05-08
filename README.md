# scrapper – repertuar kin + oceny Filmweb

Program pobiera stronę repertuaru (z renderowaniem JS), wyciąga **tylko filmy z aktualnym timetable (godzinami seansów)**, następnie pobiera ocenę każdego filmu z Filmweb i sortuje wyniki od najlepszego do najgorszego.

## Wymagania

- Python 3.10+ (zalecane 3.11/3.12)

## Instalacja

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python -m playwright install chromium
```

## Uruchomienie

```bash
python scrape_movies.py --url "TUTAJ_WKLEJ_LINK_DO_REPERTUARU" --out results
```

Wygeneruje pliki:

- `results.json`
- `results.csv`

## Uwagi

- Skrypt ma heurystyki do wyciągania tytułów + godzin z HTML (różne kina mają różny DOM). Jeśli podasz konkretny link, można dopracować selektory pod tę stronę (będzie 100% stabilniej).
- Filmweb nie ma oficjalnego publicznego API – oceny są pobierane przez HTML/JSON-LD (może się zmienić po stronie Filmweb).

## GUI (najprościej)

GUI nie wymaga dodatkowych bibliotek (Tkinter jest w standardowej instalacji Pythona).

```bash
python gui.py
```

Wklejasz link, klikasz **Start**, a wynik zapisze się do JSON/CSV (i jest posortowany od najlepszego do najgorszego).

## Gdy Filmweb nie znajduje części tytułów

Filmweb nie zawsze zwraca sensowne wyniki wyszukiwania dla krótkich/ogólnych tytułów lub stron renderowanych po JS.
Możesz dodać ręczne mapowanie tytuł → URL w pliku `overrides.json` (w katalogu projektu).

- Skopiuj `overrides.example.json` → `overrides.json`
- Dopisz brakujące tytuły i ich linki z Filmweb


