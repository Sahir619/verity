# Verity: Architecture on one page

How the pieces fit. This page is the map; the detail lives in `backend/ARCHITECTURE.md`, `backend/CONTRACTS.md` (exact interfaces, wins on interfaces) and `frontend/ARCHITECTURE.md`.

## Shape

```
Browser (Next.js static export, no secrets)
  replay path: public/runs/*.json, no network call after the file loads
  live path:   POST /threads/{id}/messages (the chat), POST /runs, GET /runs/{id}/events (SSE);
               POST /parse and POST /runs/{id}/ask stay as older paths
        |
FastAPI backend (one process, backend/verity/)
  api -> RunManager -> orchestrator (a fixed state machine; code, never a model, decides what is next)
        |                |                  |                    |
   SQLite + run files   Docker sandbox     Nebius Token Factory   Search: Tavily (web),
   (backend/data/)      verity-tools:0.2   (Nemotron, GLM 5.3)    Serper (Scholar, Patents)
```

The model never runs a tool. The backend calls fixed scripts with fixed arguments inside the sandbox image: lint and simulation (`verify`), synthesis, timing and power (`measure`), equivalence (`eqy`), more seeds (`seeds`), planted bugs (`strength`) and a waveform of one test case (`wave`). Every PASS is a real simulation against a human-written golden model that the agent cannot see or edit. "Proven" means Yosys EQY proved it.

## 1. Fact check (a thread's first chip request)

