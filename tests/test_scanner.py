from pathlib import Path
import pytest
from repoviz import scan_repo

FIXTURES = Path(__file__).parent / "fixtures"


def _collect_names(node: dict) -> set:
    names = {node["name"]}
    for child in node.get("children", []):
        names |= _collect_names(child)
    return names


def test_tree_root_name():
    tree, _, _ = scan_repo(FIXTURES / "simple_python")
    assert tree["name"] == "simple_python"


def test_tree_contains_files():
    tree, _, _ = scan_repo(FIXTURES / "simple_python")
    names = _collect_names(tree)
    assert "main.py" in names
    assert "utils.py" in names


def test_tree_contains_subdirectory():
    tree, _, _ = scan_repo(FIXTURES / "simple_python")
    top = {c["name"] for c in tree.get("children", [])}
    assert "helpers" in top


def test_node_modules_excluded():
    tree, _, _ = scan_repo(FIXTURES / "skip_test")
    names = _collect_names(tree)
    assert "node_modules" not in names


def test_graph_files_recognized_extensions_only():
    _, graph_files, _ = scan_repo(FIXTURES / "simple_python")
    assert all(f.suffix == ".py" for f in graph_files)
    assert len(graph_files) >= 2


def test_repo_summary_repo_name():
    _, _, summary = scan_repo(FIXTURES / "simple_python")
    assert summary["repo_name"] == "simple_python"


def test_repo_summary_language_breakdown():
    _, _, summary = scan_repo(FIXTURES / "simple_python")
    assert summary["language_breakdown"].get("python", 0) >= 2


def test_repo_summary_readme_excerpt():
    _, _, summary = scan_repo(FIXTURES / "simple_python")
    assert "Simple Python Fixture" in summary["readme_excerpt"]


def test_repo_summary_file_list():
    _, _, summary = scan_repo(FIXTURES / "simple_python")
    assert any("main.py" in p for p in summary["file_list"])


def test_repo_summary_file_count():
    _, _, summary = scan_repo(FIXTURES / "simple_python")
    assert summary["file_count"] >= 2
