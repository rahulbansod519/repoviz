"""repoviz — Generate visual architecture diagrams for any Git repo."""

import html
import json
import re
import sys
from pathlib import Path

import click
from openai import OpenAI, OpenAIError


SKIP_DIRS = frozenset({".git", "node_modules", "__pycache__", "venv", ".venv", "dist", "build"})
LANG_EXTENSIONS = {
    ".py": "python",
    ".js": "javascript", ".jsx": "javascript",
    ".ts": "typescript", ".tsx": "typescript",
    ".go": "go",
    ".java": "java",
    ".rb": "ruby",
}
MAX_FILE_SIZE = 1_000_000  # 1 MB
MAX_PROMPT_CHARS = 16_000


def scan_repo(repo_path: Path) -> tuple[dict, list[Path], dict]:
    """Walk repo, build tree_data, collect graph_files and repo_summary."""
    if not (repo_path / ".git").exists():
        print(
            "Warning: no .git directory found — treating as a plain directory",
            file=sys.stderr,
        )

    all_files: list[Path] = []
    for path in sorted(repo_path.rglob("*")):
        if any(part in SKIP_DIRS for part in path.relative_to(repo_path).parts):
            continue
        if not path.is_file():
            continue
        try:
            size = path.stat().st_size
        except OSError as e:
            print(
                f"Warning: skipping {path.relative_to(repo_path)} — {type(e).__name__}",
                file=sys.stderr,
            )
            continue
        if size > MAX_FILE_SIZE:
            print(
                f"Warning: skipping {path.relative_to(repo_path)} — file exceeds 1MB",
                file=sys.stderr,
            )
            continue
        all_files.append(path)

    tree_data = _build_tree(repo_path, all_files)
    graph_files = [f for f in all_files if f.suffix in LANG_EXTENSIONS]
    repo_summary = _build_summary(repo_path, all_files, graph_files)
    return tree_data, graph_files, repo_summary


def _build_tree(repo_path: Path, files: list[Path]) -> dict:
    root: dict = {"name": repo_path.name, "children": []}

    def get_or_create_dir(node: dict, name: str) -> dict:
        for child in node["children"]:
            if child["name"] == name and "children" in child:
                return child
        new_dir: dict = {"name": name, "children": []}
        node["children"].append(new_dir)
        return new_dir

    for file_path in files:
        rel = file_path.relative_to(repo_path)
        parts = rel.parts
        current = root
        for part in parts[:-1]:
            current = get_or_create_dir(current, part)
        current["children"].append({"name": parts[-1], "size": file_path.stat().st_size})

    return root


def _build_summary(repo_path: Path, all_files: list[Path], graph_files: list[Path]) -> dict:
    lang_counts: dict[str, int] = {}
    for f in graph_files:
        lang = LANG_EXTENSIONS[f.suffix]
        lang_counts[lang] = lang_counts.get(lang, 0) + 1

    top_dirs = sorted({
        f.relative_to(repo_path).parts[0]
        for f in all_files
        if len(f.relative_to(repo_path).parts) > 1
    })

    file_list = [str(f.relative_to(repo_path)) for f in graph_files]

    readme_excerpt = ""
    for name in ("README.md", "README.rst", "README.txt", "README"):
        readme_path = repo_path / name
        if readme_path.exists():
            try:
                readme_excerpt = readme_path.read_text(encoding="utf-8")[:2000]
            except (OSError, UnicodeDecodeError) as e:
                print(f"Warning: skipping {name} — {type(e).__name__}", file=sys.stderr)
            break

    return {
        "repo_name": repo_path.name,
        "language_breakdown": lang_counts,
        "top_dirs": top_dirs,
        "file_count": len(graph_files),
        "file_list": file_list,
        "readme_excerpt": readme_excerpt,
    }


def analyze_imports(graph_files: list[Path], repo_root: Path) -> dict:
    """Extract intra-repo import edges from recognized language files."""
    nodes = []
    links = []
    seen_links: set[tuple[str, str]] = set()

    for file_path in graph_files:
        rel = str(file_path.relative_to(repo_root))
        lang = LANG_EXTENSIONS[file_path.suffix]
        nodes.append({"id": rel, "language": lang})

        try:
            source = file_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as e:
            print(f"Warning: skipping {rel} — {type(e).__name__}", file=sys.stderr)
            continue

        for target in _extract_imports(file_path, source, repo_root):
            pair = (rel, target)
            if pair not in seen_links:
                seen_links.add(pair)
                links.append({"source": rel, "target": target})

    return {"nodes": nodes, "links": links}


