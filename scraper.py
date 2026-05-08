from __future__ import annotations

import csv
import json
import re
import time
import unicodedata
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Iterable, Optional
from urllib.parse import quote, urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright


# Bez granic słowa: Cinema City potrafi skleić godziny typu "15:5018:30..."
TIME_RE = re.compile(r"([01]?\d|2[0-3]):[0-5]\d")


def _cc_genre_key(tok: str) -> str:
    s = unicodedata.normalize("NFKD", tok)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]+", "", s.lower())


# Lista gatunków z kafelka Cinema City (po normalizacji do _cc_genre_key — porównanie do „czystego” stringa).
_CC_GENRES_NORM = frozenset(
    _cc_genre_key(x)
    for x in (
        "akcja",
        "animowany",
        "anime",
        "biografia",
        "dokument",
        "dokumentalny",
        "drama",
        "dramat",
        "dramat psychologiczny",
        "erotyczny",
        "familijny",
        "familijno-przygodowy",
        "familijno przygodowy",
        "fantasy",
        "fantasy przygodowy",
        "historyczny",
        "horror",
        "kino akcji",
        "komedia",
        "komediodramat",
        "kostiumowy",
        "kryminał",
        "kryminalny",
        "melodramat",
        "musical",
        "muzyczny",
        "przygodowy",
        "romans",
        "romantyczny",
        "science fiction",
        "sci-fi",
        "sensacja",
        "sportowy",
        "thriller",
        "western",
        "wojenny",
    )
)


def sanitize_cinema_city_title(raw: str) -> str:
    """Odcina z linii Cinema City końcówki typu „… gatunki | 106 min”."""

    def nz(s: str) -> str:
        return re.sub(r"\s+", " ", (s or "")).strip()

    def is_pure_genre(seg: str) -> bool:
        s = nz(seg)
        if not s:
            return False
        if _cc_genre_key(s) in _CC_GENRES_NORM:
            return True
        if "," not in s:
            return False
        parts = [nz(p) for p in s.split(",") if nz(p)]
        return bool(parts) and all(is_pure_genre(p) for p in parts)

    if not nz(raw):
        return ""

    # Pełniejsze usuwanie gatunków tylko, gdy w źródle było „… | NN min“ (standard CC).
    had_pipe_duration = bool(re.search(r"\s*\|\s*\d{1,3}\s*min\b", raw, flags=re.I))

    t = nz(raw)
    while True:
        stripped = re.sub(r"\s*\|\s*\d{1,3}\s*min\b\s*$", "", t, flags=re.I).strip()
        stripped = nz(stripped)
        if stripped == t:
            break
        t = stripped

    while True:
        if "," in t:
            left, right = t.rsplit(",", 1)
            rr = nz(right)
            if rr and is_pure_genre(rr):
                t = nz(left)
                continue
        if had_pipe_duration:
            m = re.search(r"\s+(?P<w>[^\s|,]+)$", t)
            if not m:
                tn = nz(t)
                lone = tn.strip(".,„”\"'")
                if tn and "," not in tn and " " not in tn and lone and _cc_genre_key(lone) in _CC_GENRES_NORM:
                    t = ""
                    continue
                break
            tw = nz(m.group("w").strip(".,„”\"'"))
            if _cc_genre_key(tw) in _CC_GENRES_NORM:
                t = nz(t[: m.start()])
                continue
        break

    return nz(t)


@dataclass(frozen=True)
class MovieShowings:
    title: str
    showtimes: list[str]
    rating_filmweb: Optional[float]
    filmweb_url: Optional[str]
    # Tekstowy opis przy braku linku lub niskiej pewności dopasowania.
    filmweb_feedback: Optional[str] = None
    # Kandydaci z wyszukiwania: {"url", "score", "label"} — do wyboru w GUI.
    filmweb_candidates: Optional[list[dict[str, Any]]] = None


@dataclass(frozen=True)
class FilmwebLookup:
    """Wynik wyszukiwania na Filmweb (bez wpisów z overrides.json).

    candidates: krotki (pełny_url, score, krótki opis ze strony wyszukiwania / tytułu).
    """

    url: Optional[str]
    feedback: Optional[str]
    candidates: tuple[tuple[str, float, str], ...]


