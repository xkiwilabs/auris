<!-- Thanks for contributing to auris! Keep PRs to one logical change. -->

## Summary
What does this change, and **why**? Link any issue it closes (e.g. `Closes #123`).

## Changes
- <bullet the key changes>

## Testing
- [ ] `pytest` passes (fast, model-free suite)
- [ ] `ruff check .` and `ruff format .` are clean
- [ ] New behaviour is covered by model-free tests
- [ ] If it touches real inference, it's gated behind `@pytest.mark.requires_model` and I ran it
      locally
- [ ] Heavy deps (torch / f5_tts / faster_whisper / kokoro / sounddevice) stay **lazy-imported**

## Notes for reviewers
Anything non-obvious: trade-offs, follow-ups, or areas you'd like a closer look at.