def _extract_imports(file_path: Path, source: str, repo_root: Path) -> list[str]:
    ext = file_path.suffix
    if ext == ".py":
        return _python_imports(file_path, source, repo_root)
    if ext in (".js", ".jsx", ".ts", ".tsx"):
        return _js_imports(file_path, source, repo_root)
    if ext == ".go":
        return _go_imports(file_path, source, repo_root)
    if ext == ".java":
        return _java_imports(file_path, source, repo_root)
    if ext == ".rb":
        return _ruby_imports(file_path, source, repo_root)
    return []


def _resolve_python_module(module: str, repo_root: Path) -> str | None:
    """Resolve dotted module name to relative file path. Returns None if not found."""
    parts = module.split(".")
    pkg_path = Path(*parts)
    for candidate in (
        repo_root / pkg_path / "__init__.py",
        repo_root / pkg_path.parent / (pkg_path.name + ".py"),
    ):
        if candidate.exists():
            return str(candidate.relative_to(repo_root))
    return None


def _python_imports(file_path: Path, source: str, repo_root: Path) -> list[str]:
    results = []
    for m in re.finditer(r"^\s*import\s+([\w.]+)", source, re.MULTILINE):
        t = _resolve_python_module(m.group(1), repo_root)
        if t:
            results.append(t)
    for m in re.finditer(r"^\s*from\s+([\w.]+)\s+import\s+", source, re.MULTILINE):
        t = _resolve_python_module(m.group(1), repo_root)
        if t:
            results.append(t)
    return results


def _js_imports(file_path: Path, source: str, repo_root: Path) -> list[str]:
    results = []
    pattern = r"""(?:import\s+[^;]+?\s+from|require\s*\()\s*['"](\.[^'"]+)['"]"""
    for m in re.finditer(pattern, source):
        spec = m.group(1)
        base = file_path.parent
        candidate = (base / spec).resolve()
        for p in (candidate, candidate.with_suffix(".js"), candidate.with_suffix(".ts")):
            if p.is_file():
                try:
                    results.append(str(p.relative_to(repo_root)))
                except ValueError:
                    pass
                break
    return results


def _go_imports(file_path: Path, source: str, repo_root: Path) -> list[str]:
    go_mod = repo_root / "go.mod"
    module_prefix = ""
    if go_mod.exists():
        try:
            for line in go_mod.read_text(encoding="utf-8").splitlines():
                m = re.match(r"^module\s+(\S+)", line)
                if m:
                    module_prefix = m.group(1)
                    break
        except (OSError, UnicodeDecodeError):
            pass

    if not module_prefix:
        return []

    all_imports: list[str] = []
    all_imports += re.findall(r'^\s*import\s+"([^"]+)"', source, re.MULTILINE)
    block = re.search(r"import\s*\(([^)]+)\)", source, re.DOTALL)
    if block:
        all_imports += re.findall(r'"([^"]+)"', block.group(1))

    results = []
    for imp in all_imports:
        if not imp.startswith(module_prefix):
            continue
        rel_pkg = imp[len(module_prefix):].lstrip("/")
        pkg_dir = repo_root / rel_pkg
        if pkg_dir.is_dir():
            go_files = sorted(pkg_dir.glob("*.go"))
            if go_files:
                results.append(str(go_files[0].relative_to(repo_root)))
    return results


def _detect_java_root_package(repo_root: Path) -> str:
    for java_file in sorted(repo_root.rglob("*.java")):
        if any(part in SKIP_DIRS for part in java_file.relative_to(repo_root).parts):
            continue
        try:
            for line in java_file.read_text(encoding="utf-8").splitlines():
                m = re.match(r"^\s*package\s+([\w.]+)\s*;", line)
                if m:
                    parts = m.group(1).split(".")
                    return ".".join(parts[:3])
        except (OSError, UnicodeDecodeError):
            continue
    return ""


