from __future__ import annotations

import threading
import webbrowser
from pathlib import Path
from queue import Queue, Empty
from tkinter import END, BOTH, DISABLED, NORMAL, StringVar, Tk, ttk
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