def _norm_space(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def _unique_preserve(seq: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for x in seq:
        if x not in seen:
            out.append(x)
            seen.add(x)
    return out


def render_html(url: str, timeout_ms: int = 45_000) -> str:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            )
        )
        page = context.new_page()
        try:
            page.goto(url, wait_until="networkidle", timeout=timeout_ms)
        except PlaywrightTimeoutError:
            page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)

        for _ in range(5):
            page.mouse.wheel(0, 2500)
            page.wait_for_timeout(400)

        html = page.content()
        context.close()
        browser.close()
        return html


def render_cinema_city_quickbook_text(url: str, timeout_ms: int = 60_000) -> str:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            )
        )
        page = context.new_page()
        page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)

        try:
            btn = page.locator("#onetrust-accept-btn-handler")
            if btn.count():
                btn.click(timeout=2500)
        except Exception:
            pass

        try:
            page.wait_for_selector(".quickbook-section", timeout=timeout_ms)
        except PlaywrightTimeoutError:
            pass
        page.wait_for_timeout(10_000)

        root = page.locator(".quickbook-section")
        txt = root.inner_text(timeout=10_000) if root.count() else ""
        context.close()
        browser.close()
        return txt


def extract_movies_with_showtimes_generic(html: str) -> dict[str, list[str]]:
    soup = BeautifulSoup(html, "lxml")

    time_nodes = []
    for tag in soup.find_all(["a", "button", "div", "span", "li"]):
        txt = _norm_space(tag.get_text(" ", strip=True))
        if not txt:
            continue
        if TIME_RE.search(txt):
            time_nodes.append(tag)

    def container_candidates(node):
        cur = node
        for _ in range(7):
            if cur is None:
                break
            yield cur
            cur = cur.parent

    def pick_title(container) -> Optional[str]:
        title_texts: list[str] = []
        for h in container.find_all(["h1", "h2", "h3", "h4"]):
            t = _norm_space(h.get_text(" ", strip=True))
            if 2 <= len(t) <= 120 and not TIME_RE.search(t):
                title_texts.append(t)

        if not title_texts:
            for el in container.find_all(["div", "span", "a", "p", "strong"]):
                attrs = " ".join(
                    [
                        " ".join(el.get("class", [])) if isinstance(el.get("class"), list) else str(el.get("class") or ""),
                        str(el.get("id") or ""),
                    ]
                ).lower()
                if "title" not in attrs and "tyt" not in attrs:
                    continue
                t = _norm_space(el.get_text(" ", strip=True))
                if 2 <= len(t) <= 120 and not TIME_RE.search(t):
                    title_texts.append(t)

        if not title_texts:
            best = ""
            for el in container.find_all(["div", "span", "a", "p", "strong"]):
                t = _norm_space(el.get_text(" ", strip=True))
                if not (2 <= len(t) <= 120):
                    continue
                if TIME_RE.search(t):
                    continue
                if t.lower() in {"2d", "3d", "imax", "dolby", "napisy", "dubbing"}:
                    continue
                if len(t) > len(best):
                    best = t
            if best:
                title_texts.append(best)

        if not title_texts:
            return None

        title = max(title_texts, key=len).strip()
        return title or None

    movies: dict[str, list[str]] = {}
    for tn in time_nodes:
        txt = _norm_space(tn.get_text(" ", strip=True))
        times = [m.group(0) for m in TIME_RE.finditer(txt)]
        if not times:
            continue

        chosen_title = None
        for c in container_candidates(tn):
            title = pick_title(c)
            if title:
                chosen_title = title
                break

        if not chosen_title:
            continue

        movies[chosen_title] = _unique_preserve((movies.get(chosen_title) or []) + times)

    return {t: ts for t, ts in movies.items() if ts}


