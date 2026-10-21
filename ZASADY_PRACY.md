# ZASADY PRACY — wspólne dla wszystkich projektów

Wersja 1.27 (2026-10-21). Jeden plik, wrzucany do każdego repozytorium. Claude czyta go przez
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
## W trakcie (checkpoint)  -- nadpisywany przy każdym podzadaniu; usuwany na koniec sesji
```

*Dlaczego:* praca idzie na dwóch maszynach i w wielu sesjach; bez notatki następna sesja zaczyna
od archeologii. Użytkownik **nigdy nie musi o tym przypominać** — brak notatki to błąd Claude'a.

**1.6 Checkpoint po każdym ukończonym podzadaniu.** Gdy Claude kończy jedno podzadanie i przechodzi
do następnego (np. skończyłem implementację, zaczynam testy; skończyłem testy, zaczynam notatki),
nadpisuje sekcję `## W trakcie (checkpoint)` w pliku `notes/sesje/RRRR-MM-DD-sesja.md`:

```
## W trakcie (checkpoint)
Zadanie: <co teraz>
Stan: <co jest gotowe, co nie>
Następny krok: <jedno zdanie>
```

Sekcja jest **nadpisywana**, nie doklejana. Znika z notatki na koniec sesji (zastępuje ją `## Co zrobiono`).
*Dlaczego:* kompaktowanie kontekstu generuje streszczenie z bieżącego stanu rozmowy — ale gdy rozmowa
jest długa i kompaktowanie nastąpi w środku zadania, checkpoint w pliku jest jedynym zapisem
aktualnego stanu, który nowa sesja może przeczytać bez rekonstrukcji. Reguła 1.1 (notatka na koniec)
chroni między sesjami; 1.6 chroni wewnątrz sesji.

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

**2.1 Claude nie pushuje bez prośby** (poza 2.8–2.12). Ale gdy użytkownik mówi „kończymy / koniec / rób commit /
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

**2.8 Synchronizacja na koniec sesji jest automatyczna, ale bramkowana.** Hook `SessionEnd` uruchamia
`monitor/auto_sync.py --spawn` (proces odlaczony, hook wraca natychmiast), ktory commituje i pushuje **tylko
gdy wszystkie bramki trzymaja**: (G1) istnieje notatka `notes/sesje/<dzis>-*.md` zmieniona w drzewie albo
w ostatnim commicie; (G2) żadna **nowa** ścieżka źródłowa nie zostaje poza commitem w katalogu, do którego commitujemy źródła (inaczej idzie połowa zmiany i drzewo się nie uruchamia); każda zmieniona sciezka lezy w allowliscie (`notes/`, `openspec/`,
`project_files/python/`, `monitor/`, `.claude/`, `.cursor/`, `CLAUDE.md`, `ZASADY_PRACY.md`) — artefakty,
logi i pliki nieznane zatrzymuja commit; (G3) `git diff --name-status <upstream>...HEAD` ma zero wierszy `D`;
(G4) `pytest -q` zielony; (G5) po `git fetch` upstream jest przodkiem HEAD; **gdy nie jest, automat robi
`git pull --rebase --autostash` i próbuje pushu ponownie** — rebase musi zastosować się czysto,
a po nim **G3 i G4 są sprawdzane od nowa**, na nowym drzewie. Konflikt = `rebase --abort`,
commit zostaje lokalny i raport mówi, które pliki rozjeżdżają się. **Nigdy `--force`.**
Per repo można to wyłączyć: `{"rebase": false}` w `.claude/auto_sync.json`.

