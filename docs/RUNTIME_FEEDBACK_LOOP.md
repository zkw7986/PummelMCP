# Runtime feedback loop

The Stage 14 v0.1 loop is: inspect, plan, prepare, manual Play, collect, and
evaluate. Reports retain Scene/composition identity, evidence, assertions,
diagnostics, logical-ID/GUID correlation, and repair status.

Automatic process execution, GUI automation, player control, unowned process
termination, and log mutation are outside the safety boundary. A diagnosis is a
deterministic mapping from evidence; it does not grant write authority. Repairs
may use only an existing approved writer in a separate hash-bound transaction,
with at most one repair and one replay. Unknown Components, Actions, references,
assets, raw PMH changes, and unknown runtime failures require manual review.
This is an internal capability/evidence review, not a repeated human gameplay
approval. Human review of the complete gameplay plan is required only for the
first creation of a game; requested edits to an existing Mod proceed in place.

Manual protocol: prepare the session, open the prepared Mod in the official
Editor, click Play, perform the requested minimal interaction, exit Play without
making Scene edits, then collect and evaluate evidence.

Stage 15 build reports feed their Scene hash, semantic fingerprint, composition
ID, logical mapping, and runtime expectations into this same manual-start flow.