def extract_movies_with_showtimes_cinema_city_quickbook_text(txt: str) -> dict[str, list[str]]:
    lines = [_norm_space(x) for x in (txt or "").splitlines()]
    lines = [x for x in lines if x]

    def is_title_line(s: str) -> bool:
        if TIME_RE.search(s):
            return False
        if len(s) < 2 or len(s) > 120:
            return False
        sl = s.lower()
        if sl.strip("-–— ").strip() in {"film", "maraton"}:
            return False
        if sl.lstrip().startswith(("-", "–", "—")):
            return False
        if sl.startswith("repertuar"):
            return False
        if "wybierz" in sl:
            return False
        if re.search(r"\b\d{2}/\d{2}/\d{4}\b", sl):
            return False
        if re.match(r"^(en|pl|jpn|fr|de|it|es)\b", sl):
            return False
        if sl.startswith(("2d", "3d", "imax", "4dx", "screenx", "vip")):
            return False
        if any(x in sl for x in ["projekcja", "film z", "napisami", "dubbing", "wydarzenie specjalne"]):
            return False
        ui_markers = (
            "wyświetl",
            "wyswietl",
            "pokaż",
            "pokaz",
            "ładuj",
            "laduj",
            "więcej seans",
            "wiecej seans",
            "wszystkie seans",
            "kolejne pozycje",
            " przejdź ",
            " przechod ",
            "kup bile",
            "wydarzenia specjal",
        )
        if any(u in sl for u in ui_markers):
            return False

        probe = sanitize_cinema_city_title(s)
        letters = [ch for ch in probe if ch.isalpha()]
        if len(letters) < 2:
            return False
        upper_ratio = sum(1 for ch in letters if ch.upper() == ch) / len(letters)

        # Tytuły w liście CC są najczęściej WIELKIMI literami — luzowanie przyjmowało też „gatunki | min” jako tytuł.
        return upper_ratio >= 0.75

    movies: dict[str, list[str]] = {}
    current_title: Optional[str] = None
    current_times: list[str] = []

    def flush():
        nonlocal current_title, current_times
        if current_title and current_times:
            key = sanitize_cinema_city_title(current_title) or current_title.strip()
            movies[key] = _unique_preserve((movies.get(key) or []) + current_times)
        current_title = None
        current_times = []

    for line in lines:
        if is_title_line(line):
            if current_title and not current_times:
                last = current_title.strip().split()[-1].lower() if current_title.strip().split() else ""
                if len(current_title) < 22 or last in {"jako", "do", "u", "i", "oraz"}:
                    current_title = f"{current_title.strip()} {line.strip()}"
                    continue

            flush()
            current_title = line.strip()
            continue

        if current_title:
            for m in TIME_RE.finditer(line):
                current_times.append(m.group(0))

    flush()
    return {t: _unique_preserve(ts) for t, ts in movies.items() if ts}


def extract_movies(url: str, rendered_html: str) -> dict[str, list[str]]:
    host = urlparse(url).netloc.lower()
    if "cinema-city" in host:
        txt = render_cinema_city_quickbook_text(url)
        movies = extract_movies_with_showtimes_cinema_city_quickbook_text(txt)
        if movies:
            return movies
    return extract_movies_with_showtimes_generic(rendered_html)


def _filmweb_query_variants(title: str) -> list[str]:
    t = _norm_space(title)
    variants: list[str] = [t]

    # usuń "replacement char" (częsty przy problemach z encodingiem w źródle)
    t0 = _norm_space(t.replace("\ufffd", " "))
    if t0 and t0 not in variants:
        variants.append(t0)

    # Usuń bardzo częste "dodatki" z Cinema City
    t2 = re.sub(r"\b(MARATON|FILM)\b", "", t, flags=re.I)
    t2 = _norm_space(t2)
    if t2 and t2 not in variants:
        variants.append(t2)

    # Utnij dopiski po myślniku / dwukropku (ale zostaw, jeśli zbyt krótkie)
    for sep in [" – ", " - ", " — ", ": "]:
        if sep in t:
            head = _norm_space(t.split(sep, 1)[0])
            if len(head) >= 6 and head not in variants:
                variants.append(head)

    # Zredukuj wielokrotne spacje i znaki łączące
    variants = [_norm_space(v) for v in variants if v]
    return _unique_preserve(variants)


