# Repo Visualizer CLI — Design Spec

**Date:** 2026-03-24
**Status:** Approved

---

## Overview

A Python CLI tool (`repoviz`) that accepts a path to a local directory and generates:
1. An HTML file with an interactive D3.js architecture diagram (two views: file tree + import dependency graph)
2. A plain-English explanation of what the project does (via OpenAI)
3. A "getting started" guide for new contributors (via OpenAI)
4. Optionally, a Markdown export of the explanation and guide

---

## Architecture

Single Python script (`repoviz.py`) with a flat, linear pipeline. No classes, no plugins — just top-level functions called in sequence from `main()`.

```
CLI args
  → scan_repo(repo_path)               → tree_data, graph_files, repo_summary
  → analyze_imports(graph_files, root) → graph_data
  → call_openai(repo_summary)          → ai_result  {"explanation", "getting_started"}
  → render_html(tree_data, graph_data, ai_result, output_path)
  → write_markdown(explanation, getting_started, md_path)   # only if --md
```

All data flows explicitly as return values between functions. No global state.

### CLI Interface

```bash
repoviz /path/to/repo                        # outputs <repo>-report.html
repoviz /path/to/repo --md                   # also writes <repo>-report.md
repoviz /path/to/repo -o my-report.html      # custom output; markdown → my-report.md
repoviz /path/to/repo --no-ai                # skip OpenAI, diagram only
```

**Output filename rule:** The markdown filename is derived by replacing the last extension of the HTML filename with `.md`. If the HTML filename has no extension, `.md` is appended (e.g., `-o report` → `report.md`, `-o report.txt` → `report.md`).

**Git validation:** If the target directory does not contain a `.git` subdirectory, print a warning to stderr ("Warning: no .git directory found — treating as a plain directory") and continue. Do not exit.

**Dependencies:** `openai`, `click` (plus standard library: `os`, `re`, `json`, `pathlib`)

**Distribution:** `pyproject.toml` with `[project.scripts]` entry point and `[project.dependencies]` listing `openai` and `click`. No separate `requirements.txt`.

---

## Components

### 1. Repo Scanner (`scan_repo`)

**Signature:** `scan_repo(repo_path: Path) -> tuple[dict, list[Path], dict]`
Returns `(tree_data, graph_files, repo_summary)`.

- Walks the repo with `pathlib.Path.rglob`
- Skips directories: `.git`, `node_modules`, `__pycache__`, `venv`, `.venv`, `dist`, `build`
- Skips files where size > 1MB
- Catches `PermissionError`, `UnicodeDecodeError`, and any `OSError` per-file; logs a warning to stderr including the error type (e.g., "Warning: skipping src/foo.py — PermissionError"); skips the file
- Collects:
  - `tree_data`: nested JSON dict for D3 (`{name, children: [...]}`)
  - `graph_files`: list of Path objects for recognized language extensions
  - `repo_summary`: dict with `repo_name`, `language_breakdown` (language → file count), `top_dirs` (top-level directory names), `file_count` (total recognized-language file count), `file_list` (list of relative file paths as strings, used in the OpenAI prompt and truncated first if prompt exceeds size cap), `readme_excerpt` (README content truncated to 2000 chars, or empty string)

### 2. Import Analyzer (`analyze_imports`)

**Signature:** `analyze_imports(graph_files: list[Path], repo_root: Path) -> dict`
Returns `graph_data`.

Regex-based extraction. Only intra-repo imports are drawn as graph edges. External imports are silently dropped (not drawn, not counted in graph).

**Intra-repo detection rules by language:**

