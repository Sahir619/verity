"""Composition root and the `verity-api` console script (CONTRACTS section 1).

    settings -> production guard -> packs (fail fast) -> provider (B) -> tool runner (C)
             -> SqliteStore (E) [+ SupabaseThreadStore for chats and run lists] -> research (search adapter +
             ResearchService, or off) -> RunManager (D) -> FastAPI app (E) -> uvicorn, one worker

Everything except opening the store is built synchronously here. The store opens in the app lifespan, inside the
server's event loop, followed by service.start() (startup recovery and the watchdog).
"""

import logging
import sys

import uvicorn
from fastapi import FastAPI

from .api.app import create_app
from .api.redact import RedactFilter, log_config
from .config import Settings, load_settings
from .core.errors import VerityError
from .core.pack import load_custom_packs, load_packs
from .core.parse import live_families
from .llm.factory import build_provider
from .research import ResearchService, build_search_adapter
from .runs.manager import RunManager
from .core.protocols import ThreadStore
from .store import SqliteStore
from .store.supabase_threads import SupabaseThreadStore
from .tools.factory import build_tool_runner

log = logging.getLogger("verity.main")


def build_app(settings: Settings) -> FastAPI:
    """Wire every builder's module into one app. Raises VerityError for a broken task pack."""
    packs = load_packs(settings.tasks_dir)  # fail fast on a missing or tampered pack
    custom_packs = load_custom_packs(settings.data_dir / "custom_packs")  # CE3: approved in an earlier session
    packs.update(custom_packs)
    if custom_packs:
        log.info("loaded %d custom block(s) from an earlier session", len(custom_packs))
    if not any(pack.live_preset for pack in packs.values()):
        log.warning("no live task packs found under %s: POST /runs will reject every preset", settings.tasks_dir)

    provider = build_provider(settings)  # builder B
    tools = build_tool_runner(settings)  # builder C
    store = SqliteStore(settings)
    adapter = build_search_adapter(settings)  # Serper, the fake adapter, or None (research off)
    research = (
        ResearchService(settings, store, provider, adapter, live_families(packs)) if adapter is not None else None
    )
    # Chats and each user's list of runs: Supabase when it is configured (threads.store), else SqliteStore, which is also
    # the ThreadStore (migrations 6 and 7). The agent and the routes see only the protocol.
    supabase = SupabaseThreadStore(settings) if settings.threads_store() == "supabase" else None
    threads: ThreadStore = supabase if supabase is not None else store
    service = RunManager(settings, store, provider, tools, packs, research=research, threads=threads)  # builder D
    log.info("research: %s", adapter.name if adapter is not None else "off")
    log.info("chats: %s, accounts: %s", "supabase" if supabase is not None else "sqlite", settings.supabase_auth_enabled())

    return create_app(
        settings,
        service,
        startup=[store.open, *([supabase.open] if supabase is not None else [])],
        # Run in reverse: the search and LLM clients close first, the stores last.
        shutdown=[
            store.close,
            *([supabase.close] if supabase is not None else []),
            provider.aclose,
            *([research.aclose] if research is not None else []),
        ],
    )


def _configure_logging() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    for handler in logging.getLogger().handlers:
        handler.addFilter(RedactFilter())


def main() -> None:
    _configure_logging()
    settings = load_settings()

    problems = settings.production_problems()
    if problems:
        for problem in problems:
            print(f"verity-api: refusing to start: {problem}", file=sys.stderr)
        raise SystemExit(1)

    try:
        app = build_app(settings)
    except VerityError as exc:
        print(f"verity-api: refusing to start: {exc}", file=sys.stderr)
        raise SystemExit(1) from None

    log.info(
        "starting: env=%s provider=%s tools=%s auth_required=%s listen=%s:%s",
        settings.env,
        settings.provider,
        settings.tool_backend,
        settings.auth.required,
        settings.server.host,
        settings.server.port,
    )
    # UNVERIFIED: on Windows uvicorn must keep the default Proactor event loop, which subprocess-based tool
    # runners need (asyncio.create_subprocess_exec). Check the first LocalDockerRunner call under verity-api.
    # workers=1 is required: rate limits, the run registry and the SSE fan-out are in-process (ARCHITECTURE 18).
    uvicorn.run(
        app,
        host=settings.server.host,
        port=settings.server.port,
        workers=1,
        log_config=log_config(),
        server_header=False,
    )


if __name__ == "__main__":
    main()
