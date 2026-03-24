# Repo Visualizer CLI — Design Spec

**Date:** 2026-03-24
**Status:** Approved

---

## Overview

A Python CLI tool (`repoviz`) that accepts a path to any local Git repository and generates:
1. A self-contained HTML file with an interactive D3.js architecture diagram (two views: file tree + import dependency graph)
2. A plain-English explanation of what the project does (via OpenAI)
3. A "getting started" guide for new contributors (via OpenAI)
4. Optionally, a Markdown export of the explanation and guide

---

## Architecture

Single Python script (`repoviz.py`) with a flat, linear pipeline. No classes, no plugins — just top-level functions called in sequence from `main()`.

```
CLI args
  → scan_repo()        # walk directory tree, collect files + metadata
  → analyze_imports()  # regex-based per-language import extraction
  → call_openai()      # send repo summary, get explanation + guide JSON
  → render_html()      # inject D3.js + data into inline HTML template
  → write_markdown()   # optional, extract explanation + guide as .md
```

### CLI Interface

```bash
repoviz /path/to/repo                        # outputs <repo>-report.html
repoviz /path/to/repo --md                   # also writes <repo>-report.md
repoviz /path/to/repo -o my-report.html      # custom output filename
repoviz /path/to/repo --no-ai                # skip OpenAI, diagram only
```

**Dependencies:** `openai`, `click` (plus standard library: `os`, `re`, `json`, `pathlib`)

**Distribution:** `pyproject.toml` with `[project.scripts]` entry point — `pip install .` gives the user the `repoviz` command.

---

## Components

### 1. Repo Scanner (`scan_repo`)

- Walks the repo with `pathlib.Path.rglob`
- Skips: `.git`, `node_modules`, `__pycache__`, `venv`, `.venv`, `dist`, `build`
- Collects:
  - Full file tree (path + size) → `tree_data` (nested JSON for D3)
  - Files relevant to import analysis (filtered by extension)
  - Lightweight repo summary for OpenAI: language breakdown by file count, top-level directory names, README content (truncated to 2000 chars if present)

### 2. Import Analyzer (`analyze_imports`)

Regex-based extraction of intra-repo imports only. External package imports are noted in the summary but not drawn as graph nodes.

| Language | Extensions | Patterns |
|---|---|---|
| Python | `.py` | `import X`, `from X import` |
| JavaScript/TypeScript | `.js .ts .jsx .tsx` | `import ... from 'X'`, `require('X')` |
| Go | `.go` | `import "X"`, multi-line import blocks |
| Java | `.java` | `import X.Y.Z;` |
| Ruby | `.rb` | `require 'X'`, `require_relative` |

Output: `graph_data` — nodes (files) + edges (import relationships) as JSON.

Files with no detected imports still appear in the file tree view.

### 3. OpenAI Client (`call_openai`)

- Model: `gpt-4o`
- API key: read from `OPENAI_API_KEY` environment variable
- Input: repo name, language breakdown, top-level directories, README excerpt, file count
- Output format: `response_format={"type": "json_object"}` with shape:

```json
{
  "explanation": "Plain-English paragraph describing what the project does...",
  "getting_started": "Step-by-step markdown guide for new contributors..."
}
```

- On failure (missing key, network error, rate limit): prints warning to stderr, continues with blank AI sections
- `--no-ai` flag skips this step entirely

### 4. HTML Renderer (`render_html`)

Produces a single self-contained `<repo>-report.html`:

- `tree_data` and `graph_data` injected as JSON in a `<script>` tag
- `explanation` and `getting_started` rendered as HTML sections
- D3.js v7 loaded from CDN
- Two tab buttons toggle between views:
  - **File Tree:** collapsible tree layout (`d3.hierarchy` + `d3.tree`)
  - **Import Graph:** force-directed graph (`d3.forceSimulation`), nodes colored by language
- Minimal inline CSS — no external framework

### 5. Markdown Writer (`write_markdown`)

- Triggered by `--md` flag
- Writes `<repo>-report.md` alongside the HTML
- Contains `explanation` and `getting_started` content
- Skipped (with printed notice) if `--no-ai` was used

---

## Error Handling

| Scenario | Behavior |
|---|---|
| Missing/invalid repo path | `click` prints usage error and exits |
| Unreadable file | Caught per-file, warning to stderr, file skipped |
| OpenAI failure | Warning printed, AI sections left blank in HTML |
| Empty repo / no recognized files | HTML generated with empty diagram + notice |

---

## Testing

`tests/` directory using `pytest`:

| File | What it tests |
|---|---|
| `test_scanner.py` | Tree structure on small fixture repo directory |
| `test_analyzer.py` | Per-language import extraction against fixture files |
| `test_renderer.py` | HTML output contains expected JSON blobs and section headings |

- No OpenAI mocking in unit tests
- Integration test skipped unless `OPENAI_API_KEY` is set in environment

---

## File Layout

```
repoviz/
├── repoviz.py              # single script — full pipeline
├── pyproject.toml          # entry point + metadata
├── requirements.txt        # openai, click
└── tests/
    ├── fixtures/           # small sample repos for testing
    ├── test_scanner.py
    ├── test_analyzer.py
    └── test_renderer.py
```

---

## Out of Scope (v1)

- Class hierarchy or call graph analysis
- Local LLM support
- CI/CD integration
- Watch mode / auto-regeneration
- Language plugins or extension points
