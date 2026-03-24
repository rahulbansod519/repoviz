from pathlib import Path
import pytest
from repoviz import analyze_imports, scan_repo

FIXTURES = Path(__file__).parent / "fixtures"


def _node_ids(graph_data: dict) -> set:
    return {n["id"] for n in graph_data["nodes"]}


def _link_pairs(graph_data: dict) -> set:
    return {(lk["source"], lk["target"]) for lk in graph_data["links"]}


def test_python_nodes_present():
    root = FIXTURES / "simple_python"
    _, graph_files, _ = scan_repo(root)
    gd = analyze_imports(graph_files, root)
    assert "main.py" in _node_ids(gd)
    assert "utils.py" in _node_ids(gd)


def test_python_module_import_edge():
    root = FIXTURES / "simple_python"
    _, graph_files, _ = scan_repo(root)
    gd = analyze_imports(graph_files, root)
    # main.py: `from utils import helper` → resolves to utils.py
    assert ("main.py", "utils.py") in _link_pairs(gd)


def test_python_package_import_edge():
    root = FIXTURES / "simple_python"
    _, graph_files, _ = scan_repo(root)
    gd = analyze_imports(graph_files, root)
    # main.py: `import helpers` → resolves to helpers/__init__.py
    assert ("main.py", "helpers/__init__.py") in _link_pairs(gd)


def test_node_has_language():
    root = FIXTURES / "simple_python"
    _, graph_files, _ = scan_repo(root)
    gd = analyze_imports(graph_files, root)
    node = next(n for n in gd["nodes"] if n["id"] == "main.py")
    assert node["language"] == "python"


def test_graph_data_shape():
    root = FIXTURES / "simple_python"
    _, graph_files, _ = scan_repo(root)
    gd = analyze_imports(graph_files, root)
    assert "nodes" in gd and "links" in gd
    assert all("id" in n and "language" in n for n in gd["nodes"])
    assert all("source" in lk and "target" in lk for lk in gd["links"])


def test_js_node_present():
    root = FIXTURES / "multi_lang"
    _, gf, _ = scan_repo(root)
    gd = analyze_imports(gf, root)
    assert "index.js" in _node_ids(gd)


def test_js_external_not_drawn():
    root = FIXTURES / "multi_lang"
    _, gf, _ = scan_repo(root)
    gd = analyze_imports(gf, root)
    assert not any("lodash" in n["id"] for n in gd["nodes"])


def test_go_intra_repo_edge():
    root = FIXTURES / "multi_lang"
    _, gf, _ = scan_repo(root)
    gd = analyze_imports(gf, root)
    # server.go imports github.com/example/myapp/internal/router
    # internal/router/router.go exists → edge expected
    pairs = _link_pairs(gd)
    assert any("server.go" in s and "router" in t for s, t in pairs)


def test_java_intra_repo_edge():
    root = FIXTURES / "multi_lang"
    _, gf, _ = scan_repo(root)
    gd = analyze_imports(gf, root)
    # Main.java imports com.example.myapp.service.UserService
    # com/example/myapp/service/UserService.java exists
    pairs = _link_pairs(gd)
    assert any("Main.java" in s for s, t in pairs)


def test_java_stdlib_not_drawn():
    root = FIXTURES / "multi_lang"
    _, gf, _ = scan_repo(root)
    gd = analyze_imports(gf, root)
    assert not any("java/util" in n["id"] for n in gd["nodes"])


def test_ruby_require_relative_edge():
    root = FIXTURES / "multi_lang"
    _, gf, _ = scan_repo(root)
    gd = analyze_imports(gf, root)
    # app.rb: require_relative 'lib/helper' → lib/helper.rb exists
    pairs = _link_pairs(gd)
    assert any("app.rb" in s and "helper" in t for s, t in pairs)


def test_ruby_stdlib_not_drawn():
    root = FIXTURES / "multi_lang"
    _, gf, _ = scan_repo(root)
    gd = analyze_imports(gf, root)
    assert not any(n["id"] == "json" for n in gd["nodes"])
