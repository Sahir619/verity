"""Settings: backend/config.toml, then .env, then process env (highest wins after init kwargs).

Precedence (high to low): init kwargs > VERITY_* env (nested with "__") > named env (LLM_*, SANDBOX_*, SERPER_API_KEY,
TAVILY_API_KEY, SUPABASE_*)
> backend/.env file > config.toml > field defaults.

Use `load_settings()`; never read os.environ for config anywhere else.
"""

import copy
import os
import tomllib
from contextvars import ContextVar
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

from pydantic import BaseModel, Field, SecretStr, field_validator, model_validator
from pydantic.fields import FieldInfo
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict

AGENT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = AGENT_ROOT / "config.toml"

EnvName = Literal["development", "production", "test"]
ProviderName = Literal["fake", "nebius", "openai_compat"]
ToolBackendName = Literal["local_docker", "nebius_sandbox"]
RoleName = Literal["planner", "rtl_gen", "repair", "optimizer", "router", "parser", "chat", "research", "memory"]
Tier = Literal["ultra", "super"]
ReasoningEffort = Literal["low", "medium", "high"]
# The four roles inside a run (Nemotron writes the plan and every line of Verilog) and the chat roles around it.
RUN_ROLES: tuple[RoleName, ...] = ("planner", "rtl_gen", "repair", "optimizer")
CHAT_ROLES: tuple[RoleName, ...] = ("router", "parser", "chat", "research", "memory")
ALL_ROLES: tuple[RoleName, ...] = RUN_ROLES + CHAT_ROLES
PathMode = Literal["fast", "full"]
StrategyName = Literal["SIMPLIFY", "OPTIMIZE", "EXPLORE"]


# ---------- sections ----------
class ServerConfig(BaseModel):
    host: str = "127.0.0.1"
    port: int = 8000


class FallbackStep(BaseModel):
    """One model of a fallback chain. The primary route's own thinking and reasoning_effort are never carried over."""

    model: str
    thinking: bool | None = None  # False asks a model that takes the switch not to reason; None sends nothing
    reasoning_effort: ReasoningEffort | None = None  # sent as reasoning_effort when set


class FallbackChain(BaseModel):
    """The models tried, in order, when the primary times out, cannot be reached or answers 5xx. Empty: no fallback."""

    chain: list[FallbackStep] = Field(default_factory=list)


def _default_fallback() -> dict[str, FallbackChain]:
    """GLM on Token Factory timed out on 2026-10-23 while Nemotron Ultra answered in 0.3 s. DeepSeek V4 Pro (no thinking
    switch, no reasoning_effort) stands in first, Ultra second."""
    chain = [
        FallbackStep(model="deepseek-ai/DeepSeek-V4-Pro"),
        FallbackStep(model="nvidia/Nemotron-3-Ultra-550b-a55b", thinking=False),
    ]
    return {
        "zai-org/GLM-5.3": FallbackChain(chain=list(chain)),
        "zai-org/GLM-5.3-Flash": FallbackChain(chain=list(chain)),
    }


class LLMConfig(BaseModel):
    base_url: str = "https://api.tokenfactory.nebius.com/v1"
    api_key: SecretStr | None = None
    model_ultra: str = "nvidia/Nemotron-3-Ultra-550b-a55b"
    model_super: str = "nvidia/nemotron-3-super-120b-a12b"
    retries: int = 2
    backoff_s: float = 2.0
    cache_responses: bool = False
    production_host: str = "api.tokenfactory.nebius.com"
    # Models that take chat_template_kwargs.enable_thinking (prefix match on the model id). GLM 5.3 accepts the field
    # but ignores it (probe 2026-10-22), so it is sent only where it works.
    thinking_switch_prefixes: list[str] = Field(default_factory=lambda: ["nvidia/"])
    # Fallback chains by primary model id (see OpenAICompatProvider.complete). A model that fails with a timeout, a
    # connection error or a 5xx is marked unhealthy for fallback_cooldown_s and its requests go to the next model of its
    # chain meanwhile. A worst-case hold prices each call at the highest input price and the highest output price across
    # the primary and its chain (Settings.hold_price_for), so a chain model may cost more per token than the primary.
    fallback: dict[str, FallbackChain] = Field(default_factory=_default_fallback)
    fallback_cooldown_s: float = Field(default=300.0, ge=0)

    @model_validator(mode="after")
    def _chains_do_not_loop(self) -> "LLMConfig":
        for primary, entry in self.fallback.items():
            if any(step.model == primary for step in entry.chain):
                raise ValueError(f"llm.fallback chain of {primary} lists {primary} itself")
        return self