Kazdy przebieg dopisuje blok do
`project_files/run_files/auto_sync.log` i wysyla etykiete heartbeat „auto-sync: pushed | local | skipped
(powod)", zeby wynik byl widoczny na telefonie. Testy bramek: `project_files/python/tests/test_auto_sync.py`.
*Dlaczego:* reguła 2.1 chronila przed cichym pushem, ale w praktyce co druga sesja konczyla sie commitem
lokalnym niewidocznym z drugiej maszyny; automat z bramkami daje widocznosc bez ryzyka z 2.7 (decyzja
uzytkownika 2026-09-14, sesja 65 RibnXtr2026). Rebase z automatu doszło 2026-09-16 (decyzja użytkownika: „nie ma sensu trzymać commitów niewypchniętych”) — to jest dokładnie ruch, który przepisuje reguła 2.3, a jedyną część wymagającą człowieka („obejrzeć, co tam jest”) automat sprawdza mechanicznie: czysty rebase, zero usunięć, zielone testy.
2.1 nadal obowiazuje **w trakcie** sesji: Claude nie pushuje
w srodku pracy na wlasna reke — pushuje ten sam automat, po zamknieciu sesji (tu) albo po zadaniu
dluzszym niz 10 minut (2.9); na KONIEC zadania pushuje tez sam Claude albo agent (2.10, 2.11), po tych
samych bramkach.

**2.9 Zadanie dłuższe niż 10 minut kończy się commitem i pushem — automatycznie.**
Hook `Stop` mierzy czas zadania na tej maszynie (`monitor/heartbeat.py`, zegar zadania: prompt
otwiera, prompt w trakcie tury nie restartuje, `stop` zamyka) i przy `>= MONITOR_AUTOSYNC_MIN_S`
(domyślnie **600 s**, `0` wyłącza) odpala `monitor/auto_sync.py` — ten sam automat i te same
bramki **G1–G5** co w 2.8 (notatka sesji z dziś, allowlista ścieżek, zero usunięć, `pytest`,
upstream przodkiem HEAD). Wynik (`pushed` / `local` / `skipped (powód)`) idzie do
`project_files/run_files/auto_sync.log` i jako etykieta heartbeat na telefon.
Obowiązek Claude'a jest jeden i wynika z bramki G1: **zanim zadanie się skończy, notatka sesji
ma być aktualna** (reguła 1.1) — bez niej automat świadomie nie commituje, więc praca zostaje
na jednej maszynie. Gdy Claude widzi, że zadanie dobiega końca po dłuższej pracy, uzupełnia
notatkę i mówi wprost, że push pójdzie sam.
*Dlaczego:* decyzja użytkownika 2026-09-15 — im dłuższe zadanie, tym większa szansa, że nie ma go
przy komputerze, a wynik jest mu potrzebny na repo, z drugiej maszyny albo z innego projektu.
Reguła 2.1 (nie pushuj bez prośby) chroniła przed cichym pushem **decyzji**; tu decyzja jest
podjęta z góry, a bramki pilnują ryzyka z 2.7.


