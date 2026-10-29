"""SQLite schema (ARCHITECTURE 7) plus the daily_spend table.

Migrations run in order on startup. The applied version lives in PRAGMA user_version, and every script is
written to be safe to re-run. To change the schema, append a new script; never edit an applied one.
"""

MIGRATIONS: tuple[str, ...] = (
    # ---- 1: initial schema ----
    """
    BEGIN;

    CREATE TABLE IF NOT EXISTS runs (
        id                    TEXT PRIMARY KEY,
        session_id            TEXT NOT NULL,
        preset_id             TEXT NOT NULL,
        priority              TEXT NOT NULL,
        mode                  TEXT NOT NULL,
        status                TEXT NOT NULL,
        created_at            TEXT NOT NULL,
        started_at            TEXT,
        ended_at              TEXT,
        heartbeat_at          TEXT,
        budget_json           TEXT NOT NULL,
        spent_json            TEXT NOT NULL,
        task_pack_sha256      TEXT NOT NULL,
        tool_image_digest     TEXT NOT NULL DEFAULT '',
        models_json           TEXT NOT NULL DEFAULT '{}',
        prompt_versions_json  TEXT NOT NULL DEFAULT '{}',
        error_reason          TEXT,
        winner_id             TEXT,
        git_sha               TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_runs_session ON runs (session_id, created_at);
    CREATE INDEX IF NOT EXISTS idx_runs_status ON runs (status);

    CREATE TABLE IF NOT EXISTS candidates (
        run_id             TEXT NOT NULL,
        id                 TEXT NOT NULL,
        label              TEXT NOT NULL,
        parent_id          TEXT,
        round              INTEGER NOT NULL,
        created_by         TEXT NOT NULL,
        status             TEXT NOT NULL,
        fitness            REAL,
        fitness_json       TEXT,
        ppa_json           TEXT,
        verification_json  TEXT,
        eqy_json           TEXT,
        reason             TEXT NOT NULL,
        fix_count          INTEGER NOT NULL DEFAULT 0,
        is_baseline        INTEGER NOT NULL DEFAULT 0,
        is_elite           INTEGER NOT NULL DEFAULT 0,
        updated_at         TEXT NOT NULL,
        PRIMARY KEY (run_id, id)
    );

    CREATE TABLE IF NOT EXISTS attempts (
        run_id             TEXT NOT NULL,
        candidate_id       TEXT NOT NULL,
        n                  INTEGER NOT NULL,
        attempt_by         TEXT NOT NULL,
        reason             TEXT NOT NULL,
        rtl_artifact       TEXT,
        rtl_sha256         TEXT,
        verification_json  TEXT NOT NULL,
        model_id           TEXT,
        prompt_version     TEXT,
        llm_request_id     TEXT,
        input_tokens       INTEGER NOT NULL DEFAULT 0,
        output_tokens      INTEGER NOT NULL DEFAULT 0,
        tool_image_digest  TEXT,
        created_at         TEXT NOT NULL,
        PRIMARY KEY (run_id, candidate_id, n)
    );

    CREATE TABLE IF NOT EXISTS events (
        run_id  TEXT NOT NULL,
        seq     INTEGER NOT NULL,
        t_ms    INTEGER NOT NULL,
        json    TEXT NOT NULL,
        PRIMARY KEY (run_id, seq)
    );

    CREATE TABLE IF NOT EXISTS artifacts (
        sha256      TEXT PRIMARY KEY,
        path        TEXT NOT NULL,
        bytes       INTEGER NOT NULL,
        mime        TEXT NOT NULL,
        created_at  TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS eval_results (
        eval_run_id   TEXT NOT NULL,
        task_id       TEXT NOT NULL,
        suite         TEXT NOT NULL,
        metrics_json  TEXT NOT NULL,
        git_sha       TEXT,
        created_at    TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_eval_results_run ON eval_results (eval_run_id);

    CREATE TABLE IF NOT EXISTS daily_spend (
        day  TEXT PRIMARY KEY,
        usd  REAL NOT NULL DEFAULT 0
    );

    PRAGMA user_version = 1;
    COMMIT;
    """,
    # ---- 2: params and title on runs ----
    """
    BEGIN;
    ALTER TABLE runs ADD COLUMN params_json TEXT;
    ALTER TABLE runs ADD COLUMN title TEXT;
    PRAGMA user_version = 2;
    COMMIT;
    """,
    # ---- 3: fact-check research, sources and run outcomes (design-partner plan, claim 2) ----
    # research is shared between sessions by key, so it keeps the sha256 of the user's sentence, never the sentence.
    # research_grant says which sessions were handed a research id (POST /runs researchId ownership check).
    # run_outcome and strategy_outcome are written once per run from both endings (idempotent upsert); the *_rel
    # columns are ratios against that run's own v1.
    """
    BEGIN;

    CREATE TABLE IF NOT EXISTS research (
        id               TEXT PRIMARY KEY,
        created_at       TEXT NOT NULL,
        expires_at       TEXT NOT NULL,
        key              TEXT,
        query_key        TEXT NOT NULL,
        family           TEXT,
        params_json      TEXT,
        intent_class     TEXT NOT NULL,
        intent_label     TEXT NOT NULL,
        text_sha256      TEXT NOT NULL,
        verdict          TEXT NOT NULL,
        status           TEXT NOT NULL,
        queries_json     TEXT NOT NULL,
        summary_json     TEXT NOT NULL,
        techniques_json  TEXT NOT NULL,
        done_before      INTEGER NOT NULL DEFAULT 0,
        model            TEXT,
        prompt_version   TEXT,
        input_tokens     INTEGER NOT NULL DEFAULT 0,
        output_tokens    INTEGER NOT NULL DEFAULT 0,
        llm_usd          REAL NOT NULL DEFAULT 0,
        serper_credits   INTEGER NOT NULL DEFAULT 0,
        usd              REAL NOT NULL DEFAULT 0,
        latency_ms       INTEGER NOT NULL DEFAULT 0
    );
    CREATE INDEX IF NOT EXISTS idx_research_key ON research (key, created_at);
    CREATE INDEX IF NOT EXISTS idx_research_query ON research (query_key, created_at);
    CREATE INDEX IF NOT EXISTS idx_research_family ON research (family, created_at);

    CREATE TABLE IF NOT EXISTS source (
        id          TEXT PRIMARY KEY,
        url         TEXT NOT NULL UNIQUE,
        domain      TEXT NOT NULL,
        tier        TEXT NOT NULL,
        title       TEXT NOT NULL,
        first_seen  TEXT NOT NULL,
        last_seen   TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS research_source (
        research_id  TEXT NOT NULL,
        source_id    TEXT NOT NULL,
        n            INTEGER NOT NULL,
        endpoint     TEXT NOT NULL,
        query_idx    INTEGER NOT NULL DEFAULT 0,
        rank         INTEGER NOT NULL DEFAULT 0,
        snippet      TEXT NOT NULL DEFAULT '',
        pub_date_raw TEXT,
        score        REAL NOT NULL DEFAULT 0,
        relevance    REAL NOT NULL DEFAULT 0,
        cited        INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (research_id, source_id)
    );

    CREATE TABLE IF NOT EXISTS research_grant (
        research_id   TEXT NOT NULL,
        session_hmac  TEXT NOT NULL,
        created_at    TEXT NOT NULL,
        PRIMARY KEY (research_id, session_hmac)
    );

    CREATE TABLE IF NOT EXISTS run_outcome (
        run_id             TEXT PRIMARY KEY,
        research_id        TEXT,
        family             TEXT NOT NULL,
        params_json        TEXT NOT NULL,
        key                TEXT NOT NULL,
        priority           TEXT NOT NULL,
        mode               TEXT NOT NULL,
        status             TEXT NOT NULL,
        partial            INTEGER NOT NULL DEFAULT 0,
        pack_sha256        TEXT NOT NULL,
        tool_image_digest  TEXT NOT NULL,
        models_json        TEXT NOT NULL DEFAULT '{}',
        v1_area_um2        REAL,
        v1_clock_ns        REAL,
        v1_power_mw        REAL,
        winner_id          TEXT,
        winner_label       TEXT,
        winner_strategy    TEXT,
        winner_technique   TEXT,
        winner_area_rel    REAL,
        winner_clock_rel   REAL,
        winner_power_rel   REAL,
        winner_eqy         TEXT,
        versions           INTEGER NOT NULL DEFAULT 0,
        fixes              INTEGER NOT NULL DEFAULT 0,
        rejected           INTEGER NOT NULL DEFAULT 0,
        usd                REAL NOT NULL DEFAULT 0,
        llm_calls          INTEGER NOT NULL DEFAULT 0,
        wall_s             REAL NOT NULL DEFAULT 0,
        created_at         TEXT NOT NULL,
        updated_at         TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_run_outcome_key ON run_outcome (key, created_at);

    CREATE TABLE IF NOT EXISTS strategy_outcome (
        run_id             TEXT NOT NULL,
        candidate_id       TEXT NOT NULL,
        label              TEXT NOT NULL,
        key                TEXT NOT NULL,
        pack_sha256        TEXT NOT NULL,
        tool_image_digest  TEXT NOT NULL,
        strategy           TEXT NOT NULL,
        technique          TEXT,
        result             TEXT NOT NULL,
        area_rel           REAL,
        clock_rel          REAL,
        power_rel          REAL,
        PRIMARY KEY (run_id, candidate_id)
    );
    CREATE INDEX IF NOT EXISTS idx_strategy_outcome_key ON strategy_outcome (key);

    ALTER TABLE runs ADD COLUMN research_id TEXT;
    ALTER TABLE daily_spend ADD COLUMN serper_credits INTEGER NOT NULL DEFAULT 0;

    PRAGMA user_version = 3;
    COMMIT;
    """,
    # ---- 4: Tavily credits (research uses Tavily for the web next to Serper) ----
    # research.tavily_credits is what one research spent; daily_spend.tavily_credits is the day's counter.
    """
    BEGIN;

    ALTER TABLE research ADD COLUMN tavily_credits INTEGER NOT NULL DEFAULT 0;
    ALTER TABLE daily_spend ADD COLUMN tavily_credits INTEGER NOT NULL DEFAULT 0;

    PRAGMA user_version = 4;
    COMMIT;
    """,
    # ---- 5: per-session daily spend (the session limit next to the global daily limit) ----
    # One row per session and UTC day, added to in the same transaction as daily_spend. session_id holds the user id
    # once sign-in arrives (core.budget.spend_owner is the one place that decides the key).
    """
    BEGIN;

    CREATE TABLE IF NOT EXISTS session_spend (
        session_id  TEXT NOT NULL,
        day         TEXT NOT NULL,
        usd         REAL NOT NULL DEFAULT 0,
        PRIMARY KEY (session_id, day)
    );

    PRAGMA user_version = 5;
    COMMIT;
    """,
    # ---- 6: threads (the bench chat after the first chip request, POST /threads/{id}/messages) ----
    # owner is core.budget.spend_owner(session id): the session today, the user id after sign-in. facts_json is written
    # by code only (verdict, block, settings, run ids, the pending proposal). summary is the 20 to 40 word note of the
    # turns up to summarized_through (a thread_turns.seq). A turn is one user row plus one verity row: seq 2n-1 and 2n.
    """
    BEGIN;

    CREATE TABLE IF NOT EXISTS threads (
        owner               TEXT NOT NULL,
        id                  TEXT NOT NULL,
        created_at          TEXT NOT NULL,
        updated_at          TEXT NOT NULL,
        facts_json          TEXT NOT NULL,
        summary             TEXT NOT NULL DEFAULT '',
        summarized_through  INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (owner, id)
    );

    CREATE TABLE IF NOT EXISTS thread_turns (
        owner       TEXT NOT NULL,
        thread_id   TEXT NOT NULL,
        seq         INTEGER NOT NULL,
        role        TEXT NOT NULL CHECK (role IN ('user', 'verity')),
        text        TEXT NOT NULL,
        steps_json  TEXT,
        created_at  TEXT NOT NULL,
        PRIMARY KEY (owner, thread_id, seq)
    );

    PRAGMA user_version = 6;
    COMMIT;
    """,
    # ---- 7: accounts (a user's list of runs, and the stored wire reply of a thread turn) ----
    # user_runs is the run index behind GET /runs/mine: one row per run an owner started, newest first. owner is
    # core.budget.spend_owner(session id), "u:" + the user id for a signed-in user. thread_turns.reply_json holds the
    # {"check", "reply"} wire JSON of a Verity row (at most 32 KB; NULL for older rows and for larger replies).
    """
    BEGIN;

    CREATE TABLE IF NOT EXISTS user_runs (
        owner       TEXT NOT NULL,
        run_id      TEXT NOT NULL,
        thread_id   TEXT,
        title       TEXT NOT NULL,
        created_at  TEXT NOT NULL,
        PRIMARY KEY (owner, run_id)
    );
    CREATE INDEX IF NOT EXISTS idx_user_runs_owner ON user_runs (owner, created_at DESC);

    ALTER TABLE thread_turns ADD COLUMN reply_json TEXT;

    PRAGMA user_version = 7;
    COMMIT;
    """,
    # ---- 8: user credits (runs allowed per account, default 3) ----
    # Tracks remaining run credits for an owner (u:<user id> or session id).
    """
    BEGIN;

    CREATE TABLE IF NOT EXISTS user_credits (
        owner       TEXT NOT NULL PRIMARY KEY,
        credits     INTEGER NOT NULL,
        created_at  TEXT NOT NULL,
        updated_at  TEXT NOT NULL
    );

    PRAGMA user_version = 8;
    COMMIT;
    """,
    # ---- 9: user credits email and role (super admin tracking) ----
    """
    BEGIN;

    ALTER TABLE user_credits ADD COLUMN email TEXT;
    ALTER TABLE user_credits ADD COLUMN role TEXT NOT NULL DEFAULT 'user';

    PRAGMA user_version = 9;
    COMMIT;
    """,
    # ---- 10: CPU jobs, alongside local runs and their spend ----
    """
    BEGIN;
    CREATE TABLE cpu_jobs (
        id TEXT PRIMARY KEY,
        owner TEXT NOT NULL,
        created_at TEXT NOT NULL,
        status TEXT NOT NULL,
        record_json TEXT NOT NULL
    );
    CREATE INDEX idx_cpu_jobs_owner ON cpu_jobs (owner, created_at DESC);
    PRAGMA user_version = 10;
    COMMIT;
    """,
)