| Language | Extensions | Pattern | Intra-repo filter |
|---|---|---|---|
| Python | `.py` | `import X`, `from X import Y` | Resolve dotted module name to file path (see resolution order below); drop if no match |
| JavaScript/TypeScript | `.js .ts .jsx .tsx` | `import ... from 'X'`, `require('X')` | Only specifiers starting with `./` or `../` |
| Go | `.go` | Single-line: `import "X"`; multi-line: match block `import \([\s\S]*?\)` then extract all quoted strings within it | Read `go.mod` to extract module path; only imports with that prefix are intra-repo |
| Java | `.java` | `import X.Y.Z;` | Best-effort: derive root package from the name of the top-level source directory (e.g., `src/`); only imports with that prefix are intra-repo. Limitation: may miss packages in projects without a clear root package. |
| Ruby | `.rb` | `require_relative 'X'`, `require 'X'` | `require_relative` is always intra-repo; bare `require 'X'` is intra-repo only if `<repo_root>/X.rb` or `<repo_root>/X/init.rb` exists; otherwise treated as external |

**Python module resolution order** (`from foo.bar import baz` imports the module `foo.bar`; `baz` is the name being imported, not part of the path. Try in order, use first match):
1. `foo/bar/__init__.py` (package)
2. `foo/bar.py` (module)
3. Drop (treat as external)

**Node identity:** Nodes are file paths relative to the repo root (e.g., `src/utils.py`). Edges are only drawn when the resolved target file exists in the repo. Unresolved imports are silently dropped.

**Output:** `graph_data = { "nodes": [{"id": "src/utils.py", "language": "python"}, ...], "links": [{"source": "src/main.py", "target": "src/utils.py"}, ...] }`

Recognized-language files with no detected intra-repo imports appear as isolated nodes in the import graph (no edges). Non-language files (e.g., `.md`, config files) appear only in the file tree view, not in `graph_data`.

### 3. OpenAI Client (`call_openai`)

**Signature:** `call_openai(repo_summary: dict) -> dict`
Returns `{"explanation": str, "getting_started": str}`.

- Model: `gpt-4o`
- API key: read from `OPENAI_API_KEY` environment variable
- Total prompt size capped at ~16,000 characters (approximately 4,000 tokens using a `len // 4` heuristic). Truncation priority (first to be cut): file list → directory names → README excerpt. No `tiktoken` dependency.

**System prompt:**
```
You are a technical writer. Given a summary of a software repository, return a JSON object with exactly two fields:
- "explanation": a 2-3 sentence plain-English description of what the project does and who it is for.
- "getting_started": a step-by-step markdown guide (numbered list) for a new contributor to install dependencies and run the project locally.
Return only valid JSON. Do not include markdown code fences around the JSON.
```

**User message template:**
```
Repository: {repo_name}
Languages: {language_breakdown}
Top-level directories: {top_dirs}
File count: {file_count}
Files:
{file_list}
README (excerpt):
{readme_excerpt}
```

Truncation (applied before building the message, if total character count exceeds ~16,000):
1. Truncate `file_list` first (reduce to top 100 paths, then 50, then remove entirely)
2. Truncate `top_dirs` (remove if still over limit)
3. Truncate `readme_excerpt` (reduce to 500 chars, then remove)

- Output format: `response_format={"type": "json_object"}`
- **Error handling sequence:**
  1. Attempt the API call. On network error, `AuthenticationError`, rate limit, or any `OpenAIError`: log warning to stderr, return `{"explanation": "", "getting_started": ""}` immediately.
  2. Attempt `json.loads` on the response content. On `JSONDecodeError`: log warning to stderr, return both empty immediately.
  3. For each expected key (`explanation`, `getting_started`): if key is missing or value is not a non-empty string, log a specific warning (e.g., "Warning: OpenAI response missing expected key: explanation") and set that field to `""`. The other field retains its value if valid (partial recovery).
  4. Return the resulting dict (may have one or both fields empty).
- `--no-ai` flag skips this step entirely; returns `{"explanation": "", "getting_started": ""}` directly

### 4. HTML Renderer (`render_html`)

**Signature:** `render_html(tree_data: dict, graph_data: dict, ai_result: dict, output_path: Path) -> None`

Produces the HTML file at `output_path`:

- `tree_data` and `graph_data` injected as JSON blobs in a `<script>` tag
- `explanation` rendered as a `<p>` tag (HTML-escaped). If empty, the section is hidden with a `<p class="notice">` noting AI was not run.
- `getting_started` converted from markdown to HTML using a two-pass approach:
  - **Pass 1 (classify each line):** For each line in order:
    1. `## text` → `("h2", text)`
    2. `# text` → `("h1", text)`
    3. `N. text` (starts with digit + `.`) → `("li_ol", text)`
    4. `- text` or `* text` → `("li_ul", text)`
    5. Blank line → `("blank", "")`
    6. Otherwise → `("p", text)`
  - **Pass 2 (emit HTML):** Iterate classified lines; apply inline transforms to text portion first (`**x**` → `<strong>x</strong>`, `` `x` `` → `<code>x</code>`), then emit:
    - `h1` / `h2` → `<h1>` / `<h2>` immediately
    - Consecutive `li_ol` lines → single `<ol>` block
    - Consecutive `li_ul` lines → single `<ul>` block
    - `p` → `<p>` tag
    - `blank` → no output
  - If `getting_started` is empty, render a `<p class="notice">` note instead.
- D3.js v7 loaded from CDN (`https://cdn.jsdelivr.net/npm/d3@7`). Note: requires internet connection to render diagram.
- Two tab buttons toggle between views:
  - **File Tree:** collapsible tree layout (`d3.hierarchy` + `d3.tree`)
  - **Import Graph:** force-directed graph (`d3.forceSimulation`), nodes colored by language
- Minimal inline CSS — no external framework

### 5. Markdown Writer (`write_markdown`)

**Signature:** `write_markdown(explanation: str, getting_started: str, md_path: Path) -> None`

- Triggered by `--md` flag (caller passes `md_path`; skipped entirely if not set)
- Writes raw `explanation` text and raw `getting_started` markdown to `md_path`
- If both strings are empty (AI was skipped or failed), prints a notice to stderr and does not write the file

---

## Error Handling

| Scenario | Behavior |
|---|---|
| Missing/invalid repo path | `click` prints usage error and exits |
| No `.git` directory found | Warning to stderr ("Warning: no .git directory found — treating as a plain directory"), continues |
| Unreadable file (`PermissionError`, `OSError`) | Warning to stderr with error type, file skipped |
| File > 1MB | Warning to stderr, file skipped |
| Non-UTF-8 file (`UnicodeDecodeError`) | Warning to stderr with error type, file skipped |
| OpenAI key missing / network error / rate limit | Warning to stderr, AI sections left blank in HTML |
| OpenAI response missing expected keys | Specific warning per missing key, falls back to empty string for that field |
| Empty repo / no recognized files | HTML generated with empty diagram + inline notice |
| `--md` + `--no-ai` (or AI failed) | Notice to stderr, markdown file not written |

---

## Testing

`tests/` directory using `pytest`:

| File | What it tests |
|---|---|
| `test_scanner.py` | Tree structure and skip logic (size, permission, non-UTF-8) on fixture repo |
| `test_analyzer.py` | Per-language import extraction and intra-repo filtering against fixture files |
| `test_renderer.py` | HTML output with AI fixture data; HTML output with empty AI data (`{"explanation": "", "getting_started": ""}`) asserting notice text appears and no broken empty elements are rendered |
| `test_openai_integration.py` | Live API call; skipped unless `OPENAI_API_KEY` is set |

- `test_renderer.py` uses hardcoded dicts — no live API calls in unit tests

---

## File Layout

```
repoviz/
├── repoviz.py                    # single script — full pipeline
├── pyproject.toml                # entry point, metadata, dependencies
└── tests/
    ├── fixtures/                 # small sample repos for testing
    ├── test_scanner.py
    ├── test_analyzer.py
    ├── test_renderer.py
    └── test_openai_integration.py
```

---

## Out of Scope (v1)

- Class hierarchy or call graph analysis
- Local LLM support
- Offline-capable / fully self-contained HTML (D3 is CDN-loaded)
- CI/CD integration
- Watch mode / auto-regeneration
- Language plugins or extension points
- Full markdown-to-HTML library (inline line-by-line pass only)
- Token counting via `tiktoken` (character heuristic used instead)
