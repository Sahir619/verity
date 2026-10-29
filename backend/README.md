# Verity agent (backend)

The backend of Verity: a plain-English chip request goes in; the backend fact-checks it, researches it, and runs an
engineer's loop in which Nemotron writes the Verilog, a sandbox tests it against a locked golden model, the agent fixes
its own bugs, evolves smaller and faster versions, checks the winner against v1 with Yosys EQY, and signs it off. Every
step streams to the web app as events. After a run, the chat endpoint answers questions about it from the run's own
data. Python 3.11+ (3.13 in use), FastAPI, pydantic v2, SQLite, one process.

> **Status (2026-10-29).** `pytest -q` passes 1392 tests with 44 skipped when Docker is not running (the sandbox and
> tool tests skip themselves); with Docker and `verity-tools:0.2` the full suite passed 1436 tests. Built: knob families
> and the human reference, per-role model routes, the fact check and live research with Tavily and Serper, the
> knowledge store, the chat agent, the sign-off, the CPU Lab and its upgrades. The Nebius Sandboxes runner is wired for
> the hosted backend and waits on project permissions; locally the tools run in Docker.

Specs: `ARCHITECTURE.md` (design and intent; 17.1 to 17.3 for the chat, research and sign-off), `CONTRACTS.md` (exact
interfaces; wins on interfaces), `sandbox/README.md` (tool image and licences), `tasks/self_test.md` (pack self-test).
Frontend contract: `../frontend/src/lib/contracts/` and `../frontend/ARCHITECTURE.md`.

## Layout

| Folder | What it holds |
|---|---|
| `verity/contracts/` | pydantic mirror of the web event types (camelCase on the wire), API bodies, sign-off artifacts |
| `verity/core/` | `Rtl`, `TaskPack`, knob families, the first-message parse, fitness, captions, budget, planted-bug rules (`mutants.py`), errors |
| `verity/llm/` | providers (`nebius`, `openai_compat`, `fake`), cassette wrapper, per-run gateway `RunLLM` |
| `verity/roles/` | planner, rtl_gen, repair, optimizer roles and `prompts/*.md` (planner v2, rtl_gen v2, repair v3, optimize v3 in use) |
| `verity/research/` | search adapters (Tavily, Serper, routed, fake), code-built queries, source tiers, the GLM summary, the fixed technique list, run outcomes |
| `verity/ask/` | `POST /runs/{id}/ask`: intent router, code templates, the walkthrough, proposals |
| `verity/tools/` | `LocalDockerRunner`, `NebiusSandboxRunner` (unverified), result parsers, circuit graph builder |
| `verity/orchestrator/` | the deterministic state machine (spec, plan, gate, lock, build, evolve, prove, human reference, sign-off, waveforms) |
| `verity/runs/` | `RunManager`, event hub (stamps seq, store first, then fan-out), ledger, watchdog, run log |
| `verity/store/` | SQLite store (migrations 1 to 7), write-once artifact files under `data/`, and the Supabase store for chats and run lists (`supabase_threads.py`; tables in `supabase/schema.sql`) |
| `verity/api/` | FastAPI app, routes, hand-rolled SSE, auth tokens, rate limits, log redaction |
| `verity/evals/` | evals CLI, harness, bug injectors, report generator |
| `sandbox/` | the `verity-tools:0.2` image: Dockerfile and `/verity/bin/{verify,measure,eqy,seeds,strength,wave,bundle,bench,rvfi}` |
| `tasks/` | task packs: `mac4`, `add8`, `cnt8`, `fsm_seq` (live knob families), `alu8`, `matmul4` (accepted by `POST /runs`, not offered in the chat), `mm4_int4` (eval only) |
| `scripts/` | `probe_models.py`, the capped probe that chose the model routes |
| `tests/` | pytest, one folder per package plus `e2e`, `packs` and `fixtures` |
| `evals/` | `gate-2026-10-16.md` and `probe-2026-10-18.md` (real-model evidence); `runs/` (the exported CPU job and live run records behind the numbers in the root README); generated `results/`, `report.md`, `work/` |

## Setup

Needs Python 3.11 or newer, and Docker Desktop for the real sandbox. From the repo root:

```
cd backend
python -m venv .venv
.venv\Scripts\activate                 # Windows;  source .venv/bin/activate on Linux or macOS
pip install -e ".[dev]"
docker build -t verity-tools:0.2 --build-context bench=bench -f sandbox/Dockerfile sandbox   # 10 to 20 minutes the first time
python tasks/make_manifest.py --check                               # every pack must print ok
```

The build downloads tools and the Nangate45 liberty (never committed). See `sandbox/README.md` for pins and licences.
After a change to `sandbox/`, rebuild the image and run the pack self-test again (`tasks/self_test.md`).

## Run

```
verity-api                              # same as: python -m verity.main
```

Listens on `127.0.0.1:8000` (`[server]` in `config.toml`), one worker only (rate limits, run registry and the SSE fan-out
live in process). With the default `VERITY_PROVIDER=fake` no model call is made and research uses canned sources; the
tools still use the real Docker image. Real models: `VERITY_PROVIDER=nebius` and `LLM_API_KEY` in `backend/.env`.

