# TASKS — project backlog

The single backlog for this project. **Index, not a document:** one line per task, detail by
reference; a finished entry moves to `TASKS_ARCHIVE.md`. No dates — git history is the
timeline. Format (akmon `pipelines/tasks.md`):

`- <id> · <title> · <status> · <goal, 12 words max> · [detail](link)`

The id is typed — `A` architecture · `C` code · `N` analysis · `L` learning · `V` release —
and the owning role follows from that letter, so it is not a separate field. Status is one of
`active | blocked | deferred | done`.

- A1 · classify archetype and language · active · pick archetype/language per ARCHETYPES.md; import its language profile
- C1 · pin the test runner · active · record `[test].runner` in `_aitna/.akmon.toml`, reusing this project's manager
