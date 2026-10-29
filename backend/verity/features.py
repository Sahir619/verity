"""Features this backend serves end to end, listed on GET /health. Flip a value on only when it is live."""

FEATURES: tuple[str, ...] = ("params", "human_ref", "parse", "ask", "agent", "signoff")