Web side: set `NEXT_PUBLIC_AGENT_URL=http://127.0.0.1:8000` in `frontend/.env.local`. The default CORS origin is
`http://localhost:3000`.

| Method and path | Auth when `auth.required` | Notes |
|---|---|---|
| `GET /health` | none | `liveEnabled`, `reason` (maintenance, sandbox_down, budget_exhausted, busy), `capUsd`, `provider`, `models` per role, `authRequired`, `features` |
| `POST /auth/session` | none | `{accessCode}` gives `{token, expiresAt}` |
| `GET /runs` | none | the public list of runs (`limit` up to 100, `offset`) |
| `POST /runs` | `Authorization: Bearer` | `{presetId, priority, mode?, params?, researchId?}` gives `{runId, capUsd, streamToken}`; 429 `{error, reason}` |
| `POST /parse` | Bearer | the fact check: verdict, class, family and knobs, `statedKnobs`, research (CONTRACTS section 9) |
| `POST /runs/{id}/ask` | Bearer, owner | the chat after a run: answers, walkthrough steps, proposals; ended runs only |
| `POST /runs/{id}/stream-token` | Bearer, owner | `{streamToken}`: a fresh stream token for a run this session started, for a second device or tab |
| `GET /runs/{id}/events` | `?st=<streamToken>` | SSE, `Last-Event-ID` or `?after=` resumes; a finished run replays without a token; 204 when nothing is left |
| `GET /runs/{id}/log` | owner while running, public when finished | `RunLog` (same format as `golden.json`) |
| `POST /runs/{id}/steer` | Bearer, owner | pin, toggle, gate, autopilot, winner_override; 202 |
| `POST /runs/{id}/stop` | Bearer, owner | 202, then a `run` event with phase `stopped` |

## Environment variables (names only)

`backend/.env.example` lists the names. Put values in `backend/.env` (gitignored) or the shell. Precedence: process env, then `backend/.env`,
then `config.toml`. Exact mapping: `CONTRACTS.md` section 10.

| Name | Meaning |
|---|---|
| `VERITY_ENV` | `development` (default), `production` (enforces the submission guard), `test` |
| `VERITY_PROVIDER` | `fake` (default, $0), `nebius`, `openai_compat` |
| `VERITY_TOOL_BACKEND` | `local_docker` (default) or `nebius_sandbox` (unverified) |
| `VERITY_CONFIG` | path of another `config.toml` |
| `VERITY_DATA_DIR`, `VERITY_TASKS_DIR` | paths, relative to `backend/` |
| `VERITY_CORS_ORIGINS` | comma separated origins, never `*` in production |
| `VERITY_ACCESS_CODE_HASHES` | `role:sha256hex,role:sha256hex` (sha256 of each access code) |
| `VERITY_SIGNING_SECRET` | 32+ random characters, required in production; outside production a stored secret in `data/signing-secret` is used when unset |
| `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL_ULTRA`, `LLM_MODEL_SUPER` | Nebius Token Factory (OpenAI compatible) |
| `TAVILY_API_KEY`, `SERPER_API_KEY` | Research: Tavily for general web search, Serper for Google Scholar and Patents. With one key that service serves all three; with none, research is off on a real provider. Server side only |
| `SANDBOX_BASE_URL`, `SANDBOX_API_KEY` | Nebius Sandboxes (key falls back to `LLM_API_KEY`; unverified) |
| `SUPABASE_URL`, `SUPABASE_SECRET_KEY` | Accounts and saved chats (see below). The secret key is server side only. `SUPABASE_PUBLISHABLE_KEY` is optional: only for a project that still signs tokens with the shared secret |
| `VERITY_<TABLE>__<KEY>` | any `config.toml` value, for example `VERITY_AUTH__REQUIRED=true`, `VERITY_SERVER__PORT=8021`, `VERITY_BUDGET__RUN__MAX_USD=0.3` |

`VERITY_ENV=production` refuses to start unless: provider is `nebius`, the base URL host is the Token Factory host,
`LLM_API_KEY` is set, `auth.required` is true, the signing secret is 32+ characters, sign-in exists (`SUPABASE_URL` is
set, or at least one access code hash is set; either is enough), and CORS has no `*`. When `SUPABASE_URL` is set its
host must be https and end with `.supabase.co`. Never put a secret in `frontend/` or in any `NEXT_PUBLIC_*` variable
(the one public Supabase value, the publishable key, belongs to `frontend/` only).

## Accounts and saved chats (Supabase)

Optional. Without it nothing changes: chats stay in SQLite, and the access code is the only sign-in. With it, judges
sign in with an email and password (no sign-up form: the team makes the accounts), their chats and their list of runs
follow them across devices, and run files, events and spend stay on this server. Never use the Supabase CLI or a linked
project; the project is made in the dashboard.

1. Make a new Supabase project. In its Authentication settings (sign-in providers), turn **off** "Allow new users to
   sign up" and leave "Allow anonymous sign-ins" **off**. Leave "Confirm email" **on**: step 3 confirms each account
   by hand, and if the sign-up switch is ever missed, a stranger who signs up with the public publishable key then gets
   no session.
