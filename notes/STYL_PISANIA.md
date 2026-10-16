# STYL PISANIA — zasady anti-AI-style

Wersja 1.0 (2026-10-16). Jeden plik, taki sam w każdym repo (`notes/STYL_PISANIA.md`), wciągany przez
`ZASADY_PRACY.md` (`@notes/STYL_PISANIA.md`), więc czyta go każda sesja w każdym projekcie.
Dotyczy tekstów, które powstają w plikach i trafiają do ludzi: notatki, raporty, README, opisy PR,
publikacje, maile; zasady 1–3 także odpowiedzi w rozmowie.

**Zasada nadrzędna: pisz konkretnie.** Każde zdanie ma nieść nazwę, liczbę, źródło albo decyzję.
Wzorce poniżej są *objawem* pustego zdania, nie chorobą. Podmiana słowa na synonim niczego nie leczy;
leczy treść. *Dlaczego:* Wikipedia („Signs of AI writing”) zastrzega, że same wzorce nie są problemem,
a łatanie powierzchni utrudnia wykrycie prawdziwych kłopotów (źródła, trafność).

**Liczy się skupisko, nie pojedyncze wystąpienie.** Jedno „kluczowy” to zwykłe słowo; trzy objawy
w jednym akapicie to akapit do przepisania. Wzorce zmieniają się z generacją modeli, więc lista jest
punktem wyjścia, a nie kompletem.

---

## 1. Treść (najważniejsze)

1. **Nadęta waga.** „stanowi świadectwo”, „odgrywa kluczową rolę”, „wyznacza kierunek”, „jest
   kamieniem milowym”. Powiedz, *co się zmieniło i o ile*, albo skreśl.
2. **Ogólnikowe atrybucje.** „eksperci twierdzą”, „badania wskazują”, „raporty branżowe”. Nazwij
   źródło (autor, rok) albo nie przypisuj. Jedno źródło nie jest „konsensusem”.
3. **Pozorna analiza.** Doklejony imiesłów: „…, podkreślając znaczenie…”, „…, zapewniając…”,
   „…, przyczyniając się do…”. Jeśli wniosek ma wynikać z danych, podaj dane i wniosek osobnym zdaniem.
4. **Ton reklamowy.** „bogaty”, „dynamiczny”, „imponujący”, „w sercu”, „szeroka gama”. Opis, nie broszura.
5. **Formułka „wyzwania i perspektywy”.** Akapit „mimo sukcesów … napotyka wyzwania … przyszłość
   rysuje się optymistycznie”. Wypisz konkretne ograniczenia albo pomiń sekcję.
6. **Mglista łączność.** „w związku z”, „powiązany z”, „w kontekście” zamiast faktu. Napisz, co z czym
   i jak jest połączone.
7. **Zastrzeżenie zamiast braku wiedzy.** „szczegóły nie są szeroko udokumentowane”, po czym
   spekulacja. Gdy nie wiesz: „nie sprawdziłem X”. Gdy wiesz: podaj. (To pasuje do ZASADY 7.3 i 5.9.)

## 2. Słownictwo

**Lista angielska (zmierzona).** Słowa nadreprezentowane w tekstach po LLM: *delve/delves/delving,
showcase/showcasing, underscore(s), intricate/intricacies, boasts, garnered, comprehending, realm,
groundbreaking, advancements, aligns, emphasizing, surpassing* (Juzek i Ward 2025), oraz *potential,
findings, critical* w abstraktach 2024 (Kobak i in. 2025). Wikipedia dodaje: *pivotal, crucial,
testament, tapestry, enduring, vibrant, fostering, valuable, additionally* (na początku zdania),
a w nowszych modelach *leverage, multifaceted, prioritize*.

**Odpowiedniki polskie (obserwacja własna, NIEZWERYFIKOWANA w źródłach — traktuj jako flagi):**
„kluczowy”, „istotny”, „warto zauważyć (podkreślić)”, „w dzisiejszych czasach”, „w świecie…”,
„podkreśla znaczenie”, „rzuca światło na”, „otwiera nowe możliwości”, „solidny”, „holistyczny”,
„zanurzyć się w”, „krajobraz” (w przenośni), „Co więcej”/„Dodatkowo”/„Podsumowując” otwierające zdanie,
„nie sposób nie wspomnieć”.

