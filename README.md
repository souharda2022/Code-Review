# Code-Review
# Code Review Assistant — Complete Technical Documentation
 
A self-hosted, AI-powered code review system that runs entirely on local hardware. Developers paste code or upload files, select their team, and get structured feedback grounded in their team's actual coding conventions — not generic advice.
 
**Built over 5 phases + feature expansion + multi-team system.**
 
---
 
## Table of Contents
 
1. [Project Goal](#project-goal)
2. [Server Hardware](#server-hardware)
3. [Architecture Overview](#architecture-overview)
4. [Project Structure](#project-structure)
5. [File Responsibility Map](#file-responsibility-map)
6. [Phase 0 — Skeleton](#phase-0--skeleton)
7. [Phase 1 — Context Injection Pipeline](#phase-1--context-injection-pipeline)
8. [Phase 2 — Prompt Discipline + LLM Integration](#phase-2--prompt-discipline--llm-integration)
9. [Phase 3 — Chunking Strategy](#phase-3--chunking-strategy)
10. [Phase 4 — Two-Pass Deep Review](#phase-4--two-pass-deep-review)
11. [Phase 5 — Evaluation Loop](#phase-5--evaluation-loop)
12. [Feature Expansion](#feature-expansion)
13. [Multi-Team System](#multi-team-system)
14. [The 5 Critical Design Decisions](#the-5-critical-design-decisions)
15. [Token Budget Contract](#token-budget-contract)
16. [API Reference](#api-reference)
17. [Setup Guide](#setup-guide)
18. [How to Customize](#how-to-customize)
19. [Adding a New Team/Codebase](#adding-a-new-teamcodebase)
20. [Comparison: Our System vs OpenClaw vs Claude](#comparison-our-system-vs-openclaw-vs-claude)
21. [Current Status](#current-status)
22. [What's Next](#whats-next)
---
 
## Project Goal
 
Build a code review assistant where:
 
1. A developer pastes a function and asks "What is wrong with this code?"
2. The system identifies bugs, style violations, and improvement opportunities
3. Each problem is explained clearly with severity, location, and fix suggestion
4. All feedback follows the **company's specific coding conventions** — not generic advice
5. Different teams within the company get their own isolated rules
6. Legacy codebases can be systematically reviewed and modernized
### Target Codebases
 
| Repository | Team | Language | Purpose |
|-----------|------|----------|---------|
| [spring-petclinic-rest](https://github.com/spring-petclinic/spring-petclinic-rest) | petclinic-backend | Java | Spring Boot REST API conventions |
| [spring-petclinic-angular](https://github.com/spring-petclinic/spring-petclinic-angular) | petclinic-frontend | TypeScript | Angular frontend conventions |
| [fineract-cn-office](https://github.com/Izakey/fineract-cn-office) | team-fineract | Java | Apache Fineract microservice conventions |
 
---
 
## Server Hardware
 
| Component | Spec |
|-----------|------|
| CPU | Intel Core Ultra 9 285K, 24 cores, up to 5.8 GHz |
| RAM | 128 GB (122 GB free idle) |
| GPU | NVIDIA RTX 4500 Ada Generation, 24 GB VRAM |
| Storage | 1 TB + 3 TB NVMe M.2 (4 TB total) |
| OS | Ubuntu 24.04.3 LTS |
| AI Runtime | Ollama (Docker, GPU-enabled) on port 11434 |
| Server IP | 192.168.14.74 |
 
### Models
 
| Model | Size | Role | Speed |
|-------|------|------|-------|
| qwen3-coder:latest | 18 GB | Primary reviewer (fast pass) | 3-8 seconds |
| deepseek-r1:32b | 19 GB | Deep reviewer (critique pass) | 15-60 seconds |
| nomic-embed-text:latest | 274 MB | Embedding for RAG retrieval | <1 second |
 
Both review models cannot run simultaneously in VRAM (18+19 = 37 GB > 24 GB). The system loads one at a time.
 
---
 
## Architecture Overview
 
```
Developer (any PC on LAN)
Browser: http://192.168.14.74:8090
         |
         v  POST /review {code, team, mode, language}
+------------------------------------------------------------------+
|  codereview-api (Docker, port 8090)                              |
|                                                                  |
|  main.py -- orchestrator                                         |
|     |                                                            |
|     +-- language_detect.py -- auto-detect 12 languages           |
|     |                                                            |
|     +-- teams.py -- load team config, filter by team             |
|     |                                                            |
|     +-- retriever.py -- 3-filter RAG + team isolation            |
|     |       |                                                    |
|     |       v                                                    |
|     |   ChromaDB (Docker, port 8900)                             |
|     |   23+ documents per team (rules + few-shots)               |
|     |                                                            |
|     +-- call_graph.py -- static analysis (no LLM)                |
|     +-- chunker.py -- split at method boundaries                 |
|     +-- token_router.py -- decide chunk strategy                 |
|     |                                                            |
|     +-- prompts.py -- assemble system + user prompt              |
|     |   +-- modes.py -- review/suggest/update instructions       |
|     |   +-- suggestions.py -- custom rules from UI               |
|     |                                                            |
|     +-- llm_client.py -- call Ollama, parse JSON                 |
|     |       |                                                    |
|     |       v                                                    |
|     |   Ollama (host, port 11434)                                |
|     |   qwen3-coder / deepseek-r1:32b                            |
|     |                                                            |
|     +-- deep_review.py -- Pass 2 critique + merge                |
|     +-- session.py -- chat history per session                   |
|                                                                  |
+------------------------------------------------------------------+
```
 
### Docker Services
 
| Container | Image | Port | Purpose |
|-----------|-------|------|---------|
| codereview-api | codereview-api (custom) | 8090 | FastAPI + web UI |
| chromadb-codereview | chromadb/chroma:latest | 8900 | Vector store for RAG |
| ollama | ollama/ollama (pre-existing) | 11434 | All LLM + embedding calls |
 
---
 
## Project Structure
 
```
code-review-assistant/
|
+-- docker-compose.yml              # 2 services: chromadb + api (Ollama is external)
+-- Dockerfile                      # Python 3.11 slim for the API
+-- requirements.txt                # FastAPI, httpx, tiktoken, chromadb, pydantic, python-multipart
|
+-- app/                            # Python backend (15 files)
|   +-- __init__.py                 # Empty, makes app/ a Python package
|   +-- main.py                     # FastAPI endpoints: /review, /review-deep, /review-file, etc.
|   +-- retriever.py                # 3-filter RAG pipeline with team isolation
|   +-- few_shots.py                # BAD/GOOD code pairs per team
|   +-- prompts.py                  # System prompt + mode-aware prompt assembler
|   +-- llm_client.py               # Ollama caller + JSON parser + model swap
|   +-- chunker.py                  # Splits code at method boundaries + context headers
|   +-- call_graph.py               # Static analysis: method calls, null checks, field access
|   +-- token_router.py             # Pre-flight check: send_as_is / chunk / reject
|   +-- deep_review.py              # Pass 2 critique prompt + merge logic + security detection
|   +-- modes.py                    # Review-only / Suggest-code / Auto-update mode prompts
|   +-- language_detect.py          # Auto-detect 12 languages from code content
|   +-- session.py                  # Chat session manager (in-memory, 2-hour expiry)
|   +-- suggestions.py              # Custom rule system (stored in JSON file)
|   +-- teams.py                    # Team CRUD (load/add/remove teams from JSON)
|
+-- ui/
|   +-- index.html                  # Full web UI: team dropdown, mode selector, chat panel,
|                                   # file upload, custom rules, code preview, RAG debug
|
+-- scripts/
|   +-- clone-repos.sh              # Downloads reference repos into repos/
|   +-- extract-styles.py           # Scans repos -> style-guides/chunks/*.md (team-tagged)
|   +-- index-styles.py             # Embeds chunks + few-shots -> ChromaDB (team-tagged)
|   +-- setup-models.sh             # Pulls models into Ollama (first-time)
|   +-- requirements-host.txt       # Python deps for host-side scripts
|
+-- style-guides/
|   +-- chunks/                     # Extracted convention files (auto-generated)
|       +-- java-injection.md       # PetClinic: injection patterns
|       +-- java-annotations.md     # PetClinic: annotation conventions
|       +-- java-naming.md          # PetClinic: naming conventions
|       +-- java-exceptions.md      # PetClinic: exception handling
|       +-- java-rest-api.md        # PetClinic: REST API patterns
|       +-- java-testing.md         # PetClinic: testing conventions
|       +-- java-service-layer.md   # PetClinic: service layer patterns
|       +-- ts-components.md        # PetClinic: Angular component patterns
|       +-- ts-services.md          # PetClinic: Angular service patterns
|       +-- ts-rxjs.md              # PetClinic: RxJS/Observable patterns
|       +-- ts-lifecycle.md         # PetClinic: lifecycle hook usage
|       +-- ts-modules.md           # PetClinic: module/routing patterns
|       +-- ts-typing.md            # PetClinic: TypeScript typing conventions
|       +-- fineract-injection.md   # Fineract: injection patterns
|       +-- fineract-annotations.md # Fineract: annotation patterns
|       +-- fineract-naming.md      # Fineract: naming conventions
|       +-- fineract-exceptions.md  # Fineract: exception handling
|       +-- fineract-rest-api.md    # Fineract: REST API patterns
|       +-- fineract-service-layer.md # Fineract: service layer
|       +-- fineract-testing.md     # Fineract: testing patterns
|
+-- repos/                          # Cloned reference repos
|   +-- spring-petclinic-rest/
|   +-- spring-petclinic-angular/
|   +-- fineract-cn-office/
|
+-- eval/                           # Evaluation system
|   +-- test-cases.json             # 20 test cases (10 Java, 10 TypeScript)
|   +-- run-eval.py                 # Eval harness: calls API, checks output, scores
|   +-- score-history.json          # Historical scores with timestamps
|
+-- suggestions/                    # Custom rules (persisted)
|   +-- custom-rules.json           # User-defined rules from UI
|
+-- config/                         # Team configuration
|   +-- teams.json                  # Team definitions
|
+-- chromadb-data/                  # Persistent ChromaDB storage
+-- .venv/                          # Python virtual environment
```
 
---
 
## File Responsibility Map
 
### Every file and its single job:
 
| File | Phase | Job |
|------|-------|-----|
| **main.py** | All | Orchestrator. Receives HTTP requests, calls all other modules, returns JSON responses. |
| **retriever.py** | Phase 1 | 3-filter RAG retrieval with team isolation. Language filter -> team filter -> semantic search -> category boost. Returns <=600 tokens of relevant rules. |
| **few_shots.py** | Phase 1 | Data file. 10+ BAD/GOOD code pairs tagged with language, category, and team. Gets embedded into ChromaDB by index-styles.py. |
| **prompts.py** | Phase 2 | Builds the messages array for Ollama. Combines system prompt (15 rules) + mode instructions + RAG context + custom suggestions + code. |
| **llm_client.py** | Phase 2 | Calls Ollama /api/chat. Extracts JSON from messy LLM output (strips markdown fences, thinking tags). Validates response fields. Handles timeouts. |
| **chunker.py** | Phase 3 | Splits code at method boundaries using brace-depth tracking. Prepends class context + call graph to every chunk. Handles Java + TypeScript. |
| **call_graph.py** | Phase 3 | Static analysis (pure Python regex, no LLM, <1 second). Maps: who calls who, who reads/writes which fields, who checks for null. |
| **token_router.py** | Phase 3 | Pre-flight check. Counts tokens and decides: send_as_is (<6K), chunk_by_method (6-20K), chunk_aggressive (20-28K), reject (>28K). |
| **deep_review.py** | Phase 4 | Builds the Pass 2 critique prompt for deepseek-r1:32b. Merges Pass 1 + Pass 2 results (confirm/correct/remove/add). Detects security patterns for auto-trigger. |
| **modes.py** | Feature Expansion | Three mode prompts: "no" (review only), "yes" (suggest corrected code), "update" (generate updated code + change list). |
| **language_detect.py** | Feature Expansion | Auto-detects 12 programming languages from code content using regex signal matching. Returns language + confidence. |
| **session.py** | Feature Expansion | In-memory chat session manager. Stores messages, code snapshots, review results per session. 2-hour expiry. |
| **suggestions.py** | Feature Expansion | Custom rule system. CRUD operations on a JSON file. Rules are injected into every prompt as [CUSTOM RULES]. |
| **teams.py** | Multi-Team | Team configuration CRUD. Loads from config/teams.json. Teams appear in UI dropdown. |
| **extract-styles.py** | Phase 1 (script) | Scans repos with regex. Counts patterns (injection, naming, annotations, etc.). Outputs chunked Markdown with metadata tags (language, category, team). |
| **index-styles.py** | Phase 1 (script) | Reads chunk files + few-shots. Embeds each via nomic-embed-text. Stores in ChromaDB with metadata (language, category, team). |
| **clone-repos.sh** | Phase 1 (script) | git clone --depth 1 for each reference repo. |
| **run-eval.py** | Phase 5 (script) | Calls /review for 20 test cases. Checks expected issues found (TP) and false positives (FP). Computes score. Detects regression >10%. |
| **index.html** | UI | Full web UI: team dropdown, language auto-detect, 3 modes, paste/upload/rules tabs, results panel, chat history, code preview, RAG debug. |
 
---
 
## Phase 0 — Skeleton
 
**What we built:** Docker Compose with 3 services, FastAPI with a dummy /review endpoint, and a web UI that renders JSON.
 
**Why:** Verify the plumbing works before adding AI. If Docker networking, FastAPI serving, and UI rendering don't work, nothing else matters.
 
**What was locked:**
- Port allocation: API on 8090, ChromaDB on 8900, Ollama on 11434 (existing)
- Output JSON schema: {issues, summary, style_violations, pass_used, token_info, rag_context}
- The `rule_violated` field — ties every issue back to a specific company rule
- The `location` field — line number + method name so developers know exactly where to look
- Severity levels (high/medium/low) — enables filtering
---
 
## Phase 1 — Context Injection Pipeline
 
**The problem:** Dumping the entire style guide into the system prompt causes the model to ignore most of it. Irrelevant rules dilute relevant ones.
 
**The solution:** 3-filter RAG retrieval with a hard 600-token budget. Only the most relevant rules get injected.
 
### Data Flow
 
```
PetClinic repos (GitHub)
    |
    v  clone-repos.sh
repos/spring-petclinic-rest/
repos/spring-petclinic-angular/
    |
    v  extract-styles.py (regex scanning, no LLM)
style-guides/chunks/ (13 markdown files with metadata)
    |                     +
    |               few_shots.py (10 BAD/GOOD pairs)
    |                     |
    v                     v
    index-styles.py (embeds via nomic-embed-text)
    |
    v
ChromaDB collection "style_rules"
  23 documents, each with: {vector, text, language, category, team}
    |
    v  (at request time)
retriever.py:
  1. Language filter:  WHERE language = "java"
  2. Team filter:      WHERE team = "team-fineract" OR team = "shared"
  3. Semantic search:  cosine similarity top-15
  4. Category boost:   @Autowired detected -> injection rules boosted
  5. Token budget:     assemble top results until 600 tokens
    |
    v
Context string (<=600 tokens of the most relevant rules + examples)
```
 
### What extract-styles.py scans for:
 
| Language | Categories | What it detects |
|----------|-----------|----------------|
| Java | injection | @Autowired field vs constructor, private final |
| Java | annotations | @RestController, @GetMapping frequency |
| Java | naming | Class suffixes (Controller, Service), method prefixes (find, get) |
| Java | exceptions | Custom exceptions, @ExceptionHandler, try/catch count |
| Java | api | Endpoint mappings, ResponseEntity usage, URL patterns |
| Java | testing | JUnit 5, @WebMvcTest, @MockBean |
| Java | service | @Service, @Transactional, interface+impl pattern |
| TypeScript | components | @Component, selectors, class names |
| TypeScript | services | @Injectable, HttpClient usage |
| TypeScript | rxjs | .subscribe(), .pipe(), async pipe, operators |
| TypeScript | lifecycle | ngOnInit, ngOnDestroy, ngOnChanges |
| TypeScript | modules | @NgModule, routing, lazy loading |
| TypeScript | typing | `any` usage, interface count |
 
### Few-shot pairs (why they matter):
 
```
LLM sees rule:    "Always use constructor injection"
                  -> sometimes ignores it
 
LLM sees example: BAD:  @Autowired private X x;
                  GOOD: private final X x; constructor(X x){this.x=x;}
                  -> copies the pattern almost every time
```
 
Each pair is tagged with language, category, AND team for multi-team isolation.
 
---
 
## Phase 2 — Prompt Discipline + LLM Integration
 
**The problem:** LLM output is unpredictable — sometimes lists, sometimes paragraphs, sometimes "Sure! Let me analyze..."
 
**The solution:** Fixed JSON schema enforced via system prompt. Low temperature (0.1) for consistent output. Robust JSON parser that handles markdown fences, thinking tags, and preamble text.
 
### Prompt Structure
 
```
+----------------------------------------------------------+
| SYSTEM MESSAGE (~500 tokens)                             |
|   15 universal rules + JSON output schema                |
|   + mode instructions (review/suggest/update)            |
+----------------------------------------------------------+
| USER MESSAGE                                             |
|   [LANGUAGE]: java                                       |
|   [METHODS ALREADY REVIEWED]: (carry-forward summaries)  |
|   [CODE]: (the submitted code with call graph)           |
|   [QUESTION]: What is wrong with this code?              |
|   [STYLE CONTEXT]: (RAG-retrieved rules, <=600 tokens)   |
|   [CUSTOM RULES]: (user-defined rules from UI)           |
+----------------------------------------------------------+
```
 
### JSON Extraction from Messy LLM Output
 
llm_client.py handles 4 failure modes:
 
1. Clean JSON -> direct parse
2. Markdown fences (```json ... ```) -> strip fences, parse
3. Preamble text ("Sure! Here's...") -> find outermost { } by brace depth
4. Deepseek thinking tags (<think>...</think>) -> strip tags first
If parsing fails entirely, returns empty issues with error message. The UI never crashes.
 
---
 
## Phase 3 — Chunking Strategy
 
**The problem:** A 500-line file is too much for a 32K context model. Attention degrades, rules get ignored.
 
**The solution:** Split at method boundaries (not character counts). Each chunk gets class context + call graph. Carry-forward passes summaries between chunks.
 
### Token Router Decision Table
 
```
Total tokens    Strategy
< 6,000         send_as_is (single LLM call)
6,000-20,000    chunk_by_method (split at method boundaries)
20,000-28,000   chunk_aggressive (50-line blocks)
> 28,000        reject (ask user to paste less)
```
 
### Call Graph (Static Analysis)
 
Pure Python regex, no LLM, runs in <1 second. Extracts per method:
 
- What methods it calls
- What methods call it
- Which fields it reads/writes
- Whether it checks parameters for null
- Whether it can return null
This gets injected into every chunk so the LLM can detect cross-method bugs:
 
```
[CALL GRAPH]
- setStatus(String status)
    called by: process
    writes: this.status
    WARNING: no null check on parameters
- process()
    calls: setStatus, getStatus
```
 
### Summary Carry-Forward
 
Reviews happen sequentially. After each chunk, the LLM generates a method_summary. This summary gets passed to the next chunk's prompt:
 
```
Chunk 1: review setStatus() -> "no null check on parameter"
    |
    v  pass summary forward
Chunk 2: review getStatus() -> knows about setStatus's issues
    |
    v  pass both summaries forward
Chunk 3: review process() -> knows setStatus(null) is dangerous
    -> catches the cross-method NPE bug
```
 
### Chunking is Universal
 
Chunking works the same regardless of team. It splits at method boundaries based on code structure (brace depth tracking), not coding conventions. The RULES inside the review are team-specific. The MECHANISM of splitting is universal.
 
---
 
## Phase 4 — Two-Pass Deep Review
 
**The problem:** Fast model (qwen3-coder) misses subtle bugs. Slow model (deepseek-r1:32b) is too slow for interactive use.
 
**The solution:** Fast pass first, deep pass on demand or auto-triggered.
 
### Flow
 
```
Pass 1: qwen3-coder (3-8 seconds)
    |
    +-> Returns issues immediately
    |
    +-> Auto-trigger check:
    |     Security patterns detected? (auth, crypto, SQL)
    |     Zero issues on >50 lines? (suspicious)
    |     Only low-severity on >100 lines?
    |
    +-> If auto-triggered or user clicks "Deep Review":
          |
          v
        Unload qwen3-coder from VRAM
        Load deepseek-r1:32b
          |
          v
        Pass 2: deepseek CRITIQUES Pass 1 (not re-review):
          - Validates: "Issue #1 is correct" (confirmed)
          - Corrects: "Issue #2 should be high, not medium" (corrected)
          - Removes: "Issue #3 is wrong, code is fine" (false positive)
          - Adds: "You missed SQL injection on line 15" (new)
          |
          v
        Merge Pass 1 + Pass 2
        Return: "2 confirmed, 1 corrected, 0 removed, 3 new"
```
 
### Security Pattern Detection
 
```python
SECURITY_PATTERNS = {
    "authentication": ["authenticate", "login", "password", "token", "jwt"],
    "cryptography":   ["encrypt", "decrypt", "hash", "cipher", "bcrypt"],
    "sql_injection":  ["executeQuery", "createQuery.*+", "SELECT.*+"],
    "file_access":    ["FileInputStream", "readFile", "writeFile"],
    "injection":      ["Runtime.exec", "eval(", "innerHTML"],
}
```
 
If any pattern matches, the UI shows a purple banner: "Deep review recommended — Security-sensitive patterns detected: authentication"
 
### Verified Result
 
Test with authentication code: Pass 1 (qwen3-coder) found 4 issues. Pass 2 (deepseek-r1:32b) confirmed 2, added 4 new (ResponseEntity, exception handling, password exposure, hardcoded URL). Total: 2 -> 6 issues after deep review.
 
---
 
## Phase 5 — Evaluation Loop
 
**The problem:** Model updates, prompt changes, new rules — and you don't know if the system is getting better or worse.
 
**The solution:** 20 test cases run after every change. Score drops >10% = change doesn't ship.
 
### Test Case Structure
 
```json
{
    "id": "java-tc-01",
    "language": "java",
    "input_code": "... code with known bugs ...",
    "expected_issues": ["field injection", "constructor injection"],
    "should_not_flag": ["naming convention"]
}
```
 
### Score Formula
 
```
score = (true_positives / expected_issues) - (false_positives / should_not_flag)
```
 
### Baseline Results
 
```
Overall score:  0.708 (70.8%)
Java score:     0.690
TypeScript:     0.727
Passed:         15/20
True positives: 42/47 (89%)
False positives: 4/21 (19%)
```
 
### Usage
 
```bash
# Run eval after any change
python3 eval/run-eval.py -v -t "description-of-change"
 
# View history
python3 eval/run-eval.py --history
 
# If score drops >10%, exit code = 1 (blocks CI/CD)
```
 
---
 
## Feature Expansion
 
Added on top of Phase 1-5 without changing the core pipeline:
 
### Three Modes
 
| Mode | What it does | Extra JSON fields |
|------|-------------|-------------------|
| `"no"` (Review Only) | Find issues, explain them. No full code output. | (none) |
| `"yes"` (Suggest Code) | Find issues + complete corrected code with // CHANGED: comments | `suggested_code` |
| `"update"` (Auto Update) | Find issues + updated code ready to use + change list | `updated_code`, `changes` |
 
### Language Auto-Detection
 
Detects 12 languages from code content: Java, TypeScript, JavaScript, Python, Go, Rust, C#, C++, Kotlin, Ruby, PHP, Swift. Uses regex signal matching with confidence levels (high/medium/low).
 
### Chat History
 
In-memory sessions with 2-hour expiry. Each session tracks messages, code snapshots, and review results. Developers can ask follow-up questions in the chat panel.
 
### Custom Rules
 
Add rules from the UI that get injected into every prompt:
 
```
Title: "Use Lombok @Data"
Rule: "Use @Data annotation instead of manual getters/setters"
Language: Java
Team: petclinic-backend
```
 
Stored in `suggestions/custom-rules.json`. No re-indexing needed.
 
### Preview Before Apply
 
In `mode=update`, the system shows a preview of the updated code. Developer clicks "Approve" before it's finalized. Without approval, changes are not applied.
 
---
 
## Multi-Team System
 
**The problem:** If Team A uses constructor injection and Team B uses field injection, mixing their rules causes the LLM to give contradictory advice.
 
**The solution:** Every rule, few-shot, and chunk is tagged with a `team` field. The RAG retriever filters by team at query time.
 
### How It Works
 
```
Developer selects: Team = "team-fineract"
    |
    v
ChromaDB query:
  WHERE language = "java"
  AND (team = "team-fineract" OR team = "shared")
    |
    v
Returns: ONLY fineract rules + shared company rules
NOT returned: PetClinic rules (filtered out)
    |
    v
LLM reviews with fineract conventions only
```
 
### Team Configuration
 
Teams are defined in `config/teams.json` and loaded dynamically:
 
```json
[
    {"id": "petclinic-backend", "name": "PetClinic Backend", "languages": ["java"]},
    {"id": "petclinic-frontend", "name": "PetClinic Frontend", "languages": ["typescript"]},
    {"id": "team-fineract", "name": "Team Fineract - Office", "languages": ["java"]},
    {"id": "shared", "name": "Company-Wide (Shared)", "languages": ["all"]}
]
```
 
Teams can be added via API (`POST /teams`) or by editing the JSON file. The UI team dropdown loads dynamically from the `/teams` endpoint.
 
### What's Team-Specific vs Universal
 
| Component | Team-Specific or Universal |
|-----------|---------------------------|
| RAG rules (style-guides/chunks/) | Team-specific (tagged with team) |
| Few-shot pairs | Team-specific (tagged with team) |
| Custom suggestions | Team-scoped (from UI) |
| System prompt (15 rules) | Universal (same for all) |
| Chunking | Universal (splits by code structure) |
| Call graph | Universal (detects method relationships) |
| Carry-forward | Universal (passes summaries) |
| Token router | Universal (counts tokens) |
| Deep review | Universal (same critique mechanism) |
| Language detection | Universal (same regex signals) |
| Eval harness | Universal harness, team-specific test cases |
 
---
 
## The 5 Critical Design Decisions
 
| # | Problem | Solution | Key Constraint |
|---|---------|----------|----------------|
| 1 | Context injection — model ignores buried rules | 3-filter RAG (language -> team -> semantic -> category boost) | Hard 600-token context budget |
| 2 | Prompt discipline — unparseable LLM output | Fixed JSON schema + tagged prompt structure + robust parser | rule_violated field mandatory |
| 3 | Chunking — half-methods reviewed without context | Split at method boundaries + class context + call graph + carry-forward | Max ~2,000 tokens per chunk |
| 4 | Latency — fast model misses bugs, slow model unusable | Two-pass: qwen3-coder first, deepseek-r1:32b as opt-in critique | Pass 2 is opt-in or auto-triggered |
| 5 | Evaluation — no idea if system is improving or degrading | 20 test cases, score formula, regression detection (>10% drop blocks changes) | Run after every system change |
 
---
 
## Token Budget Contract
 
```
+----------------------------------------------------+
| SYSTEM PROMPT (rules + JSON schema + mode)         |  ~500-800 tokens
+----------------------------------------------------+
| RAG CONTEXT (team-filtered rules + few-shots)       |  <=600 tokens (hard budget)
+----------------------------------------------------+
| CUSTOM RULES (from UI suggestions)                  |  ~0-200 tokens
+----------------------------------------------------+
| CALL GRAPH (static analysis output)                 |  ~50-200 tokens
+----------------------------------------------------+
| CODE (the method being reviewed)                    |  ~300-2000 tokens
+----------------------------------------------------+
| CARRY-FORWARD (previous method summaries)           |  ~0-500 tokens
+----------------------------------------------------+
| QUESTION                                            |  ~20 tokens
+----------------------------------------------------+
| TOTAL INPUT                                         |  ~1,500-4,000 tokens typical
| MODEL OUTPUT (JSON)                                 |  ~400-1500 tokens
| MODEL CONTEXT WINDOW                                |  32,768 tokens
+----------------------------------------------------+
```
 
---
 
## API Reference
 
### Core Endpoints
 
| Method | Path | Purpose |
|--------|------|---------|
| POST | /review | Submit code for review (supports mode, team, language) |
| POST | /review-deep | Pass 2 deep review with deepseek-r1:32b |
| POST | /review-file | Upload a file for chunked review |
| POST | /apply-preview | Approve previewed update code |
 
### Configuration Endpoints
 
| Method | Path | Purpose |
|--------|------|---------|
| GET | /teams | List all teams |
| POST | /teams | Add a new team |
| DELETE | /teams/{id} | Remove a team |
| GET | /suggestions | List custom rules (filterable by team/language) |
| POST | /suggestions | Add a custom rule |
| DELETE | /suggestions/{id} | Remove a custom rule |
 
### Chat Endpoints
 
| Method | Path | Purpose |
|--------|------|---------|
| GET | /chat/history/{session_id} | Get chat messages for a session |
| GET | /chat/sessions | List all active sessions |
 
### Debug Endpoints
 
| Method | Path | Purpose |
|--------|------|---------|
| POST | /retrieval-test | Test RAG retrieval for code + team |
| POST | /chunk-test | See how code gets chunked + call graph |
| POST | /detect-language | Detect language from code snippet |
| GET | /health | System status, models, teams |
 
---
 
## Setup Guide
 
### Prerequisites
 
- Docker + Docker Compose
- Python 3.10+
- Existing Ollama with: qwen3-coder:latest, deepseek-r1:32b, nomic-embed-text:latest
### Installation
 
```bash
cd /Data/Souharda_Sifat/code-review-assistant
 
# 1. Virtual environment
python3 -m venv .venv
source .venv/bin/activate
pip install -r scripts/requirements-host.txt
 
# 2. Clone reference repos
bash scripts/clone-repos.sh
 
# 3. Extract style conventions
python3 scripts/extract-styles.py
 
# 4. Start Docker services
mkdir -p suggestions config
echo '[]' > suggestions/custom-rules.json
docker compose up -d --build
 
# 5. Index into ChromaDB
python3 scripts/index-styles.py
 
# 6. Rebuild API
docker compose up -d --build
 
# 7. Verify
curl -s http://localhost:8090/health | python3 -m json.tool
```
 
### Access
 
- Web UI: `http://192.168.14.74:8090`
- API: `POST http://192.168.14.74:8090/review`
- Health: `GET http://192.168.14.74:8090/health`
---
 
## How to Customize
 
| What to change | File to edit | Then run |
|----------------|-------------|----------|
| Coding rules per team | scripts/extract-styles.py (write_chunk text) | python3 extract-styles.py && python3 index-styles.py |
| BAD/GOOD examples | app/few_shots.py | python3 scripts/index-styles.py && docker compose up -d --build |
| Universal rules in system prompt | app/prompts.py (SYSTEM_PROMPT_BASE) | docker compose up -d --build |
| Mode behavior (review/suggest/update) | app/modes.py | docker compose up -d --build |
| Token budget (600) | app/retriever.py (TOKEN_BUDGET) | docker compose up -d --build |
| Category boost signals | app/retriever.py (JAVA_SIGNALS/TS_SIGNALS) | docker compose up -d --build |
| LLM temperature | app/llm_client.py (temperature: 0.1) | docker compose up -d --build |
| Deep review timeout | app/llm_client.py (timeout: 600.0) | docker compose up -d --build |
| Security patterns for auto-trigger | app/deep_review.py (SECURITY_PATTERNS) | docker compose up -d --build |
| Add a new language | app/language_detect.py (LANGUAGE_SIGNALS) | docker compose up -d --build |
| Token routing thresholds | app/token_router.py | docker compose up -d --build |
| Large method split size | app/chunker.py (line_count > 80) | docker compose up -d --build |
| Add custom rules (quick) | UI -> Rules tab -> Add Rule | Instant, no rebuild |
| Add new team (quick) | POST /teams API or edit config/teams.json | docker compose up -d --build |
 
---
 
## Adding a New Team/Codebase
 
### Quick Version (5 steps)
 
```bash
# 1. Clone the repo
cd repos && git clone --depth 1 https://github.com/org/repo.git && cd ..
 
# 2. Add team via API
curl -s http://localhost:8090/teams -X POST \
  -H "Content-Type: application/json" \
  -d '{"team_id":"new-team","name":"New Team","languages":["java"],"repo":"repo"}'
 
# 3. Add analyze function to scripts/extract-styles.py
#    Add few-shot pairs to app/few_shots.py
 
# 4. Re-extract and re-index
python3 scripts/extract-styles.py
python3 scripts/index-styles.py
docker compose up -d --build
 
# 5. Verify
python3 eval/run-eval.py -v -t "added-new-team"
```
 
### What You Re-run vs What You Skip
 
| Step | Needed? | Why |
|------|---------|-----|
| Clone repo | Yes | Need the source code |
| Add team config | Yes | Team appears in UI dropdown |
| Extract styles | Yes | New conventions to learn |
| Write few-shots | Recommended | Better review quality |
| Index to ChromaDB | Yes | New rules need embedding |
| Rebuild Docker | Yes | Pick up new code |
| Run eval | Yes | Verify score didn't drop |
| Change chunker | No | Universal |
| Change call graph | No | Universal |
| Change LLM client | No | Universal |
| Change deep review | No | Universal |
 
---
 
## Comparison: Our System vs OpenClaw vs Claude
 
| Feature | Our System | OpenClaw | Claude API |
|---------|-----------|----------|------------|
| Company-specific rules | Yes (RAG + team isolation) | No (generic prompt only) | No (system prompt only) |
| Cross-method bug detection | Yes (call graph + carry-forward) | No | Partially (large context) |
| Multi-team isolation | Yes (ChromaDB team filter) | No | No |
| Two-pass review | Yes (qwen3-coder + deepseek) | No | No |
| Evaluation/regression | Yes (20 test cases, score tracking) | No | No |
| Data privacy | 100% local | 100% local (if self-hosted) | Cloud (code sent to Anthropic) |
| Languages | 12 auto-detected | Any (via LLM) | Any (via LLM) |
| Messaging integration | Web UI only | Slack, Telegram, Discord, WhatsApp | Web/API only |
| Persistent agents | No | Yes (runs 24/7) | No |
| Cost | Free (local GPU) | Free (self-hosted) | $0.01-0.10 per review |
| Review quality | 85-90% (with RAG + call graph) | 50-60% (generic) | 95% (massive model) |
 
### Best Combined Architecture
 
```
OpenClaw (automation layer) -- monitors PRs, posts to Slack
    |
    v  calls our API
Our System (review brain) -- RAG + chunking + call graph + team rules
    |
    v  developer reads review
Claude Code (implementation) -- developer applies fixes in IDE
```
 
---
 
## Current Status
 
| Component | Status | Notes |
|-----------|--------|-------|
| Phase 0: Skeleton | Done | Docker, FastAPI, UI, JSON schema |
| Phase 1: Context Injection | Done | 3-filter RAG, 600 token budget, team-tagged |
| Phase 2: LLM Integration | Done | qwen3-coder, structured JSON, robust parser |
| Phase 3: Chunking | Done | Method boundaries, call graph, carry-forward |
| Phase 4: Deep Review | Done | deepseek-r1:32b critique, auto-triggers, merge |
| Phase 5: Evaluation | Done | 20 test cases, baseline score 0.708 |
| Feature Expansion: Modes | Done | Review/Suggest/Update modes |
| Feature Expansion: Multi-language | Done | 12 languages auto-detected |
| Feature Expansion: Chat | Done | Session-based chat history |
| Feature Expansion: Custom Rules | Done | UI-based rule management |
| Multi-Team System | Done | Team-scoped RAG, config, UI dropdown |
| PetClinic Backend team | Done | 7 Java chunks + 5 few-shots |
| PetClinic Frontend team | Done | 6 TypeScript chunks + 5 few-shots |
| Fineract team | In Progress | Repo cloned, extraction script ready |
 
---
 
## What's Next
 
### Short-term (can build now)
 
1. **OpenClaw integration** -- Write an OpenClaw skill that calls our /review API, posts results to Slack/Telegram
2. **GitHub PR integration** -- Webhook that auto-reviews when a PR is opened
3. **Scenario B: Legacy modernization** -- File-level review pipeline with human-in-the-loop gate
4. **VS Code extension** -- Review code directly in the editor
5. **Improve eval score** -- Currently 0.708, target 0.80+ by tuning prompts and adding test cases
### Medium-term (needs planning)
 
6. **Multi-team eval** -- Separate test cases per team, team-specific scoring
7. **Fine-tuning experiment** -- Fine-tune qwen3-coder on company code for even better results
8. **Streaming responses** -- Stream LLM output to the UI for better UX on slow reviews
9. **Code diff view** -- Show before/after diff instead of just the corrected code
10. **Authentication** -- User login so teams can't access each other's rules
### Long-term (needs hardware)
 
11. **GPU upgrade (48GB+)** -- Run 70B models, eliminate chunking for most files
12. **Multi-file review** -- Review relationships between classes (service + controller + repository)
13. **Auto-fix pipeline** -- Review -> approve -> apply -> run tests -> commit (fully automated)