class RouteConfig(BaseModel):
    """One role's model. `model` (an exact model id) wins over `tier`; the tier stays the fallback."""

    tier: Tier = "super"
    model: str | None = None
    temperature: float = 0.2
    reasoning_effort: ReasoningEffort | None = None  # sent as reasoning_effort when set (GLM 5.3)
    thinking: bool | None = None  # False asks a model that takes the switch not to reason; None sends nothing
    max_output_tokens: int | None = None  # None: plan_max_output for the planner, rtl_max_output otherwise
    timeout_s: int | None = None  # None: the tier timeout


def _default_routes() -> dict[RoleName, RouteConfig]:
    return {
        "planner": RouteConfig(tier="ultra", temperature=0.2),
        "rtl_gen": RouteConfig(tier="ultra", temperature=0.2),
        "repair": RouteConfig(tier="ultra", temperature=0.1),
        "optimizer": RouteConfig(tier="super", temperature=0.6),
        **_default_chat_routes(),
    }


def _default_chat_routes() -> dict[RoleName, RouteConfig]:
    """Chat roles (probe 2026-10-22, scripts/probe_models.py). GLM 5.3 always reasons; effort low keeps it short."""
    glm = "zai-org/GLM-5.3"
    return {
        "router": RouteConfig(
            model="nvidia/Nemotron-3_5-Lightning", temperature=0.0, thinking=False, max_output_tokens=300, timeout_s=30
        ),
        "parser": RouteConfig(model=glm, temperature=0.0, reasoning_effort="low", max_output_tokens=2_000, timeout_s=60),
        "chat": RouteConfig(model=glm, temperature=0.3, reasoning_effort="low", max_output_tokens=2_000, timeout_s=60),
        "research": RouteConfig(
            model=glm, temperature=0.2, reasoning_effort="low", max_output_tokens=3_000, timeout_s=90
        ),
        "memory": RouteConfig(model=glm, temperature=0.0, reasoning_effort="low", max_output_tokens=800, timeout_s=45),
    }


class Price(BaseModel):
    input_per_m: float
    output_per_m: float


def _default_prices() -> dict[str, Price]:
    return {
        "nvidia/Nemotron-3-Ultra-550b-a55b": Price(input_per_m=1.0, output_per_m=3.0),
        "nvidia/nemotron-3-super-120b-a12b": Price(input_per_m=0.3, output_per_m=0.9),
        "fallback": Price(input_per_m=1.0, output_per_m=3.0),
    }


class RunBudget(BaseModel):
    max_llm_calls: int = 20
    max_input_tokens: int = 150_000
    max_output_tokens: int = 200_000
    max_usd: float = 0.50
    max_candidates: int = 6
    max_repairs_baseline: int = 2
    max_repairs_child: int = 1
    max_rounds: int = 2
    max_wall_s: int = 900


class CallBudget(BaseModel):
    plan_max_output: int = 12_000
    rtl_max_output: int = 24_000
    max_prompt_tokens: int = 12_000


class BudgetConfig(BaseModel):
    run: RunBudget = Field(default_factory=RunBudget)
    call: CallBudget = Field(default_factory=CallBudget)


