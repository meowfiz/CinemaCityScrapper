from __future__ import annotations

import json
import threading
import webbrowser
from pathlib import Path
from queue import Queue, Empty
from tkinter import END, BOTH, DISABLED, NORMAL, StringVar, Tk, Toplevel, ttk
from tkinter import Listbox
from tkinter.scrolledtext import ScrolledText
from tkinter import messagebox

from scraper import run_scrape


class App(Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Scrapper repertuaru → Filmweb")
        self.geometry("900x620")

        self.url_var = StringVar(value="https://www.cinema-city.pl/kina/czerwonadroga/1077#/buy-tickets-by-cinema?in-cinema=1077&at=2026-05-08&view-mode=list")
        self.out_var = StringVar(value="results_gui")

        self._queue: Queue[dict] = Queue()
        self._worker: threading.Thread | None = None
        self._last_json: Path | None = None
        self._last_csv: Path | None = None
        self._rows: list[str] = []
        self._items: list = []

        self._build_ui()
        self.after(150, self._poll_queue)

    def _build_ui(self) -> None:
        root = ttk.Frame(self, padding=12)
        root.pack(fill=BOTH, expand=True)

        form = ttk.LabelFrame(root, text="Wejście", padding=12)
        form.pack(fill="x")

        ttk.Label(form, text="Link do repertuaru").grid(row=0, column=0, sticky="w")
        url_entry = ttk.Entry(form, textvariable=self.url_var, width=110)
        url_entry.grid(row=1, column=0, columnspan=3, sticky="we", pady=(4, 10))

        ttk.Label(form, text="Nazwa pliku wyjściowego (prefix)").grid(row=2, column=0, sticky="w")
        out_entry = ttk.Entry(form, textvariable=self.out_var, width=40)
        out_entry.grid(row=3, column=0, sticky="w", pady=(4, 0))

        self.start_btn = ttk.Button(form, text="Start", command=self._start)
        self.start_btn.grid(row=3, column=1, padx=(10, 0), sticky="w")

        self.open_json_btn = ttk.Button(form, text="Otwórz JSON", command=self._open_json, state=DISABLED)
        self.open_json_btn.grid(row=3, column=2, padx=(10, 0), sticky="w")

        self.open_csv_btn = ttk.Button(form, text="Otwórz CSV", command=self._open_csv, state=DISABLED)
        self.open_csv_btn.grid(row=3, column=3, padx=(10, 0), sticky="w")

        form.columnconfigure(0, weight=1)

        results_box = ttk.LabelFrame(root, text="Wyniki (posortowane po ocenie)", padding=12)
        results_box.pack(fill=BOTH, expand=True, pady=(12, 0))

        cols = ("rating", "title", "showtimes", "filmweb")
        self.tree = ttk.Treeview(results_box, columns=cols, show="headings", height=11)
        self.tree.heading("rating", text="Ocena")
        self.tree.heading("title", text="Tytuł")
        self.tree.heading("showtimes", text="Godziny")
        self.tree.heading("filmweb", text="Filmweb")
        self.tree.column("rating", width=70, anchor="e", stretch=False)
        self.tree.column("title", width=320, anchor="w", stretch=True)
        self.tree.column("showtimes", width=170, anchor="w", stretch=False)
        self.tree.column("filmweb", width=260, anchor="w", stretch=True)

        yscroll = ttk.Scrollbar(results_box, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=yscroll.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        yscroll.grid(row=0, column=1, sticky="ns")
        filmweb_btns = ttk.Frame(results_box)
        filmweb_btns.grid(row=1, column=0, columnspan=2, sticky="w", pady=(8, 0))
        ttk.Button(filmweb_btns, text="Uwagi Filmweb / wybór kandydata", command=self._open_filmweb_dialog).pack(
            side="left"
        )
        results_box.columnconfigure(0, weight=1)
        results_box.rowconfigure(0, weight=1)

        log_box = ttk.LabelFrame(root, text="Log", padding=12)
        log_box.pack(fill="x", pady=(12, 0))
        self.log = ScrolledText(log_box, height=7)
        self.log.pack(fill="x", expand=False)
        self.log.insert(END, "Wklej link i kliknij Start.\n")
        self.log.configure(state=DISABLED)

        hint = ttk.Label(
            root,
            text="Tip: pierwsze uruchomienie może trwać dłużej (Playwright odpala Chromium).",
        )
        hint.pack(anchor="w", pady=(10, 0))

    def _append_log(self, msg: str) -> None:
        self.log.configure(state=NORMAL)
        self.log.insert(END, msg.rstrip() + "\n")
        self.log.see(END)
        self.log.configure(state=DISABLED)

    def _set_results(self, items) -> None:
        self._items = list(items)
        for iid in self._rows:
            try:
                self.tree.delete(iid)
            except Exception:
                pass
        self._rows = []

        for it in items:
            rating = "" if it.rating_filmweb is None else f"{it.rating_filmweb:.1f}"
            iid = self.tree.insert(
                "",
                "end",
                values=(
                    rating,
                    it.title,
                    ", ".join(it.showtimes),
                    it.filmweb_url or "",
                ),
            )
            self._rows.append(iid)

    def _open_filmweb_dialog(self) -> None:
        sel = self.tree.selection()
        if not sel:
            messagebox.showinfo("Filmweb", "Zaznacz wiersz w tabeli wyników.")
            return
        idx = self.tree.index(sel[0])
        if idx < 0 or idx >= len(self._items):
            messagebox.showerror("Filmweb", "Nie znaleziono danych dla zaznaczonego wiersza.")
            return
        it = self._items[idx]

        top = Toplevel(self)
        top.title(f"Filmweb — {it.title}")
        top.geometry("720x480")
        frm = ttk.Frame(top, padding=10)
        frm.pack(fill=BOTH, expand=True)

        ttk.Label(frm, text="Przyczyna / dopasowanie (feedback z skryptu):").pack(anchor="w")
        fb = ScrolledText(frm, height=8, wrap="word")
        fb.pack(fill="x", expand=False, pady=(4, 8))
        fb_text = (
            (it.filmweb_feedback or "").strip()
            or "Brak dodatkowego opisu (wysoka pewność dopasowania albo brak danych)."
        )
        if it.filmweb_url:
            fb_text = f"Aktualny link: {it.filmweb_url}\n\n{fb_text}"
        fb.insert(END, fb_text)
        fb.configure(state=DISABLED)

        ttk.Label(frm, text="Kandydaci z wyszukiwania (wybierz i otwórz lub zapisz do overrides.json):").pack(
            anchor="w", pady=(8, 0)
        )

        cand_urls: list[str] = []
        lb_frame = ttk.Frame(frm)
        lb_frame.pack(fill=BOTH, expand=True, pady=(4, 8))
        ylb = ttk.Scrollbar(lb_frame, orient="vertical")
        lb = Listbox(lb_frame, height=12, yscrollcommand=ylb.set)
        ylb.config(command=lb.yview)
        lb.pack(side="left", fill=BOTH, expand=True)
        ylb.pack(side="right", fill="y")

        if it.filmweb_candidates:
            for c in it.filmweb_candidates:
                url = str(c.get("url", ""))
                sc = c.get("score", "")
                label = str(c.get("label", ""))[:100]
                cand_urls.append(url)
                lb.insert(END, f"{sc}  |  {label}  →  {url}")
        else:
            lb.insert(END, "(brak listy — zobacz opis powyżej lub wklej link ręcznie poniżej)")

        url_var = StringVar(value=it.filmweb_url or "")

        ttk.Label(frm, text="URL do otwarcia / zapisu (edytuj, jeśli trzeba):").pack(anchor="w")
        ttk.Entry(frm, textvariable=url_var, width=92).pack(fill="x", pady=(2, 8))

        def open_url(u: str | None = None) -> None:
            s = (u or url_var.get()).strip()
            if not s:
                messagebox.showwarning("Filmweb", "Podaj lub wybierz adres URL.")
                return
            webbrowser.open(s)

        def sync_from_selection() -> None:
            sel_i = lb.curselection()
            if sel_i and cand_urls:
                i = sel_i[0]
                if 0 <= i < len(cand_urls):
                    url_var.set(cand_urls[i])

        def save_override() -> None:
            s = url_var.get().strip()
            if not s:
                messagebox.showwarning("Filmweb", "Podaj URL do zapisania.")
                return
            path = Path("overrides.json")
            data: dict = {}
            if path.exists():
                try:
                    raw = json.loads(path.read_text(encoding="utf-8"))
                    if isinstance(raw, dict):
                        data = raw
                except Exception:
                    messagebox.showerror("Filmweb", "Nie udało się odczytać overrides.json (uszkodzony JSON?).")
                    return
            title_key = str(it.title).strip()
            if not title_key:
                messagebox.showerror("Filmweb", "Pusty tytuł wiersza — nie mogę zapisać mapowania.")
                return
            data[title_key] = s
            try:
                path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            except Exception as exc:
                messagebox.showerror("Filmweb", f"Zapis nie powiódł się: {exc}")
                return
            messagebox.showinfo("Filmweb", f"Zapisano w {path.resolve()}\n\nUruchom ponownie Start, by użyć nowego linku.")

        lb.bind("<Double-Button-1>", lambda _e: sync_from_selection())

        btn_row = ttk.Frame(frm)
        btn_row.pack(fill="x")
        ttk.Button(btn_row, text="Użyj zaznaczonego kandydata", command=sync_from_selection).pack(side="left", padx=(0, 8))
        ttk.Button(btn_row, text="Otwórz URL w przeglądarce", command=lambda: open_url()).pack(side="left", padx=(0, 8))
        ttk.Button(btn_row, text="Zapisz do overrides.json", command=save_override).pack(side="left")

    def _poll_queue(self) -> None:
        try:
            while True:
                ev = self._queue.get_nowait()
                t = ev.get("type")
                if t == "log":
                    self._append_log(ev.get("msg", ""))
                elif t == "done":
                    self._last_json = ev.get("json")
                    self._last_csv = ev.get("csv")
                    self._set_results(ev.get("items") or [])
                    self.open_json_btn.configure(state=NORMAL if self._last_json else DISABLED)
                    self.open_csv_btn.configure(state=NORMAL if self._last_csv else DISABLED)
                    self._set_running(False)
                    self._append_log("Gotowe.")
                elif t == "error":
                    self._set_running(False)
                    msg = ev.get("msg") or "Nieznany błąd."
                    self._append_log(f"Błąd: {msg}")
                    messagebox.showerror("Błąd", msg)
        except Empty:
            pass
        self.after(150, self._poll_queue)

    def _set_running(self, running: bool) -> None:
        self.start_btn.configure(state=DISABLED if running else NORMAL)

    def _start(self) -> None:
        if self._worker and self._worker.is_alive():
            return

        url = self.url_var.get().strip()
        if not url:
            messagebox.showerror("Błąd", "Wklej link do repertuaru.")
            return

        out_prefix = Path(self.out_var.get().strip() or "results_gui")
        self._last_json = None
        self._last_csv = None
        self.open_json_btn.configure(state=DISABLED)
        self.open_csv_btn.configure(state=DISABLED)
        self._set_results([])

        self._append_log("=== START ===")
        self._set_running(True)

        def progress_cb(m: str) -> None:
            self._queue.put({"type": "log", "msg": m})

        def worker() -> None:
            try:
                items, json_path, csv_path = run_scrape(url=url, out_prefix=out_prefix, progress_cb=progress_cb)
                self._queue.put({"type": "done", "items": items, "json": json_path, "csv": csv_path})
            except Exception as e:
                self._queue.put({"type": "error", "msg": str(e)})

        self._worker = threading.Thread(target=worker, daemon=True)
        self._worker.start()

    def _open_json(self) -> None:
        if self._last_json and self._last_json.exists():
            webbrowser.open(self._last_json.resolve().as_uri())

    def _open_csv(self) -> None:
        if self._last_csv and self._last_csv.exists():
            webbrowser.open(self._last_csv.resolve().as_uri())


if __name__ == "__main__":
    App().mainloop()

