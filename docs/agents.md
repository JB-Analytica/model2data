# Using model2data from a coding agent or an LLM

## `model2data guide`

Driving model2data from a coding agent? Have it start here; the instructions ship with the
installed version and pick a topic from the current directory:

```bash
uvx model2data@latest guide
```

`model2data guide [setup|triage|tune]` prints one page of Markdown written for a coding agent:
`setup` (install, a starter model, the first run), `triage` (what `validate`, `generate` and
`dbt build` print and what to do about each, in order) and `tune` (every option, model key and
exit code). Without a topic it picks `triage` when the current directory holds a
`*.model2data.yml` or `*.dbml` file, else `setup`, and says so on its first line. It reads only
file names in the current directory and writes nothing.

## LLMS.md

If you want to go from a plain-English description of a data model straight to a running,
demo-ready dbt project, [LLMS.md](../LLMS.md) is written for an LLM/agent to read: it covers the
DBML feature set model2data understands (enums, notes, defaults, composite keys, both
relationship syntaxes, self-references), which it converts to a spec 0.2.0 model and the exact command sequence to run. Point an
LLM-backed coding assistant at it and describe your data model — it can author the DBML and run
model2data for you.
