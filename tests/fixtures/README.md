# Test fixtures

Place the original, unmodified Joker 21 `MainScene.scene` in this directory.

Expected path:

```text
tests/fixtures/MainScene.scene
```

The test suite opens this fixture read-only and verifies that its SHA-256
digest is unchanged after the complete test session. Do not commit modified
copies of the scene over this fixture.
