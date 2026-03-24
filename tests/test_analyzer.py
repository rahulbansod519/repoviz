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
