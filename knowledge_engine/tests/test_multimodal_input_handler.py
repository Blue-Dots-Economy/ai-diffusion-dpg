"""
tests/test_multimodal_input_handler.py

Unit tests for MultimodalInputHandlerBlock (Block 5).
File I/O and LLM calls are mocked — no real files or API calls.
"""

import os
import pytest
from unittest.mock import MagicMock, patch, mock_open

from src.blocks.multimodal_input_handler import MultimodalInputHandlerBlock
from src.base import KEContext
from src.models import SessionState, LLMResponse


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

CONFIG = {
    "knowledge": {
        "blocks": {
            "multimodal_input_handler": {
                "enabled": True,
                "supported_types": ["pdf", "image"],
                "audio_enabled": False,
                "image_model": "claude-sonnet-4-6",
                "max_file_size_mb": 10,
            }
        }
    }
}


def make_context(raw_input: str = "Hello there") -> KEContext:
    return KEContext(
        session_id="test-session",
        raw_input=raw_input,
        normalised_input=raw_input,
        detected_language="english",
        intent="unknown",
        entities={},
        confidence=0.0,
        retrieval_chunks=[],
        always_include_chunks=[],
        session_state=SessionState.empty("test-session"),
    )


@pytest.fixture
def block():
    return MultimodalInputHandlerBlock()


@pytest.fixture
def mock_llm():
    llm = MagicMock()
    llm.call.return_value = LLMResponse(
        content="This is a job application form with fields for name and trade.",
        stop_reason="end_turn",
        model_used="claude-sonnet-4-6",
    )
    return llm


# ---------------------------------------------------------------------------
# Disabled block
# ---------------------------------------------------------------------------


def test_disabled_block_passes_context_unchanged(block, mock_llm):
    disabled_config = {
        "knowledge": {
            "blocks": {
                "multimodal_input_handler": {"enabled": False}
            }
        }
    }
    ctx = make_context("Hello no file here")
    result = block.process(ctx, mock_llm, disabled_config)
    assert result.raw_input == "Hello no file here"
    mock_llm.call.assert_not_called()


# ---------------------------------------------------------------------------
# No file references
# ---------------------------------------------------------------------------


def test_no_file_reference_returns_context_unchanged(block, mock_llm):
    ctx = make_context("kaam chahiye Hubli mein")
    result = block.process(ctx, mock_llm, CONFIG)
    assert result.raw_input == "kaam chahiye Hubli mein"
    mock_llm.call.assert_not_called()


# ---------------------------------------------------------------------------
# PDF extraction
# ---------------------------------------------------------------------------


def test_pdf_text_appended_to_raw_input(block, mock_llm):
    with patch("os.path.exists", return_value=True), \
         patch("os.path.getsize", return_value=1024 * 500), \
         patch.object(block, "_extract_pdf_text", return_value="Extracted PDF content here"):
        ctx = make_context("Please process this FILE:/docs/application.pdf")
        result = block.process(ctx, mock_llm, CONFIG)

    assert "Extracted PDF content here" in result.raw_input
    assert "[Extracted from PDF]" in result.raw_input


def test_pdf_extraction_calls_fitz(block, mock_llm):
    """Verify _extract_pdf_text uses PyMuPDF."""
    mock_page = MagicMock()
    mock_page.get_text.return_value = "Page 1 content about PMKVY"
    mock_doc = MagicMock()
    mock_doc.__iter__ = MagicMock(return_value=iter([mock_page]))
    mock_doc.close = MagicMock()

    with patch("fitz.open", return_value=mock_doc):
        text = block._extract_pdf_text("/fake/path.pdf")

    assert "Page 1 content" in text


# ---------------------------------------------------------------------------
# Image description
# ---------------------------------------------------------------------------


