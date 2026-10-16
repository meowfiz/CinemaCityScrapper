# STYL PISANIA — pełna wersja

Wersja 1.1 (2026-10-16). Skrót importowany do każdej sesji: `notes/STYL_PISANIA.md` (do 3 KB, pilnuje tego test).
Ten plik czytaj, gdy piszesz tekst dłuższy niż kilka zdań: README, raport, publikacja, opis PR, mail.

**Zasada nadrzędna.** Każde zdanie powinno wnosić konkretną informację, przykład, ograniczenie, źródło albo
decyzję. Zdania bez treści skreśl lub uzupełnij. Nie wymuszaj nazwy, liczby ani źródła w każdym zdaniu:
zmyślona liczba jest gorsza niż ogólnik. Wzorce poniżej to *objawy* pustego zdania; leczy treść, nie synonim.
*Dlaczego:* Wikipedia („Signs of AI writing”) zastrzega, że wzorce nie są problemem, a łatanie powierzchni
utrudnia wykrycie prawdziwych kłopotów (źródła, trafność).

**Liczy się skupisko.** Jedno „kluczowy” to zwykłe słowo; trzy objawy w akapicie to akapit do przepisania.

## 0. Rodzaj tekstu i język

**Gatunek.** Treść (sekcja 1) i higiena (5, 6) obowiązują wszędzie. Format i rejestr zależą od odbiorcy:

| Gatunek | Wolno | Nadal nie |
|---|---|---|
| Rozmowa, mail, notatka | prosty tekst, krótkie zdania | listy i pogrubienia z automatu |
| README, dokumentacja, opis PR | listy z nagłówkiem, tabele dla parametrów i opcji, nagłówki 2–3 poziomów | emoji, ozdobne pogrubienia |
| Raport, publikacja | formalny rejestr, ostrożność wynikająca z niepewności | zastrzeżenia zamiast wiedzy (1.7), puste uogólnienia |

**Język.** Wzorce pochodzą z literatury angielskiej (angielska Wikipedia, abstrakty PubMed). Co z tego przenosi się na inne języki:
- *Struktura, treść, format, uczciwość* (sekcje 1, 3–6): mechanizm nie zależy od języka (pusta waga, źródło bez nazwy,
  doklejki, resztki po chatbocie). Zasada obowiązuje w każdym języku, ale dowody są angielskie.
- *Słownictwo* (sekcja 2): zależy od języka. **EN** zmierzone; **PL** to moja obserwacja do zmierzenia; **inne języki**
  (np. japoński) — brak danych, listy nie stosuj, tylko struktury. Nie tłumacz listy angielskiej słowo w słowo:
  „delve” → „zagłębiać się” niczego nie dowodzi.
- *Typografia*: sygnały zależą od języka. Cudzysłowy „…” są w polskim normą, nie objawem; pauza i krzywe apostrofy
  to wskazówki angielskie.
- Kalki z angielskiego w polskim tekście („odgrywa kluczową rolę” ≈ *plays a crucial role*, „w świetle” ≈ *in light of*)
  — obserwacja własna, flaga, nie dowód.

## 1. Treść

1. **Nadęta waga.** „stanowi świadectwo”, „odgrywa kluczową rolę”, „jest kamieniem milowym”. Powiedz, co się zmieniło i o ile.
2. **Ogólnikowe atrybucje.** „eksperci twierdzą”, „badania wskazują”. Nazwij źródło (autor, rok) albo nie przypisuj.
   Jedno źródło nie jest „konsensusem”.
3. **Pozorna analiza.** Doklejony imiesłów: „…, podkreślając znaczenie…”, „…, przyczyniając się do…”. Podaj dane i wniosek osobnym zdaniem.
4. **Ton reklamowy.** „bogaty”, „dynamiczny”, „imponujący”, „w sercu”. Opis, nie broszura.
5. **Formułka „wyzwania i perspektywy”.** Wypisz konkretne ograniczenia albo pomiń sekcję.
6. **Mglista łączność.** „w związku z”, „powiązany z”, „w kontekście” zamiast faktu.
7. **Zastrzeżenie zamiast braku wiedzy.** „szczegóły nie są szeroko udokumentowane”, po czym spekulacja.
   Gdy nie wiesz: „nie sprawdziłem X”. Gdy wiesz: podaj.
