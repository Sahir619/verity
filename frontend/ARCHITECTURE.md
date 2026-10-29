# Verity architecture (frontend focus)

How the Verity web app is built and how it talks to the agent backend. Written 2026-10-04 as the build plan; the app and the backend have been built since, and this file is kept current for the pages (`/`, `/studio`, `/bench`, `/results`), the store and the bench (section 9).

Status of claims: library APIs below were checked against current docs via Context7 and versions via `npm view` on 2026-10-04. Anything not checked is marked **(unverified)**. The backend API is built; section 10 summarises it and `../backend/CONTRACTS.md` section 9 has the exact shapes.

---

## 1. System overview

```
                     replay path ($0, no network after the run file loads)
 +------------------+   GET /runs/*.json (static files)        +-----------------------+
 | Browser          | <----------------------------------------| frontend/ static      |
 | frontend/        |                                          | export (any host)     |
 | (Next.js static  |                                          +-----------------------+
 |  export)         |   live path (knob families, capped)
 |  one reducer  <--+-- SSE  GET  {AGENT_URL}/runs/{id}/events +-----------------------+
 |  for both paths  |-- REST POST /parse, /runs, /runs/{id}/ask| backend/ FastAPI      |
 +------------------+         /steer, /stop; GET /health, /log |  - Nemotron and GLM   |
                                                               |    5.3 on Nebius      |
                                                               |    Token Factory      |
                                                               |  - Tavily and Serper  |
                                                               |    research           |
                                                               |  - Docker sandbox:    |
                                                               |    Verilator, Icarus, |
                                                               |    cocotb, Yosys,     |
                                                               |    OpenSTA, Yosys EQY |
                                                               |  - golden model and   |
                                                               |    tests outside the  |
                                                               |    agent's write path |
                                                               +-----------------------+
```

- `frontend/`: the Next.js app. Builds to static files. Holds no secrets.
- `backend/`: the FastAPI agent (`../backend/README.md`). Owns model calls, research, sandbox runs, the locked golden model, fitness computation, money caps and the canonical event stream. API keys live only in `backend/.env` (`LLM_API_KEY`, `TAVILY_API_KEY`, `SERPER_API_KEY`).
- The golden run is a recorded event log (`frontend/public/runs/golden.json`) played through the same reducer as live events. Replay never calls the agent.

## 2. Frontend stack (verified 2026-10-04)