`POST /parse` (run inside `POST /threads/{id}/messages` until the thread's first chip request) reserves the worst-case cost on the daily budget, then one GLM 5.3 call (the parser route) sorts the sentence into seven classes and maps it onto a chip family and knob values. Code validates family and knobs against the pack knob tables, turns a model "not a chip" on a sentence with a hardware word into "unsure", sets the verdict from the class (buildable now, buildable with changes, not buildable here, not a chip, could not check), and reports which knobs the sentence itself stated (`statedKnobs`; the rest show as "I assumed"). "Not a chip" runs no search. The seventh class, a question about chips or about Verity, gets an answer from the agent and no verdict.

## 2. Research

Queries come from code templates (family and knobs, or the label reduced to hardware words), never raw user text: at most three. A request inside a family gets a web query and a Scholar query; a "too big" or "not digital" request can also get a Patents query. With both keys, Tavily answers the web query and Serper answers Scholar and Patents; with one key that service answers all three; with none, research is off on a real provider. Code keeps only allowlisted hosts (tiers A, B, C, P), ranks them and keeps up to 8 sources, at most 2 per domain. One GLM 5.3 call (the research route, no tools) summarises them; code drops any citation, URL, number or technique it cannot trace. Web text reaches no other model.

## 3. Database (Verity's own knowledge store)

SQLite under `backend/data/` (migrations in `backend/verity/store/migrations.py`): runs, versions, attempts, events, artifacts, eval results and daily spend (migration 1), run params and titles (2), research, sources, research grants, run outcomes and per-version strategy outcomes (3), Tavily credits (4), per-session spend (5), chat threads and their turns (6). Each run also has a folder of write-once files (prompts, answers, tool logs, Verilog, artifacts). Research is cached for 30 days by a family-and-knobs key. A new run reads research by its id, then the exact key, then the nearest knobs, and past outcomes only from runs with the same pack hash and tool image. Only technique ids from the fixed list (`backend/verity/research/techniques.py`) and numbers written by code reach Nemotron. The store keeps a hash of the user's sentence, never the sentence.

## 4. Run

`spec, plan, lock, v1 (three drafts in parallel, lint, simulate, fix, synthesize), improve (up to 2 rounds, at most 6 versions), winner, prove (EQY vs v1), human reference, done`. Every new version comes from a passing one. The winner is the best measured fitness against our own v1; v1 can win. Fast mode stops after v1. A version that fails its tests gets a waveform of its first failing case, and its repair prompt carries a cycle table around the first mismatch; the optimizer sees cell types and the critical path.

## 5. Sign-off

After `run done` and the Done caption, every run (fast and full) checks the winner again: the locked tests on 8 more seeds, lint by tag and latches, the proofs against v1 and against the human design, test strength from up to 20 bugs planted by seven fixed text rules (no model), and a waveform of the winner's first directed case. It never delays the result; the run slot stays held until it ends. The stream stays open for it: the browser reconnects with `Last-Event-ID` until the server answers 204. A stopped run has no sheet.

## 6. Chat: the fact check, then the agent

`POST /threads/{id}/messages` serves every bench message. A thread is unchecked until its first chip request: each message runs the fact check of section 1 first. A question gets an answer and no verdict, and "Not a chip" leaves the thread unchecked. After the first chip request the thread is checked and never fact-checked again. From then on one agent (GLM 5.3, the `chat` route) acts like a senior RTL and ASIC engineer and picks from ten fixed tools (`verity/ask/agent_tools.py`): `list_blocks`, `research`, `run_facts`, `walkthrough`, `propose_run`, `open_tab`, `draft_custom_block`, `propose_cpu_job`, `propose_cpu_retry` and `propose_cpu_upgrade`, at most 4 tool steps and 5 chat calls per message. Every reply shows its steps as fixed code phrases. Selection, drops, timing and cost are read by code from the run, and "walk me through it" is a nine-step walkthrough built by code. The agent sees research only as checked summary sentences and source titles. A validator checks every number against the facts, a tool result, the fact check and the user's message (one retry, then the code answer), and Verilog is stripped. `propose_run` returns a chip card; a run starts only after the user presses Run or Confirm. The older `POST /runs/{id}/ask` path (ended runs only, free keyword rules, then the Lightning router, last three turns in server memory) stays.

## 7. Threads and memory

Each agent call gets a facts record that only code writes, the last 10 turns verbatim and a 20 to 40 word summary of older turns (route `memory`, GLM 5.3). Threads live in SQLite (migration 6) behind a `ThreadStore` protocol, keyed by `spend_owner(session id)`, and in the browser (`verity.threads`), so a reload or a restart keeps the thread. Since 2026-10-26, when `SUPABASE_URL` is set, Supabase Postgres keeps chats and each user's run list behind the same protocol (`backend/ARCHITECTURE.md` 17.5); sign-in is email and password with accounts made by the team (no sign-ups); the backend checks tokens with the project's JWKS; run files, events and spend ledgers stay in Verity's database and on disk. Custom blocks, where GLM 5.3 drafts a golden model that only the user's Approve locks, are in `backend/ARCHITECTURE.md` 17.6.

## CPU Lab (RV32I)

A CPU job builds a small RV32I core from seven block runs: the immediate generator, instruction decoder, ALU, branch comparator, load/store unit, register file and control FSM (`backend/verity/runs/cpu_job.py`). Each block is a normal chip run against its own locked golden model (`backend/tasks/rv32i_*`), with research first, and a block keeps a smaller version only when Yosys EQY proves it equal to its own v1. Nemotron then writes the core that joins the seven winners (`backend/tasks/rv32i_core`); the core runs real RV32I programs and the riscv-formal checks in the sandbox image. From a passed CPU v1 the board offers upgrades (`backend/verity/cpu/upgrades.py`), a 2-stage pipeline and a 16-word cache, and each starts only after the user confirms its spend. Every number on the board comes from the block and core run records.

## Who writes what

| Job | Route | Model |
|---|---|---|
| Plan the design | planner | Nemotron 3 Ultra |
| Write and fix the Verilog | rtl_gen, repair | Nemotron 3 Ultra |
| Make smaller or faster versions | optimizer | Nemotron 3 Super |
| Sort a chat message on the ask path after a run | router | Nemotron 3.5 Lightning |
| Read the first sentence, run the agent loop, word chat answers, summarise research, summarise older turns | parser, chat, research, memory | GLM 5.3 |

Nemotron writes the plan and the logic of every design. GLM never writes Verilog. Routes live in `backend/config.toml`; `GET /health` lists the model of each role.

## The bench (frontend)

`/bench` is a chat workspace: a sidebar, the chat, and a right panel with Progress, Result, Versions, Verilog and Architecture tabs. The chat is the control; the run data is the proof. Selecting a version puts a removable chip in the composer and opens a tab about it. The Architecture tab shows the engineer's steps and the real circuit, with Expand for a big view. A verdict, a step and a sign-off line are each a shape plus a word. Every number comes from run data and is stated against our own v1; the one labelled exception is the human reference. `/studio` plays recorded runs; `/try` forwards to `/bench`.

## Trust boundaries

- Secrets (Nebius, Tavily and Serper keys) live only in `backend/.env`; so does the Supabase secret key. The frontend holds only public URLs and the Supabase URL and publishable key, which Supabase designs to be public.
- The browser is untrusted: the server validates knobs and enforces per-run and daily money caps, rate limits and access codes.
- Pack files (`backend/tasks/<id>/`: `golden.py`, `test_dut.py`, `reference.v`) are hashed and re-checked before every tool call. More seeds and planted bugs reuse the locked tests, so hashes never move.
- LLM output is untrusted: code decides what passes, what is shown as a number, what a step label says, and what starts a run. The agent cannot start a run.

## Where things are

| Need | Read |
|---|---|
| Backend design, roles, tools, budgets | `backend/ARCHITECTURE.md` (17.1 to 17.4 for the chat, research, sign-off, and the agent with its threads and memory) |
| Exact request, event and artifact shapes | `backend/CONTRACTS.md` |
| Frontend stack, reducer, store, bench | `frontend/ARCHITECTURE.md` |
| Colors and tokens | `frontend/src/app/globals.css` |
| The locked golden models and their self-test | `backend/tasks/` (hashed packs), `backend/tasks/self_test.md` |