def filmweb_lookup_movie(title: str, session: requests.Session) -> FilmwebLookup:
    """Wyszukuje film na Filmweb; zwraca URL lub None + przyczynę oraz kandydatów."""

    def norm(s: str) -> str:
        s = unicodedata.normalize("NFKD", s or "")
        s = "".join(ch for ch in s if not unicodedata.combining(ch))
        s = s.lower()
        s = re.sub(r"[^a-z0-9]+", " ", s).strip()
        return s

    def score(candidate_text: str, href: str) -> float:
        q = norm(title)
        c = norm(candidate_text) or norm(href)
        if not q or not c:
            return 0.0
        q_tokens = set(q.split())
        c_tokens = set(c.split())
        if not q_tokens or not c_tokens:
            return 0.0
        # wymagaj sensownego pokrycia tokenów; inaczej wolimy brak oceny niż zły film
        overlap = len(q_tokens & c_tokens) / len(q_tokens)
        prefix = 1.0 if c.startswith(q) or q.startswith(c) else 0.0

        # jeśli mamy "długie" tokeny, muszą się w większości zgadzać
        long_q = {t for t in q_tokens if len(t) >= 4}
        if long_q:
            long_overlap = len(long_q & c_tokens) / len(long_q)
            if long_overlap < 0.6 and prefix == 0.0:
                return 0.0

        # bonus jeśli kandydat zawiera wszystkie tokeny z query (krótkie pomijamy)
        must = {t for t in q_tokens if len(t) >= 5}
        contains_all = 1.0 if (must and must.issubset(c_tokens)) else 0.0
        return overlap + 0.35 * prefix + 0.15 * contains_all

    # Wszystkie zobaczone /film/ (także score 0) — do pokazania w GUI przy krótkich / dziwnych tytułach.
    display_candidates: dict[str, tuple[float, str]] = {}

    def merge_display(url_: str, sc_: float, lbl_: str) -> None:
        lbl_short = lbl_[:200]
        if url_ not in display_candidates:
            display_candidates[url_] = (sc_, lbl_short)
            return
        old_sc, old_lbl = display_candidates[url_]
        if sc_ > old_sc or (sc_ == old_sc and len(lbl_short) > len(old_lbl)):
            display_candidates[url_] = (sc_, lbl_short)

    def search_once(qt: str, *, allow_vod: bool) -> list[tuple[str, float, str]]:
        q = quote(qt)
        url = f"https://www.filmweb.pl/search?q={q}"
        resp = session.get(url, timeout=20)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml")

        scored: list[tuple[str, float, str]] = []
        for a in soup.select("a[href^='/film/']"):
            href = a.get("href") or ""
            if not href.startswith("/film/"):
                continue
            if (not allow_vod) and ("/vod" in href):
                continue
            text = _norm_space(a.get_text(" ", strip=True))
            if not text:
                text = _norm_space(a.get("title") or "") or _norm_space(a.get("aria-label") or "")
            sc = score(text, href)
            full = urljoin("https://www.filmweb.pl", href)
            lbl = text or href.strip("/")
            merge_display(full, sc, lbl)
            if sc <= 0:
                continue
            scored.append((full, sc, lbl))

        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:8]

    def page_title_from_film(url: str) -> str:
        try:
            resp = session.get(url, timeout=20)
            resp.raise_for_status()
        except Exception:
            return ""
        soup = BeautifulSoup(resp.text, "lxml")
        h1 = soup.find(["h1", "h2"])
        if h1:
            t = _norm_space(h1.get_text(" ", strip=True))
            if t:
                return t
        ogt = soup.find("meta", attrs={"property": "og:title"})
        if ogt and ogt.get("content"):
            return _norm_space(str(ogt.get("content")))
        return ""

    def search_playwright(qt: str, *, allow_vod: bool) -> list[tuple[str, float, str]]:
        # Filmweb wyniki wyszukiwania są często renderowane po stronie JS.
        q = quote(qt)
        url = f"https://www.filmweb.pl/search?q={q}"
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                context = browser.new_context(
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
                )
                page = context.new_page()
                page.goto(url, wait_until="domcontentloaded", timeout=45_000)
                # poczekaj aż pojawią się linki do filmów
                try:
                    page.wait_for_selector("a[href^='/film/']", timeout=12_000)
                except Exception:
                    pass
                anchors = page.locator("a[href^='/film/']")
                n = min(anchors.count(), 60)
                scored: list[tuple[str, float, str]] = []
                for i in range(n):
                    a = anchors.nth(i)
                    href = a.get_attribute("href") or ""
                    if not href.startswith("/film/"):
                        continue
                    if (not allow_vod) and ("/vod" in href):
                        continue
                    txt = _norm_space(a.inner_text(timeout=2000) or "")
                    if not txt:
                        txt = _norm_space(a.get_attribute("title") or "") or _norm_space(a.get_attribute("aria-label") or "")
                    full = urljoin("https://www.filmweb.pl", href)
                    sc = score(txt, href)
                    merge_display(full, sc, txt or href.strip("/"))
                    if sc > 0:
                        scored.append((full, sc, txt or href.strip("/")))
                context.close()
                browser.close()
        except Exception:
            return []

        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:8]

    def extract_year_from_url(u: str) -> int:
        m = re.search(r"-(19\d{2}|20\d{2})-(\d+)(?:/|$)", u)
        return int(m.group(1)) if m else -1

    def merge_into(store: dict[str, tuple[float, str]], items: Iterable[tuple[str, float, str]]) -> None:
        for url_, sc_, lbl_ in items:
            if url_ not in store or sc_ > store[url_][0]:
                store[url_] = (sc_, lbl_)

    def pick_newest_url(store: dict[str, tuple[float, str]]) -> Optional[str]:
        if not store:
            return None
        best_u = sorted(
            store.keys(),
            key=lambda u_: (extract_year_from_url(u_), store[u_][0]),
            reverse=True,
        )[0]
        return best_u

    def verify_pairs(items: Iterable[tuple[str, float, str]]) -> None:
        nonlocal best_url, best_score
        for cand_url_, sc_, __ in items:
            pt = page_title_from_film(cand_url_)
            scv = max(sc_, score(pt, cand_url_))
            if scv > best_score:
                best_url = cand_url_
                best_score = scv

    variants_try = _filmweb_query_variants(title)
    url_to_best: dict[str, tuple[float, str]] = {}
    chosen_url: Optional[str] = None
    heuristic_note: Optional[str] = None

    best_url = None
    best_score = 0.0

    for qt in variants_try:
        cands = search_once(qt, allow_vod=False)
        merge_into(url_to_best, cands)
        verify_pairs(cands)
        if best_score < 0.35:
            cands_p = search_playwright(qt, allow_vod=False)
            merge_into(url_to_best, cands_p)
            verify_pairs(cands_p)

    if best_url and best_score >= 0.35:
        chosen_url = best_url
    else:
        nw_pre = pick_newest_url(url_to_best)
        if nw_pre:
            chosen_url = nw_pre
            heuristic_note = (
                "Brak pewnego automatycznego dopasowania; użyto heurystyki „najnowszy rok\" w adresie Filmweb."
            )
        else:
            for qt in variants_try:
                cands = search_once(qt, allow_vod=True)
                merge_into(url_to_best, cands)
                verify_pairs(cands)
                if best_score < 0.32:
                    cands_p = search_playwright(qt, allow_vod=True)
                    merge_into(url_to_best, cands_p)
                    verify_pairs(cands_p)

            if best_url and best_score >= 0.32:
                chosen_url = best_url
            else:
                nw2 = pick_newest_url(url_to_best)
                if nw2:
                    chosen_url = nw2
                    heuristic_note = (
                        "Brak pewnego dopasowania; użyto heurystyki „najnowszy rok\" (po uwzględnieniu /vod)."
                    )

    def build_candidate_ranking(limit: int = 18) -> tuple[tuple[str, float, str], ...]:
        merged: dict[str, tuple[float, str]] = dict(display_candidates)
        for u_, pair in url_to_best.items():
            sc_, lbl_ = pair
            if u_ not in merged or sc_ > merged[u_][0]:
                merged[u_] = (sc_, lbl_[:200])
            elif abs(sc_ - merged[u_][0]) < 1e-9:
                merged[u_] = (sc_, max(lbl_, merged[u_][1], key=len)[:200])
        ranking = sorted(
            merged.items(),
            key=lambda item: (
                item[1][0],
                extract_year_from_url(item[0]),
                len(item[1][1]),
            ),
            reverse=True,
        )[:limit]
        return tuple((str(u_), float(sc_), str(lbl_)[:160]) for u_, (sc_, lbl_) in ranking)

    tup = build_candidate_ranking()

    if chosen_url:
        fb: Optional[str] = None
        if best_score >= 0.35:
            fb = heuristic_note  # rzadkie: pewność OK, ale użytkownik i tak dostaje czysty wiersz
        else:
            fb = heuristic_note or (
                f"Wybrano URL z automatycznego lub heurystycznego dopasowania (wynik wg algorytmu: {best_score:.2f})."
            )

        ambiguous = False
        if tup and len(tup) >= 2 and 0.22 <= best_score < 0.35:
            ambiguous = True
            if fb:
                fb = fb + "\nKilka wyników ma zbliżoną pewność – warto sprawdzić listę i ewentualnie wybrać inny adres."
            else:
                fb = "Kilka wyników ma zbliżoną pewność – warto sprawdzić listę i ewentualnie wybrać inny adres."

        return FilmwebLookup(
            url=chosen_url,
            feedback=fb,
            candidates=tup if (fb or ambiguous) else (),
        )

    lines = [
        f"Tytuł z repertuaru: {title!r}",
        f"Wypróbowane warianty zapytania: {variants_try}",
        "Nie znaleziono pewnego ani heurystycznego adresu /film na Filmweb (brak lub zerowy automatyczny dopasowany score).",
    ]
    if tup:
        lines.append(
            "Na stronie wyszukiwania Filmweb są wyniki w liście poniżej — score 0 oznacza, że tytuł z kina "
            "nie pokrywa się z algorytmem dopasowania; wybierz właściwy adres ręcznie."
        )
    elif not display_candidates:
        lines.append(
            "Filmweb nie zwrócił żadnych linków /film/ (możliwy rendering po stronie przeglądarki albo blokada)."
            "\nSpróbuj wkleić adres z ręcznego wyszukania na filmweb.pl."
        )

    return FilmwebLookup(url=None, feedback="\n".join(lines), candidates=tup)