**Reguła:** słowo z listy zostaje tylko wtedy, gdy nie da się powiedzieć prościej i konkretniej.

## 3. Składnia

1. **Unikanie „jest”.** „pełni rolę”, „stanowi”, „charakteryzuje się”, „odnosi się do”, „posiada”
   tam, gdzie wystarczy „jest”/„ma”. Pierwsze zdanie definicji mówi, czym rzecz *jest*.
2. **Paralelizm przeczący.** „nie tylko X, ale także Y”, „to nie X, to Y”, „zamiast X — Y”.
   Użyj, gdy naprawdę prostujesz czyjeś założenie; nie jako rytm.
3. **Reguła trzech.** Wyliczenia po trzy z automatu („A, B i C”), nawet gdy są dwa fakty albo cztery.
   Wypisz tyle, ile jest.
4. **Elegancka wariacja.** Synonimy zamiast powtórzenia nazwy („projekt”, „inicjatywa”, „przedsięwzięcie”
   o tym samym). Nazwa raz ustalona zostaje ta sama; to dotyczy zwłaszcza terminów technicznych.
5. **Zdania-ramy.** Zapowiedź („Poniżej przedstawiono…”) i podsumowanie sekcji powtarzające, co
   właśnie padło. Skreśl.
6. **Rytm.** Same zdania podobnej długości i budowy. Mieszaj, ale bez sztuczek.

## 4. Format

1. **Markdown tylko tam, gdzie coś niesie.** Gwiazdki i krzyżyki w tekście, który nie jest `.md`, to
   błąd. Linie poziome między sekcjami: bez powodu — nie.
2. **Pogrubienia** oszczędnie: jedno na akapit, i to na rzecz, którą czytelnik ma zapamiętać.
3. **Lista z pogrubionym nagłówkiem i dwukropkiem** w każdej pozycji — tylko gdy to naprawdę
   słownik pojęć. Zwykłe argumenty piszemy zdaniami.
4. **Tabela** — gdy są co najmniej trzy kolumny albo liczby do porównania; dwie kolumny to akapit.
5. **Emoji** jako ozdoba nagłówków i punktów: nie.
6. **Nagłówki:** po polsku tylko pierwsze słowo wielką literą (nie „Wpływ Technologii Na Pracę”);
   bez przeskoków poziomów; bez nagłówka, pod którym są tylko podnagłówki.
7. **Pauza (—):** heurystyka własna — nie częściej niż raz na akapit i tylko zamiast nawiasu lub
   dwukropka. Pauza jako tik interpunkcyjny jest jednym z historycznych sygnałów (Wikipedia).
8. **Cudzysłowy:** w polskim tekście „…” (dolne-górne); nie mieszaj z prostymi `"` w jednym dokumencie.

## 5. Zdania do użytkownika, które zostały w treści

Usuń, zanim tekst wyjdzie z sesji: „Oczywiście!”, „Mam nadzieję, że to pomoże”, „Daj znać, jeśli…”,
„Czy chcesz, żebym…” w środku dokumentu, „jako model językowy”, „według moich danych do…”,
„Oto szczegółowy przegląd:”. Usuń też znaczniki narzędzi i śmieci po cytowaniu: `oaicite`,
`contentReference`, `[cite: 1]`, `grok_card`, parametry `utm_source=`, puste placeholdery
(`<nazwa projektu>`, `[wstaw]`).

## 6. Cytowania

Każde cytowanie: tytuł, autorzy, czasopismo, rok, DOI — sprawdzone. Niezweryfikowane oznacz
„DO WERYFIKACJI”, nie podawaj z pamięci (ZASADY 6.3). Typowe usterki tekstów generowanych: martwe
linki, DOI prowadzące do innej pracy, książki bez stron. Sprawdź je zanim dasz.

