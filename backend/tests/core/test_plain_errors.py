"""Infrastructure failures end a run with one plain sentence; the raw detail stays in the server log."""

import pytest

from verity.core import captions as cap

# The exact error_reason text a real run stored on 2026-10-22 when Docker Desktop was not running.
REAL_DOCKER_DOWN = (
    "verify: sandbox exited with code 1: failed to connect to the docker API at npipe:////./pipe/"
    "dockerDesktopLinuxEngine; check if the path is correct and if the..."
)


@pytest.mark.parametrize(
    ("detail", "expected"),
    [
        (REAL_DOCKER_DOWN, cap.DOCKER_DOWN),
        ("cannot start docker ('docker'): [WinError 2] The system cannot find the file specified", cap.DOCKER_DOWN),
        ("verify: sandbox exited with code 125: docker: Error response from daemon: No such image: verity-tools:0.1", cap.IMAGE_MISSING),
        ("docker call failed: TimeoutError()", cap.SANDBOX_TIMEOUT),
        ("measure: unusable output: no json here", cap.SANDBOX_BROKEN),
    ],
)
def test_sandbox_failures_map_to_plain_sentences(detail: str, expected: str) -> None:
    assert cap.sandbox_failed(detail) == expected


@pytest.mark.parametrize(
    ("detail", "status", "retryable", "expected"),
    [
        ("model endpoint error HTTP 429: too many requests", 429, True, cap.MODEL_RATE_LIMITED),
        ("model call timed out after 120 s", None, True, cap.MODEL_TIMEOUT),
        ("could not reach the model endpoint", None, True, cap.MODEL_UNREACHABLE),
        ("model endpoint refused the credentials (HTTP 401)", 401, False, cap.MODEL_KEY),
        ("LLM_API_KEY is not set", None, False, cap.MODEL_KEY),
        ("model endpoint error HTTP 503: unavailable", 503, True, cap.MODEL_DOWN),
        ("model returned no choices", None, True, cap.MODEL_DOWN),
        ("prompt for planner is about 13000 tokens, the per-call limit is 12000", None, False, None),
    ],
)
def test_model_failures_map_to_plain_sentences(detail: str, status: int | None, retryable: bool, expected) -> None:
    assert cap.model_failed(detail, status, retryable) == expected


def test_plain_sentences_are_short_ascii_and_survive_the_caption_cleaner() -> None:
    sentences = [
        cap.DOCKER_DOWN, cap.IMAGE_MISSING, cap.SANDBOX_TIMEOUT, cap.SANDBOX_BROKEN, cap.MODEL_TIMEOUT,
        cap.MODEL_UNREACHABLE, cap.MODEL_RATE_LIMITED, cap.MODEL_KEY, cap.MODEL_DOWN,
        cap.RESTARTED,
    ]
    for text in sentences:
        assert text.isascii()  # no em or en dash, no other non-ASCII
        assert text.endswith(".") and len(text) <= 120
        assert cap.clean(text, 300) == text  # survives the caption cleaner unchanged