class LimitsConfig(BaseModel):
    auth_per_ip: int = 10
    auth_window_s: int = 600
    api_per_session_per_min: int = 60
    runs_per_session_per_hour: int = 3
    parse_per_session_per_min: int = 6
    ask_per_session_per_min: int = 6  # POST /runs/{id}/ask per session
    message_per_session_per_min: int = 6  # POST /threads/{id}/messages per session
    concurrent_per_session: int = 1
    concurrent_global: int = 2
    daily_usd: float = 10.0
    session_usd: float = 3.0  # per session (later per signed-in user) per UTC day, persisted in SQLite
    default_mode: PathMode = "full"


class TimeoutsConfig(BaseModel):
    lint_s: int = 20
    sim_s: int = 60
    synth_s: int = 120
    sta_s: int = 60
    eqy_s: int = 180
    golden_check_s: int = 20  # CE3: pure Python, no tool startup, so a short timeout
    sandbox_overhead_s: int = 15
    llm_ultra_s: int = 120
    llm_super_s: int = 120

    @property
    def verify_s(self) -> int:
        """Inner budget of /verity/bin/verify (contract + lint + sim)."""
        return self.lint_s + self.sim_s

    @property
    def measure_s(self) -> int:
        """Inner budget of /verity/bin/measure (synth + sta + circuit)."""
        return self.synth_s + self.sta_s


class AuthConfig(BaseModel):
    required: bool = False
    session_ttl_s: int = 43_200
    stream_ttl_s: int = 1_800
    admin_emails: list[str] = Field(default_factory=lambda: ["admin@verity.com"])
    super_admin_emails: list[str] = Field(default_factory=lambda: ["supadmin@verity.com"])
    initial_credits: int = 3
    # Local testing only (VERITY_AUTH__LOCAL_ADMIN): with required = false, an anonymous visitor is a super admin.
    # Ignored outside development; the production guard refuses it.
    local_admin: bool = False

    @field_validator("admin_emails", mode="before")
    @classmethod
    def _parse_admin_emails(cls, value: Any) -> list[str]:
        if isinstance(value, str):
            return [x.strip().lower() for x in value.split(",") if x.strip()]
        if isinstance(value, (list, tuple)):
            return [str(x).strip().lower() for x in value if str(x).strip()]
        return ["admin@verity.com"]

    @field_validator("super_admin_emails", mode="before")
    @classmethod
    def _parse_super_admin_emails(cls, value: Any) -> list[str]:
        if isinstance(value, str):
            return [x.strip().lower() for x in value.split(",") if x.strip()]
        if isinstance(value, (list, tuple)):
            return [str(x).strip().lower() for x in value if str(x).strip()]
        return ["supadmin@verity.com"]


class SSEConfig(BaseModel):
    ping_s: int = 15
    max_event_bytes: int = 65_536


class WatchdogConfig(BaseModel):
    interval_s: int = 30
    stall_s: int = 90
    heartbeat_s: int = 10


class PlanConfig(BaseModel):
    gate_auto_continue_ms: int = 10_000
    gate_after_replan_ms: int = 6_000
    max_hold_s: int = 120
    max_replans: int = 1
    max_blocks: int = 8
    json_retries: int = 1


class RtlConfig(BaseModel):
    max_bytes: int = 20_000


class BaselineConfig(BaseModel):
    """R4: v1 is sampled `samples` times in parallel; only the best one is repaired."""

    samples: int = Field(default=3, ge=1, le=5)
    temperature: float = 1.0  # the model card's recommendation; used for the samples only
    top_p: float = 0.95


class EvolutionConfig(BaseModel):
    round1_children: int = 3
    round1_strategies: list[StrategyName] = Field(
        default_factory=lambda: ["SIMPLIFY", "OPTIMIZE", "EXPLORE"]
    )
    round2_children: int = 2
    elites: int = 2
    round2_requires_improvement: bool = True
    max_prove_attempts: int = 2