def test_image_description_appended_to_raw_input(block, mock_llm):
    with patch("os.path.exists", return_value=True), \
         patch("os.path.getsize", return_value=1024 * 200), \
         patch("builtins.open", mock_open(read_data=b"fake_image_bytes")):
        ctx = make_context("Please check this FILE:/images/certificate.jpg")
        result = block.process(ctx, mock_llm, CONFIG)

    assert "Image description" in result.raw_input
    mock_llm.call.assert_called_once()
    call_kwargs = mock_llm.call.call_args[1]
    assert call_kwargs.get("model_override") == "claude-sonnet-4-6"


def test_image_description_failure_returns_context_unchanged(block, mock_llm):
    mock_llm.call.return_value = LLMResponse(content=None, stop_reason="error")

    with patch("os.path.exists", return_value=True), \
         patch("os.path.getsize", return_value=1024 * 100), \
         patch("builtins.open", mock_open(read_data=b"fake_image_bytes")):
        original = "Please check FILE:/images/cert.png"
        ctx = make_context(original)
        result = block.process(ctx, mock_llm, CONFIG)

    # LLM failed — no description appended, but no crash
    assert "[Image description]" not in result.raw_input


# ---------------------------------------------------------------------------
# Audio stub
# ---------------------------------------------------------------------------


def test_audio_file_logs_stub_not_processed(block, mock_llm):
    """Audio files should be logged as out-of-scope and not processed."""
    with patch("os.path.exists", return_value=True), \
         patch("os.path.getsize", return_value=1024 * 500):
        ctx = make_context("Voice note FILE:/audio/message.mp3")
        result = block.process(ctx, mock_llm, CONFIG)

    # Audio should not be appended
    assert "transcription" not in result.raw_input.lower()
    mock_llm.call.assert_not_called()


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_file_not_found_skipped_gracefully(block, mock_llm):
    with patch("os.path.exists", return_value=False):
        ctx = make_context("Check FILE:/nonexistent/file.pdf")
        result = block.process(ctx, mock_llm, CONFIG)

    assert result.raw_input == "Check FILE:/nonexistent/file.pdf"
    mock_llm.call.assert_not_called()


def test_file_too_large_skipped(block, mock_llm):
    with patch("os.path.exists", return_value=True), \
         patch("os.path.getsize", return_value=1024 * 1024 * 15):  # 15MB > 10MB limit
        ctx = make_context("Big FILE:/large/document.pdf")
        result = block.process(ctx, mock_llm, CONFIG)

    assert "[Extracted from PDF]" not in result.raw_input


def test_multiple_file_references_processed(block, mock_llm):
    with patch("os.path.exists", return_value=True), \
         patch("os.path.getsize", return_value=1024 * 100), \
         patch.object(block, "_extract_pdf_text", return_value="PDF text content"), \
         patch.object(block, "_describe_image", return_value="Image description text"):
        ctx = make_context("FILE:/doc.pdf and FILE:/image.jpg attached")
        result = block.process(ctx, mock_llm, CONFIG)

    assert "PDF text content" in result.raw_input
    assert "Image description text" in result.raw_input


def test_unsupported_file_type_skipped_no_error(block, mock_llm):
    with patch("os.path.exists", return_value=True), \
         patch("os.path.getsize", return_value=1024 * 10):
        ctx = make_context("FILE:/data/export.xlsx")
        result = block.process(ctx, mock_llm, CONFIG)

    assert "[Extracted" not in result.raw_input
    # Must not raise


def test_exception_during_processing_returns_context(block, mock_llm):
    """Any exception inside processing must not crash the caller."""
    with patch("os.path.exists", return_value=True), \
         patch("os.path.getsize", return_value=1024 * 10), \
         patch.object(block, "_extract_pdf_text", side_effect=RuntimeError("fitz error")):
        ctx = make_context("FILE:/problem.pdf")
        result = block.process(ctx, mock_llm, CONFIG)

    assert result is not None  # must return context, not raise


def test_extract_file_references_empty_input(block):
    assert block._extract_file_references("") == []
    assert block._extract_file_references("no files here") == []


def test_extract_file_references_finds_paths(block):
    paths = block._extract_file_references("Here is FILE:/a/b.pdf and FILE:/c/d.jpg")
    assert "/a/b.pdf" in paths
    assert "/c/d.jpg" in paths
