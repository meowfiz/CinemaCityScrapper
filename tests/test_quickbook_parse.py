"""Testy bez sieci: sanityzacja tytułów CC i ekstrakcja z tekstu stylizowanego na quickbook."""

from scraper import extract_movies_with_showtimes_cinema_city_quickbook_text as parse_cc
from scraper import sanitize_cinema_city_title as sanitize


def test_sanitize_strips_pipe_and_genres() -> None:
    assert sanitize("WOLNOŚĆ PO WŁOSKU Biografia, Dramat | 115 min") == "WOLNOŚĆ PO WŁOSKU"
    assert sanitize("MORTAL KOMBAT II Akcja, Przygodowy | 116 min") == "MORTAL KOMBAT II"
    assert sanitize("ZA DUŻY NA BAJKI 3 Familijny | 90 min") == "ZA DUŻY NA BAJKI 3"
    assert sanitize("Komedia, Dramat | 119 min") == ""
    assert sanitize("Przygodowy, Animowany, Familijny | 98 min") == ""
    assert sanitize("NORMAL Akcja, Kryminał, Thriller | 90 min") == "NORMAL"


def test_quickbook_three_cards() -> None:
    sample = """SPRINGFIELD STORY Komedia, Romantyczny | 106 min
08:55 09:41

MORTAL KOMBAT EXTRA Akcja | 118 min
15:03 17:42

ZA DUŻY NA BAJKI 3 Familijny | 90 min
21:05 21:52
"""
    m = parse_cc(sample)
    assert m["SPRINGFIELD STORY"] == ["08:55", "09:41"]
    assert m["MORTAL KOMBAT EXTRA"] == ["15:03", "17:42"]
    assert m["ZA DUŻY NA BAJKI 3"] == ["21:05", "21:52"]
    blob = "|".join(m.keys())
    assert "Familijny" not in blob
    assert "Komedia," not in blob


def test_taxi_caps_title() -> None:
    m = parse_cc("TAXI DRIVER Dramat | 114 min\n12:03 23:52\n")
    assert any(k == "TAXI DRIVER" for k in m)