2. Open the project's SQL Editor, paste all of `backend/supabase/schema.sql` and run it. It is safe to run again. It
   makes `threads`, `thread_turns` and `user_runs` with row level security on, no policies, and no grants for the
   browser roles.
3. Add each judge in Authentication, Users: "Add user", email and password, "Auto Confirm User" on. Hand the pair over.
4. Put the values in `backend/.env` (never the secret key anywhere else), then restart the backend:
   `SUPABASE_URL` (the Project URL, `https://<ref>.supabase.co`) and `SUPABASE_SECRET_KEY` (the `sb_secret_...` key
   from API Keys). `SUPABASE_PUBLISHABLE_KEY` is only needed when the project's JWKS is empty (a legacy project).
   `GET /health` then lists `accounts` in `features`; the chats move to Supabase because `[threads] store = "auto"`
   sees both values (`VERITY_THREADS__STORE=sqlite` keeps them local).
5. In `frontend/`, set `NEXT_PUBLIC_SUPABASE_URL` and `NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY` (public by design) and
   rebuild; with neither set the web app has no sign-in gate.

A user's session id is `u:<user id>`; a Supabase outage turns every chat and run-list call into
`502 "Saved chats are unreachable right now."`. The one chat store serves every session, so this includes access-code
sessions: their bench chat stops too. Starting a run, runs already going, replays and the fact check keep working.

## Tests

```
pytest -q                               # from backend/
```

Most tests use the fake provider, a fake tool runner and stub services. The tool, pack and edge tests run the real
`verity-tools:0.2` image and need Docker Desktop running. Folders: `contracts`, `core`, `orchestrator`, `runs`, `e2e`,
`llm`, `roles`, `research`, `ask`, `tools`, `store`, `api`, `packs`, `evals`; `fixtures` holds a recorded real run used
by the ask tests. `tests/contracts/test_web_validate_fixture.py` shells out to
`node frontend/scripts/validate-fixture.ts <path>` and skips itself when node or `frontend/node_modules` is missing.

## Evals

```
python -m verity.evals --suite <packs|functional|repair|optimization|regression|performance|isolation|all> \
    --tasks all --provider fake|nebius --repeats N
python -m verity.evals --report-only
```

Writes `evals/results/<eval_run_id>.jsonl` (one line per task per repeat, fields of ARCHITECTURE 14.4) and regenerates
`evals/report.md`. `--provider fake` costs $0 and tests the pipeline, not the model; `--provider nebius` spends credits
(`--max-total-usd`, default 3). `--skip-sandbox` skips every step that needs the tool image. Exit code 1 when a
self-check suite (packs, isolation, regression) has a failing row.

Per-call records (R3): every model call is logged with `finish_reason` and reasoning tokens in `final/llm-calls.json`
of the run folder, in `meta` of each `attempt-<n>.llm.json`, and in the eval rows (`reasoning_tokens`, `finish_reasons`;
the performance report prints them). `finish_reason` `length` means the call was cut at its output cap.

Nebius facts measured on 2026-10-16 (one tiny prompt per setting, so token counts are indicative only):
`completion_tokens` already includes `reasoning_tokens`. Both models honour `chat_template_kwargs.enable_thinking: false`
(reasoning 0). Super also honours `reasoning_effort` `none` (0) and `low` (150 down to 9) and `low_effort` (150 down to 8).
Ultra's `reasoning_effort: low` changed nothing against its default (49 both), and `medium_effort` reached the template
(prompt tokens +7) but did not lower reasoning on that prompt. None of these is enabled in the config.

| Suite | Needs | What it answers |
|---|---|---|
| `packs` | sandbox | reference passes, every mutant in `tasks/self_test.md` fails, EQY sanity |
| `isolation` | nothing (width lock needs the sandbox) | banned RTL rejected before any sandbox, reference never leaves the pack, no leak into the brief |
| `functional` | LLM, sandbox | pass@1: spec to first RTL, no repair |
| `repair` | LLM, sandbox | injected bugs (wrong width, off by one, missing reset, swapped operands) repaired within N attempts |
| `optimization` | LLM, sandbox | best fitness vs v1, area and clock gain, share improved, winner proven |
| `regression` | after a run | winner vs v1: EQY and the locked tests re-run, pack hash unchanged |
| `performance` | after a run | calls, tokens, estimated dollars, wall time, time per stage |

Gains are relative to our own v1, never to published numbers.

## Known limits

- EQY cannot prove a shift-add multiplier equal to `*` within its 180 s limit, so such a winner is "Tested, not proven".
  At 8 lanes, mac4 inputs wider than 8 bits come back inconclusive (`evals/probe-2026-10-18.md`).
- The heavy packs (`matmul4`, `mm4_int4`) take about 5 minutes in the packs suite because both EQY sanity calls run to
  their limit.
- Some code comments are still marked `UNVERIFIED` (grep for it): chiefly the Nebius Sandboxes runner, Yosys and EQY
  output details, and licence rows in `sandbox/README.md` that still need checking. Trust the code comments
  over any claim here.
