from __future__ import annotations

import csv
import json
import re
import time
import unicodedata
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Iterable, Optional
from urllib.parse import quote, urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright


# Bez granic słowa: Cinema City potrafi skleić godziny typu "15:5018:30..."
TIME_RE = re.compile(r"([01]?\d|2[0-3]):[0-5]\d")


@dataclass(frozen=True)
class MovieShowings:
    title: str
    showtimes: list[str]
    rating_filmweb: Optional[float]
    filmweb_url: Optional[str]


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

        letters = [ch for ch in s if ch.isalpha()]
        if len(letters) < 2:
            return False
        upper = sum(1 for ch in letters if ch.upper() == ch)
        return (upper / max(1, len(letters))) >= 0.75

    movies: dict[str, list[str]] = {}
    current_title: Optional[str] = None
    current_times: list[str] = []

    def flush():
        nonlocal current_title, current_times
        if current_title and current_times:
            movies[current_title] = _unique_preserve((movies.get(current_title) or []) + current_times)
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


def filmweb_search_first_movie_url(title: str, session: requests.Session) -> Optional[str]:
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
        overlap = len(q_tokens & c_tokens) / len(q_tokens)
        prefix = 1.0 if c.startswith(q) or q.startswith(c) else 0.0
        return overlap + 0.25 * prefix

    q = quote(title)
    url = f"https://www.filmweb.pl/search?q={q}"
    resp = session.get(url, timeout=20)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "lxml")

    best_url = None
    best_score = 0.0

    for a in soup.select("a[href^='/film/']"):
        href = a.get("href") or ""
        if not href.startswith("/film/"):
            continue
        if "/vod" in href:
            continue
        text = _norm_space(a.get_text(" ", strip=True))
        sc = score(text, href)
        if sc > best_score:
            best_score = sc
            best_url = urljoin("https://www.filmweb.pl", href)

    if best_url and best_score >= 0.35:
        return best_url
    return None


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


def enrich_with_filmweb_ratings(
    movies: dict[str, list[str]],
    *,
    sleep_s: float = 0.4,
    progress_cb=None,
) -> list[MovieShowings]:
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
        try:
            film_url = filmweb_search_first_movie_url(title, session=session)
            if film_url:
                rating = filmweb_extract_rating(film_url, session=session)
        except Exception:
            film_url = film_url
            rating = None

        out.append(
            MovieShowings(
                title=title,
                showtimes=showtimes,
                rating_filmweb=rating,
                filmweb_url=film_url,
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
        w = csv.DictWriter(f, fieldnames=["title", "rating_filmweb", "filmweb_url", "showtimes"])
        w.writeheader()
        for it in items:
            w.writerow(
                {
                    "title": it.title,
                    "rating_filmweb": it.rating_filmweb,
                    "filmweb_url": it.filmweb_url,
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