class SignoffConfig(BaseModel):
    """Sign-off after the winner (CONTRACTS 6.3): more seeds, lint by tag, planted bugs; and failing-case waveforms."""

    enabled: bool = True
    extra_seeds: list[int] = Field(default_factory=lambda: [2, 3, 4, 5, 6, 7, 8, 9])
    max_mutants: int = Field(default=20, ge=0, le=40)
    max_survivor_proofs: int = Field(default=3, ge=0, le=6)  # EQY of surviving mutants against the winner
    mutant_timeout_s: int = 30  # one mutant's simulation
    strength_budget_s: int = 150  # all mutants together; the rest are not run
    max_s: int = 300  # the whole sign-off, after the result is shown; also capped by the run's wall-clock limit
    waves_on_repair: bool = True  # a waveform of the first failing case goes into each repair prompt


ResearchAdapterName = Literal["auto", "serper", "tavily", "fake", "off"]
# What research_adapter() resolves to. "both": Tavily searches the web, Serper searches Google Scholar and Patents.
ResearchAdapterMode = Literal["serper", "tavily", "both", "fake", "off"]


class ResearchConfig(BaseModel):
    """Fact-check research (web search plus one research-role summary). Keys: SERPER_API_KEY and TAVILY_API_KEY."""

    # auto: with a real LLM provider, Tavily for the web plus Serper for Scholar and Patents when both keys are set,
    # else whichever key exists; the fake adapter only on the fake LLM provider; research off on a real provider
    # without a key (canned sources are never shown as real research).
    # serper | tavily: that one service only (needs its key). fake: canned results, no network (refused in
    # production). off: no research.
    adapter: ResearchAdapterName = "auto"
    serper_base_url: str = "https://google.serper.dev"
    serper_host: str = "google.serper.dev"  # production guard: the key is only ever sent to this host
    api_key: SecretStr | None = None  # the Serper key (SERPER_API_KEY)
    tavily_base_url: str = "https://api.tavily.com"
    tavily_host: str = "api.tavily.com"  # production guard: the key is only ever sent to this host
    tavily_api_key: SecretStr | None = None  # TAVILY_API_KEY
    timeout_s: float = 8.0
    num: int = Field(default=10, ge=1, le=10)  # results per query
    max_queries: int = Field(default=3, ge=1, le=3)  # Serper calls (credits) per research
    max_sources: int = 8
    per_domain: int = 2
    snippet_chars: int = 300
    ttl_days: int = 30  # web research expires after this
    usd_per_credit: float = 0.001  # Serper Starter pack rate, counted into the daily spend
    tavily_usd_per_credit: float = 0.008  # Tavily pay-as-you-go rate (a basic search is 1 credit), counted the same way
    per_session_per_hour: int = 10  # new (not reused) research per session
    serper_credits_per_day: int = 300
    tavily_credits_per_day: int = 100
    nearest_distance: float = 0.25  # knob distance for reusing research of the same family


class CpuJobConfig(BaseModel):
    width: int = Field(default=3, ge=1, le=3)
    max_usd: float = Field(default=4.00, gt=0, le=4.00)
    block_retry: int = Field(default=1, ge=0, le=1)
    improve_rounds: int = Field(default=2, ge=0, le=2)


class AgentConfig(BaseModel):
    """The thread agent (POST /threads/{id}/messages)."""

    # The agent part of one message (its model calls and tools, not the fact check before it). After this many seconds it
    # stops calling the model and code answers with what its steps found. The web app gives up at 120 s.
    turn_deadline_s: float = Field(default=90.0, gt=0)


class SupabaseConfig(BaseModel):
    """Accounts and saved chats (backend ARCHITECTURE 17.5). SUPABASE_URL, SUPABASE_SECRET_KEY, SUPABASE_PUBLISHABLE_KEY."""

    url: str | None = None  # https://<ref>.supabase.co; sign-in tokens are checked against it
    secret_key: SecretStr | None = None  # server only; sent as the `apikey` header to the Data API, never to a browser
    publishable_key: SecretStr | None = None  # only for the legacy check of a project that still uses the shared secret
    jwks_cache_s: int = Field(default=600, ge=0, le=600)  # the signing keys are never kept longer than 10 minutes
    timeout_s: float = Field(default=8.0, gt=0)
    host_suffix: str = ".supabase.co"  # production guard: the url host must end with this


