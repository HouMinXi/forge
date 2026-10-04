"""Mutation execution settings shared by configuration and public entry points."""

DEFAULT_MUTATION_TIMEOUT = 600


def validate_mutation_timeout(value: object, name: str = "timeout") -> int:
    """Reject absent or unbounded deadlines instead of passing them to subprocess."""
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ValueError(f"{name} must be a positive integer")
    return value