def filmweb_search_first_movie_url(title: str, session: requests.Session) -> Optional[str]:
    return filmweb_lookup_movie(title, session).url


def filmweb_extract_rating(film_url: str, session: requests.Session) -> Optional[float]:
    resp = session.get(film_url, timeout=20)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "lxml")

    micro = soup.select_one('[itemprop="ratingValue"]')
    if micro:
        val = _norm_space(micro.get_text(" ", strip=True))
        if val:
            try:
                return float(val.replace(",", "."))
            except Exception:
                pass

    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        raw = script.get_text(strip=True)
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except Exception:
            continue

        candidates = data if isinstance(data, list) else [data]
        for obj in candidates:
            if not isinstance(obj, dict):
                continue
            agg = obj.get("aggregateRating")
            if isinstance(agg, dict) and "ratingValue" in agg:
                try:
                    return float(str(agg["ratingValue"]).replace(",", "."))
                except Exception:
                    pass
            if "ratingValue" in obj:
                try:
                    return float(str(obj["ratingValue"]).replace(",", "."))
                except Exception:
                    pass

    meta = soup.find("meta", attrs={"property": "og:rating"})
    if meta and meta.get("content"):
        try:
            return float(str(meta["content"]).replace(",", "."))
        except Exception:
            return None

    return None