# auto: Supabase when the url and the secret key are set, else SQLite. sqlite: always local. supabase: Supabase only
# (the production guard then needs the url and the secret key).
ThreadStoreName = Literal["auto", "sqlite", "supabase"]


class ThreadsConfig(BaseModel):
    store: ThreadStoreName = "auto"


class NebiusSandboxConfig(BaseModel):
    base_url: str = ""
    api_key: SecretStr | None = None
    image_ref: str = ""


class ToolsConfig(BaseModel):
    image: str = "verity-tools:0.2"
    docker_bin: str = "docker"
    docker_user: str = "1000:1000"  # "" omits --user (Docker Desktop bind mounts may need it, see README)
    memory: str = "2g"
    cpus: str = "2"
    pids_limit: int = 256
    output_cap_bytes: int = 65_536
    infra_retries: int = 1
    nebius_sandbox: NebiusSandboxConfig = Field(default_factory=NebiusSandboxConfig)


# ---------- custom sources ----------
_TOML_DATA: ContextVar[dict[str, Any]] = ContextVar("verity_toml_data", default={})
_DOTENV_DATA: ContextVar[dict[str, str]] = ContextVar("verity_dotenv_data", default={})

# Named env vars that do not carry the VERITY_ prefix.
NAMED_ENV: dict[str, tuple[str, ...]] = {
    "LLM_BASE_URL": ("llm", "base_url"),
    "LLM_API_KEY": ("llm", "api_key"),
    "LLM_MODEL_ULTRA": ("llm", "model_ultra"),
    "LLM_MODEL_SUPER": ("llm", "model_super"),
    "SANDBOX_BASE_URL": ("tools", "nebius_sandbox", "base_url"),
    "SANDBOX_API_KEY": ("tools", "nebius_sandbox", "api_key"),
    "SERPER_API_KEY": ("research", "api_key"),
    "TAVILY_API_KEY": ("research", "tavily_api_key"),
    "SUPABASE_URL": ("supabase", "url"),
    "SUPABASE_SECRET_KEY": ("supabase", "secret_key"),
    "SUPABASE_PUBLISHABLE_KEY": ("supabase", "publishable_key"),
}


class _TomlSource(PydanticBaseSettingsSource):
    def get_field_value(self, field: FieldInfo, field_name: str) -> tuple[Any, str, bool]:
        return None, field_name, False

    def __call__(self) -> dict[str, Any]:
        return copy.deepcopy(_TOML_DATA.get())


