# Camera capabilities

Two kinds of file live here, and they answer different questions.

| File | Question | Maintained by |
|---|---|---|
| `<model>-fw<version>.json` / `.md` | What does the camera *say* it supports? | generated — `just capabilities` |
| `<model>-caveats.md` | Where does the camera *lie*, or differ from the reference? | by hand |

The generated files come from the camera's own `/ccapi` manifest and a GET of
every readable setting. They are the source of truth for which endpoints exist
and which values a setting accepts.

**They are not the source of truth for what works.** An endpoint being listed in
the manifest only means the firmware advertises it. It can still answer 400, 503
or `unknown command` in practice, and a documented request parameter can be
ignored outright. Those findings cannot be discovered by dumping — they only
show up when a call fails — so they go in the caveats file, with the evidence.

Check the caveats file before assuming a request will work, and add to it
whenever a camera surprises you. Regenerating the dump will not overwrite it.
