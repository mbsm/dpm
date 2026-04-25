"""Shared daemon-side byte-count limits.

Kept in a dedicated module so ``processes`` and ``telemetry`` can import
without pulling in ``daemon`` (which imports both of them — a circular
reference that previously forced deferred imports inside function bodies).
"""

# Maximum bytes sent per process per publish cycle. Prevents a chatty
# process from producing LCM messages too large to fragment reliably
# over UDP.
MAX_OUTPUT_CHUNK = 64 * 1024  # 64 KB
