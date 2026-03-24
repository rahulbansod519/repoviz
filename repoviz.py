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
    raise NotImplementedError


def analyze_imports(graph_files: list[Path], repo_root: Path) -> dict:
    raise NotImplementedError


def _build_openai_prompt(summary: dict) -> str:
    raise NotImplementedError


def call_openai(repo_summary: dict) -> dict:
    raise NotImplementedError


def md_to_html(text: str) -> str:
    raise NotImplementedError


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
