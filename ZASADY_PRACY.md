# ZASADY PRACY — wspólne dla wszystkich projektów

Wersja 1.3 (2026-09-11). Jeden plik, wrzucany do każdego repozytorium. Claude czyta go przez
`@ZASADY_PRACY.md` w `CLAUDE.md`. Projekt zbiorczy (integrujący widok na wszystkie repozytoria)
polega na **sekcji 9** — stałych ścieżkach i nazwach, po których da się czytać każde repo tak samo.

Każda reguła ma jedno zdanie **dlaczego**. Reguła bez „dlaczego" jest przesądem i nie da się jej
poprawić, gdy przestanie pasować.

---

## 0. Jak używać

- W `CLAUDE.md` projektu: linia `@ZASADY_PRACY.md`. Reguły specyficzne dla projektu idą **pod** nią
  i mogą zawęzić, ale nie unieważnić, reguły stąd.
- Ten plik jest **taki sam w każdym repo**. Zmiana reguły = zmiana we wszystkich (patrz sekcja 10).
- Język: notatki, OpenSpec i ten plik po polsku; **kod, komentarze w kodzie i komunikaty
  commitów w ASCII** (sekcja 4.1).

---

## 1. Sesja i notatki — OBOWIĄZKOWE, bez wyjątków

**1.1 Każda sesja kończy się notatką.** Przed commitem Claude:
1. aktualizuje sekcję `## Ostatnia sesja` w `notes/start.md` (data, co zrobiono, aktywne TODO),
2. tworzy lub uzupełnia `notes/sesje/RRRR-MM-DD-sesja.md` wg szablonu:

```
# Sesja RRRR-MM-DD
## Co zrobiono
## Kluczowe decyzje / zmiany semantyki
## Aktywne TODO / pending
## Pliki zmienione        (plik: co i dlaczego)
```

*Dlaczego:* praca idzie na dwóch maszynach i w wielu sesjach; bez notatki następna sesja zaczyna
od archeologii. Użytkownik **nigdy nie musi o tym przypominać** — brak notatki to błąd Claude'a.

**1.2 Notatka zapisuje liczby, nie przymiotniki.** „Poprawiło się" nie jest wynikiem;
„95,1% → 94,8%, optymizm 0,4 pkt proc." jest.

**1.3 Notatka zapisuje własne błędy Claude'a** z tej sesji i co je wyłapało. *Dlaczego:* błąd,
którego nie zapisano, wraca.

**1.4 Pliki „handoff"** (`notes/HANDOFF_*.md`) — jeden punkt wejścia do tematu: co działa, czego
wymaga, jak odtworzyć, znane pułapki. Aktualizowany, gdy zmienia się coś, co druga maszyna musi
wiedzieć.

**1.5 Nazwa zadania na starcie.** Gdy zadanie zapowiada się na dłużej niż minutę (testy, build, wiele
plików, rollout, pomiar), Claude **na początku** nadaje mu nazwę jednym wywołaniem:
`python monitor/heartbeat.py --event label --label "Parser tasks.md: kontynuacje"` — rzeczownik + obiekt,
do 40 znaków, bez ścieżek; to tekst, który wyląduje na ekranie blokady telefonu. Odpowiedzi poniżej minuty
(rozmowa, nauka języka) nazwy nie potrzebują. *Dlaczego:* monitor liczy czas zadania od promptu do stopu
i powiadamia dopiero powyżej progu; bez nazwy powiadomienie pokazuje pierwsze słowa promptu, które rzadko
mówią, co się właściwie skończyło.

---

## 2. Git i dwie maszyny

**2.1 Claude nie pushuje bez prośby.** Ale gdy użytkownik mówi „kończymy / koniec / rób commit /
wypchnij / przenoszę się na drugą maszynę", Claude sprawdza `git status -sb` i **albo pushuje na
prośbę, albo mówi wprost: „commit jest lokalny, nie wypchnięty"**. *Dlaczego:* lokalny commit jest
niewidoczny z drugiej maszyny; ciche pominięcie kosztuje pół dnia.

