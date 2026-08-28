# Changelog

All notable changes to Passagen are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
