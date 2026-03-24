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


_SYSTEM_PROMPT = (
    "You are a technical writer. Given a summary of a software repository, "
    "return a JSON object with exactly two fields:\n"
    '- "explanation": a 2-3 sentence plain-English description of what the project does and who it is for.\n'
    '- "getting_started": a step-by-step markdown guide (numbered list) for a new contributor '
    "to install dependencies and run the project locally.\n"
    "Return only valid JSON. Do not include markdown code fences around the JSON."
)


def _build_openai_prompt(summary: dict) -> str:
    """Build OpenAI user message, truncating to stay under MAX_PROMPT_CHARS."""
    file_list = list(summary.get("file_list", []))
    top_dirs = list(summary.get("top_dirs", []))
    readme = summary.get("readme_excerpt", "")

    def build(fl: list, td: list, re_text: str) -> str:
        fl_str = "\n".join(fl) if fl else "(none)"
        td_str = ", ".join(str(d) for d in td) if td else "(none)"
        return (
            f"Repository: {summary['repo_name']}\n"
            f"Languages: {summary.get('language_breakdown', {})}\n"
            f"Top-level directories: {td_str}\n"
            f"File count: {summary.get('file_count', 0)}\n"
            f"Files:\n{fl_str}\n"
            f"README (excerpt):\n{re_text}"
        )

    for fl in (file_list, file_list[:100], file_list[:50], []):
        prompt = build(fl, top_dirs, readme)
        if len(prompt) <= MAX_PROMPT_CHARS:
            return prompt

    prompt = build([], [], readme)
    if len(prompt) <= MAX_PROMPT_CHARS:
        return prompt

    prompt = build([], [], readme[:500])
    if len(prompt) <= MAX_PROMPT_CHARS:
        return prompt

    return build([], [], "")


def call_openai(repo_summary: dict) -> dict:
    """Call OpenAI gpt-4o for explanation and getting_started."""
    empty: dict = {"explanation": "", "getting_started": ""}
    client = OpenAI()

    try:
        response = client.chat.completions.create(
            model="gpt-4o",
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": _build_openai_prompt(repo_summary)},
            ],
        )
    except OpenAIError as e:
        print(f"Warning: OpenAI API call failed — {e}", file=sys.stderr)
        return empty

    try:
        data = json.loads(response.choices[0].message.content)
    except (json.JSONDecodeError, IndexError) as e:
        print(f"Warning: failed to parse OpenAI response — {e}", file=sys.stderr)
        return empty

    result: dict = {}
    for key in ("explanation", "getting_started"):
        val = data.get(key)
        if isinstance(val, str) and val.strip():
            result[key] = val
        else:
            print(f"Warning: OpenAI response missing expected key: {key}", file=sys.stderr)
            result[key] = ""

    return result


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