**2.2 Weryfikacja pusha to trzy niezależne rzeczy**, nie „ok" z terminala: brak `[ahead N]`,
`git rev-parse HEAD` = `origin/<branch>`, i **`git ls-remote origin <branch>`** (pytanie do
zdalnego repo, nie do lokalnej kopii referencji).

**2.3 Odrzucony push (non-fast-forward) = ktoś pchnął z drugiej maszyny.** Najpierw `fetch`
i obejrzeć, **co** tam jest — to może być zadanie zostawione dla tej maszyny. Potem
`pull --rebase` (autostash). **Nigdy `--force`.**

**2.4 Konflikt na plikach ustawień lokalnych** (listy uprawnień, cache) rozwiązuje się **sumą**
obu stron, nie wyborem. *Dlaczego:* to nie treść projektu, tylko dwie maszyny, które widziały
różne komendy.

**2.5 Artefakty pomiarowe i logi NIE są commitowane** (`project_files/run_files/` i podobne).
*Dlaczego:* zasypałyby historię i rozdęły klon. Konsekwencja: druga maszyna ma kod i zero
pomiarów — stąd 2.6.

**2.6 Paczka przenoszenia.** Skrypt w repo (`*_transfer_package.py`) buduje zip z tym, co nie
jest w repo, z **manifestem SHA-256 każdego pliku, commitem repo, README z krokami w kolejności
i otwartymi zadaniami czytanymi z OpenSpec**, oraz trybem `--verify`. Kolejne uruchomienie
porównuje się z poprzednim manifestem i wypisuje **nowe / zmienione / usunięte** — odpowiedź na
„daj aktualną paczkę" jest pomiarem, nie zgadywaniem. Zmierz kompresowalność **przed** wyborem
nośnika: tablice etykiet kompresują się setki razy, wagi sieci wcale.

