# Runtime capabilities

Stage 14 audits runtime surfaces without launching software. Every capability is
graded `CONFIRMED`, `PARTIAL`, `NOT_OBSERVED`, `UNAVAILABLE`, or `UNKNOWN`.
Executable paths are accepted only through the explicit
`PUMMELMCP_EDITOR_EXE` and `PUMMELMCP_GAME_EXE` configuration and do not enable
launching in v0.1. Exact `Player.log` and `Editor.log` files may be discovered in
the configured Mod root and its proven ancestors. Steam defaults are never
guessed. The supported launch mode is `MANUAL_START_ONLY`.