_HTML_TEMPLATE = """\
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>__REPO_NAME__ — repoviz</title>
  <script src="https://cdn.jsdelivr.net/npm/d3@7"></script>
  <style>
    body{font-family:system-ui,sans-serif;margin:0;padding:20px;background:#f8f9fa;color:#1a1a1a}
    h1{font-size:1.5rem;color:#1a1a2e;margin-top:0}
    .tabs{display:flex;gap:8px;margin-bottom:16px}
    .tab-btn{padding:8px 16px;border:1px solid #ccc;background:#fff;cursor:pointer;border-radius:4px;font-size:.9rem}
    .tab-btn.active{background:#1a1a2e;color:#fff;border-color:#1a1a2e}
    .view{display:none}.view.active{display:block}
    #tree-svg,#graph-svg{width:100%;height:600px;border:1px solid #e0e0e0;background:#fff;border-radius:4px;display:block}
    .section{margin-top:24px;background:#fff;padding:20px;border-radius:4px;border:1px solid #e0e0e0}
    .section h2{margin-top:0;font-size:1.1rem;color:#1a1a2e}
    .notice{color:#888;font-style:italic}
    .node circle{fill:#fff;stroke:#1a1a2e;stroke-width:1.5px;cursor:pointer}
    .node text{font-size:11px}
    .link{fill:none;stroke:#ccc;stroke-width:1px}
    .graph-node{cursor:pointer}
    .graph-link{stroke:#ccc;stroke-opacity:.6;fill:none}
  </style>
</head>
<body>
  <h1>__REPO_NAME__</h1>
  <div class="tabs">
    <button class="tab-btn active" onclick="switchTab('tree',this)">File Tree</button>
    <button class="tab-btn" onclick="switchTab('graph',this)">Import Graph</button>
  </div>
  <div id="tree-view" class="view active"><svg id="tree-svg"></svg></div>
  <div id="graph-view" class="view">
    <svg id="graph-svg">
      <defs><marker id="arrow" markerWidth="8" markerHeight="6" refX="8" refY="3" orient="auto">
        <path d="M0,0 L0,6 L8,3 z" fill="#999"/>
      </marker></defs>
    </svg>
  </div>
  <div class="section"><h2>What this project does</h2>__EXPLANATION__</div>
  <div class="section"><h2>Getting Started</h2>__GETTING_STARTED__</div>
  <script>
    const TREE_DATA=__TREE_DATA__;
    const GRAPH_DATA=__GRAPH_DATA__;
    function switchTab(n,b){
      document.querySelectorAll('.tab-btn').forEach(x=>x.classList.remove('active'));
      document.querySelectorAll('.view').forEach(x=>x.classList.remove('active'));
      b.classList.add('active');
      document.getElementById(n+'-view').classList.add('active');
      if(n==='tree')drawTree();else drawGraph();
    }
    function drawTree(){
      const svg=d3.select('#tree-svg');svg.selectAll('*').remove();
      const W=svg.node().getBoundingClientRect().width||800,H=600;
      const m={top:20,right:160,bottom:20,left:40};
      const g=svg.append('g').attr('transform',`translate(${m.left},${m.top})`);
      svg.call(d3.zoom().scaleExtent([.1,4]).on('zoom',e=>g.attr('transform',e.transform)));
      const root=d3.hierarchy(TREE_DATA);
      root.descendants().forEach(d=>{d._children=d.children||null;});
      const layout=d3.tree().size([H-m.top-m.bottom,W-m.left-m.right]);
      function update(){
        layout(root);
        g.selectAll('.link').data(root.links(),d=>d.target.data.name+d.depth)
          .join('path').attr('class','link')
          .attr('d',d3.linkHorizontal().x(d=>d.y).y(d=>d.x));
        const node=g.selectAll('.node').data(root.descendants(),d=>d.data.name+d.depth)
          .join('g').attr('class','node').attr('transform',d=>`translate(${d.y},${d.x})`)
          .on('click',(e,d)=>{d.children=d.children?null:d._children;update();});
        node.selectAll('circle').data(d=>[d]).join('circle').attr('r',4);
        node.selectAll('text').data(d=>[d]).join('text')
          .attr('dy','.31em').attr('x',d=>d.children||d._children?-8:8)
          .attr('text-anchor',d=>d.children||d._children?'end':'start')
          .text(d=>d.data.name);
      }
      update();
    }
    const LC={python:'#3572A5',javascript:'#f1e05a',typescript:'#2b7489',go:'#00ADD8',java:'#b07219',ruby:'#701516'};
    function drawGraph(){
      const svg=d3.select('#graph-svg');
      svg.selectAll('*:not(defs)').remove();
      const W=svg.node().getBoundingClientRect().width||800,H=600;
      if(!GRAPH_DATA.nodes.length){
        svg.append('text').attr('x',W/2).attr('y',H/2).attr('text-anchor','middle').attr('fill','#888')
          .text('No import relationships found.');return;
      }
      const g=svg.append('g');
      svg.call(d3.zoom().scaleExtent([.1,4]).on('zoom',e=>g.attr('transform',e.transform)));
      const nodes=GRAPH_DATA.nodes.map(d=>({...d}));
      const links=GRAPH_DATA.links.map(d=>({...d}));
      const sim=d3.forceSimulation(nodes)
        .force('link',d3.forceLink(links).id(d=>d.id).distance(90))
        .force('charge',d3.forceManyBody().strength(-250))
        .force('center',d3.forceCenter(W/2,H/2));
      const link=g.append('g').selectAll('line').data(links).join('line')
        .attr('class','graph-link').attr('marker-end','url(#arrow)');
      const node=g.append('g').selectAll('g').data(nodes).join('g').attr('class','graph-node')
        .call(d3.drag()
          .on('start',(e,d)=>{if(!e.active)sim.alphaTarget(.3).restart();d.fx=d.x;d.fy=d.y;})
          .on('drag',(e,d)=>{d.fx=e.x;d.fy=e.y;})
          .on('end',(e,d)=>{if(!e.active)sim.alphaTarget(0);d.fx=null;d.fy=null;}));
      node.append('circle').attr('r',7).attr('fill',d=>LC[d.language]||'#aaa').attr('stroke','#333').attr('stroke-width',.5);
      node.append('title').text(d=>d.id);
      node.append('text').attr('dx',10).attr('dy','.35em').attr('font-size','10px').text(d=>d.id.split('/').pop());
      sim.on('tick',()=>{
        link.attr('x1',d=>d.source.x).attr('y1',d=>d.source.y).attr('x2',d=>d.target.x).attr('y2',d=>d.target.y);
        node.attr('transform',d=>`translate(${d.x},${d.y})`);
      });
    }
    drawTree();
  </script>
</body>
</html>"""