8. **Nie udawaj wykonania czynności.** Nie opisuj jako sprawdzonych, uruchomionych, zmierzonych ani zweryfikowanych
   czynności, których nie wykonano. Rozróżniaj „sprawdziłem / uruchomiłem / zmierzyłem” od „wynika z kodu”,
   „prawdopodobnie”, „nie sprawdziłem”. Brak dostępu do danych, pliku, repo lub narzędzia — powiedz wprost
   (ZASADY 7.3, 5.9).

## 2. Słownictwo

Lista jest **listą kontrolną, nie zakazem**. Nie zastępuj słowa synonimem, żeby uniknąć flagi; jeśli słowo jest
w zdaniu najtrafniejsze, zostaw je. Flaga ma wywołać pytanie „czy da się konkretniej?”.

**EN (zmierzone).** Nadreprezentowane po LLM: *delve(s/ing), showcase/showcasing, underscore(s), intricate/intricacies,
boasts, garnered, comprehending, realm, groundbreaking, advancements, aligns, emphasizing, surpassing* (Juzek i Ward 2025);
*potential, findings, critical* w abstraktach 2024 (Kobak i in. 2025). Wikipedia dodaje: *pivotal, crucial, testament,
tapestry, enduring, vibrant, fostering, valuable, additionally* (początek zdania); nowsze modele: *leverage, multifaceted,
prioritize*. Listy zależą od generacji modelu i się zmieniają.

**PL (obserwacja własna, NIEZWERYFIKOWANA).** „kluczowy”, „istotny”, „warto zauważyć”, „w dzisiejszych czasach”,
„w świecie…”, „podkreśla znaczenie”, „rzuca światło na”, „otwiera nowe możliwości”, „solidny”, „holistyczny”,
„krajobraz” (przenośnia), „Co więcej”/„Dodatkowo”/„Podsumowując” otwierające zdanie.

## 3. Składnia

1. **Preferuj proste czasowniki.** Nie zastępuj „jest”, „ma”, „używa”, „zwraca” formami typu „stanowi”, „posiada”,
   „wykorzystuje”, „charakteryzuje się”, jeśli prostsza forma jest równie precyzyjna.
2. **Paralelizm przeczący.** „nie tylko X, ale także Y”, „to nie X, to Y” — gdy naprawdę prostujesz założenie, nie dla rytmu.
3. **Reguła trzech.** Wyliczenia po trzy z automatu; wypisz tyle, ile jest.
4. **Elegancka wariacja.** Synonimy zamiast powtórzenia nazwy. Termin raz ustalony zostaje ten sam.
5. **Zdania-ramy.** Zapowiedź („Poniżej przedstawiono…”) i podsumowanie powtarzające, co padło. Skreśl.
6. **Rytm.** Same zdania podobnej długości i budowy; mieszaj bez sztuczek.

## 4. Format

1. Markdown tylko tam, gdzie coś niesie; gwiazdki i krzyżyki w tekście, który nie jest `.md`, to błąd; linie poziome bez powodu — nie.
2. Pogrubienia oszczędnie: jedno na akapit, na rzecz do zapamiętania.
3. Lista z pogrubionym nagłówkiem i dwukropkiem tylko dla słownika pojęć, parametrów lub opcji; argumenty piszemy zdaniami.
4. Tabela od trzech kolumn albo dla liczb do porównania; dwie kolumny to akapit.
5. Emoji jako ozdoba nagłówków i punktów: nie.
6. Nagłówki: po polsku tylko pierwsze słowo wielką literą; bez przeskoków poziomów; bez nagłówka z samymi podnagłówkami.
7. Pauza (—): heurystyka własna, najwyżej raz na akapit i zamiast nawiasu lub dwukropka.
8. Cudzysłowy: w polskim „…”, nie mieszaj z prostymi `"` w jednym dokumencie.

## 5. Zdania do użytkownika, które zostały w treści