def normalize_override_key(title: str) -> str:
    """Klucz dopasowania override odporny na polskie znaki i zastępniki Unicode (�)."""
    s = (title or "").replace("\ufffd", " ")
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = s.lower()
    s = re.sub(r"\s+", " ", s).strip()
    return s


def enrich_with_filmweb_ratings(
    movies: dict[str, list[str]],
    *,
    sleep_s: float = 0.4,
    progress_cb=None,
) -> list[MovieShowings]:
    # Optional overrides: mapowanie tytuł -> URL Filmweb
    # Plik: overrides.json w katalogu uruchomienia (obok skryptu).
    overrides: dict[str, str] = {}
    try:
        p = Path("overrides.json")
        if p.exists():
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                overrides = {
                    normalize_override_key(str(k)): str(v).strip() for k, v in data.items() if k and v
                }
    except Exception:
        overrides = {}

    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "pl-PL,pl;q=0.9,en-US;q=0.7,en;q=0.6",
        }
    )

    out: list[MovieShowings] = []
    items = list(movies.items())
    for idx, (title, showtimes) in enumerate(items, start=1):
        if progress_cb:
            progress_cb(f"Filmweb {idx}/{len(items)}: {title}")

        film_url = None
        rating = None
        feedback: Optional[str] = None
        cand_entries: Optional[list[dict[str, Any]]] = None
        try:
            key = normalize_override_key(title)
            override_u = overrides.get(key)
            if override_u:
                film_url = override_u
                if film_url:
                    rating = filmweb_extract_rating(film_url, session=session)
            else:
                lk = filmweb_lookup_movie(title, session=session)
                film_url = lk.url
                feedback = lk.feedback
                if lk.candidates:
                    cand_entries = [
                        {"url": u, "score": round(s, 4), "label": lbl} for u, s, lbl in lk.candidates
                    ]
                if film_url:
                    rating = filmweb_extract_rating(film_url, session=session)
        except Exception as exc:
            feedback = f"Błąd podczas wyszukiwania / pobierania oceny Filmweb: {exc}"

        out.append(
            MovieShowings(
                title=title,
                showtimes=showtimes,
                rating_filmweb=rating,
                filmweb_url=film_url,
                filmweb_feedback=feedback,
                filmweb_candidates=cand_entries,
            )
        )
        time.sleep(sleep_s)

    out.sort(key=lambda m: (m.rating_filmweb is not None, m.rating_filmweb or -1.0, m.title), reverse=True)
    return out


