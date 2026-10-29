# Verity web

The Verity web app: Next.js 16 (App Router, static export), Tailwind v4, shadcn/ui, React Flow. Describe a chip in plain English; watch an agent write, test, fix and evolve the Verilog. The app holds no secrets and builds to plain static files.

Docs: `ARCHITECTURE.md` in this folder (stack, structure, contracts).

Pages: `/` landing, `/studio` (recorded and finished runs), `/bench` (the live chat workspace; `/try` forwards to it), `/results`.

## Commands

Run from this folder.

```
npm run dev              # local dev server
npm run typecheck        # tsc --noEmit
npm run lint             # eslint (Next 16 removed `next lint`)
npm run validate:fixture # checks golden.json, mac4-real-raw.json, mac4-real.json and cpu-real.json (Node 24 type stripping)
npm run build            # static export to out/
```

After `npm run build`, serve `out/` with any static server (for example `npx serve out`) and open `/`.

Expected build output: `out/index.html`, `out/studio/index.html`, `out/bench/index.html`, `out/try/index.html`, `out/results/index.html`, `out/runs/golden.json`, `out/og.png`, `out/404.html`.

## Verify

Run from this folder, in this order. All of them pass on the integrated build.

```
npm run typecheck
npm run lint                  # 0 errors, 0 warnings (prints nothing)
npm run validate:fixture      # golden.json OK (6 versions, winner c5); mac4-real-raw.json and mac4-real.json OK (6 versions, winner c4); cpu-real.json OK (7 blocks, 2 upgrades)
npm run build
rg -n "sources/live|EventSource|AGENT_URL" src/lib/sources/replay.ts src/lib/hooks/use-replay-driver.ts   # prints nothing
rg -nP "\x{2014}|\x{2013}" src public                                                                     # prints nothing
```

Then serve `out/` (for example `npx serve out -l 3104`) and check by hand: the replay on `/` and `/studio` makes no request after `/runs/golden.json` (DevTools network tab, or `performance.getEntriesByType("resource")`); the final frame shows 12 content nodes; `Download design (.zip)` on `/results` gives `verity-<runId>.zip` with `rtl/`, `tests/`, `reports/`, `run-log.json` and `README.txt`. Internal links use `prefetch={false}` so a replay never triggers a background page fetch.

## Environment

Copy `.env.example` to `.env.local` if you need any of these. Only public URLs go here, and the Supabase publishable key (public by design). Never a secret key.

| Variable | Meaning |
|---|---|
| `NEXT_PUBLIC_AGENT_URL` | Base URL of the agent backend (`backend/`). Unset means replay only; live entry points show "Live runs are offline". |
| `NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY` | The Supabase project URL and its publishable key (`sb_publishable_...`). Both set: the landing's live buttons open `/signin` and the bench asks for a sign-in or an access code when the server requires one. Either unset: no sign-in gate. The secret key is `SUPABASE_SECRET_KEY` in `backend/.env`, never here. |
| `NEXT_PUBLIC_SITE_URL` | Public URL of this site, used for `metadataBase`. Defaults to `http://localhost:3000`. |
| `NEXT_PUBLIC_GITHUB_URL`, `NEXT_PUBLIC_DEVPOST_URL`, `NEXT_PUBLIC_DEMO_VIDEO_URL` | Public links for the nav and footer. A link whose URL is unset is not rendered. |
| `NEXT_DIST_DIR` | Dev-only knob. Sets Next's `distDir` so parallel builders can run isolated dev servers (for example `NEXT_DIST_DIR=.next-b npm run dev`). Directories matching `.next-*` are gitignored. Production builds use the default `.next`. Next appends the custom dir to `tsconfig.json` `include` and `next-env.d.ts`; remove those lines afterwards. |

## Layout

```
src/app/                    pages, layout.tsx, globals.css (brand tokens)
src/components/ui/          shadcn primitives (generated)
src/components/brand|domain presentational components, props only
src/components/sections/    page sections, where store hooks meet components
                            (rack/: landing; studio/: rack modules; bench/: the chat workspace; results/)
src/lib/contracts/          types, view types, zod schemas, stages, presets, research and ask replies
src/lib/{run,canvas,sources,store,hooks,export,verilog}/
public/runs/golden.json     recorded sample run (placeholder data, labeled Sample data)
public/runs/mac4-real*.json the featured real Nebius run (2026-10-16): mac4-real.json is the shortened playback, mac4-real-raw.json the raw recording
scripts/validate-fixture.ts checks the fixture
```

Replay never touches the network after `golden.json`. All live code lives in `src/lib/sources/live.ts`.

## Notes from the scaffold

- `globals.css` carries the brand token block. shadcn init wrote the same three `@import` lines (`tailwindcss`, `tw-animate-css`, `shadcn/tailwind.css`), so nothing differs.
- shadcn 4.21.1 generates components that import `cn` from the `cn` npm package (the shadcn-ui `cn` utility, a drop-in for clsx plus tailwind-merge). `src/lib/utils.ts` re-exports it, so `@/lib/utils` still works.
- `package.json` must not get a `"type"` field: the fixture validator relies on Node's module syntax detection (it prints a harmless `MODULE_TYPELESS_PACKAGE_JSON` warning).
- `src/lib/contracts/schemas.check.ts` is a compile-time check that the zod schemas and `types.ts` describe the same shapes. It runs inside `npm run typecheck`.