def render_html(
    tree_data: dict, graph_data: dict, ai_result: dict, output_path: Path
) -> None:
    """Render the complete HTML report file."""
    explanation = ai_result.get("explanation", "")
    getting_started = ai_result.get("getting_started", "")

    explanation_html = (
        f"<p>{html.escape(explanation)}</p>"
        if explanation
        else '<p class="notice">AI was not run — no explanation available.</p>'
    )
    getting_started_html = (
        md_to_html(getting_started)
        if getting_started
        else '<p class="notice">AI was not run — no getting started guide available.</p>'
    )

    repo_name = tree_data.get("name", "Repository")
    content = _HTML_TEMPLATE
    content = content.replace("__REPO_NAME__", html.escape(repo_name))
    content = content.replace("__TREE_DATA__", json.dumps(tree_data))
    content = content.replace("__GRAPH_DATA__", json.dumps(graph_data))
    content = content.replace("__EXPLANATION__", explanation_html)
    content = content.replace("__GETTING_STARTED__", getting_started_html)
    output_path.write_text(content, encoding="utf-8")


def write_markdown(explanation: str, getting_started: str, md_path: Path) -> None:
    """Write explanation and getting_started to a markdown file."""
    if not explanation and not getting_started:
        print("Notice: AI content is empty — markdown file not written.", file=sys.stderr)
        return
    content = ""
    if explanation:
        content += f"## What This Project Does\n\n{explanation}\n\n"
    if getting_started:
        content += f"## Getting Started\n\n{getting_started}\n"
    md_path.write_text(content, encoding="utf-8")


@click.command()
@click.argument("repo_path", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option("-o", "--output", "output_path", default=None, help="Output HTML filename")
@click.option("--md", "write_md", is_flag=True, default=False, help="Also write Markdown export")
@click.option("--no-ai", "no_ai", is_flag=True, default=False, help="Skip OpenAI, diagram only")
def main(repo_path: Path, output_path: str | None, write_md: bool, no_ai: bool) -> None:
    """Generate a visual architecture diagram and explanation for a Git repository."""
    repo_path = repo_path.resolve()

    if output_path is None:
        html_path = Path(f"{repo_path.name}-report.html")
    else:
        html_path = Path(output_path)

    md_path = html_path.with_suffix(".md") if html_path.suffix else Path(str(html_path) + ".md")

    tree_data, graph_files, repo_summary = scan_repo(repo_path)
    graph_data = analyze_imports(graph_files, repo_path)

    ai_result = {"explanation": "", "getting_started": ""} if no_ai else call_openai(repo_summary)

    render_html(tree_data, graph_data, ai_result, html_path)
    click.echo(f"Report written to {html_path}")

    if write_md:
        write_markdown(ai_result["explanation"], ai_result["getting_started"], md_path)
        if md_path.exists():
            click.echo(f"Markdown written to {md_path}")


if __name__ == "__main__":
    main()