def write_outputs(items: list[MovieShowings], out_prefix: Path) -> tuple[Path, Path]:
    json_path = out_prefix.with_suffix(".json")
    csv_path = out_prefix.with_suffix(".csv")

    json_path.write_text(
        json.dumps([asdict(x) for x in items], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    with csv_path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(
            f,
            fieldnames=[
                "title",
                "rating_filmweb",
                "filmweb_url",
                "filmweb_feedback",
                "filmweb_candidates",
                "showtimes",
            ],
        )
        w.writeheader()
        for it in items:
            w.writerow(
                {
                    "title": it.title,
                    "rating_filmweb": it.rating_filmweb,
                    "filmweb_url": it.filmweb_url,
                    "filmweb_feedback": it.filmweb_feedback or "",
                    "filmweb_candidates": (
                        json.dumps(it.filmweb_candidates, ensure_ascii=False) if it.filmweb_candidates else ""
                    ),
                    "showtimes": ", ".join(it.showtimes),
                }
            )

    return json_path, csv_path


def run_scrape(
    *,
    url: str,
    out_prefix: Path,
    timeout_ms: int = 45_000,
    dump_html: Optional[Path] = None,
    progress_cb=None,
) -> tuple[list[MovieShowings], Path, Path]:
    if progress_cb:
        progress_cb("Renderuję stronę…")
    html = render_html(url, timeout_ms=timeout_ms)
    if dump_html:
        dump_html.write_text(html, encoding="utf-8")

    if progress_cb:
        progress_cb("Wyciągam filmy z timetable…")
    movies = extract_movies(url, html)
    if not movies:
        raise RuntimeError("Nie znalazłem żadnych godzin seansów na stronie.")

    if progress_cb:
        progress_cb(f"Znalezione tytuły: {len(movies)}. Pobieram oceny z Filmweb…")
    items = enrich_with_filmweb_ratings(movies, progress_cb=progress_cb)

    if progress_cb:
        progress_cb("Zapisuję wyniki…")
    json_path, csv_path = write_outputs(items, out_prefix=out_prefix)
    if progress_cb:
        progress_cb(f"Gotowe. Zapisano {json_path.name} i {csv_path.name}.")
    return items, json_path, csv_path