**2.10 Praca skonczona jest pracą wypchniętą — Claude pushuje sam.** Gdy zadanie
dobiega końca (użytkownik mówi „kończymy", pyta o podsumowanie albo temat jest
zamknięty), Claude **nie czeka na prośbę o push**: uzupełnia notatkę sesji,
commituje i pushuje sam, po tych samych bramkach co automat z 2.8 — notatka sesji
z dziś, allowlista ścieżek, zero wierszy `D` w `git diff --name-status`, zielony
`pytest -q`, upstream przodkiem HEAD (a gdy nie jest: `pull --rebase --autostash`
i bramki od nowa). Weryfikacja jak w 2.2: brak `[ahead N]`, `git rev-parse HEAD`
= `origin/<branch>`, `git ls-remote origin <branch>`. **Nigdy `--force`.**
Gdy któraś bramka nie trzyma, commit zostaje lokalny, a Claude **mówi wprost,
czego brakuje** — cisza jest zakazana. W trakcie zadania 2.1 nadal obowiązuje:
Claude nie pushuje w środku pracy, tylko na jej koniec.
*Dlaczego:* decyzja użytkownika 2026-09-20 („wypychaj takie rzeczy sam
automatycznie"). Automaty z 2.8 i 2.9 łapią sesję i długie zadanie, ale krótkie
zadanie zamknięte w rozmowie zostawiało commit lokalny — czyli dokładnie ten
stan, przed którym chroni 2.1, tyle że odwrotnie: niewidoczny z drugiej maszyny.

**2.11 Praca samodzielna agenta kończy się commitem i pushem — bezwzględnie.**
Każdy agent działający w trybie autonomous (pętla profesor-student, Agent worktree,
orchestrator, remote tasks, dowolna sesja Agent uruchomiona w tle) **na koniec
pracy** — naturalny koniec zadania, STOP od użytkownika, osiągnięta godzina końca
pętli — commituje i pushuje przez te same bramki G1–G5 co automat z 2.8: notatka
sesji z dziś, allowlista ścieżek, zero wierszy `D` w `git diff --name-status`,
zielony `pytest -q`, upstream przodkiem HEAD (gdy nie jest: `pull --rebase
--autostash` i bramki od nowa). Weryfikacja jak w 2.2. **Nigdy `--force`.**
Gdy którakolwiek bramka nie trzyma — commit zostaje lokalny, agent raportuje
**wprost, czego brakuje** — cisza jest zakazana. W trakcie pracy 2.1 nadal
obowiązuje: agent nie pushuje w środku zadania, tylko na jego koniec.
*Dlaczego:* decyzja użytkownika 2026-10-10 — „jak pracują samodzielnie to na
koniec muszą wypychać zmiany, bo druga maszyna musi móc zawsze pobrać najświeższe
repo". Reguły 2.8 i 2.9 łapią odpowiednio koniec sesji i zadanie >= 10 min, a 2.10
łapie Claude kończącego zadanie w rozmowie; żadna z nich nie opisuje jawnie
agenta autonomous, który może skończyć pracę poza rozmową i bez hooka sesji.

**2.12 `start` odtwarza na maszynie wszystkie repozytoria — także nowe.** `tools/ready.py --apply`
(skill `start`) sprawdza i naprawia trzy rzeczy, nie tylko repo zbiorcze: (1) **nowe repo** —
repo właściciela na GitHubie z `ZASADY_PRACY.md` w korzeniu, którego nie ma w `tools/repos.txt`,
zostaje zarejestrowane (`new_project.register`: lista, mapa repo, paczka, przełącznik wykonywania,
aliasy głosowe, dodatek HA, panele), a commit **samych zmienionych plików huba** idzie od razu
na origin; (2) **brak klonu** — każdy projekt z listy bez klonu na tej maszynie jest klonowany obok
huba (z kodem wyjścia czytanym wprost i czystym statusem po klonie, jak w 2.7; powyżej 2 GB tylko
raport); (3) **klon za origin** — `start` **zawsze robi pull**: czyste drzewo dostaje
`pull --ff-only`, drzewo ze zmianami lub z lokalnymi commitami `pull --rebase --autostash`;
rebase, który się nie stosuje, jest cofany (`rebase --abort`), nigdy `--force` (2.3), a klon zostaje
człowiekowi z opisem. Nieśledzony plik identyczny bajtowo z wersją z origin, który blokuje
fast-forward, jest usuwany. *Decyzja użytkownika 2026-10-17:* RibnXtr2026 został 7 commitów za origin,
bo jeden śledzony plik pomiarowy był zmieniony. Kto zakłada
repo ręcznie, a nie przez `new_project.py`, i tak zostanie wyłapany przy najbliższym `start`.
*Dlaczego:* 2026-10-13 `sasagiri_book` założone ręcznie w pracy (paczka wypchnięta, wpis tylko
w tamtym `monitor_repos.env`) było niewidoczne w domu, 4 projekty z listy nie miały tu klonu,
a 16 z 19 klonów było za origin (RibnXtr2026 o 43 commity) — bo `start` podciągał tylko hub.

**2.13 Dane projektu jadą między maszynami przez SSH, nie na dyskach.** To, czego nie niesie git
(artefakty, pomiary, etykiety — reguła 2.5), przenosi `python tools/transfer.py --send --for <maszyna>
--repo <projekt>` z repo `project_integration` (skill `przerzuc`, ten sam w każdym projekcie): paczka to
**różnica spisów** obu maszyn wg `.claude/transfer.json` projektu, transport SSH przez Tailscale
z **wznawianiem** (od rozmiaru pliku po stronie zdalnej) i **SHA-256 całości** po obu stronach, rozpakowanie
na drugiej maszynie **bez nadpisywania i bez usuwania** (różny plik = `KONFLIKT` w raporcie, zostaje
nietknięty). Projekt bez deklaracji najpierw deklaruje (`--check` zgłasza `[ZADEKLARUJ]`). Przed startem
sprawdź, czy ten projekt nie ma już trwającego przerzutu (dwa procesy w jeden plik zdalny go psują).
*Dlaczego:* decyzja użytkownika 2026-10-18 („nie będę latał z dyskami”). vESSA pokazała mechanizm na
paczce 2,4 GB (0,24 MB/s przez przekaźnik DERP: godziny, więc wznawianie jest obowiązkowe; kontrola rozmiaru
wykryła dwa procesy piszące do jednego pliku); hub miał już spisy i paczki na dysk (`--check`, `--pack`),
brakowało transportu.

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

**4.10 Tekst z zewnątrz czytamy jako bajty i dekodujemy UTF-8 jawnie.** `sys.stdin.read()`, `subprocess.run(..., text=True)` i `open()` bez `encoding=` używają **kodowej strony konsoli** (cp1250 na polskim Windowsie), a wszystko, co przychodzi z narzędzi i hooków, jest UTF-8. Czytaj `sys.stdin.buffer` i `.decode("utf-8", "replace")`, dopisuj `encoding="utf-8"` do `subprocess` i `open`. *Dlaczego:* 2026-09-15 `monitor/heartbeat.py` przez półtora miesiąca wysyłał każdą polską literę jako dwa znaki mojibake na telefon i do Home Assistant; błąd był niewidoczny, bo JSON parsował się poprawnie, a testy miały `PYTHONIOENCODING=utf-8` w fixture. Test ma **wymuszać** złą stronę kodową (`PYTHONIOENCODING=cp1250`), inaczej niczego nie sprawdza.

**4.11 Nieodwracalna naprawa danych ma warunek akceptacji, nie tylko heurystykę.** Skrypt, który przepisuje zapisane dane (naprawa kodowania, migracja, czyszczenie), musi mieć jawny warunek „to na pewno było zepsute" i zostawiać nietknięte wszystko, co go nie spełnia. *Dlaczego:* przy odwracaniu mojibake para `Ćż` koduje się na poprawny UTF-8 innego znaku — bez listy dopuszczalnych zakresów naprawa zepsułaby poprawny tekst, i to nieodwracalnie.


**4.12 Teksty piszemy wg `notes/STYL_PISANIA.md`.** Notatki, raporty, README, opisy PR, publikacje, maile
i odpowiedzi do ludzi: konkretnie (nazwa, liczba, źródło, decyzja), bez wzorców „pisania AI” (nadęta waga,
ogólnikowe atrybucje, imiesłowowe doklejki, słownik typu *delve/kluczowy*, reguła trzech, nadmiar
formatowania, zdania do użytkownika w treści). Importowany jest **skrót** (do 3 KB, pilnuje tego test);
pełna wersja z listami słów, gatunkami, językami i testem przed oddaniem: `notes/STYL_PISANIA_PELNY.md`,
czytana przy tekście dłuższym niż kilka zdań. Liczy się skupisko objawów, nie pojedyncze słowo. Pliki jadą
z paczką (`install_pack.py`) do każdego repo, także nowego (`new_project.py`). **Zasady piszemy zwięźle:**
to, co czytane w każdej sesji, ma być skrótem, szczegóły na żądanie.
@notes/STYL_PISANIA.md
*Dlaczego:* polecenie użytkownika 2026-10-16 — teksty mają być wolne od przypadłości stylu AI, bo
puste zdania ukrywają brak treści, a recenzent je widzi (ZASADY 5.14). Wzorce opisane na podstawie
Wikipedii („Signs of AI writing”) i dwóch prac recenzowanych (Kobak i in. 2025, Juzek i Ward 2025).

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

**7.5 Lakonicznie. Odpowiedź jest krótka, chyba że użytkownik poprosi o długą.** Wynik i to, co
z niego wynika — bez tabel podsumowujących, bez powtarzania tego, co już powiedziane, bez
opisywania drogi, którą się doszło. Liczby i nazwy plików zostają; narracja nie. *Dlaczego:*
użytkownik prosił o to trzeci raz (2026-09-18, 2026-09-19: „bądź w końcu lakoniczny"), a ściana
tekstu ukrywa jedno zdanie, po które sięgnął.

**7.6 Polecenie głosowe dostaje odpowiedź do ucha.** Prompt zaczynający się od `[glos]`
(dokłada go bramka głosowa, dyspozytor „do okna X” i zlecenia z telefonu; użytkownik może go
też wpisać ręcznie, np. w oknie projektu na telefonie) ma **dwie linie `Glosowo: …`**:
1. **po rozeznaniu, przed właściwą robotą** — co mogę zrobić, co jest potrzebne albo czego nie
   zrobię i dlaczego; przy pytaniu, na które odpowiedź jest od razu, wystarczy jedna linia;
2. **na koniec** (krótkie zadania) — co wyszło i co użytkownik może teraz zrobić.

**Pytanie głosowe dostaje bezpośrednią odpowiedź.** Gdy prompt z `[glos]` jest pytaniem (a nie
zleceniem), linia `Glosowo:` zawiera **odpowiedź na pytanie** — nie opis procesu, nie
potwierdzenie, że pytanie usłyszano. Krótko, po ludzku, jak rozmowa. „Jak idzie projekt?” →
`Glosowo: Idzie dobrze, właśnie skończyliśmy obsługę dźwięku.` Bez technikaliów, bez nazw
plików, bez liczb, chyba że liczba jest sednem pytania.

Linia `Glosowo:` to **jedno–dwa zdania potoczną polszczyzną, jak człowiek do człowieka**: bez
ścieżek, nazw plików, komend, skrótów commitów, gałęzi.
**Z polskimi diakrytykami (ą, ę, ć, ł, ś, ż, ź, ó, ń)** — bez wyjątku. Reguła 4.1 (ASCII) dotyczy
źródeł kompilowanych i komentarzy kodu, a nie tekstu czytanego głośno przez TTS: `Glosowo:
rosnące archiwum zdjęć`, nigdy `Glosowo: rosnace archiwum zdjec`. *Dlaczego:* syntezator mówi
to, co widzi — „zdjec” wychodzi jako „zjetc” zamiast „zdjęć”; słownik fonetyczny w bramce łata
tylko znane formy, a Claude ma ich generować nieskończenie wiele.
Reszta odpowiedzi zostaje techniczna, jak zawsze — **czytana jest tylko linia `Glosowo:`**
(bramka bierze ostatnią; dyspozytor okien mówi każdą nową od razu, gdy się pojawi), głosem
wybranym na stronie bramki / w HA. Projekt może w swoim `CLAUDE.md` ogłosić **tryb rozmowy**
(np. nauka języka): wtedy cała odpowiedź jest do odsłuchu i znacznik nie jest potrzebny.

*Dlaczego (reguła 7.6 jako całość):* prośba użytkownika 2026-10-11 — przy zleceniu z auta chce usłyszeć „co mogę zrobić /
co zrobiłem”, a nie zalew parametrów; bramka czytała dotąd całą odpowiedź modelu, z PR-ami
i ścieżkami.

**Routing głosowy między urządzeniami (machine-tag).** Jeśli prompt ma tag `[glos-NAME]`
(dokłada go strona okna w przeglądarce, `NAME` to identyfikator urządzenia), odpowiedź ma
postać `Glosowo-NAME:` z tym samym identyfikatorem. Przeglądarka odtwarza tylko linie
z własnym `NAME` — inne urządzenia ich nie słyszą. Nietagowane `Glosowo:` gra na wszystkich.
*Dlaczego:* bez tagu PC grał odpowiedzi wysłane z telefonu i odwrotnie (broadcast 2026-10-14).

**7.7 Mail wychodzi tylko na wyraźną prośbę.** Wysyłka przez skill `mail`
(`project_integration/tools/send_mail.py`, Gmail): do siebie (`--to self`) od razu; do kogokolwiek
innego **najpierw adresat, temat i treść do akceptacji**, dopiero po „tak" `--potwierdzone`. Hasło
tylko w Menedżerze poświadczeń Windows (`--set-password` w zwykłej konsoli) — nigdy w pliku, repo
ani w rozmowie; limit dzienny, załączniki tylko z katalogu projektu. *Dlaczego:* decyzja
użytkownika 2026-10-11 („chcę móc wysłać maila z okien"); mail do osoby trzeciej jest
nieodwracalny i publikuje treść poza nasze systemy.

**7.8 Zadanie dłuższe niż 2 minuty dostaje stempel czasowy.** Pierwsza linia odpowiedzi: `[HH:MM]`
(sam czas startu). Ostatnia linia odpowiedzi: `[HH:MM→HH:MM · Nm]` (start→koniec + czas trwania;
`1h 05m` dla dłuższych). Krótsze odpowiedzi (rozmowa, pytanie, drobna poprawka) stempla nie
potrzebują — dodawałby więcej szumu niż wartości. *Dlaczego:* prośba użytkownika 2026-10-13 —
ma otwarte okna z Claude'em pracującym w tle i bez stempli nie wie, ile dane okno było bezczynne
między zadaniami ani ile konkretne zadanie trwało. Monitor liczy czas po stronie serwera (zegar
zadania w `heartbeat.py`, reguła 2.9), ale to widać na telefonie, nie w oknie. Format wybrany,
by był grepowalny i nie zajmował miejsca.

**7.9 Odpowiedź na pytanie kończy się sekcją w punktach.** Każda odpowiedź na pytanie (nie zlecenie)
ma na końcu blok: pełne zdania, prosty język, liczby tam gdzie są istotne, zero bełkotu i technikaliów
niezwiązanych z pytaniem. Punkty zawierają maksymalnie zwartą informację — nie opis procesu dochodzenia,
nie powtórkę tego, co widać w logach. Jedno zdanie = jeden fakt lub decyzja. *Dlaczego:* prośba
użytkownika 2026-10-17 — po serii wywołań narzędzi odpowiedź na proste pytanie o stan maszyn
była zakopana za wydrukami API.

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
| `monitor/services.py` | jeden punkt wejścia do usług tła tej maszyny (`--start` podnosi brakujące, tabela mówi, co działa): worker pytań, worker zleceń, bramka głosowa. Wywoływany przez hook `SessionStart` i przez wpis w Autostarcie (`tools/install_autostart.py`). Stan jest **pomiarem** — świeżość zamka, otwarty port — nie deklaracją | człowiek („czy mogę pracować zdalnie"), Claude Code |
| `monitor/heartbeat.py`, `monitor/taskparse.py` | heartbeat do serwera monitora (tylko stdlib, ASCII; adres i token w `~/.claude/monitor.env`, nigdy w repo); `--event label --label "..."` nadaje nazwę bieżącemu zadaniu (reguła 1.5); parser `tasks.md` wspólny z `gen_openspec_status.py`; zegar zadania (prompt → stop) odpala `auto_sync.py` po 10 min (reguła 2.9) | meta-projekt (stan „pracuje / czeka / skończył" na żywo, czas i nazwa zadania) |
| `notes/STYL_PISANIA.md`, `notes/STYL_PISANIA_PELNY.md` | zasady stylu pisania (anti-AI-style): skrot importowany przez ZASADY 4.12 + pelna wersja na zadanie; identyczne wszedzie | Claude |
| `project_files/python/` (lub `src/`) | kod analityczny | — |
| `project_files/python/tests/` | testy nazywające porażki | CI / meta-projekt (`pytest -q`) |
| `project_files/run_files/` | artefakty i logi, **nie w repo** | paczka przenoszenia |
| `tools/ready.py`, `tools/statusline.py` | **konfiguracja MASZYNY**, nie repozytorium: `ready.py --apply` doprowadza maszyne do stanu z repo (klon, nowe repo i klony projektow -- regula 2.12, pack, przelacznik wykonywania, uslugi tla, swiezosc bramki glosowej, wersja dodatku HA, pasek statusu z modelem sesji -- wersja 1.22) i wypisuje w punktach, czego nie umial zrobic sam; `statusline.py --install` wpina pasek zuzycia kontekstu i limitow w `~/.claude/settings.json`, czyli **raz na maszyne, dla wszystkich projektow naraz**. Dlatego tego NIE MA w packu -- pack instaluje pliki DO repozytoriow, a to sa ustawienia maszyny. Skill `start` wola `ready.py --apply`, wiec nowa maszyna nie wymaga pamietania krokow | czlowiek przy nowej maszynie, Claude Code |
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
projektową obowiązującą dalej. Wpis w tabeli poniżej (tu zostają dwie ostatnie wersje, starsze przenosi się do
`project_integration/notes/ZASADY_HISTORIA.md`, gdzie jest pełna tabela) + podbicie wersji w nagłówku +
wgranie do wszystkich repo.

| wersja | data | co i skąd |
|---|---|---|
| 1.27 | 2026-10-21 | Reguła 1.6: checkpoint po każdym podzadaniu — sekcja `## W trakcie (checkpoint)` nadpisywana na bieżąco, chroni przed utratą stanu przy kompaktowaniu kontekstu w środku sesji. Szablon notatki sesji w 1.1 rozszerzony o tę sekcję. *Skąd:* prośba użytkownika 2026-10-21 o profesjonalne rozwiązanie problemu kompaktowania kontekstu we wszystkich projektach. |
| 1.26 | 2026-10-19 | Spojnosc regul po skroceniu pliku: 2.1 wymienia wyjatki 2.8-2.12; 2.8 mowi, ze na koniec zadania pushuje tez Claude/agent (2.10, 2.11); 7.6 ma jedno ogolne *Dlaczego* przed sekcja machine-tag; sekcja 10 opisuje podzial tabeli (tu dwie ostatnie wersje, reszta w ZASADY_HISTORIA). Bez zmiany znaczenia regul. *Skad:* raport agenta Opus po skroceniu pliku, akceptacja uzytkownika 2026-10-19. |
| 1.25 | 2026-10-19 | Skrocenie pliku ponizej limitu 40 000 znakow (ostrzezenie Claude Code): historia wersji i rozszerzone uzasadnienia przeniesione do notes/ZASADY_HISTORIA.md, tresc bez zmian. *Skad:* ostrzezenie uzytkownika o dlugosci pliku. |

Starsze wpisy (pełna tabela 1.0–1.25): `project_integration/notes/ZASADY_HISTORIA.md`.
