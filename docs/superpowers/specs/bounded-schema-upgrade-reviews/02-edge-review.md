# Independent Edge Case Review

Read `C:/Users/admin/.agents/skills/bmad-review-edge-case-hunter/SKILL.md` fully and follow it.

Do not modify code, operate the target, push, create a PR, change keys, delete containers/volumes, or run destructive tests. Do not spawn further agents. Use the same model capability as the originating task. The source is frozen pending review; report concrete findings with file and line references. Return results in Chinese.

Review input: `D:/江苏润盛/tmp-test-logs/bounded-review-20260908-1140/implementation.diff`.
This is the full code delta (tracked and untracked) in the isolated release tree from approved baseline `d839330a91cdf7cae3f4c30aff9396fb99a47120`; it also contains previously deployed compatibility fixes, which must not be mistaken for a newly authorized scope expansion. Read all diff chunks without truncation.

Read-only project access: `C:/ProgramData/Ruisheng/publisher-build/bounded-schema-multidevice-20260908`. Trace every boundary directly reachable from changed lines and referenced helpers, including crash windows, real process lifetime after timeouts, lock loss, database state, and data preservation. No specification or conversation context. Return only the skill's JSON finding array.