class _NamedEnvSource(PydanticBaseSettingsSource):
    """LLM_*, SANDBOX_*, SERPER_API_KEY, TAVILY_API_KEY and SUPABASE_*. Process env wins over backend/.env. Empty values are ignored."""

    def get_field_value(self, field: FieldInfo, field_name: str) -> tuple[Any, str, bool]:
        return None, field_name, False

    def __call__(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        dotenv = _DOTENV_DATA.get()
        for name, path in NAMED_ENV.items():
            value = os.environ.get(name) or dotenv.get(name)
            if not value:
                continue
            node = out
            for key in path[:-1]:
                node = node.setdefault(key, {})
            node[path[-1]] = value
        return out


# ---------- settings ----------
class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="VERITY_",
        env_nested_delimiter="__",
        extra="ignore",
        case_sensitive=False,
    )

    env: EnvName = "development"
    provider: ProviderName = "fake"
    tool_backend: ToolBackendName = "local_docker"
    data_dir: Path = Path("data")
    tasks_dir: Path = Path("tasks")
    cors_origins: str = "http://localhost:3000"
    access_code_hashes: SecretStr | None = None  # "role:sha256hex,role:sha256hex"
    signing_secret: SecretStr | None = None
    git_sha: str | None = None

    server: ServerConfig = Field(default_factory=ServerConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    routes: dict[RoleName, RouteConfig] = Field(default_factory=_default_routes)
    prices: dict[str, Price] = Field(default_factory=_default_prices)
    budget: BudgetConfig = Field(default_factory=BudgetConfig)
    limits: LimitsConfig = Field(default_factory=LimitsConfig)
    timeouts: TimeoutsConfig = Field(default_factory=TimeoutsConfig)
    auth: AuthConfig = Field(default_factory=AuthConfig)
    sse: SSEConfig = Field(default_factory=SSEConfig)
    watchdog: WatchdogConfig = Field(default_factory=WatchdogConfig)
    plan: PlanConfig = Field(default_factory=PlanConfig)
    rtl: RtlConfig = Field(default_factory=RtlConfig)
    baseline: BaselineConfig = Field(default_factory=BaselineConfig)
    evolution: EvolutionConfig = Field(default_factory=EvolutionConfig)
    signoff: SignoffConfig = Field(default_factory=SignoffConfig)
    tools: ToolsConfig = Field(default_factory=ToolsConfig)
    research: ResearchConfig = Field(default_factory=ResearchConfig)
    agent: AgentConfig = Field(default_factory=AgentConfig)
    cpu_job: CpuJobConfig = Field(default_factory=CpuJobConfig)
    supabase: SupabaseConfig = Field(default_factory=SupabaseConfig)
    threads: ThreadsConfig = Field(default_factory=ThreadsConfig)

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        # Earlier entries win.
        return (
            init_settings,
            env_settings,
            _NamedEnvSource(settings_cls),
            dotenv_settings,
            _TomlSource(settings_cls),
        )

    @model_validator(mode="after")
    def _resolve_paths(self) -> "Settings":
        if not self.data_dir.is_absolute():
            self.data_dir = (AGENT_ROOT / self.data_dir).resolve()
        if not self.tasks_dir.is_absolute():
            self.tasks_dir = (AGENT_ROOT / self.tasks_dir).resolve()
        missing = set(RUN_ROLES) - set(self.routes)
        if missing:
            raise ValueError(f"routes missing for roles: {sorted(missing)}")
        for role, route in _default_chat_routes().items():
            self.routes.setdefault(role, route)  # a config.toml that predates the chat roles keeps working
        if "fallback" not in self.prices:
            raise ValueError("prices.fallback is required")
        return self

    # ---------- derived values ----------
    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def access_codes(self) -> list[tuple[str, str]]:
        """[(role, sha256 hex of the access code)]. Format: "team:abc...,judge:def..."."""
        if self.access_code_hashes is None:
            return []
        out: list[tuple[str, str]] = []
        for item in self.access_code_hashes.get_secret_value().split(","):
            item = item.strip()
            if not item:
                continue
            role, _, digest = item.partition(":")
            if not digest:
                role, digest = "user", role
            out.append((role.strip(), digest.strip().lower()))
        return out

    @property
    def run_cap_usd(self) -> float:
        return self.budget.run.max_usd

    def model_for(self, role: RoleName) -> str:
        """The route's exact model id first, then its tier."""
        route = self.routes[role]
        if route.model:
            return route.model
        return self.llm.model_ultra if route.tier == "ultra" else self.llm.model_super

    def temperature_for(self, role: RoleName) -> float:
        return self.routes[role].temperature

    def max_output_for(self, role: RoleName) -> int:
        route = self.routes[role]
        if route.max_output_tokens is not None:
            return route.max_output_tokens
        return self.budget.call.plan_max_output if role == "planner" else self.budget.call.rtl_max_output

    def llm_timeout_for(self, role: RoleName) -> int:
        route = self.routes[role]
        if route.timeout_s is not None:
            return route.timeout_s
        return self.timeouts.llm_ultra_s if route.tier == "ultra" else self.timeouts.llm_super_s

    def thinking_for(self, role: RoleName) -> bool | None:
        """The route's thinking switch, only for models that take it (llm.thinking_switch_prefixes)."""
        thinking = self.routes[role].thinking
        if thinking is None:
            return None
        model = self.model_for(role)
        return thinking if any(model.startswith(p) for p in self.llm.thinking_switch_prefixes) else None

    def reasoning_effort_for(self, role: RoleName) -> str | None:
        return self.routes[role].reasoning_effort

    def fallback_chain_for(self, model: str) -> list[FallbackStep]:
        """The models tried after `model` fails with a timeout, a connection error or a 5xx; empty when it has none."""
        entry = self.llm.fallback.get(model)
        return list(entry.chain) if entry is not None else []

    def price_for(self, model: str) -> Price:
        return self.prices.get(model) or self.prices["fallback"]

    def hold_price_for(self, model: str) -> Price:
        """The price a worst-case hold uses for a call to `model`: the highest input price and the highest output price
        across the model and every model of its fallback chain, since any of them can be the one that answers."""
        prices = [self.price_for(model), *(self.price_for(step.model) for step in self.fallback_chain_for(model))]
        return Price(input_per_m=max(p.input_per_m for p in prices), output_per_m=max(p.output_per_m for p in prices))

    def models_by_role(self) -> dict[str, str]:
        return {role: self.model_for(role) for role in ALL_ROLES}

    def has_serper_key(self) -> bool:
        key = self.research.api_key
        return key is not None and bool(key.get_secret_value())

    def has_tavily_key(self) -> bool:
        key = self.research.tavily_api_key
        return key is not None and bool(key.get_secret_value())

    def research_adapter(self) -> ResearchAdapterMode:
        """Which research adapter this process uses (see ResearchConfig.adapter)."""
        mode = self.research.adapter
        serper, tavily = self.has_serper_key(), self.has_tavily_key()
        if mode == "off":
            return "off"
        if mode == "serper":
            return "serper" if serper else "off"
        if mode == "tavily":
            return "tavily" if tavily else "off"
        if mode == "fake":
            return "fake"
        # auto: canned results only ever stand in for a search service on the fake LLM provider. A real provider
        # without a key gets research off, so no invented source is summarised by a paid model and shown as a real
        # citation. With both keys Tavily searches the web and Serper searches Scholar and Patents.
        if self.provider == "fake":
            return "off" if self.env == "production" else "fake"
        if serper and tavily:
            return "both"
        return "tavily" if tavily else "serper" if serper else "off"

    def sandbox_api_key(self) -> SecretStr | None:
        return self.tools.nebius_sandbox.api_key or self.llm.api_key

    def supabase_auth_enabled(self) -> bool:
        """Sign-in tokens are accepted (the project url is set)."""
        return bool(self.supabase.url and self.supabase.url.strip())

    def has_supabase_secret(self) -> bool:
        key = self.supabase.secret_key
        return key is not None and bool(key.get_secret_value())

    def threads_store(self) -> Literal["sqlite", "supabase"]:
        """Where chats and each user's list of runs live (see ThreadsConfig)."""
        mode = self.threads.store
        if mode == "auto":
            return "supabase" if self.supabase_auth_enabled() and self.has_supabase_secret() else "sqlite"
        return mode

    def is_super_admin(self, email: str | None, role: str) -> bool:
        """True if the principal is a super administrator (role == 'super_admin' or email in super_admin_emails)."""
        if role == "super_admin":
            return True
        if not email:
            return False
        clean = email.strip().lower()
        return clean in [e.strip().lower() for e in self.auth.super_admin_emails]

    def is_admin(self, email: str | None, role: str) -> bool:
        """True if the principal is an administrator (super_admin, role in team/admin, or email in admin/super_admin_emails)."""
        if self.is_super_admin(email, role):
            return True
        if role in ("admin", "team"):
            return True
        if not email:
            return False
        clean = email.strip().lower()
        return clean in [e.strip().lower() for e in self.auth.admin_emails]

    @staticmethod
    def can_build(role: str) -> bool:
        """Any signed-in principal may start every kind of build and is never charged credits. Signed in means a role
        other than 'anonymous' (a Supabase account, an access code session, or the local-admin visitor)."""
        return role != "anonymous"

    def production_problems(self) -> list[str]:
        """Submission guard (ARCHITECTURE 21). main() refuses to start if this is non-empty in production."""
        if self.env != "production":
            return []
        problems: list[str] = []
        if self.provider != "nebius":
            problems.append(f"provider must be 'nebius' in production, got '{self.provider}'")
        host = urlparse(self.llm.base_url).hostname or ""
        if host != self.llm.production_host:
            problems.append(f"LLM_BASE_URL host must be {self.llm.production_host}, got '{host}'")
        if self.llm.api_key is None or not self.llm.api_key.get_secret_value():
            problems.append("LLM_API_KEY is not set")
        if not self.auth.required:
            problems.append("auth.required must be true in production")
        if self.auth.local_admin:
            problems.append("auth.local_admin must be off in production")
        if self.signing_secret is None or len(self.signing_secret.get_secret_value()) < 32:
            problems.append("VERITY_SIGNING_SECRET must be set (32+ chars)")
        if not self.access_codes and not self.supabase_auth_enabled():
            problems.append("sign-in needs SUPABASE_URL or VERITY_ACCESS_CODE_HASHES with at least one code hash")
        if self.supabase_auth_enabled():
            parsed = urlparse(self.supabase.url or "")
            if parsed.scheme != "https":
                problems.append("SUPABASE_URL must be an https url")
            if not (parsed.hostname or "").endswith(self.supabase.host_suffix):
                problems.append(f"SUPABASE_URL host must end with {self.supabase.host_suffix}, got '{parsed.hostname or ''}'")
        if self.threads_store() == "supabase" and not (self.supabase_auth_enabled() and self.has_supabase_secret()):
            problems.append("threads.store is 'supabase' but SUPABASE_URL or SUPABASE_SECRET_KEY is not set")
        if any(o == "*" for o in self.cors_origin_list):
            problems.append("CORS origin '*' is not allowed in production")
        if self.research.adapter == "fake":
            problems.append("research.adapter 'fake' is not allowed in production")
        if self.research.adapter == "serper" and self.research_adapter() != "serper":
            problems.append("research.adapter is 'serper' but SERPER_API_KEY is not set")
        if self.research.adapter == "tavily" and self.research_adapter() != "tavily":
            problems.append("research.adapter is 'tavily' but TAVILY_API_KEY is not set")
        if self.research_adapter() in ("serper", "both"):
            serper_host = urlparse(self.research.serper_base_url).hostname or ""
            if serper_host != self.research.serper_host:
                problems.append(f"research.serper_base_url host must be {self.research.serper_host}, got '{serper_host}'")
        if self.research_adapter() in ("tavily", "both"):
            tavily_host = urlparse(self.research.tavily_base_url).hostname or ""
            if tavily_host != self.research.tavily_host:
                problems.append(f"research.tavily_base_url host must be {self.research.tavily_host}, got '{tavily_host}'")
        return problems


def _read_dotenv(path: Path) -> dict[str, str]:
    """Minimal KEY=VALUE parser for backend/.env (no interpolation)."""
    out: dict[str, str] = {}
    if not path.is_file():
        return out
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export ") :]
        key, _, value = line.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        out[key.strip()] = value
    return out


def load_settings(config_path: Path | None = None, **overrides: Any) -> Settings:
    """Load config.toml (or $VERITY_CONFIG), backend/.env, and env. `overrides` win over everything."""
    path = config_path or Path(os.environ.get("VERITY_CONFIG", DEFAULT_CONFIG_PATH))
    data: dict[str, Any] = {}
    if path.is_file():
        with path.open("rb") as fh:
            data = tomllib.load(fh)
    dotenv_path = AGENT_ROOT / ".env"
    toml_token = _TOML_DATA.set(data)
    dotenv_token = _DOTENV_DATA.set(_read_dotenv(dotenv_path))
    try:
        env_file = dotenv_path if dotenv_path.is_file() else None
        return Settings(_env_file=env_file, **overrides)  # type: ignore[call-arg]
    finally:
        _TOML_DATA.reset(toml_token)
        _DOTENV_DATA.reset(dotenv_token)
