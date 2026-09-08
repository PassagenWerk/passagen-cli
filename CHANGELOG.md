# Changelog

All notable changes to Passagen are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Added `collection synthesize` and `collection compare` with Markdown/JSON output, explicit
  partial-coverage and force-regeneration controls, and machine-readable stdout.

### Changed

- Updated the documented and example generation budgets for the 1M-context default model.

## [0.5.0] - 2026-09-05

### Changed

- Aligned the CLI minor version with the required Passagen Core `0.5.x` line; releases on the
  same minor share one compatibility line, while patch versions remain independent. No command
  behavior changes beyond `0.4.0`.

## [0.4.0] - 2026-09-05

### Added

- Explicit, non-blocking `abstract` stage and command, which extracts missing canonical abstracts
  and creates or refreshes validated cleaned-abstract artifacts before summarization.

### Changed

- Raised the `passagen-core` requirement to `>=0.5,<0.6` for the abstract stage and updated
  shared configuration defaults.
- Reorganized documentation around user startup and operations, with shared configuration owned by
  Passagen Core, forge-neutral cross-repository links, and the example configuration
  defaulting to DeepSeek `deepseek-flash-v4`.

## [0.3.1] - 2026-09-05

### Changed

- Widened the `passagen-core` dependency range to `>=0.3,<0.5` so the CLI can be installed
  alongside Passagen Core 0.4.x; no command behavior changes.

## [0.3.0] - 2026-09-04

### Changed

- `passagen update` now runs through the shared Core `ProcessingService`: every update is a
  persisted run (`update_runs` table, `data/runs/<run-id>/` snapshot and progress events) with
  per-paper conflict detection, while console output and exit codes stay unchanged.
- `passagen config check` also validates the new `full` and `reduce` summary prompt templates and
  displays the summarization strategy, chunk budget, and global LLM budget settings.
- `passagen.yaml` / `passagen.example.yaml`: removed `summarization.max_chunk_characters`; added
  `summarization.strategy`, `chunk_max_input_tokens`, `chunk_overlap_paragraphs`,
  `full_prompt_path`, `reduce_prompt_path`, and the global LLM budget fields
  under `providers.llm`.

## [0.2.0] - 2026-09-04

### Changed

- Moved shared business services to the `passagen-core` dependency and renamed the CLI-only Python
  package to `passagen_cli` without changing the `passagen` executable.

## [0.1.1] - 2026-09-03

### Added

- Public `passagen.catalog` application service exposing paper reading, metadata
  editing, Library Tags, and ordered collections for Passagen Web.
- LLM call and token usage statistics.
- External service reachability checking via `passagen check`.

### Changed

- Migrated direct SQL access to SQLAlchemy 2.0 with Alembic-managed schema
  migrations; existing databases are upgraded by `passagen db init`.
- Reorganized the CLI into a dedicated command package and improved execution
  logging.

## [0.1.0] - 2026-08-29

### Added

- Managed PDF import with SHA-256 deduplication.
- Local PDF metadata extraction and Crossref, arXiv, and GROBID enrichment.
- GROBID and PyMuPDF full-text parsing.
- Validated English Structured Summary Schema v2 generation through an
  OpenAI-compatible LLM endpoint.
- Hierarchical English technical outline generation from validated summaries.
- Versioned built-in prompts and configurable external prompt templates.
- Resumable pipeline execution from the last successful processing stage.
- SQLite database backup and managed-artifact integrity checks.
- Execution logs with LLM request diagnostics.
- Chinese usage documentation and English CLI help.
