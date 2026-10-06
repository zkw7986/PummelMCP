# Playtest session

`plan_playtest_session` validates a Scene to exact EOF, records its raw SHA-256
and semantic fingerprint, and retains an immutable planned Scene snapshot,
captures each discovered log's size, full prefix hash and timestamp, and stores
optional composition, GameplaySpec, assertion, and logical-ID/GUID data. Session
state lives under `.pummelmcp-playtests` in the configured Mod root.

`start_playtest_session` rechecks the exact Scene hash and fails with
`STALE_PLAYTEST_SCENE` after any change. It refreshes the baseline and returns
`PREPARED_WAITING_FOR_MANUAL_START`; it never starts or stops a process. Timeouts
are bounded to 10–3600 seconds and recorded for the manual protocol.

Evaluation records planned and observed raw hashes and semantic fingerprints.
It returns `EXACT_SCENE_MATCH` for byte identity, `SEMANTIC_SCENE_MATCH` only
when the semantic fingerprints match under the approved field-specific Editor
normalization, and `STALE_PLAYTEST_SCENE` for every semantic or unknown change.
Legacy sessions without a retained snapshot fail closed.