Usuń: „Oczywiście!”, „Mam nadzieję, że to pomoże”, „Daj znać, jeśli…”, „jako model językowy”, „według moich danych do…”,
„Oto szczegółowy przegląd:”. Usuń też znaczniki narzędzi (`oaicite`, `contentReference`, `[cite: 1]`, `grok_card`,
`utm_source=`) i puste placeholdery (`<nazwa projektu>`, `[wstaw]`).

## 6. Cytowania

Tytuł, autorzy, czasopismo, rok, DOI — sprawdzone. Niezweryfikowane: „DO WERYFIKACJI” (ZASADY 6.3). Typowe usterki
tekstów generowanych: martwe linki, DOI do innej pracy, książki bez stron.

## 7. Test przed oddaniem

1. Co wnosi to zdanie (informację, przykład, ograniczenie, źródło, decyzję)? Nic — skreśl lub uzupełnij.
2. Zlicz objawy z 1–5 na akapit; ≥3 → przepisz akapit.
3. Pierwsze zdanie sekcji mówi coś nowego, ostatnie nie powtarza pierwszego?
4. Jeden byt, jedna nazwa?
5. Każda liczba ma jednostkę i pochodzenie (ZASADY 1.2)?
6. Czy czynności opisane jako wykonane naprawdę wykonano (1.8)?
7. Przeczytaj jedno przypadkowe zdanie na głos: czy powiedziałby je człowiek do kolegi z zespołu?

Nie przesadzaj w drugą stronę: bez celowych literówek, bez dzielenia zdań „dla rytmu”, bez zamiany wszystkiego na punkty.

## 8. Czego ten plik nie robi

- To nie jest sposób na przejście detektorów. Detektory mają realne wskaźniki błędów, a ludzie odróżniają tekst LLM
  od ludzkiego niewiele lepiej niż losowo (badanie z 2025 r. cytowane przez Wikipedię; DO WERYFIKACJI, oryginału nie czytałem).
- Nie zwalnia z ujawnienia użycia AI tam, gdzie wymaga tego wydawca, uczelnia lub regulamin (ZASADY 5.14).
- Wzorce są opisem, nie wyrokiem: ludzie też piszą „kluczowy”.

## 9. Źródła (stan na 2026-10-16)

- Wikipedia, „Wikipedia:Signs of AI writing”, en.wikipedia.org/wiki/Wikipedia:Signs_of_AI_writing — wersja z października 2026,
  przeczytane ok. 200 tys. z ok. 254 tys. znaków; pauzę i elegancką wariację znam tylko ze spisu treści. Wersji
  w innych językach nie sprawdzałem.
- Kobak D., González-Márquez R., Horvát E.-Á., Lause J., „Delving into LLM-assisted writing in biomedical publications
  through excess vocabulary”, *Science Advances* 11(27), 2 lipca 2025, DOI 10.1126/sciadv.adt3813 (arXiv:2406.07016).
  Dane (>15 mln abstraktów PubMed 2010–2024, ≥13,5% abstraktów z 2024 r. przetworzonych LLM) z opisu w wyszukiwarce;
  pracy nie czytałem — szczegóły DO WERYFIKACJI. Materiał angielski.
- Juzek T. S., Ward Z. B., „Why Does ChatGPT ‘Delve’ So Much? Exploring the Sources of Lexical Overrepresentation in Large
  Language Models”, COLING 2025 (arXiv:2412.11385; czytana v1, grudzień 2024). Strony i DOI proceedings DO WERYFIKACJI.
  Wniosek: przyczyna najpewniej w RLHF, niepotwierdzona. Materiał angielski.
- Nieużyte: doniesienie z bloga o podwojeniu częstości pauzy w abstraktach 2021–2025 (nie znalazłem oryginału).

## 10. Zmiany

| wersja | data | co i skąd |
|---|---|---|
| 1.1 | 2026-10-16 | Zasada nadrzędna bez wymuszania nazwy/liczby; „proste czasowniki” zamiast „unikaj jest”; 1.8 „nie udawaj wykonania”; gatunki i język (sekcja 0); lista słów jako lista kontrolna; skrót importowany + pełna wersja na żądanie. *Skąd:* uwagi użytkownika po v1.0 i pytanie o różne języki. |
| 1.0 | 2026-10-16 | Pierwsza wersja z polecenia użytkownika; lokalnego skilla anti-AI nie było. |
