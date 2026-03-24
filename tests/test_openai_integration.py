"""Integration tests for call_openai — skipped without OPENAI_API_KEY."""
import os
import pytest
from repoviz import call_openai

pytestmark = pytest.mark.skipif(
    not os.environ.get("OPENAI_API_KEY"),
    reason="OPENAI_API_KEY not set",
)


def test_call_openai_returns_nonempty_strings():
    summary = {
        "repo_name": "repoviz",
        "language_breakdown": {"python": 1},
        "top_dirs": ["tests"],
        "file_count": 1,
        "file_list": ["repoviz.py"],
        "readme_excerpt": "A tool that generates repo diagrams.",
    }
    result = call_openai(summary)
    assert isinstance(result["explanation"], str) and result["explanation"]
    assert isinstance(result["getting_started"], str) and result["getting_started"]
