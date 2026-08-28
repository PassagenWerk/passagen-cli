# Passagen

Passagen is a local CLI that imports paper PDFs, resolves metadata, parses full text, produces a
validated English summary, and generates a Chinese outline.

## Install

Requirements:

- Python 3.12 or newer
- `uv`
- A text-based PDF
- An OpenAI-compatible LLM endpoint
- Optional GROBID service; PyMuPDF can be used without GROBID

```bash
uv sync
cp passagen.example.yaml passagen.yaml
uv run passagen --help
```

Set the API key named by `providers.llm.api_key_env`:

```bash
export PASSAGEN_API_KEY=your-key
```

Initialize and process a directory:

```bash
uv run passagen db init
uv run passagen run ./papers
```

`run` is equivalent to scanning the directory and then updating all papers. Repeating it is
idempotent: existing PDFs and papers already at `outlined` are skipped.

## Recovery

A failed stage leaves the Paper at its last successful status. Fix the external service or input,
then retry manually:

```bash
uv run passagen update <paper-id>
```

Use `--force` to invalidate downstream stages and rebuild from metadata:

```bash
uv run passagen update <paper-id> --force
```

## Maintenance

```bash
uv run passagen db backup
uv run passagen artifacts check
uv run passagen logs clean
```

Database backups default to `data/backups/`. Artifact checking verifies every database-indexed
file's location, size, and SHA-256 when available.

See [`docs/operations.md`](docs/operations.md) for GROBID, configuration, and troubleshooting.
