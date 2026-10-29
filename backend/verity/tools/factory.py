"""Pick the ToolRunner backend from settings (CONTRACTS section 1: build_tool_runner)."""

from ..config import Settings
from ..core.protocols import ToolRunner
from .local_docker import LocalDockerRunner
from .nebius_sandbox import NebiusSandboxRunner


def build_tool_runner(settings: Settings) -> ToolRunner:
    """`tool_backend = "local_docker"` (default) or `"nebius_sandbox"`.

    Construction never fails and never touches the network or docker. A backend that is not configured reports
    why through `config_problems()` (used by the health check) and raises ToolInfraError on the first call.
    """
    if settings.tool_backend == "nebius_sandbox":
        return NebiusSandboxRunner(settings)
    return LocalDockerRunner(settings)
