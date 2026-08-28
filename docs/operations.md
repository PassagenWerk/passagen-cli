# Installation And Operations

## Clean Installation

```bash
git clone <repository-url> Passagen
cd Passagen
uv sync --frozen
cp passagen.example.yaml passagen.yaml
uv run passagen config check
uv run passagen db init
```

Runtime data is stored below the configured `data_dir`. The local `passagen.yaml`, database,
artifacts, logs, and generated distributions are excluded from Git.

Before the first stable release, database Schema v1 is intentionally rebuildable. When the
development Schema changes, remove the old local `data/passagen.db` and import the PDFs again.

## GROBID

Start GROBID locally with Docker:

```bash
docker run --rm --init -p 8070:8070 lfoppiano/grobid:0.8.2
curl http://localhost:8070/api/isalive
```

Configure it in `passagen.yaml`:

```yaml
providers:
  grobid:
    base_url: http://localhost:8070
    timeout_seconds: 60
pipeline:
  parsing:
    parser: auto
```

Use `parser: pymupdf` when GROBID is unavailable. `auto` prefers GROBID and uses the configured
fallback behavior; `grobid` requires the service.

## LLM And LiteLLM

```yaml
providers:
  llm:
    base_url: http://localhost:4000/v1
    model: paper-facts
    api_key_env: PASSAGEN_API_KEY
    timeout_seconds: 120
    disable_thinking: true
```

```bash
export PASSAGEN_API_KEY=your-key
```

When `disable_thinking` is true, Passagen adds this top-level request field:

```json
{"thinking": {"type": "disabled"}}
```

The API key value is used only in the Authorization header. Configuration output, execution logs,
LLM diagnostics, and database rows do not store it.

## Commands

```bash
passagen scan <directory>
passagen update [paper-id] [--force]
passagen run <directory>
passagen metadata <paper-id> [--force]
passagen parse <paper-id> [--parser auto|grobid|pymupdf] [--force]
passagen summarize <paper-id> [--force]
passagen outline <paper-id> [--force]
passagen list [--status STATUS]
passagen show <paper-id>
passagen db backup [destination]
passagen artifacts check
```

There is no automatic in-process retry. A failure leaves the Paper at the last successful stage;
correct the cause and invoke `update` again. `--force` rebuilds from metadata instead of resuming.

## Backup And Transfer

Create a transactionally consistent SQLite backup before copying user data:

```bash
uv run passagen db backup
uv run passagen artifacts check
```

To move Passagen data to another machine, copy the complete `data_dir`, including the database,
`pdfs/`, and `papers/`. Keep relative paths unchanged, then run `artifacts check` at the destination.

## Common Errors

`environment variable PASSAGEN_API_KEY is not set`

Set the environment variable named by `providers.llm.api_key_env` in the same shell that starts
Passagen.

`Provider llm is unavailable`

Check `base_url`, authentication, the LiteLLM `/models` route, and firewall access. A successful
health check does not guarantee that the selected model alias exists.

`OpenAI-compatible LLM returned an empty response`

Inspect `finish_reason`, output tokens, and reasoning tokens in the error. For supported reasoning
models, enable `disable_thinking` or increase the output limit if reasoning consumed the budget.

`Provider grobid is unavailable`

Check `/api/isalive`, or select `pipeline.parsing.parser: pymupdf`.

`no_text_layer`

The PDF is scanned or contains no usable text layer. OCR is outside the first release scope.

`artifact file is missing` or a hash mismatch

Restore the complete `data_dir` from backup. Do not edit managed files directly.