| Package | Version at plan time | License | How it was checked |
|---|---|---|---|
| `next` (via `create-next-app@16.3.8`) | 16.3.8 | MIT | `npm view`; App Router, static export, `next/font`, `next/dynamic` docs (Context7 `/vercel/next.js/v16.2.9`) |
| `react`, `react-dom` | 19.x (whatever create-next-app installs; npm latest 19.3.0) | MIT | `npm view`; next peer range `^19.0.0` |
| `typescript` | `^5` (keep create-next-app's pin; npm `latest` is 7.0.2, do not upgrade) | Apache-2.0 | create-next-app 16.3.8 source |
| `eslint` + `eslint-config-next` | `^9` + 16.3.8 (keep pins) | MIT | create-next-app source; Next 16 removed `next lint` |
| `tailwindcss`, `@tailwindcss/postcss` | 4.3.3 | MIT | Context7 `/websites/tailwindcss` (`@theme`, `@theme inline`, namespaces, `--text-*--line-height`) |
| `shadcn` (CLI) | 4.21.1 | MIT | Context7 `/shadcn-ui/ui` (Tailwind v4 globals.css, `init`, `add`) |
| `tw-animate-css` | 1.4.0 (installed by shadcn init) | MIT | shadcn docs |
| `@xyflow/react` | 12.12.0 | MIT | Context7 `/websites/reactflow_dev` (custom nodes, `NodeProps`, `Handle`, `updateNodeData`, Zustand pattern, dagre example, SSR notes, theming vars, a11y props, `FitViewOptions`) |
| `zustand` | 5.0.15 | MIT | Context7 `/pmndrs/zustand/v5.0.12` (slices pattern, `useShallow`) |
| `@dagrejs/dagre` | 3.1.1 (ships types) | MIT | `npm view`; React Flow dagre example |
| `zod` | 4.6.5 | MIT | Context7 `/colinhacks/zod/v4.3.6` (`discriminatedUnion`, `safeParse`, `z.infer`) |
| `lucide-react` | 1.51.0 | ISC | type exports grepped for every icon in the brand icon set |
| `diff` (jsdiff) | 9.0.0 (ships types) | BSD-3-Clause | Context7 `/kpdecker/jsdiff` (`diffLines`) |
| `jszip` | 3.10.2 | MIT OR GPL-3.0-or-later (we use it under MIT) | Context7 `/stuk/jszip` (`file`, `folder`, `generateAsync({type:"blob"})`) |

Fonts: `Sofia_Sans_Extra_Condensed` (display, weights 800 and 900), `Sofia_Sans` (body) and `Martian_Mono` (data and labels) from `next/font/google`, declared in `components/sections/shared/rack-fonts.ts` as `RACK_FACES` (CSS variables `--font-rk-display`, `--font-rk-body`, `--font-rk-mono`, mapped in `globals.css` to `--font-display`, `--font-body`, `--font-data`). Atkinson, Archivo and JetBrains Mono are no longer used.

Not used, on purpose: no chart library (2 to 4 bars are plain SVG), no syntax-highlighter package (a 40-line Verilog tokenizer in `lib/verilog/`), no FileSaver (download via object URL), no tldraw (license key), no elkjs (EPL/GPL; dagre is enough).

Node on the dev machine: v24.13.1, npm 11.8.0.

## 3. Routing and the static export decision

**Decision: `output: "export"`, client-rendered app shell, no server runtime.** Every page works as static HTML plus client JS, so the demo can be hosted for free anywhere and the replay path has no server to fail.

| Route | File | Notes |
|---|---|---|
| `/` | `app/page.tsx` | Landing. Auto-plays the golden run in the hero. |
| `/studio` | `app/studio/page.tsx` | No `run`: the CPU run video (`sections/studio-video/`, data from `public/studio/manifest.json`). `?run=<id>`: the rack replay (`mac4-real`, `golden` or a live run id). |
| `/bench` | `app/bench/page.tsx` | The live chat workspace (section 9 below). `?run=<id>` reopens a run. |
| `/workspace` | `app/workspace/page.tsx` | Home, behind the sidebar's Home link. The runs started in this browser, newest first, with totals, family filter, two charts and a Combine preview (section 9, Workspace specifics). Same frame and sidebar as `/bench`; no query params. |
| `/signin` | `app/signin/page.tsx` | Email and password sign-in for the live bench (section 8.4). `?next=` is the path to return to. No sign-up. |
| `/try` | `app/try/page.tsx` | Forwards to `/bench` on the client (a static export has no server redirects). |
| `/results` | `app/results/page.tsx` | Summary, ranking, winner override, zip export. `?run=` as above. |
| 404 | `app/not-found.tsx` | Exported as `404.html`. |

Why query params and not `/studio/[runId]`: static export does not support dynamic segments without `generateStaticParams` (and live run ids are unknown at build time). Docs confirmed: dynamic routes with `dynamicParams: true` or without `generateStaticParams()`, route handlers using `Request`, server actions, rewrites, redirects, headers, ISR and default-loader image optimization are all unsupported in export mode.

Consequences the builders must respect:
- Pages that read `useSearchParams` wrap that client component in `<Suspense>` (otherwise `next build` fails with "Missing Suspense boundary with useSearchParams").
- No route handlers, no server actions, no middleware/proxy, no `cookies()`/`headers()`.
- `next.config.ts`:

```ts
import type { NextConfig } from "next";
const nextConfig: NextConfig = {
  output: "export",
  trailingSlash: true,          // emits /studio/index.html: works on plain static hosts
  images: { unoptimized: true },
};
export default nextConfig;
```

- Since 2026-10-15 /studio uses no canvas library. Since 2026-10-16 it is a rack of modules (TRANSPORT, LOOP, MONITOR, VERSIONS) in plain CSS grid, with no SVG wire layer (`components/sections/studio/`). It renders on the client like every studio section.
- The site is hosted on Vercel. The static `out/` folder also works on any static host.

## 4. `frontend/` folder structure

Ownership tags refer to the build plan tasks: **S** scaffold, **A** domain components, **B** canvas and state, **C** pages and sections, **I** integrate.

```
frontend/
  package.json  next.config.ts  tsconfig.json  eslint.config.mjs   (S)
  postcss.config.mjs  components.json  .env.example  README.md     (S)
  scripts/validate-fixture.ts                                       (S)
  public/
    runs/golden.json            sample golden run (S)
    og.png                      copy of Verity_AI_Hardware_Verification.png (S)
  src/
    app/
      layout.tsx  globals.css                                       (S)
      page.tsx                         landing                      (C)
      studio/page.tsx  bench/page.tsx  workspace/page.tsx  try/page.tsx  results/page.tsx  not-found.tsx (C)
    components/
      ui/            shadcn primitives (generated)                  (S)
      brand/         Wordmark, PoweredBy                            (A)
      domain/        presentational, props-only components          (A)
      sections/      rack/* (landing), landing/links.ts, studio/*, bench/*, workspace/*, results/*, shared/*  (C)
                     rack/: RackPage, RackNav, Inside, RackFooter, Ears (studio only), icons, rack.css; landing/: Hero, Problem, LoopSection, LoopVideo, Art, CpuToday, Ladder, DemoVideo, cpu-real.ts, landing.css
                     studio/: chrome/ (Mod, Seg7, StudioBand), loop/ (LoopModule, derive-leds), timeline/ (TimelineBar, Narration),
                              detail/ (DetailPanel, follow, CandidateDetail, ItemDetails), table/ (CharacteristicsTable),
                              figure/ (derive.ts, proof.ts only)
                     bench/: AppShell, Sidebar, ChatThread, AgentTurn, Composer, ChipCard, Research, AskTurns, OutputPanel, ProgressTab,
                             ResultModule, RtlModule, VersionTab, ArchitectureTab, CircuitView, Signoff, Waveform, AccessCode,
                             ExamplePrompts, chat.ts, rail.ts, artifacts.ts, derive.ts, bench.css (section 9)
                     workspace/: WorkspaceView, RunCard, Charts (DeltaChart, SpendChart), BarRows, CombinePreview, derive.ts, workspace.css
                     shared/: RackBar + rack-bar.css, AppHeader, rack-fonts.ts, app-roots.ts, datasheet-base.css, app-pages.css, VignetteGrid
    lib/
      utils.ts       cn() from shadcn                               (S)
      env.ts         AGENT_URL, isLiveConfigured                    (S)
      format.ts      number, delta, unit, time formatters           (S)
      contracts/     types.ts, view-types.ts, schemas.ts,           (S)
                     stages.ts, presets.ts, research.ts, ask.ts, agent.ts
      verilog/       highlight.ts                                   (A)
      run/           reducer.ts, fitness.ts, selectors.ts           (B)
      canvas/        layout.ts, zones.ts, visibility.ts (node data the reducer still builds for the studio views) (B)
      sources/       replay.ts, live.ts                             (B)
      store/         index.ts, slices/*.ts                          (B)
      hooks/         use-replay-driver.ts, use-live-run.ts,         (B)
                     use-live-health.ts, use-live-session.ts, use-countdown.ts,
                     use-reduced-motion.ts, use-run-views.ts,
                     use-recent-runs.ts, use-threads.ts, use-runs-list.ts, use-media-query.ts
      export/        zip.ts                                         (B)
```

Rule: a module may import from layers below it only (section 9). `components/domain` never imports the store; `components/sections` is where store hooks meet presentational components.

## 5. Data contracts

File: `src/lib/contracts/types.ts` (S writes it verbatim). `schemas.ts` mirrors every type with zod (`z.discriminatedUnion("t", [...])` for `RunEvent`, `z.discriminatedUnion("op", [...])` for `CanvasOp`) and exports `RunLogSchema` and `RunEventSchema`. `schemas.ts` imports only `zod` (no path aliases) so the Node fixture validator can load it. Avoid `z.record`; use arrays.

```ts
// ---------- enums ----------
export type Priority = "area" | "speed" | "power";
export type Operator = "FIX" | "SIMPLIFY" | "OPTIMIZE" | "EXPLORE";
/** What created a candidate. FIX never creates a candidate: it repairs one in place (an Attempt). */
export type CreatedBy = "INIT" | "SIMPLIFY" | "OPTIMIZE" | "EXPLORE";
export type StageId = "spec" | "plan" | "lock" | "rtl" | "lint" | "sim" | "fix" | "synth" | "evolve" | "prove";
export type StageStatus = "pending" | "active" | "done" | "failed" | "skipped";
export type CandidateStatus =
  | "drafting" | "linting" | "simulating" | "fail" | "fixing"
  | "pass" | "synthesizing" | "measured" | "rejected";
export type RunMode = "replay" | "live";
export type RunPhase = "idle" | "starting" | "running" | "done" | "error" | "cap_reached" | "stopped";
export type ZoneId = "plan" | "tree";
export type NodeKind = "spec" | "block" | "golden" | "candidate";
export type EdgeKind = "plan" | "lineage" | "checks" | "proof";
export type Actor = "ai" | "user" | "system";

// ---------- spec and plan ----------
export interface Port { name: string; dir: "in" | "out"; width: number; count?: number }
export interface Spec {
  presetId: string; title: string; text: string; ports: Port[];
  priority: Priority; testCount: number; maxLines: number;
}
export interface Variant { id: string; label: string; options: string[]; value: string }
export interface PlanBlock {
  id: string; name: string; reason: string;
  pinOptions: string[]; pins: string[]; variants: Variant[];
}
export interface PlanDiff { added: string[]; changed: string[]; removed: string[] }

// ---------- verification ----------
export interface TestCase {
  name: string; inputs: string; expected: string; got: string; pass: boolean; cycle?: number;
}
export interface Verification {
  lint: "pass" | "fail"; lintLog?: string;
  passed: number; total: number;
  failures: TestCase[];          // every failing case (expected vs got)
  diagnosis?: string;            // agent's one-paragraph reading of the failures
  tool: string;                  // e.g. "iverilog 12 + cocotb"
  durationMs: number;
}
export interface Attempt {
  n: number;                     // 1-based
  by: CreatedBy | "FIX";         // attempt 1 = creating operator, later attempts = FIX
  reason: string;                // what changed and why
  rtl: string;
  verification: Verification;
}

// ---------- PPA, fitness, proof ----------
export interface PPA {
  areaUm2: number | null; cellCount: number | null;
  clockPeriodNs: number | null; powerMw: number | null;   // null = not measured
  tools: string[];               // e.g. ["Yosys 0.x", "OpenSTA"]
  pdk: string;                   // e.g. "Nangate45"
}
export interface FitnessWeights { area: number; timing: number; power: number }
export interface Fitness {
  score: number;                 // weighted gain, see section 8.3
  priority: Priority; weights: FitnessWeights;
  gains: { area: number | null; timing: number | null; power: number | null }; // percent, positive = better
  baselineId: string;
}
export interface Equivalence {
  status: "proven" | "not_equivalent" | "inconclusive" | "not_run";
  againstId: string; tool: "yosys-eqy"; log?: string;
}

// ---------- candidate: the REvolution-lite record ----------
export interface Candidate {
  id: string;                    // "c5"
  label: string;                 // "v5"
  round: number;                 // 0 = initial build
  parentId: string | null;
  createdBy: CreatedBy;
  reason: string;                // generation / change reason
  rtl: string;                   // current RTL = last attempt's RTL
  attempts: Attempt[];
  verification: Verification | null;   // = last attempt's verification
  ppa: PPA | null;               // only after PASS + synthesis
  fitness: Fitness | null;       // null for FAIL, rejected, or baseline-less
  status: CandidateStatus;
  fixCount: number;
  isBaseline: boolean; isElite: boolean;
  eqy?: Equivalence;
}

// ---------- human inputs and credits ----------
export type HumanInput =
  | { kind: "spec"; tMs: number; presetId: string; priority: Priority }
  | { kind: "pin"; tMs: number; blockId: string; pin: string }
  | { kind: "toggle"; tMs: number; blockId: string; variantId: string; value: string }
  | { kind: "winner_override"; tMs: number; candidateId: string; local?: boolean }
  | { kind: "stop"; tMs: number };
export interface Credits { usedUsd: number; capUsd: number | null }

// ---------- canvas ops: the closed set the AI may emit ----------
export type CanvasOp =
  | { op: "add_node"; id: string; kind: NodeKind; zone: ZoneId; label: string; refId?: string; near?: string }
  | { op: "connect"; source: string; target: string; kind: EdgeKind; label?: string }
  | { op: "move_node"; id: string; x: number; y: number }
  | { op: "update_status"; id: string; status: string; note?: string }
  | { op: "remove"; id: string };

// ---------- the event stream (live SSE and recorded log share this) ----------
interface EventBase { seq: number; tMs: number }
export type RunEvent = EventBase & (
  | { t: "run"; phase: RunPhase; message?: string }
  | { t: "stage"; stage: StageId; status: StageStatus; detail?: string; round?: number }
  | { t: "op"; by: Actor; op: CanvasOp }
  | { t: "spec"; spec: Spec }
  | { t: "plan"; revision: number; blocks: PlanBlock[]; diff?: PlanDiff; reason?: string }
  | { t: "gate"; state: "open" | "held" | "closed"; autoContinueMs: number }
  | { t: "candidate"; candidate: Candidate }        // upsert the full record
  | { t: "round"; round: number; parents: string[]; elites?: string[] }
  | { t: "winner"; candidateId: string; by: "ai" | "user"; aiPickId: string }
  | { t: "human"; input: HumanInput }
  | { t: "caption"; text: string; chapter?: string }
  | { t: "credits"; usedUsd: number; capUsd: number | null }
  | { t: "artifact"; path: string; content: string }
);

// ---------- recorded log (golden run fixture) ----------
export interface Chapter { id: string; label: string; tMs: number }
export interface RunLog {
  format: "verity.runlog/1";
  meta: {
    runId: string; sample: boolean; title: string; presetId: string;
    recordedAt: string; durationMs: number; chapters: Chapter[]; note?: string;
  };
  events: RunEvent[];            // sorted by seq; tMs non-decreasing
}

// ---------- view-side state (store) ----------
// A type alias, not an interface: @xyflow/react 12.12.0 declares
// Node<NodeData extends Record<string, unknown>>, and interfaces do not satisfy that constraint.
export type NodeData = {
  kind: NodeKind | "zone"; refId: string; label: string; zone: ZoneId;
  status: string; origin: "ai" | "user";
  diff?: "new" | "changed"; diffAtMs?: number; note?: string;
};
export interface RunState {
  runId: string; mode: RunMode; sample: boolean; title: string; phase: RunPhase;
  spec: Spec | null;
  plan: { revision: number; blocks: PlanBlock[]; lastDiff: PlanDiff | null; reason?: string };
  stages: Record<StageId, { status: StageStatus; detail?: string; round?: number }>;
  candidates: Record<string, Candidate>; candidateOrder: string[];
  round: number; elites: string[];
  baselineId: string | null; winnerId: string | null; aiWinnerId: string | null; winnerBy: "ai" | "user" | null;
  gate: { state: "open" | "held" | "closed"; autoContinueMs: number; openedAtMs: number };
  humanInputs: HumanInput[]; credits: Credits;
  caption: { text: string; chapter?: string; tMs: number } | null;
  artifacts: { path: string; content: string }[];
  lastSeq: number; tMs: number;
}
```

`NodeData` and `VerityNode = Node<NodeData>` / `VerityEdge = Edge` (from `@xyflow/react`) are exported from `lib/canvas/zones.ts` by B; S only declares `NodeData` in types.ts.

UI vocabulary: code says `Candidate`; the UI says "version".

## 6. One reducer for live and replay

`src/lib/run/reducer.ts` exports the only function that turns events into state:

```ts
export interface RunSnapshot { run: RunState; nodes: VerityNode[]; edges: VerityEdge[] }
export function initialSnapshot(init: { runId: string; mode: RunMode; sample: boolean; title: string }): RunSnapshot;
export function reduce(s: RunSnapshot, e: RunEvent): RunSnapshot;   // pure, deterministic, no timers, no I/O
```

Rules inside `reduce`:
1. **`op` events** apply the closed op set. Unknown ops cannot exist (zod rejects them before the reducer).
   - `add_node`: ignore if the id exists (idempotent). New nodes get `origin: "ai"`, `diff: "new"` for plan blocks after revision 1. Position comes from layout (rule 3), never from the AI.
   - `connect`: edge id `${source}->${target}`; drop if either end is missing or the edge exists. Kinds map to styles: `plan` and `lineage` solid `--edge`, `checks` dotted `--edge`, `proof` dashed `--edge-proof` with its label.
   - `move_node`: applied only when `by !== "ai"` or the node's `origin === "ai"`. **Once a user moves a node, the AI may update its data but never its position.**
   - `update_status`: merges `status` and `note` into node data. Never touches position.
   - `remove`: deletes the node and its edges; clears selection if needed (selection lives in the UI slice, which subscribes).
2. **Entity events** (`spec`, `plan`, `candidate`, `round`, `winner`, `human`, `credits`, `caption`, `artifact`, `stage`, `gate`, `run`) update `RunState`. `candidate` is an upsert of the whole record. `plan` replaces blocks and stores `lastDiff`; blocks in `diff.changed` get `diff: "changed"` and `diffAtMs = e.tMs` on their nodes.
3. **Layout runs inside the reducer** after any topology change (`add_node`, `connect`, `remove`): `layoutPlan()` places plan-zone nodes in a fixed column, `layoutTree()` runs dagre (rankdir `TB`, nodesep 24, ranksep 56) over candidate nodes and lineage edges, then zone frames are resized. Nodes with `origin: "user"` keep their positions. Dagre is deterministic, so replay and live produce identical pictures.
4. Node sizes are fixed and set on every node (`width`, `height`) so `fitView` is stable: spec 240x112, block 240x72, golden 240x88, candidate 192x104 (React Flow SSR docs recommend explicit dimensions; we also skip SSR for the canvas).
5. Visibility budget (`lib/canvas/visibility.ts`): at most 12 content nodes visible (zone frames do not count). Over budget: hide rejected candidates oldest first, then non-elite measured candidates from earlier rounds (`hidden: true`); the tree zone header shows "+N hidden versions" and the inspector list still includes them.
6. The reducer does not check `seq`. Drivers dedupe (live) or replay in order (replay). Local user events (replay winner override) go through the same `reduce`.

Edges created by the golden run: plan chain (spec to first block, block to block), one `checks` edge (golden model to v1, label "checks every version"), `lineage` edges (parent to child), one `proof` edge (winner to v1, label "Proven equal (EQY)").

## 7. Store (Zustand, slices pattern)

Four slices: `run`, `playback`, `ui`, `live` (there is no canvas slice: the studio has drawn no canvas since 2026-10-15, and the bench circuit view keeps its own React state). `src/lib/store/index.ts` builds one module-level store with the documented slices pattern (`create<Store>()((...a) => ({ ...createRunSlice(...a), ... }))`) and components select with `useShallow`. A module-level store is safe here because the app is a static client SPA (no per-request server state). It also means the landing hero's playback position carries into `/studio` on client navigation.

| Slice | State | Actions |
|---|---|---|
| `run` | `snapshot: RunSnapshot` | `applyEvent(e)`, `applyEvents(es)`, `resetRun(init)` |
| `playback` | `source: "replay" \| "live" \| null`, `status: "loading" \| "ready" \| "playing" \| "paused" \| "ended" \| "error"`, `tMs`, `durationMs`, `speed: 1 \| 2`, `chapters`, `log: RunEvent[]`, `cursor` | `load(log)`, `play()`, `pause()`, `seek(tMs)`, `restart()`, `setSpeed()`, `tick(dtMs)` |
| `ui` | `selection: { kind: NodeKind; id: string } \| null`, `inspectorTab: "rtl" \| "diff" \| "tests" \| "ppa"`, `autopilot: boolean` (default true), `instant: boolean` (suppresses enter animations during seeks), `inspectorOpen` (narrow screens) | `select()`, `setTab()`, `setAutopilot()` |
| `live` | `connection: "idle" \| "connecting" \| "open" \| "retrying" \| "closed" \| "error"`, `runId`, `error?`, `pendingSteers`, `tailOpen` (the stream stays open after the result for the sign-off) | `startLive(presetId, priority, params?, title?, researchId?)`, `steer(req)`, `stopRun()`, `overrideWinnerLocal(id)`, `adoptLiveRun(runId)` |

Hooks (B) that sections (C) consume:
- `useRun<T>(selector)`: typed selector over `snapshot.run`.
- `useReplayDriver(opts: { src: string; autoplay: boolean; loop: boolean })`: loads and validates the log, drives `tick` with `requestAnimationFrame`, honors reduced motion (starts paused at the end when `autoplay` and reduced motion are both set).
- `useLiveRun(runId: string)`: owns the `EventSource`.
- `useCountdown(gate, tMs)`: remaining ms for `CountdownGate`.
- `useSelectedEntity()`: resolves the selection to `{ kind, spec | block | golden | candidate }`.
- `useStageViews()`, `useRunSummary()`: derived view models.
- `useReducedMotion()`.

### 7.1 Exact signatures (the contract between tasks A, B and C)

View types live in `src/lib/contracts/view-types.ts` (S writes them):

```ts
import type { Candidate, Chapter, CandidateStatus, CreatedBy, Credits, NodeKind, PlanBlock, PlanDiff,
  PPA, Priority, RunMode, RunPhase, Spec, StageId, StageStatus } from "./types";

export type VisualStatus = "idle" | "queued" | "running" | "pass" | "fail" | "warn" | "rejected" | "locked" | "done";
export type PlaybackStatus = "loading" | "ready" | "playing" | "paused" | "ended" | "error";
export type LiveConnection = "idle" | "connecting" | "open" | "retrying" | "closed" | "error";
export type InspectorTab = "rtl" | "diff" | "tests" | "ppa";
export type Selection = { kind: NodeKind; id: string };
export type StageView = { id: StageId; label: string; status: StageStatus; detail?: string; hint: string };
export type CandidateListItem = { id: string; label: string; status: CandidateStatus; createdBy: CreatedBy;
  fitness: number | null; tags: string[]; hidden?: boolean };
export type Preset = { id: string; title: string; oneLine: string; spec: Spec; approxMinutes: number; recommended?: boolean };
export type SteerRequest =
  | { kind: "pin"; blockId: string; pin: string }
  | { kind: "toggle"; blockId: string; variantId: string; value: string }
  | { kind: "gate"; action: "build_now" | "hold" | "resume" }
  | { kind: "autopilot"; on: boolean }
  | { kind: "winner_override"; candidateId: string };
export type SelectedEntity =
  | { kind: "spec"; spec: Spec }
  | { kind: "block"; block: PlanBlock; lastDiff: PlanDiff | null; planRevision: number }
  | { kind: "golden"; testCount: number; files: string[]; source: string | null }
  | { kind: "candidate"; candidate: Candidate; parentLabel: string | null; baseline: PPA | null;
      isWinner: boolean; winnerBy: "ai" | "user" | null; aiWinnerLabel: string | null };
export type RunSummary = {
  title: string; mode: RunMode; sample: boolean; phase: RunPhase;
  winnerId: string | null; aiWinnerId: string | null; winnerBy: "ai" | "user" | null;
  versionsTried: number; rejected: number; rounds: number; fixes: number;
  humanInputItems: { label: string; local?: boolean }[];
  credits: Credits; recordedUsd: number | null; priority: Priority | null;
  candidates: CandidateListItem[];
};
export type LiveHealth = { status: "checking" | "ready" | "not_configured" | "budget_exhausted" | "busy" | "error"; capUsd: number | null };
```

Hooks and modules (B implements; S creates typed stubs first):

```ts
// src/lib/store/index.ts
export function useRun<T>(selector: (run: RunState) => T): T;
export function usePlayback(): {
  status: PlaybackStatus; tMs: number; durationMs: number; speed: 1 | 2; chapters: Chapter[];
  play(): void; pause(): void; seek(tMs: number): void; restart(): void; setSpeed(s: 1 | 2): void;
};
export function useUi(): {
  selection: Selection | null; inspectorTab: InspectorTab; autopilot: boolean; instant: boolean;
  select(sel: Selection | null): void; setTab(t: InspectorTab): void; setAutopilot(on: boolean): void;
};
export function useLive(): {
  connection: LiveConnection; runId: string | null; error: string | null; pendingSteers: SteerRequest[];
  startLive(presetId: string, priority: Priority): Promise<{ runId: string } | { error: string }>;
  steer(req: SteerRequest): Promise<void>;          // live only
  stopRun(): Promise<void>;                         // live only
  overrideWinnerLocal(candidateId: string): void;   // replay: local human + winner events through reduce()
};

// src/lib/hooks/*.ts
export function useReplayDriver(opts: { src: string; autoplay: boolean; loop: boolean }): void;
export function useLiveRun(runId: string | null): void;
export function useLiveHealth(): LiveHealth;
export function useCountdown(): { state: "counting" | "held" | "waiting" | "closed"; remainingMs: number; totalMs: number };
export function useReducedMotion(): boolean;
export function useSelectedEntity(): SelectedEntity | null;   // in use-run-views.ts
export function useStageViews(): StageView[];                  // in use-run-views.ts
export function useRunSummary(): RunSummary;                   // in use-run-views.ts

// src/lib/export/zip.ts
export function buildRunZip(run: RunState): Promise<Blob>;
export function downloadBlob(blob: Blob, filename: string): void;
```

`OnNodesChange`, `OnEdgesChange`, `OnNodeDrag`, `NodeMouseHandler`, `Node` and `Edge` are `@xyflow/react` 12.12.0 type exports (generic signatures checked in the package's `.d.ts` files on 2026-10-04).

## 8. Sources: replay and live share one code path

```
replay: fetch /runs/golden.json -> RunLogSchema.safeParse -> playback.load(log)
        rAF tick: apply every event with tMs <= cursor time ----+
                                                                 +--> store.applyEvent(e) --> reduce()
live:   EventSource {AGENT_URL}/runs/{id}/events                 |
        onmessage -> RunEventSchema.safeParse -> dedupe seq -----+
```

### 8.1 Replay (`lib/sources/replay.ts`, `hooks/use-replay-driver.ts`)
- Loads `/runs/golden.json` with `fetch`, validates with zod, shows the error state on failure (and logs the zod issues in dev).
- Plays at 1x or 2x. Seeking backward resets to `initialSnapshot` and re-applies events up to the target with `ui.instant = true` (no enter animations). Seeking forward more than 1 s also uses `instant`.
- `ended` at `meta.durationMs`. Landing loops after a 6 s hold on the final frame; Studio stops.
- **Must not import `lib/sources/live.ts` or anything that calls the network.** Acceptance check in the build plan greps for this.

### 8.2 Live (`lib/sources/live.ts`, `hooks/use-live-run.ts`)
- `EventSource(`${AGENT_URL}/runs/${runId}/events`)`. The server sends `id: <seq>` so the browser resumes with `Last-Event-ID` on reconnect (standard SSE behavior). Client ignores events with `seq <= lastSeq`.
- After 3 failed reconnects within 30 s: close, set `connection: "error"`, show the "Lost connection" banner.
- After the terminal `run` event the tail stays open (`live.tailOpen`, at most 180 s), because the sign-off sheet arrives after the result shows. The server ends the SSE response 5 s after its last event, so `EventSource` reconnects with `Last-Event-ID`, receives the sign-off events, and the server answers 204 when nothing is left. Only that close (`readyState` CLOSED) or the 180 s bound ends the tail; a close that is still reconnecting does not (`use-live-run.ts`). A finished run with no sheet therefore shows "Sign-off checks running" for about one reconnect before it reads "none"; the server asks for that reconnect after 250 ms (`retry: 250`) when it replays a finished run, and after 3 s while a run is live.
- Steering: `POST /runs/{id}/steer` returns 202; the server echoes a `human` event, which is the only thing that increments the Human inputs counter. Pending steers show as "queued" until echoed.
- If `NEXT_PUBLIC_AGENT_URL` is unset or `/health` says live is off, live entry points render the "Live runs are offline" state; replay is unaffected.

### 8.3 Fitness (computed by the agent, displayed by the web app)

REvolution-lite, measured tool output only, relative to our own v1 (the baseline = first candidate that passes):

```
gain_m  = 100 * (v1_m - c_m) / v1_m        for m in {area, clock period, power}; lower is better for all three
fitness = w_area * gain_area + w_timing * gain_timing + w_power * gain_power
weights: area priority  {0.60, 0.25, 0.15}
         speed priority {0.25, 0.60, 0.15}
         power priority {0.25, 0.15, 0.60}
missing metric (null for v1 or c) -> drop it, renormalize the remaining weights to sum 1
FAIL or rejected -> fitness null, never ranked
elites: best 2 by fitness among measured versions so far (v1 counts, score 0)
winner: highest fitness; tie -> smaller area. The user may override.
```

`lib/run/fitness.ts` (B) contains only display helpers (breakdown rows, priority metric) and a `recomputeFitness()` used by a dev assertion that the fixture's stored scores match this formula. It is never used to decide anything in live mode. The UI never asks an LLM whether a design is better.

### 8.4 Accounts (Supabase sign-in)

Chats and each account's list of runs live in Supabase Postgres, behind the server (the browser never reads them from Supabase). The browser only signs in. Run files, events and spend ledgers stay in Verity's own SQLite and disk.

- **Email and password only, and no sign-up anywhere.** The team makes each account in the Supabase dashboard and gives it to a judge. `signUp` is never called; the sign-in page has no sign-up link or wording.
- **Env (public by design):** `NEXT_PUBLIC_SUPABASE_URL` and `NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY`, read in `lib/env.ts` (`isAccountsConfigured`, and `LIVE_HREF`, where the landing's live buttons go). The secret key is the server's and never reaches `frontend/`. With either unset there is no sign-in gate: the bench works as in local development, and shows the access code form when the server asks for one.
- **Client:** `lib/sources/live.ts` makes the `@supabase/supabase-js` client on first use from a dynamic `import()` (`detectSessionInUrl: false`), so the replay path and a build without accounts never load it. `signIn(email, password)`, `signOut()` (scope `local`: the account stays signed in on its other devices), `currentAccount()` (the email or null; `getSession` refreshes an expired token) and `onAccountChange(cb)`. `useAccount()` (`hooks/use-account.ts`) gives `unknown` until the saved session is read, then `signed_in` or `signed_out`. Plain errors: a wrong login is "That email and password do not match."; an unreachable service says so; a 429 asks for a minute.
- **Auth header:** `authHeaders()` is async. It sends the Supabase access token when signed in (from `getSession`, which refreshes it), else the access code session token as before. The server tries its own token first, then the Supabase one; a Supabase user's session id is `u:<user id>`, so rate limits, spend limits, run ownership, stream tokens and chats follow the account to another device. The stream token from `POST /runs` is kept in `sessionStorage` of the tab that started the run; `openEventStream` in any other tab or device finds none and gets one from `POST /runs/{id}/stream-token` first (then opens the stream), so an account's running run streams on a second device.
- **`/signin`** (`sections/signin/SignInView.tsx`, `signin.css` under `.vs.signin`): title "Sign in to Verity", email and password, "Sign in" as the one amber action, and "Have an access code?" opening the bench's `AccessCode` form (`createSession`). On success it goes to `?next=` when that is a path on this site and not `/signin` (`safeNext`), else `/bench`. A reader who is already signed in is forwarded at once.
- **Entry points:** the landing's "Try it live" buttons (the hero's is the page's one amber action; the landing bar's is the quiet style) go to `/signin?next=/bench` when accounts are configured, else `/bench`. Other "Try it live" links go to `/bench`, which the guard below handles.
- **Bench guard** (`BenchView`): when `/health` says `authRequired`, accounts are configured, and there is neither an account nor a code session, the page goes to `/signin?next=<current path and query>` and renders nothing meanwhile. Nothing is decided until the saved account has been read, so a signed-in reader never bounces. An account counts as an access code until the server rejects its token; then the access code form shows, as before. `/studio` and the rest of the replay stay public.
- **Sidebar lists** (`Sidebar.tsx`, `hooks/use-account-lists.ts`): signed in, "Chats" is `GET /threads` and "Your runs" is `GET /runs/mine` (`listThreads`, `listMyRuns`, zod-checked; an item that does not read is skipped). They load on mount, after a message is answered, a run starts or a run ends (`refreshAccountLists()`), and when the window is focused again. A list that fails keeps its last answer and says so. Not signed in, the sidebar shows the browser's own "Your runs" as before and no Chats. The foot shows the account's email and "Sign out", which also forgets the chats and runs kept in this browser (`verity.threads`, `verity.openThread`, `verity.recentRuns`), so the next person on this browser does not find them.
- **Opening a chat** from the Chats list is a link to `/bench?thread=<id>`. `BenchView` reads `GET /threads/{id}` (`readThread`) and `GET /runs/mine` together and rebuilds the messages with `threadFromServer` (`bench/chat.ts`): each Verity turn is rebuilt from its stored `reply` and `check` the way a live answer is (`agentMessages`), and a turn stored without them comes back as its text and its step line. A run joins the chat at the point it started, by its `createdAt`; a chip card or proposal that a run followed shows as sent. The chat continues with the same thread id, its latest run comes into view (`?thread=` gives way to `?run=`), and `verity.threads` keeps a copy as a cache.

## 9. Component architecture

Layers, bottom to top. A layer imports only from layers below it.

| # | Layer | Folder | Rule |
|---|---|---|---|
| 1 | Tokens | `app/globals.css` | Only place colors, fonts, radii and motion are defined |
| 2 | UI primitives | `components/ui` | shadcn-generated. Restyle through tokens, not per-use overrides |
| 3 | Domain components | `components/brand`, `components/domain` | Presentational, props in and callbacks out. No store, no fetch, no router |
| 4 | Rack chrome | `components/sections/studio/chrome`, `components/sections/shared/RackBar.tsx` | `Mod` (a rack module: ears, "MOD · NAME" label), `Seg7` (seven-segment readout), the shared top bar. Presentational; no run logic |
| 5 | Sections | `components/sections/*` | Compose 2 to 4 with store hooks. All store-to-props wiring happens here |
| 6 | Pages | `app/**/page.tsx` | Thin: metadata, `Suspense`, one or more sections. No business logic, no styling beyond layout |

**Pages compose reusable components; they never re-implement them.** If two sections need the same visual, it becomes a domain component.

Studio specifics (rack re-skin 2026-10-16; replaces the earlier Figure 1 and datasheet frame):
- One frame: the shared `RackBar` (via `StudioBand`), then a TRANSPORT module on top (`timeline/TimelineBar`, seven-segment readouts, run metadata), then three modules side by side: LOOP (`loop/LoopModule`), MONITOR (`timeline/Narration` plus `detail/DetailPanel`) and VERSIONS (`table/CharacteristicsTable`). `StudioWorkspace` composes them. Full viewport with no page scroll from 1100px wide; narrower, the modules stack and the page scrolls.
- LOOP shows one LED column per stage (PLAN, LOCK, BUILD, TEST, EVOLVE, PROVE) with a status square and word, fail markers on TEST and the round. `loop/derive-leds.ts` derives all of it from run state only (stage statuses, candidates, `evolve.detail`), never from constants.
- The detail panel follows the run by default (`detail/follow.ts` compares each run state with the previous one); a click on a VERSIONS row pins it through `ui.select`, and "Follow run" or Escape clears the selection. `figure/derive.ts` and `figure/proof.ts` remain as shared derivations; the figure components (`LoopFigure`, `CandidateBox`, `Wires`, `describe.ts`) were deleted.
- Scroll model: the detail body (`.dp-body`) is the one vertical scroller, with `overscroll-behavior: contain`; code boxes scroll only sideways. Below 1100px the page scrolls and the detail body grows.
- No pan, zoom or dragging. Round columns come from the run data, never a constant. The reducer still builds xyflow-typed nodes (`lib/canvas/*`) that `useRunSummary` reads, so `@xyflow/react` and `@dagrejs/dagre` stay installed.
- Theme: the rack world under `.vs` (`studio.css`, tokens `--rk-*` in `globals.css`) on top of `sections/shared/datasheet-base.css` (the file keeps its old name). Root class `STUDIO_ROOT` = `vs` + `RACK_FACES`.
- Landing (`components/sections/rack/`, scope `.rk.land`): `RackPage` = `RackNav`, then from `sections/landing/` `Hero`, `Problem`, `LoopSection` (the CPU job: ask, build, upgrade), then `Inside` (four trust checks), `CpuToday`, `Ladder`, `DemoVideo`, and `RackFooter`. Centered, no frames or ears; the type scale and spacing tokens sit on `.rk.land` in `rack.css` (rem, 4px type steps, 8px spacing). CPU figures come from `landing/cpu-real.ts` (`cpu-real.json`); a loop MP4 shows only if its file exists at build time (`landing/media.ts`). /try (`.vt`), /results (`.vr`) and the 404 page (`.vn`) take the same tokens (`app-roots.ts`, `app-pages.css`); every page uses the one `RackBar` top bar (/try, /results and 404 through `AppHeader`).

Workspace specifics:
- The sidebar's closed state (900px and up) is `lib/hooks/use-sidebar.ts`, kept in localStorage `verity.sidebar`; `AppShell` sets `data-side="shut"` and its menu button reopens it. `AppShell`'s `actions` slot holds the bench's Open the panel button.
- `WorkspaceView` reuses the bench frame (`AppShell` with its `label` prop, `Sidebar` with `home`, `bench.css`) and adds `workspace.css` under `.vs`. Home in the sidebar is `/workspace`; the VERITY logo is still `/`. New chip goes to `/bench`.
- Which runs: `useRecentRuns()` (localStorage `verity.recentRuns`) joined by id to `GET /runs?limit=100` (`useRunsList`, `listRuns` in `lib/sources/live.ts`, zod-checked; an item it cannot read is skipped, a field it cannot read becomes absent). `derive.ts` does the join and every number the page shows. A listed run the server did not return keeps its card with "Not in the server's latest runs"; when the request fails the page says so and shows runs from the browser only. Nothing is read before hydration, so an empty list is never shown by mistake.
- A card links to `/bench?run=<id>` (the query `BenchView` reads). Its status square is amber only when the server says done and names a winner; a run known only from this browser gets the neutral square. Area, clock and power are `outcome.*VsV1` (ratios against that run's own v1) shown as signed percent; the sign-off line is `outcome.signoff`; spend is `outcome.usd`. Every figure is run data and says "vs v1".
- Charts are plain SVG (`BarRows`, no chart library), sized to their container in pixels so text keeps its size on a phone. Horizontal bars, one row per run, from a single baseline (the 0% line for changes: left of it is better). Marks are neutral inks (`--rk-ink-2`, `--rk-ink-3`), never amber. Each chart has a caption, a legend when it has two series, a hover and focus tooltip, a per-row accessible name and a "View as table" twin. The palette was checked with the dataviz validator: the two inks separate (CVD and normal-vision delta E 15.0) and clear 3:1 contrast on the panel; the lightness-band and chroma checks fail by design, because the brand allows no hue in a chart, so identity also rides the legend, the value labels and the table. `BarRows` passes the tick step to `tickText`, so the spend axis shows 3 decimals while the step is under one cent ($0.005, $0.010) and never repeats a label.
- No limit figure anywhere: the UI shows what runs spent, never `capUsd` or a daily or session limit. The new 429 reason `session_budget_exhausted` maps to "You have reached today's spend limit for this session. Try again tomorrow." in `lib/sources/live.ts`. On POST /parse that reason arrives as `reason: "spend_limit"` (a plain rate limit stays `rate_limited`); `replyTo` then makes an error message with `spendLimit: true` and the chat hides "Pick it yourself", because Run is refused by the same limit. Ask and proposal turns saved in this browser before the limit figure was removed may still say "Cap $0.50." or "of its $0.50 cap"; `withoutCap` (`bench/chat.ts`) drops that clause when they render.
- Combine is a preview (`CombinePreview`): a dashed panel, a "Coming soon" tag, a diagram, a disabled button and one sentence. Nothing in it runs.

Bench specifics (all new wire fields are optional, so an older server shows what it showed before):
- Picking a version anywhere (a Versions row, the Verilog or Architecture picker, a version name in an answer) calls `BenchView.pickVersion`: the panel selection (`ui.select`) and a run-scoped composer chip (`attached`, React state) are set together but kept apart, so removing the chip leaves the panel alone. A Versions row or an answer link also opens the version tab ("v4", `VersionTab.tsx`); a picker keeps the tab it is on. The chip clears on New chip and on another run; it is not kept across a reload. Ask sends its label as `focusVersion`, and the saved user turn keeps `version`.
- Architecture tab (`ArchitectureTab.tsx`): a step rail derived by `rail.ts` from run data, each step opening its evidence; the circuit of the version in view from `circuit/<id>.json`, drawn by `CircuitView.tsx` (xyflow plus dagre, top to bottom, loaded with `next/dynamic`, first view at a readable zoom, Fit for the whole). Expand moves it into the main area (`expanded`), with the composer still under it. A walkthrough is the `steps` of an ask answer: its highlight (rail step, circuit parts, and its own version) is separate from the reader's picks, and Next and Back never call the model.
- Artifacts are parsed with zod in `artifacts.ts` (`signoff/<id>.json`, `waves/<id>.json`, `spec/rules.json`, `circuit/<id>.json`, and the synth and STA text reports); a file that does not parse reads as "could not be read". `Signoff.tsx` renders the sheet and the one line on the Result tab; `Waveform.tsx` renders expected next to got with an outline and a word on a mismatch.
- The first message's verdict, research and assumed knobs (`statedKnobs` from POST /parse) are saved with the run in `verity.recentRuns`, so a reload of `?run=` shows the same first reply.

Chat thread with the chip engineer agent (used when `GET /health` lists `agent`; without it every path above runs as before):
- One path for every message. `BenchView.send` calls `postMessage(threadId, text, runId?, focusVersion?, signal?)` (`lib/sources/live.ts`, `POST /threads/{threadId}/messages`), before a run and after one. `threadId` is a UUID made in the browser (`newThreadId` in `chat.ts`), one per thread; `runId` is the run on screen and `focusVersion` the version on the chat box. The chat box stays locked while a run is going. A second message before any run no longer reaches POST /parse: only the first message of a thread is fact-checked, and the server does it inside the same call.
- The reply has two parts. `check` (the POST /parse reply, present only when this message ran the fact check) is read by `replyTo` into the card, refusal or error it always was, with the verdict, research and "I assumed" chips. `reply` follows it: `answer` (words, bullets, suggestion chips, research with its sources, a walkthrough in `steps`), `proposal` or `refused`, each with `toolSteps`. `reply` is null only for "Not a chip". Everything optional is read one by one, so a tool step, a tab or a source this bench cannot read is dropped and never fails the reply (`lib/contracts/agent.ts`).
- Step line (`AgentTurn.tsx`, `ToolSteps`): one closed dim line, "Used 3 tools" (a failed tool is counted there too), that opens to one line per tool step, a check mark when it worked and a plain mark when it did not, in the server's words. Ink only. A reply keeps its plain words visible (verdict, reason or answer and bullets, suggestion chips, the closest-chip line and buttons, chip and proposal cards, walkthroughs) and folds the rest into one closed "Details" (`ReplyDetails` in `Research.tsx`, a native `details`): the research sentences, each followed by its S1 S2 chips on a row of their own, the sources and "Show more sources", "Searched", "Used saved research", "What this check cost", "What Verity cannot build" and the basis line. A live chip card keeps its own "What Verity cannot build" dropdown. While a reply is pending the thread says "Verity is thinking..."; the first message of a thread keeps the longer "checking it against sources" wait.
- A proposal before any run is a chip card with Run (`agentMessages` in `chat.ts` builds a `card` message from `presetId`, `params` and `priority`, and the existing `ChipCard` path starts the run). After a run it is today's proposal turn (`ProposalCard`) with Confirm. Nothing runs until Run or Confirm. A chip card, refusal or proposal stays live until a later one, or a run, replaces it; an answer in between does not end it.
- `openTab` goes through `showTab`: for the run on screen the tab opens at once (the version it names is picked; "signoff" is the winner's version tab); for another run of this browser the page opens that run first (the thread stays) and the tab follows once the run is read.
- The thread is one list of messages (`ThreadMessage` in `lib/hooks/use-threads.ts`): the reader's messages, the check, the agent's replies, and a `run` marker where each run started. The run lines (progress, the end) show at the marker of the run in the URL, other runs show a quiet "Open that run" note, and replies after a run sit after its lines. A run with no stored thread (started before the agent) is brought into a thread when its first message is sent (`seedThread`): the saved question, card and turns, then a marker.
- Memory in this browser (`use-threads.ts`): `verity.threads` keeps the newest 20 threads by `threadId`, each with its newest 60 messages and an `updatedAt`. A waiting line, a failed reply and a question that got no reply are not kept (`persistableMessages`), and a stored message that does not read is dropped alone. `verity.openThread` names the open thread. A reload restores the open thread (also before any run), read once after hydration; opening a run from "Your runs" restores the thread its entry names (`RecentRun.threadId`, set when Run is pressed from a thread; `POST /runs` carries `threadId`). New chip starts a new thread (a new id and an empty list; the Workspace page clears `verity.openThread` before it opens the bench). The server keeps its own memory of the thread.
- The thread decides the path, not the last health answer. `viaAgent` is `agentOn` (the server lists `agent`) or a thread that has an id (only the agent path makes one), so once a thread has used the agent, every later message in it goes to `postMessage`, also while `/health` does not answer. `useLiveHealth` keeps the last known `features` and `authRequired` when a recheck fails (the status still says "error"). A failed message never calls `beginThread` and never clears the thread, so the stored thread is never overwritten with a shorter list by a path switch. Send waits (disabled, "Connecting...") while the first `/health` answer is still `checking`, because the path is not known yet.
- A failed message shows its error with "Try again" in the chat (`ChatThread`, agent thread only; not for an access code or a spend limit). A first message that failed or timed out is sent again on a new thread id (`resend`): the server may have run, and paid for, its fact check without this page seeing it, and only a thread's first message is fact-checked, so the same id could answer without a card. A later message is sent again on the same thread. "Try again" on a failed fact check (verdict `could_not_check`) also asks on a new thread id, and is offered only while the check is the whole conversation.
- The agent part of a message stops at 90 s on the server (`[agent] turn_deadline_s`) and code answers from the steps found; the web gives up at 120 s (`MESSAGE_TIMEOUT_MS`), which is the "took too long" retry above. A message about a run that is still going is a 409 (`still_running`, "This run is still going. Ask again when it ends."); the chat box is locked while a run goes, so this is a guard only.
- Errors reuse the spend-limit sentences (a 429 with `budget_exhausted` or `session_budget_exhausted` is reason `spend_limit`, with no figure; `rate_limited` is a wait of a minute). A 403, or a 404 with a run, means the run is not this session's. A failed later message leaves the question in the chat box.

## 10. Agent API (built; exact shapes in `../backend/CONTRACTS.md` section 9)

Base URL from `NEXT_PUBLIC_AGENT_URL` (a public URL, not a secret). CORS must allow the web origin. All validation, rate limits and money caps are enforced server-side; the browser is untrusted. Every call lives in `src/lib/sources/live.ts`. Each call may carry `Authorization: Bearer <token>`: a Verity session token (from `POST /auth/session`) or a Supabase access token (section 8.4).

| Method and path | Used for |
|---|---|
| `GET /health` | live or offline, `capUsd`, `models` per role, `authRequired`, `features` |
| `GET /runs` | the runs this server knows, newest first, each with an optional `outcome` (ratios against that run's own v1, versions, actual spend, sign-off seeds); public; the Workspace page |
| `POST /auth/session` | the access code, when the server requires one |
| `GET /threads`, `GET /threads/{threadId}` | the signed-in account's chats, and one chat's stored turns with each reply and check (section 8.4); owner only |
| `GET /runs/mine` | the runs the caller started, newest first, at most 50: `{id, title, threadId, createdAt, status}`; owner only |
| `POST /parse` | the first message without the agent: verdict, research, family and knobs, `statedKnobs` |
| `POST /threads/{threadId}/messages` | every message with the agent (`agent` in `features`): `{text, runId?, focusVersion?}` gives `{threadId, check, reply}`; the first message of a thread also carries the fact check |
| `POST /runs` | start a run: `{presetId, priority, mode?, params?, researchId?, threadId?}` gives `{runId, capUsd, streamToken}` |
| `POST /runs/{id}/stream-token` | a fresh stream token for a run the caller started (owner only): `openEventStream` asks for one when the tab has none stored, as on an account's other device |
| `GET /runs/{id}/events` | SSE with `?st=<streamToken>`, resumes with `Last-Event-ID`; 204 when nothing is left |
| `GET /runs/{id}/log` | a finished run as a `RunLog` (public) |
| `POST /runs/{id}/ask` | the chat after a run without the agent: answers, walkthrough steps, proposals |
| `POST /runs/{id}/steer`, `POST /runs/{id}/stop` | plan gate steering and Stop |

`SteerRequest` is defined in `src/lib/contracts/view-types.ts` (section 7.1). The backend emits canvas ops from run state, never from a model (`../backend/ARCHITECTURE.md` section 17), and the frontend only sees events that passed its zod schemas. Live default `autoContinueMs` for the plan gate: 10000.

## 11. Golden-run fixture

- Location: `frontend/public/runs/golden.json` (served as `/runs/golden.json`, copied into `out/` by the export). Swapping in the real recorded run later needs no code change.
- Format: `RunLog` (section 5), `format: "verity.runlog/1"`.
- Until the real run is recorded, `meta.sample` is `true`, `meta.runId` is `"golden-sample-001"`, and `meta.note` says "SAMPLE DATA. Placeholder numbers for building the UI. Not results." Every UI surface that shows a metric shows the "Sample data" badge while `sample` is true.
- Validate with `npm run validate:fixture` (section 13).

### 11.1 Sample run content (S authors the JSON from this)

Preset `mac4`: "4-lane MAC array, 8-bit". Spec text: "A 4-lane multiply-accumulate unit. Each cycle it multiplies four pairs of unsigned 8-bit inputs and adds the four products to a running sum. After 16 cycles it raises done and holds the 22-bit result until reset. Smallest area matters most." Ports: `clk` in 1, `rst_n` in 1, `start` in 1, `a` in 8 x4, `b` in 8 x4, `acc` out 22, `done` out 1. 16 tests. Max 300 lines. Priority `area`.

Plan revision 1 (4 blocks): `blk-in` Input registers; `blk-mult` Multiplier array (4 lanes) with variant `pipeline` (`none` / `2-stage`, value `none`); `blk-add` Adder tree; `blk-acc` Accumulator and control with variant `width` (`auto` / `22-bit`). Each block has a one-line reason and 1 to 2 `pinOptions` (for example "Minimize area", "Share one multiplier", "Minimize registers").

Recorded human inputs (counter ends at 2): the spec at 0 ms, and a toggle at 12000 ms: `blk-mult` pipeline to `2-stage`. Plan revision 2 marks `blk-mult` as changed ("Multiplier array (4 lanes, 2-stage)").

Versions (all numbers are sample data; PDK "Nangate45", tools "Yosys", "OpenSTA"; OpenROAD until 2026-10-23):

| Version | id | Round | Parent | Created by | Reason | Tests | Area µm² | Cells | Period ns | Power mW | Fitness | Final status |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| v1 | c1 | 0 | none | INIT | First version, written from the plan | attempt 1: 13/16 FAIL; attempt 2 (FIX: widened accumulator 16 to 22 bits): 16/16 | 1840 | 412 | 3.20 | 1.92 | 0 (baseline) | measured, baseline |
| v2 | c2 | 1 | v1 | SIMPLIFY | Removed resets from datapath registers | 16/16 | 1690 | 371 | 3.18 | 1.84 | 5.67 | measured, elite |
| v3 | c3 | 1 | v1 | OPTIMIZE | Merged lane adders into one balanced adder tree | attempt 1: 14/16 FAIL (done one cycle late); attempt 2 (FIX): 16/16 | 1610 | 356 | 3.45 | 1.76 | 6.80 | measured, elite |
| v4 | c4 | 1 | v1 | EXPLORE | Tried carry-save accumulation | 16/16 | 1950 | 455 | 2.70 | 2.05 | -0.70 | measured |
| v5 | c5 | 2 | v3 | OPTIMIZE | Trimmed pipeline registers to the widths the math needs | 16/16 | 1520 | 334 | 3.38 | 1.66 | 11.06 | measured, winner, EQY proven vs v1 |
| v6 | c6 | 2 | v2 | SIMPLIFY | Merged the IDLE and LOAD control states | 14/16, then 15/16 after one FIX | none | none | none | none | null | rejected after 2 attempts |

Fitness check (area weights 0.60 / 0.25 / 0.15): v5 gains are area +17.39, timing -5.63, power +13.54, so 0.6 x 17.39 + 0.25 x -5.63 + 0.15 x 13.54 = 11.06. Displayed deltas vs v1 for v5: area -17.4%, clock period +5.6% (slower), power -13.5%.

Example failing cases for v1 attempt 1 (expected vs got): `acc_16_cycles_max` inputs "a=[255,255,255,255] b=[255,255,255,255] x16", expected `0x3F8040`, got `0x008040`; `acc_random_seed_7` expected `0x01A2B3`, got `0x00A2B3`; `acc_carry_boundary` expected `0x010000`, got `0x000000`. Diagnosis: "The accumulator is 16 bits wide, so sums above 65,535 wrap. Sixteen cycles of four 8-bit products need 22 bits." v3 attempt 1: `done_timing_basic` expected "done at cycle 18", got "done at cycle 19" (and one more timing case). v6: `done_timing_back_to_back` expected cycle 18, got 17.

RTL: short, plausible, clearly commented `// SAMPLE RTL for UI development` Verilog modules (20 to 40 lines each) for every attempt. Artifacts: `tests/golden_model.py`, `tests/test_mac4.py`, `reports/synth_v5.txt`, `reports/eqy_v5_vs_v1.log`, each a few lines and marked sample.

Timeline (`durationMs: 90000`, compressed with highlights). Chapters: Spec 0, Plan 4000, Lock 20000, Build and fix 23000, Round 1 44000, Round 2 62000, Prove 80000, Done 88000.

| tMs | Events (captions in quotes) |
|---|---|
| 0 | run running; human spec; spec; op add_node spec; stage spec done; "Spec read: 4 lanes of 8-bit multiply-accumulate over 16 cycles. Goal: smallest area." |
| 4000 to 8000 | stage plan active; "Drafting the plan."; plan rev 1; add_node x4 (500 ms apart); connect plan edges |
| 9000 | gate open (autoContinueMs 10000); "Plan drafted: 4 blocks. Building in 10 s unless you steer." |
| 12000 | human toggle; "You set the multiplier to a 2-stage pipeline. Replanning." |
| 13500 | plan rev 2 (diff changed blk-mult); update_status blk-mult; gate open (autoContinueMs 6000); "Plan updated. Building in 6 s." |
| 19500 | gate closed; stage plan done |
| 20000 to 22000 | stage lock active then done; add_node golden; "Answer key locked: 16 tests from the golden model. The agent cannot edit them." |
| 23000 | stage rtl active; add_node c1 (drafting); connect checks golden to c1; "Writing v1 from the plan." |
| 26000 to 28000 | stage rtl done; lint active then done; sim active; c1 simulating; "Simulating 16 tests against the golden model." |
| 29000 | c1 fail 13/16; stage sim failed; "v1 failed 3 of 16 tests. Reading the mismatches." |
| 31000 | stage fix active; c1 fixing; "Diagnosis: the accumulator is 16 bits and wraps. Widening it to 22 bits." |
| 34000 to 36000 | c1 simulating (attempt 2) then pass 16/16; stage sim done; fix done (detail "1 fix"); "v1 passes 16 of 16. This is the baseline." |
| 37000 to 40000 | stage synth active then done; c1 measured, baseline; "v1 measured: 1,840 µm², 3.20 ns, 1.92 mW. Everything after is compared to this." |
| 44000 to 47000 | stage evolve active (round 1); round 1 parents [c1]; add_node c2, c3, c4 + lineage edges; "Round 1: three new versions from v1." |
| 49000 | c2 pass, c4 pass, c3 fail 14/16; "v3 failed 2 tests: done is one cycle late. Fixing it before it can compete." |
| 52000 to 54000 | c3 fixing then pass (fixCount 1) |
| 55000 to 58000 | c2, c4, c3 measured |
| 59000 | round 1 elites [c3, c2]; "Kept the two best: v3 and v2. v4 was faster but larger." |
| 62000 to 63000 | round 2 parents [c3, c2]; add_node c5, c6 + lineage edges; "Round 2: two new versions from the elites." |
| 66000 | c5 pass; c6 fail 14/16 |
| 68000 to 72000 | c6 fixing, fail 15/16, rejected; "v6 failed again after a fix and was rejected." |
| 73000 to 76000 | c5 synthesizing then measured; "v5: area -17.4% vs v1, clock period +5.6%, power -13.5%." |
| 78000 | winner c5 by ai; stage evolve done; "Winner by fitness: v5." |
| 80000 | stage prove active; "Checking v5 against v1 with Yosys EQY." |
| 85000 | c5 eqy proven; connect proof c5 to c1; stage prove done; "Proven equal to v1." |
| 86000 | credits usedUsd 0.42, capUsd null (recorded cost, sample); artifact events |
| 88000 | run done; "Done. 6 versions, 2 fixes, 1 rejected. Human inputs: 2." |

## 12. Conventions

- **Client components:** every file under `components/sections` and every hook starts with `"use client"`. Domain components add it only if they use state or effects. Pages stay server components that render client sections inside `Suspense`.
- **Secrets:** none in `frontend/`. The env vars are public URLs only (`NEXT_PUBLIC_AGENT_URL`, `NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY`, `NEXT_PUBLIC_SITE_URL`, `NEXT_PUBLIC_GITHUB_URL`, `NEXT_PUBLIC_DEVPOST_URL`, `NEXT_PUBLIC_DEMO_VIDEO_URL`) plus the dev-only `NEXT_DIST_DIR`. Never add `NEXT_PUBLIC_*` keys, tokens, the Nebius key or the Tavily and Serper keys. The one key allowed there is the Supabase publishable key, which Supabase designs to be public; the Supabase secret key lives only in `backend/.env`. `.env*` stays gitignored; commit `.env.example` only.
- **Untrusted input:** every event from the network or the fixture passes `RunEventSchema.safeParse` before `reduce`. Rendered strings from events are plain text (React escapes them); never use `dangerouslySetInnerHTML` (the Verilog highlighter returns React nodes, not HTML).
- **Naming:** components `PascalCase.tsx`, one component per file; hooks `use-kebab-case.ts` exporting `useCamelCase`; lib files `kebab-case.ts`; types `PascalCase`; zod schemas `PascalCaseSchema`; CSS custom properties `--kebab-case`. Node ids: `spec`, `blk-*`, `golden`, `c<n>`; labels `v<n>`.
- **Formatting numbers:** only through `lib/format.ts` (`formatArea`, `formatNs`, `formatMw`, `formatMhz`, `formatPct`, `formatDelta` with the U+2212 minus, `formatUsd`, `formatClock`). Measured values render in `font-mono`.
- **Copy:** the exact strings in the components; no em or en dashes.
- **Dependencies:** only S edits `package.json`. A later task that needs a package writes it down for I.
- **No speculative features:** nothing from the stretch lists is built before the MVP acceptance checks pass.

## 13. Verification commands

Run from `frontend/`:

| Command | What it proves |
|---|---|
| `npm run typecheck` (`tsc --noEmit`) | Contracts and components agree |
| `npm run lint` (`eslint`) | Lint clean (Next 16 removed `next lint`; the script calls ESLint directly) |
| `npm run validate:fixture` (`node scripts/validate-fixture.ts <file>`) | `public/runs/golden.json`, `mac4-real-raw.json` and `mac4-real.json` parse with `RunLogSchema`, events are seq-ordered, `tMs` non-decreasing, every `candidate.fitness.score` matches section 8.3 within 0.01 |
| `npm run build` (`next build`) | Static export succeeds; `out/index.html`, `out/studio/index.html`, `out/bench/index.html`, `out/try/index.html`, `out/results/index.html`, `out/runs/golden.json` exist |
| `npx serve out` or any static server, then open `/` | Replay plays with the network tab showing no requests after `golden.json` |
| `grep -rn "sources/live\|EventSource\|AGENT_URL" src/lib/sources/replay.ts src/lib/hooks/use-replay-driver.ts` | Prints nothing: the replay path cannot call the agent |
| `grep -rnP "\x{2014}\|\x{2013}" src public ../*.md` (in a shell, the pattern is `"\x{2014}|\x{2013}"`; the backslash here only escapes the table pipe) | Prints nothing: no em or en dashes |

`validate-fixture.ts` relies on Node 24 type stripping. Verified 2026-10-04 on Node v24.13.1 with zod 4.6.5: `node scripts/validate-fixture.ts` importing `../src/lib/contracts/schemas.ts` (relative path with the `.ts` extension) works when `package.json` has no `"type"` field (prints a harmless `MODULE_TYPELESS_PACKAGE_JSON` warning) and fails with `"type": "commonjs"`. So: do not add `"type": "commonjs"`; add `"scripts"` to the `exclude` list in `tsconfig.json` (the `.ts` import extension is not allowed in the app's type check); `schemas.ts` must use only erasable TypeScript (no `enum`, no namespaces) and import only `zod`. Fallback if anything changes: `npx tsx scripts/validate-fixture.ts`.

## 14. Unverified or open technical items

- Verified late on 2026-10-04 and already applied: React Flow's real `--xy-*` variable names (from `dist/style.css`), its `outline: none` on focused nodes (restored in `globals.css`), and the `Node<NodeData extends Record<string, unknown>>` constraint (so `NodeData` is a type alias).
- Settled: no model emits canvas ops or calls tools; the backend builds ops from run state, so Nemotron tool-call reliability does not matter here.
- EQY equivalence between versions with different pipeline latency may not prove. The UI supports `not_equivalent`, `inconclusive` and `not_run`; the agent team decides which versions are eligible.
