# Publishing the spec

Every model file's first line points editors at the schema:

```yaml
# yaml-language-server: $schema=https://www.jbanalytica.com/model2data/spec/0.3.0/model.schema.json
```

(a 0.2.0 document keeps pointing at `/spec/0.2.0/`, so both versions are served).

and `model.schema.json` carries the same URL as its `$id`. Until that URL answers, VS Code,
JetBrains and every other YAML-aware editor fail to fetch the schema and offer nothing: no
completion, no validation. Publishing it is what turns a `.model2data.yml` file into one those
editors understand, with no extension to install.

This is not automated, because both steps happen outside this repository.

## 1. Host the schema at its `$id`

On the website (`JB-Analytica/jba-website`), serve this directory's files at the versioned path:

| URL | File |
| --- | --- |
| `https://www.jbanalytica.com/model2data/spec/0.3.0/model.schema.json` | `model2data/spec/model.schema.json` of engine 1.9.0 |
| `https://www.jbanalytica.com/model2data/spec/0.3.0/README.md` (optional, or render it as a page) | `model2data/spec/README.md` of engine 1.9.0 |
| `https://www.jbanalytica.com/model2data/spec/0.2.0/model.schema.json` | `model2data/spec/model.schema.json` of engine 1.8.0 |
| `https://www.jbanalytica.com/model2data/spec/0.2.0/README.md` (optional) | `model2data/spec/README.md` of engine 1.8.0 |

Requirements:

- **Exactly this path, one directory per version.** A version is never overwritten; 0.3.0 gets
  `/spec/0.3.0/` beside it. The `$id` inside the file must equal the URL it is served at.
- **`Access-Control-Allow-Origin: *`**, so browser-based editors (the studio, vscode.dev, the
  Monaco playgrounds) can fetch it.
- **`Content-Type: application/schema+json`** (or `application/json`).
- Copy the file from the released engine version, not from a branch: the tag that ships each
  version (0.2.0: engine 1.8.0; 0.3.0: engine 1.9.0).

Check it:

```bash
curl -sI https://www.jbanalytica.com/model2data/spec/0.3.0/model.schema.json | grep -i -e content-type -e access-control
curl -s https://www.jbanalytica.com/model2data/spec/0.3.0/model.schema.json | python -c "import json,sys; print(json.load(sys.stdin)['\$id'])"
```

The second command must print the URL itself.

## 2. Register the file pattern with SchemaStore

[SchemaStore](https://www.schemastore.org) is the catalogue editors consult to find a schema by
file name. Once `*.model2data.yml` is in it, a model file gets validation even without the
`# yaml-language-server` line.

Open a pull request to [SchemaStore/schemastore](https://github.com/SchemaStore/schemastore)
adding this entry to `src/api/json/catalog.json` (the list is kept alphabetical by `name`):

```json
{
  "name": "model2data model",
  "description": "A data model for model2data: tables, keys, relationships and how each column's values are generated (spec 0.3.0)",
  "fileMatch": ["*.model2data.yml", "*.model2data.yaml", "*.model2data.json"],
  "url": "https://www.jbanalytica.com/model2data/spec/0.3.0/model.schema.json"
}
```

The schema stays hosted on our site (SchemaStore links to it, it does not copy it), so a
new spec version only needs the `url` updated in a follow-up PR, or a `versions` map added once
there is more than one version to offer.

## After both

- Open a `.model2data.yml` in VS Code with the Red Hat YAML extension (or any editor using
  yaml-language-server): completion on `generate:` keys and an error on `nul_rate` mean it works.
- Flip the Notion row "Publish the spec's JSON Schema at its $id URL and register
  *.model2data.yml in SchemaStore" to Done.