**2.7 Zero usunięć przed pushem z automatu.** Każdy skrypt lub pętla, która klonuje, commituje i pushuje
(rollout do wielu repo, synchronizacja plików), przed `push` sprawdza trzy rzeczy i przerywa przy pierwszej
niezgodnej: (1) kod wyjścia `clone` czytany wprost, **bez `| tail`/`| head`**, które go połykają;
(2) `git status --short` puste po klonie (na Windows klon z `-c core.longpaths=true`); (3) po commicie
`git diff --name-status <baza> HEAD` zawiera **zero wierszy `D`** i tylko oczekiwane pliki. Tożsamość
committera podana jawnie (`-c user.email -c user.name`), inaczej `commit`/`revert` padają cicho z kodem 128.
*Dlaczego:* 2026-09-10 płytki klon padł na checkout przez długie ścieżki, `tail` zjadł kod błędu,
`git add <pliki>` + `commit` zapisał drzewo z częściowego indeksu i push usunął 43 / 2587 / 5009 plików
w trzech repo na gałęzi domyślnej. Naprawa revertem bez `--force`; wyłapały to dopiero statystyki commita
(„+6 plików" nie pasowało do 304 989 usunięć).

---

## 3. Specyfikacja i planowanie

**3.1 OpenSpec.** Zmiany żyją w `openspec/changes/<nazwa>/` (`proposal.md`, `design.md`,
`tasks.md`). Każdy osobny temat = osobna zmiana (np. przenoszenie między maszynami nie siedzi
w zmianie o metodzie).

**3.2 `openspec/STATUS.md` jest generowany, nigdy edytowany ręcznie.** Po każdej zmianie
jakiegokolwiek `tasks.md` przed commitem: `python notes/gen_openspec_status.py`.

**3.3 PREREJESTRACJA przed każdą analizą, która ma się skończyć zdaniem „X przewiduje Y".**
Wpis w `tasks.md` **przed przebiegiem**, z polami: hipoteza, dane i wykluczenia, definicja
etykiety, jedna statystyka, **liczba testowanych porównań**, poprawka na wielokrotność, konfuzje
do zmierzenia, **warunek NEGATYWU** (przy którym kończę i nie szukam dalej), **czego wynik NIE
uprawnia**. Szablon: `notes/publikacja/PREREJESTRACJA_szablon.md`.
*Dlaczego:* po zobaczeniu liczb nie da się już uczciwie zdecydować, ile testów „się właściwie
zrobiło". Nie dotyczy pomiarów opisowych i odtwarzania wcześniejszych liczb.

**3.4 Plan „co mamy / czego brakuje / jakimi metodami"** w jednym pliku, aktualizowany przy
cofaniu się. Opiera się na tym, co **leży na dysku zmierzone**, nie na zamiarach.

---

## 4. Kod

**4.1 Tylko ASCII w źródłach kompilowanych i w komentarzach kodu.** *Dlaczego:* MSVC z kodową
stroną systemową (CP1252/1250) wywala `C2065` na literałach z polskimi znakami; w Pythonie
konsola Windows (cp1250) potrafi wywalić skrypt **po** zapisaniu plików — raporty pisać w UTF-8,
wyjście kierować do pliku.

**4.2 Długie łatki plików piszemy narzędziem Write/Edit, nie heredoc w bashu.** *Dlaczego:*
heredoc z apostrofami, `\n` i cudzysłowami wielokrotnie zostawiał plik w stanie **mieszanym**
(część zmian weszła, część nie) i kosztował godziny. Bash do komend; do treści — narzędzie.

**4.3 Każdy `s.replace(old, new)` w skrypcie łatającym ma `assert old in s`.** *Dlaczego:*
`replace` bez dopasowania **cicho nic nie robi**; jeden taki blok CSS przeżył kilka rund oceny
wizualnej jako „złe rozmiary czcionek".

**4.4 Testy nazywają porażkę, której zapobiegają**, i są **sprawdzone mutacją** (wprowadź błąd
z powrotem — test ma paść). *Dlaczego:* zły percentyl i odwrócony kierunek nierówności nie były
widoczne przy czytaniu kodu; pięciolinijkowy test na danych syntetycznych łapie oba. Uruchamiać
przed każdym raportowaniem liczb.

**4.5 Logika decyzyjna w małych funkcjach czystych, nie w pętli raportu.** *Dlaczego:* zły znak
w trzylinijkowej funkcji łapie jedna asercja; ten sam znak zakopany w `main()` nie łapie nikt.

**4.6 Skrypty wsadowe drukują postęp** (co N elementów: `[i/N]`, opcjonalnie ETA), nie tylko
podsumowanie. *Dlaczego:* bez tego nie da się odróżnić „liczy" od „zawisło".

**4.7 Algorytmy nietrywialne mają własny opis w MD**, aktualizowany razem z algorytmem.

**4.8 Projekty Qt: zawsze wersje serializowane widgetów** (`QComboBox2`, `QLineEdit2`,
`QPushButton2`), bo zwykłe gubią stan między sesjami; zdarzenia ciągłe (drag, scrub) zapisują
konfigurację przez **debounce** (dedykowany single-shot `QTimer`, ~300 ms), nie w handlerze.

**4.9 Bez zmian semantyki, o które nikt nie prosił.** Diagnostyka i refaktor zachowują
zachowanie. Propozycję zmiany najpierw w tekście. Dotyczy zwłaszcza kalibracji, mapowań,
progów, orientacji.

---

## 5. Nauka i pomiar

**5.1 Kalibruj na populacji, do której stosujesz.** Próg wycięty na jednej populacji (prawda
podstawowa, pojedyncze komponenty, inna skala oceny, inny aparat) przyłożony do innej jest
błędem — ten sam błąd popełniono **siedem razy pod siedmioma postaciami**, zanim stał się regułą.

**5.2 Mierz, nie oceniaj okiem** — układ figury (kolizje prostokątów etykiet, kadr, piksele na
jednostkę viewBoksu), zgodność progu z zapisanym, czy LOO w ogóle coś zmienił (oceny mają się
różnić liczbowo). *Dlaczego:* oko widzi objaw, pomiar wskazuje przyczynę; „za duże czcionki"
było brakującą regułą CSS.

**5.3 Kontrola odtworzenia przed każdą walidacją z pominięciem.** Przecięcie progu **bez
wytrzymywania niczego musi odtworzyć wartość zapisaną**, inaczej skrypt **odmawia uruchomienia**
— mierzyłby niezgodność populacji, nie optymizm metody. Po przebiegu sprawdź, że każdy próg
**drgnął**. *Dlaczego:* LOO, które cicho wraca do progu z całości, raportuje zerowy optymizm,
czyli dokładnie to, co chcielibyśmy zobaczyć.

**5.4 Wielokrotność testów jako OBLICZENIE, nie komentarz.** Przy k testowanych statystykach
i małej próbie policz szansę, że **którakolwiek** rozdzieli przez szum; przy 3 dobrych na 9
i 8 statystykach to 17,5%. Po przekroczeniu zadeklarowanej liczby testów — **przestań szukać**;
dalsze szukanie jest procedurą produkującą wynik pozorny.

**5.5 Konfuzje mierzone, nie przedyskutowane** — wiek, rozmiar, aparat. Konfuzja odrzucona
tylko, gdy sama nie przewiduje wyniku **i** efekt trzyma się wewnątrz warstw.

**5.6 Przedziały predykcyjne, nie ufności**, gdy pytanie brzmi „czego oczekiwać u nowego
przypadku". Sprawdzaj **pokrycie** przedziału w LOO — to test samego przedziału, nie punktu.

**5.7 Gwarancja zamiast percentyla, gdzie się da.** Cięcie na 1. percentylu przy n=86 obiecuje
stopę, której nie da się zagwarantować (najmniejsza gwarantowalna α to 1/(n+1)). Raportuj, co
próg **naprawdę** gwarantuje: jeśli m z n ocen leży poniżej, P(odrzucony) ≤ (m+1)/(n+1). Uważaj
na **kierunek**: próg ostrzejszy zachowuje mniej, więc nie dziedziczy gwarancji luźniejszego.
Wymienność: jeden przypadek = jedna ocena, gdy przypadek wnosi kilka skorelowanych.

**5.8 Wynik negatywny zapisuje się jako negatywny**, z przyczyną. Zapisuje się też próby
naprawy, które zawiodły, i **w którą stronę**. *Dlaczego:* negatyw ze zmierzoną przyczyną jest
wynikiem; negatyw zamieciony wraca jako ta sama ślepa uliczka.

**5.9 Korekty własnych twierdzeń wprost i krótko.** „Przeszacowałem X, bo Y" — bez tłumaczenia
się i bez ukrywania w przypisie. Twierdzenie, które okazało się przesadzone, zostaje poprawione
w tej samej sesji, w której to wyszło.

**5.10 Metodologia nie rozrasta się — jest uzupełniana o rzeczy, które coś wnoszą.** Jedna
zadeklarowana reguła zamiast tuningu per przypadek (tuning po wyniku na korpusie to dopasowanie
do korpusu). Każda dołożona cecha musi pokazać zysk **wobec bazy**, nie sama w sobie.

**5.11 Klasyczne przetwarzanie przed retreningiem.** Sieci robią to, w czym są dobre; kontrola
i uzupełnienie idą klasyką (symetria, morfologia, priory geometryczne). Retrening tylko
z jawnej decyzji, gdy klasyka zawiedzie.

**5.12 Metoda pół-automatyczna: liczbą wynikową jest czułość flagi**, nie procent poprawnych.
Automatyzuj maksymalnie, ale użytkownik zawsze ma akceptację lub wybór wariantu — zróżnicowanie
anatomiczne i sprzętowe nie pozwala gwarantować pełnej automatyki. Raportuj **parę**: czystość
zbioru akceptowanego i koszt przeglądu. Pamiętaj, że procent poprawnych **nagradza
nieprzypisywanie najsłabszej roli wcale**.

**5.13 Mała próba to ograniczenie, nie wyzwanie.** Gdy każda ślepa uliczka kończy się na liczbie
przypadków, odpowiedzią są dane (anotacja), nie kolejna cecha.

**5.14 Uczciwość wobec recenzenta.** „Nikt nie każe ściemniać — to uczciwy research — ale musi
być sensowne, żeby recenzenci nie podważyli nic." Każde twierdzenie w figurze i tekście ma pomiar
za sobą; jawne granice („bez kontroli A+A' nie da się rozdzielić…") są częścią wyniku.

---

## 6. Prowenienecja

**6.1 Każdy raport ma nagłówek pochodzenia**: commit i branch, czystość drzewa, dokładna
komenda, **skrót SHA-256 każdego pliku wejściowego**. Moduł `*_provenance.py`, funkcja
`provenance_block(argv, inputs)`. *Dlaczego:* **ścieżka nie jest tożsamością** — ten sam plik
znaczył dwie różne rzeczy w jednej sesji; pytanie „która kalibracja dała tę liczbę" zajęło
kilka obrotów.

**6.2 Nie mieszaj konfiguracji.** Liczba zmierzona na konfiguracji A nie jest liczbą
konfiguracji B (LOO opublikowanej ≠ LOO hybrydy). Przy zmianie konfiguracji **przelicz**.

**6.3 Cytowania zweryfikowane** (tytuł, autorzy, czasopismo, rok, DOI) przed wpisaniem;
niezweryfikowane oznaczone „DO WERYFIKACJI", nigdy podane z pamięci jako pewne.

---

## 7. Komunikacja

**7.1 Logi czyta Claude, nie kopiuje użytkownik.** Jeśli plik jest w workspace, Claude go czyta.
Prośba o fragment tylko, gdy plik jest niedostępny — i o najmniejszy możliwy.

**7.2 Odpowiedź na pytanie strategiczne = rekomendacja z uzasadnieniem**, nie katalog opcji.
Kiedy użytkownik podjął decyzję, nie relitygować jej.

**7.3 Raportuj wiernie.** Testy padły — powiedz z wyjściem. Krok pominięty — powiedz. Gotowe
i zweryfikowane — powiedz bez asekuracji.

**7.4 Gdy użytkownik mówi „mieszasz"** — to zwykle prawda; zapisz w notatce, co konkretnie
i jaka reguła z tego wynika (sekcja 4.2 i 4.3 stąd powstały).

---

## 8. RTK (token-optimised CLI)

Prefiks `rtk` przed komendami dev (`rtk git …`, `rtk pytest`, `rtk grep …`) — hook przepisuje
automatycznie; w łańcuchach `&&` też. Gdy filtr RTK gubi treść (np. nazwy funkcji z `grep`),
użyj `rtk proxy <cmd>` albo pythona do wypisania. `rtk gain` — statystyki oszczędności.

---

## 9. STANDARDOWY UKŁAD REPO — na tym polega projekt zbiorczy

Każde repo ma te same punkty wejścia, czytane mechanicznie:

| ścieżka | co tam jest | kto czyta |
|---|---|---|
| `CLAUDE.md` | `@ZASADY_PRACY.md` + reguły projektu | Claude |
| `ZASADY_PRACY.md` | ten plik, identyczny wszędzie | Claude, meta-projekt (wersja w nagłówku) |
| `notes/start.md` | sekcja `## Ostatnia sesja — DATA (sesja N)` na górze | człowiek, meta-projekt (stan projektu) |
| `notes/sesje/RRRR-MM-DD-*.md` | notatki sesji wg szablonu 1.1 | człowiek, meta-projekt (oś czasu) |
| `notes/HANDOFF_*.md` | punkty wejścia do tematów | druga maszyna |
| `notes/publikacja/` | materiały pod publikację, plan „co mamy / co chcemy", szablon prerejestracji | człowiek |
| `openspec/changes/*/tasks.md` | zadania `- [ ]` / `- [x]`, wpisy PREREJESTRACJA | meta-projekt (otwarte zadania) |
| `openspec/STATUS.md` | **generowany** `notes/gen_openspec_status.py` | meta-projekt (postęp %) |
| `.claude/settings.json` | hooki Claude Code (`SessionStart`, `UserPromptSubmit`, `PostToolUse`, `Stop`, `SessionEnd`) wywołujące heartbeat; **scalane sumą** z ustawieniami projektu | Claude Code |
| `monitor/heartbeat.py`, `monitor/taskparse.py` | heartbeat do serwera monitora (tylko stdlib, ASCII; adres i token w `~/.claude/monitor.env`, nigdy w repo); `--event label --label "..."` nadaje nazwę bieżącemu zadaniu (reguła 1.5); parser `tasks.md` wspólny z `gen_openspec_status.py` | meta-projekt (stan „pracuje / czeka / skończył" na żywo, czas i nazwa zadania) |
| `project_files/python/` (lub `src/`) | kod analityczny | — |
| `project_files/python/tests/` | testy nazywające porażki | CI / meta-projekt (`pytest -q`) |
| `project_files/run_files/` | artefakty i logi, **nie w repo** | paczka przenoszenia |
| `*_provenance.py`, `*_transfer_package.py` | prowenienecja, paczka | — |

Meta-projekt może więc dla każdego repo odczytać: **stan** (`start.md`), **oś czasu**
(`notes/sesje/`), **otwarte zadania i postęp** (`openspec/`), **zdrowie kodu** (`pytest`),
**wersję reguł** (nagłówek tego pliku) — bez znajomości domeny projektu.

Paczkę instaluje `tools/install_pack.py` z repo `project_integration` (idempotentnie, nic nie usuwa);
ten plik synchronizuje `tools/rules_sync.py` (kanon w `project_integration/ZASADY_PRACY.md`).
Wzbogacenie reguł w jednym repo → `rules_sync.py --pull <repo>` → scalenie ręczne do kanonu → wpis w sekcji 10
→ `rules_sync.py --push`.

---

## 10. Zmiany tego pliku

Reguła dochodzi, gdy **coś poszło źle i wiemy dlaczego**, albo gdy użytkownik podjął decyzję
projektową obowiązującą dalej. Wpis w tabeli poniżej + podbicie wersji w nagłówku + wgranie do
wszystkich repo.

| wersja | data | co i skąd |
|---|---|---|
| 1.3 | 2026-09-11 | Reguła 1.5 „nazwa zadania na starcie" (`heartbeat.py --event label`); wiersz sekcji 9 o etykiecie i stanie „czeka"; hooki w `.claude/settings.json` zakotwiczone w `$CLAUDE_PROJECT_DIR` i rozszerzone o `StopFailure` i `Notification`. *Skąd:* zmiana OpenSpec `task-timer-panel` w `project_integration` — monitor liczy czas zadania (prompt → stop) i powiadamia tylko powyżej progu (60 s), więc potrzebuje lakonicznej nazwy w chwili startu; hook z względną ścieżką padł, gdy `cd` narzędzia Bash zmieniło katalog roboczy sesji. |
| 1.2 | 2026-09-10 | Reguła 2.7 „zero usunięć przed pushem z automatu" (kod wyjścia klonu bez potoku, czysty status, zero `D` w diffie, jawna tożsamość committera). *Skąd:* incydent rollout'u paczki w `project_integration` — płytki klon + połknięty kod błędu = usunięte drzewa w 3 repo, naprawione revertem. |
| 1.1 | 2026-09-10 | Meta-projekt `project_integration` (zmiana OpenSpec `project-monitor`): sekcja 9 zyskuje `.claude/settings.json` (hooki heartbeat) i `monitor/heartbeat.py` + `taskparse.py`; opis instalacji paczki i synchronizacji tego pliku. *Dlaczego:* bez wspólnych hooków nie ma sygnału „pracuje teraz", a bez jednego parsera `tasks.md` liczba w STATUS.md i na telefonie rozjeżdżają się. |
| 1.0 | 2026-09-10 | Konsolidacja: CLAUDE.md projektu, reguły `.cursor/rules`, pamięć feedback, wnioski sesji 58–61 (7× błąd kalibracji na innej populacji, heredoc, `assert` przy `replace`, prerejestracja, wielokrotność, prowenienecja, gwarancje konformalne, pół-automat, paczka przenoszenia) |
