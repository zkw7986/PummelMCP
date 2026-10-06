# Runtime evidence

Evidence collection is read-only and incremental. A log contributes evidence
only when its baseline bytes still have the recorded size and SHA-256 prefix.
Unity `Player.log` rotation is accepted only when `Player-prev.log` retains the
exact recorded baseline prefix; the complete new `Player.log` then forms the
session window. Unproven truncation or rotation is reported as
`LOG_ROTATED_OR_TRUNCATED` and invalidates claims based on absence of errors. New lines are normalized into timestamp,
severity, source, message, error taxonomy, GUIDs, and correlated logical IDs.

The v0.1 taxonomy includes scene load, missing asset/reference, Component,
Action, Trigger, spawn, exception, crash, timeout, and unknown runtime failures.
Unknown evidence is returned and is never repaired automatically.