## 7. Test przed oddaniem tekstu dłuższego niż kilka zdań

1. Do każdego zdania: *jaka nazwa, liczba, źródło albo decyzja z niego wynika?* Brak → skreśl lub dopisz.
2. Przeszukaj tekst pod kątem list z punktów 1–5; zlicz objawy **na akapit**; ≥3 → przepisz akapit.
3. Czy pierwsze zdanie każdej sekcji mówi coś nowego, a ostatnie nie powtarza pierwszego?
4. Czy ten sam byt ma w całym tekście jedną nazwę?
5. Czy każda liczba ma jednostkę i pochodzenie (ZASADY 1.2)?
6. Czy każde „kluczowy/istotny/warto” da się wyrzucić bez straty? Jeśli tak — wyrzuć.
7. Czytaj na głos jedno przypadkowe zdanie: czy powiedziałby je człowiek, pisząc do kolegi z zespołu?

**Nie przesadzaj w drugą stronę.** Nie psuj tekstu celowo (literówki, sztuczny chaos), nie dziel
zdań dla „rytmu”, nie zamieniaj wszystkiego na punkty. Cel to tekst konkretny i prawdziwy.

## 8. Czego ten plik NIE robi

- **To nie jest sposób na przejście detektorów.** Detektory mylą się (mają realne wskaźniki błędów),
  a ludzie rozróżniają tekst LLM od ludzkiego niewiele lepiej niż losowo (badanie z 2025 r. cytowane
  przez Wikipedię — DO WERYFIKACJI, nie czytałem oryginału). Celem jest jakość i uczciwość.
- **Nie zwalnia z ujawnienia użycia AI** tam, gdzie wymaga tego wydawca, uczelnia lub regulamin
  (ZASADY 5.14).
- Wzorce są opisem, nie wyrokiem: ludzie też piszą „kluczowy”. Wystąpienie bez skupiska to nie błąd.

## 9. Źródła (stan na 2026-10-16)

- Wikipedia, „Wikipedia:Signs of AI writing”, en.wikipedia.org/wiki/Wikipedia:Signs_of_AI_writing
  — wersja z października 2026; przeczytana w ok. 200 000 z ok. 254 000 znaków; fragmenty o pauzie
  i elegancie wariacji znam tylko ze spisu treści.
- Kobak D., González-Márquez R., Horvát E.-Á., Lause J., „Delving into LLM-assisted writing in
  biomedical publications through excess vocabulary”, *Science Advances* 11(27), 2 lipca 2025,
  DOI 10.1126/sciadv.adt3813 (preprint arXiv:2406.07016). Dane (>15 mln abstraktów PubMed 2010–2024,
  ≥13,5% abstraktów z 2024 r. przetworzonych LLM) wzięte z opisu w wynikach wyszukiwania; pracy
  nie czytałem w całości — DO WERYFIKACJI w szczegółach.
- Juzek T. S., Ward Z. B., „Why Does ChatGPT ‘Delve’ So Much? Exploring the Sources of Lexical
  Overrepresentation in Large Language Models”, COLING 2025 (arXiv:2412.11385; czytana wersja v1,
  grudzień 2024). Strony i DOI proceedings — DO WERYFIKACJI. Wniosek: przyczyna najpewniej w RLHF,
  ale niepotwierdzona; zdanie o „delve” nie jest wyrokiem o autorstwie.
- Nieużyte (niezweryfikowane): doniesienie z bloga o podwojeniu częstości pauzy w abstraktach
  2021–2025 — nie znalazłem oryginału.

## 10. Zmiany tego pliku

| wersja | data | co i skąd |
|---|---|---|
| 1.0 | 2026-10-16 | Pierwsza wersja. *Skąd:* polecenie użytkownika — „przejrzyj Wikipedię i 2–3 najnowsze źródła o stylu pisania AI, sformułuj zasady, zaaplikuj do wszystkich projektów i dołączaj w każdym kolejnym”. Lokalnego skilla anti-AI nie było. |
