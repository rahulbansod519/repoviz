from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest
from click.testing import CliRunner
from repoviz import md_to_html, render_html, write_markdown, call_openai, main

FIXTURES = Path(__file__).parent / "fixtures"

FIXTURE_AI = {
    "explanation": "This project is a web server. It serves HTTP requests.",
    "getting_started": "1. Install deps\n2. Run server",
}
EMPTY_AI = {"explanation": "", "getting_started": ""}


def test_md_numbered_list():
    result = md_to_html("1. First step\n2. Second step")
    assert "<ol>" in result
    assert "<li>First step</li>" in result
    assert "<li>Second step</li>" in result


def test_md_numbered_list_grouped():
    result = md_to_html("1. A\n2. B\n3. C")
    assert result.count("<ol>") == 1
    assert result.count("</ol>") == 1


def test_md_unordered_list():
    result = md_to_html("- Item one\n- Item two")
    assert "<ul>" in result
    assert "<li>Item one</li>" in result


def test_md_headings():
    assert "<h2>Section</h2>" in md_to_html("## Section")
    assert "<h1>Title</h1>" in md_to_html("# Title")


def test_md_inline_code():
    assert "<code>pytest</code>" in md_to_html("Run `pytest` now")


def test_md_bold():
    assert "<strong>important</strong>" in md_to_html("This is **important**")


def test_md_blank_lines_no_output():
    assert md_to_html("\n\n").strip() == ""


def test_md_empty_string():
    assert md_to_html("") == ""