def _java_imports(file_path: Path, source: str, repo_root: Path) -> list[str]:
    root_pkg = _detect_java_root_package(repo_root)
    if not root_pkg:
        return []
    results = []
    for m in re.finditer(r"^\s*import\s+([\w.]+);", source, re.MULTILINE):
        fqn = m.group(1)
        if not fqn.startswith(root_pkg):
            continue
        candidate = repo_root / (fqn.replace(".", "/") + ".java")
        if candidate.exists():
            results.append(str(candidate.relative_to(repo_root)))
    return results


def _ruby_imports(file_path: Path, source: str, repo_root: Path) -> list[str]:
    results = []
    # require_relative 'X' — always intra-repo, relative to current file
    for m in re.finditer(r"""require_relative\s+['"]([^'"]+)['"]""", source):
        spec = m.group(1)
        base = file_path.parent
        for candidate in ((base / spec).with_suffix(".rb"), base / spec):
            if candidate.is_file():
                try:
                    results.append(str(candidate.relative_to(repo_root)))
                except ValueError:
                    pass
                break
    # require 'X' — intra-repo only if repo_root/X.rb exists
    for m in re.finditer(r"""require\s+['"]([^'"]+)['"]""", source):
        spec = m.group(1)
        candidate = repo_root / (spec + ".rb")
        if candidate.is_file():
            try:
                results.append(str(candidate.relative_to(repo_root)))
            except ValueError:
                pass
    return results


def _build_openai_prompt(summary: dict) -> str:
    raise NotImplementedError


def call_openai(repo_summary: dict) -> dict:
    raise NotImplementedError


def md_to_html(text: str) -> str:
    """Convert a markdown subset to HTML using a two-pass approach."""
    if not text:
        return ""

    # Pass 1: classify each line
    classified: list[tuple[str, str]] = []
    for line in text.splitlines():
        s = line.strip()
        if not s:
            classified.append(("blank", ""))
        elif s.startswith("## "):
            classified.append(("h2", s[3:]))
        elif s.startswith("# "):
            classified.append(("h1", s[2:]))
        elif re.match(r"^\d+\.\s", s):
            classified.append(("li_ol", re.sub(r"^\d+\.\s+", "", s)))
        elif s.startswith(("- ", "* ")):
            classified.append(("li_ul", s[2:]))
        else:
            classified.append(("p", s))

    # Pass 2: emit HTML with inline transforms
    def inline(t: str) -> str:
        t = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", t)
        t = re.sub(r"`(.+?)`", r"<code>\1</code>", t)
        return t

    parts: list[str] = []
    i = 0
    while i < len(classified):
        kind, content = classified[i]
        if kind == "blank":
            i += 1
        elif kind in ("h1", "h2"):
            parts.append(f"<{kind}>{inline(content)}</{kind}>")
            i += 1
        elif kind == "li_ol":
            items = []
            while i < len(classified) and classified[i][0] == "li_ol":
                items.append(f"<li>{inline(classified[i][1])}</li>")
                i += 1
            parts.append("<ol>" + "".join(items) + "</ol>")
        elif kind == "li_ul":
            items = []
            while i < len(classified) and classified[i][0] == "li_ul":
                items.append(f"<li>{inline(classified[i][1])}</li>")
                i += 1
            parts.append("<ul>" + "".join(items) + "</ul>")
        else:
            parts.append(f"<p>{inline(content)}</p>")
            i += 1

    return "\n".join(parts)


def render_html(tree_data: dict, graph_data: dict, ai_result: dict, output_path: Path) -> None:
    raise NotImplementedError


def write_markdown(explanation: str, getting_started: str, md_path: Path) -> None:
    raise NotImplementedError


@click.command()
@click.argument("repo_path", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option("-o", "--output", "output_path", default=None, help="Output HTML filename")
@click.option("--md", "write_md", is_flag=True, default=False, help="Also write Markdown export")
@click.option("--no-ai", "no_ai", is_flag=True, default=False, help="Skip OpenAI, diagram only")
def main(repo_path: Path, output_path: str | None, write_md: bool, no_ai: bool) -> None:
    """Generate a visual architecture diagram and explanation for a Git repository."""
    raise NotImplementedError


if __name__ == "__main__":
    main()
