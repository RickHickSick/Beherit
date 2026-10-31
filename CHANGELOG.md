# Changelog

Wszystkie istotne zmiany w projekcie **Beherit** są dokumentowane w tym pliku.
Format bazuje na zasadach [Keep a Changelog](https://keepachangelog.com/pl/1.0.0/), a wersjonowanie podlega regułom [Semantic Versioning](https://semver.org/lang/pl/).

---

## [0.4.0] - 2026-10-31

### Dodano (Added)
* **Wieloagentowy Rój i Delegowanie Zadań (`BEH-060`):**
  * Narzędzie `call_agent(agent_slug, prompt)` umożliwiające jednemu agentowi dynamiczne delegowanie podzadań do innych wyspecjalizowanych agentów w systemie.
  * Zabezpieczenie przed pętlami rekurencyjnymi z limitem głębokości wywołań (`max_depth=3`).
  * Rejestracja zdarzeń `agent_delegation` i `agent_delegation_result` w strumieniu SSE oraz wizualizacja drzewa wywołań roju w interfejsie Web UI.
* **Natywne Wywoływanie Narzędzi (ReAct / Function Calling) dla Gemini i Claude Direct (`BEH-061`):**
  * Pełna obsługa pętli ReAct bezpośrednio przez API Google Gemini Direct (narzędzia `tool_declarations` i `functionCall`) oraz Anthropic Claude Direct (bloki `tools` i `tool_use`).
  * Autonomiczne wykonywanie narzędzi w locie (do 10 iteracji) przed zwróceniem syntetycznej odpowiedzi do użytkownika.
* **Długoterminowa Pamięć Trwała per Agent (`BEH-062`):**
  * Nowy plik `memory.md` przechowywany w katalogu roboczym każdego agenta, zsynchronizowany z bazą SQLite.
  * Zestaw dedykowanych narzędzi: `save_memory(key, fact_content)`, `recall_memory(key)` oraz `forget_memory(key)`.
  * Automatyczna injekcja zapamiętanych faktów do promptu systemowego agenta w sekcji `[PERSISTENT LONG-TERM MEMORY]`.
  * Zakładka podglądu i bezpośredniej edycji pamięci w oknie agenta w Web UI.
* **Autonomiczna Pętla Celów i Notatnik Zadań (`BEH-063`):**
  * Plik `todo.md` per agent dla dekompozycji i śledzenia wieloetapowych celów (`[ ]`, `[/]`, `[x]`).
  * Wbudowane narzędzia: `read_todos()`, `update_todo(task_id, status, notes)`, `add_todo(title, description, priority)`.
  * Wstrzykiwanie aktualnego stanu celów do kontekstu systemowego pod `[AUTONOMOUS GOAL & TODO SCRATCHPAD]`.
  * Tablica zadań Todo z filtrowaniem i wskaźnikami postępu w Web UI.
* **Multimodalny Czat z Przesyłaniem Obrazów i Analizą Wizyjną (`BEH-064`):**
  * Możliwość przeciągania (drag-and-drop) oraz dodawania załączników graficznych i plików PDF bezpośrednio w oknie czatu.
  * Automatyczna konwersja do Base64 oraz integracja z modelami multimodalnymi (Gemini 2.5/3.8 Flash & Pro, Claude 3.5/3.7 Sonnet, Antigravity SDK).
  * Podgląd miniaturek załączników w pasku wprowadzania wiadomości oraz w historii rozmowy.
* **Dwukierunkowa Integracja z Plane API (`BEH-065`):**
  * Zestaw wbudowanych narzędzi MCP/ReAct: `plane_list_issues`, `plane_create_issue`, `plane_update_state`, `plane_add_comment`.
  * Samodzielne pobieranie zadań, aktualizowanie statusów (Backlog, Todo, In Progress, Done) i dodawanie komentarzy podsumowujących realizację celów.
* **Dynamiczne Skrypty Umiejętności (`skills/`) per Agent (`BEH-066`):**
  * Katalog `skills/` w przestrzeni agenta z automatycznym wykrywaniem skryptów Bash (`.sh`), Python (`.py`) oraz specyfikacji Markdown (`.md`).
  * Narzędzia dynamiczne: `list_skills()`, `run_skill(skill_name, args)` oraz dedykowane narzędzia `skill_{name}` syntetyzowane w serwerze MCP.
  * Modal zarządzania umiejętnościami w Web UI z edytorem kodu i testowym uruchamianiem.
* **Graph RAG i Podążanie za Wikilinkami Obsidiana `[[...]]` (`BEH-067`):**
  * Parser linków wiki Obsidiana (`[[Dokument]]`, `[[Dokument|Alias]]`, `[[Dokument#Sekcja]]`) budujący graf powiązań w tabeli `knowledge_links`.
  * Wieloskokowa eksploracja (1-hop i 2-hop) wstrzykująca kontekst powiązanych notatek do zapytań agenta.
  * Narzędzie `query_knowledge_graph(starting_concept, hops)`.
  * Interaktywna zakładka wizualizacji grafu relacji w bazie wiedzy Web UI.
* **Dwukierunkowy Bot Telegram dla Agentów Beherit (`BEH-068`):**
  * Asynchroniczna usługa bota Telegram (`TelegramBotService`) działająca w cyklu życia aplikacji (lifespan) w trybie long-polling.
  * Autoryzacja po ID użytkownika Telegram (`TELEGRAM_ALLOWED_USER_IDS`).
  * Obsługa poleceń `/start`, `/agents`, `/agent <slug>`, `/status`, `/todo`, `/clear` oraz strumieniowania odpowiedzi na żywo.
  * Narzędzie `send_telegram_notification(chat_id, message)` dla proaktywnych powiadomień.
  * Panel kontrolny i monitor stanu bota Telegram w Web UI.
* **Utwardzona Izolacja Kontenerowa - Bubblewrap Sandbox (`BEH-069`):**
  * Ephemeralny sandbox bazujący na Bubblewrap (`bwrap`) dla narzędzia `execute_bash`.
  * Montowanie systemowych katalogów w trybie tylko do odczytu (`--ro-bind /usr`, `--ro-bind /bin`), odizolowany prywatny `/tmp`, unsharing przestrzeni PID/IPC/UTS.
  * Inteligentna detekcja i automatyczny fallback do utwardzonego środowiska z filtrem regex w zagnieżdżonych kontenerach Docker bez przywilejów.

### Zmieniono (Changed)
* **Podniesienie wersji projektu do 0.4.0:**
  * `pyproject.toml` (`version = "0.4.0"`).
  * `src/beherit/__init__.py` (`__version__ = "0.4.0"`).
  * `src/beherit/mcp/server.py` (`"version": "0.4.0"`).
  * `src/beherit/static/index.html` (odznaki `v0.4.0` w nagłówku i instrukcji).
  * `README.md` oraz `docs/MANUAL.md`.

---

## [0.3.2] - 2026-10-31

### Dodano (Added)
* **Zarządzanie wątkami czatu z agentami (`BEH-058`):**
  * Możliwość tworzenia nowej nazwanej rozmowy za pomocą przycisku `+ Nowy Czat` z modalnym oknem nadawania tytułu wątku.
  * Zmiana i edycja tytułu aktywnego wątku czatu bezpośrednio z interfejsu (przycisk ołówka `✏️`) z natychmiastowym zapisem w bazie danych SQLite.
  * Nowy punkt końcowy REST API: `PATCH /api/agents/{slug}/chats/{session_id}` oraz metoda `DatabaseManager.update_chat_session_title(session_id, title)`.
  * Bezpieczne, trwałe usuwanie wątków rozmowy (przycisk `🗑️`) z kaskadowym usuwaniem wszystkich powiązanych wiadomości z tabeli `chat_messages` bazy danych.
  * Nowy punkt końcowy REST API: `DELETE /api/agents/{slug}/chats/{session_id}` oraz metoda `DatabaseManager.delete_chat_session(session_id)`.
* **Interaktywna instrukcja obsługi w aplikacji webowej (`BEH-057`):**
  * Dedykowana zakładka **`Instrukcja`** w głównym pasku nawigacyjnym (`currentView === 'manual'`).
  * Szybki przycisk akcji **`📖 Instrukcja Obsługi`** umieszczony bezpośrednio na pulpicie agentów obok przycisku *Odśwież*.
  * Błyskawiczna wyszukiwarka na żywo (`guideSearchQuery`) filtrująca tematy, instrukcje i tabele słownika funkcji w czasie rzeczywistym.
  * Filtry tematyczne rozdziałów: Szybki start, Jak podłączyć agenta (SDK & API), Możliwości platformy, Słownik funkcji i elementów UI, Integracja z n8n & cURL, Narzędzia MCP & Piaskownica, REST API & CLI, FAQ.
  * Szczegółowy słownik opisujący działanie każdego przycisku na kafelku agenta, każdego pola formularzy kreatora i parametrów webhooka.
* **Oficjalny podręcznik użytkownika w Markdown:**
  * Kompletny dokument [`docs/MANUAL.md`](docs/MANUAL.md) w repozytorium kodu.
  * Kopia i integracja z notatkami w sejfie Obsidian (`Instrukcja Obsługi - Beherit.md`).

### Zmieniono (Changed)
* **Ochrona przed buforowaniem przeglądarki:**
  * Dodano nagłówki `Cache-Control: no-cache, no-store, must-revalidate`, `Pragma: no-cache` oraz `Expires: 0` dla głównej trasy `/` w `src/beherit/main.py`, eliminując buforowanie starej wersji UI w przeglądarkach Safari iOS oraz desktopowych.
* **Podniesienie wersji projektu do 0.3.2:**
  * `pyproject.toml` (`version = "0.3.2"`).
  * `src/beherit/__init__.py` (`__version__ = "0.3.2"`).
  * `src/beherit/mcp/server.py` (`"version": "0.3.2"`).
  * `src/beherit/static/index.html` (odznaki `v0.3.2` w nagłówku i instrukcji).
  * Zaktualizowano `docs/MANUAL.md` oraz notatki Obsidian.

### Naprawiono (Fixed)
* Eliminacja osieroconych wiadomości czatu w SQLite przy usuwaniu sesji dialogowych dzięki transakcyjnemu, kaskadowemu czyszczeniu powiązań w bazie danych.

---

## [0.3.0] - 2026-10-25

### Dodano (Added)
* **Uniwersalny Webhook REST API (`POST /api/agents/{slug}/run`):**
  * Zewnętrzne wywoływanie agentów z n8n, cURL, bash lub innych systemów.
  * Obsługa parametrów: `context`, `destination_file`, `file_mode`, `save_report`, `callback_url`, `async_execution`.
* **Centrum integracji n8n i generator cURL w UI:**
  * Modal integracji z gotowymi konfiguracjami węzła HTTP Request w n8n, poleceniami cURL oraz formularzem testowym na żywo.
* **Wielozadaniowy Harmonogram Multi-Cron:**
  * Obsługa wielu niezależnych reguł Cron per agent (`ScheduledJob`), split-menu na kafelku pulpitu oraz wywoływanie promptów doraźnych (ad-hoc).
* **Multimodalne Raporty i Wykrywanie Artefaktów:**
  * Autodetekcja plików binarnych (wykresy, tabele, pliki PNG/JPG) tworzonych przez agentów i bezpieczny streaming przez `GET /api/reports/artifact`.
* **Klocki MCP (Presety):**
  * 7 gotowych serwerów Model Context Protocol (Brave Search, PostgreSQL, SQLite, Filesystem, GitHub, Puppeteer, Fetch).
* **Dynamiczne odświeżanie modeli:**
  * Endpoint `GET /api/agents/models/available` i przycisk `🔄` w UI odpytujący dostawców o aktualnie dostępne modele.

---

## [0.2.0] - 2026-10-24

### Dodano (Added)
* **Wbudowany serwer Model Context Protocol (MCP):**
  * `beherit mcp --agent-slug <slug>` udostępniający piaskownicę agenta jako serwer narzędzi MCP.
* **Lokalny Hybrydowy RAG (Knowledge Base):**
  * Wyszukiwanie semantyczne SQLite FTS5 z rankingiem BM25, automatyczne dzielenie plików z `knowledge/` na chunki i wstrzykiwanie kontekstu.
  * Obsługa plików PDF, obrazów i linków zewnętrznych.
* **Natychmiastowe przerywanie generowania czatu (Abort/Stop):**
  * Przycisk Zatrzymaj w czacie wysyłający `SIGKILL` do procesu wykonawczego i zapisujący dotychczasowy fragment odpowiedzi.
* **Konteneryzacja Docker:**
  * Oficjalny wieloarchitekturwy `Dockerfile` (ARM64 dla Raspberry Pi 5 + AMD64) i konfiguracja `docker-compose.yml`.

---

## [0.1.0] - 2026-10-23

### Dodano (Added)
* Pierwsze publiczne wydanie platformy **Beherit**.
* Architektura Markdown-First (pliki agentów w czystym Markdown: `agent.md`, `instructions.md`).
* Per-Agent Subscription Isolation (niezależne uwierzytelnianie agentów oparte o sesje SDK CLI w `.auth/`).
* Interfejs PWA zoptymalizowany pod Safari iOS i ekrany dotykowe.
* Piaskownica uprawnień terminala `CapabilitiesGuard` (Restricted / Unrestricted).
* Baza danych SQLite dla historii zadań i wątków czatu.
