"""
knowledge_engine/src/blocks/multimodal_input_handler.py

Block 5 — Multimodal Input Handler

Extracts text from non-text inputs (PDF, image) sent mid-conversation and
appends the extracted content to context.raw_input.

PoC status: Built and registered but disabled by default via config (`enabled: false`).
Enable for deployments that receive image or audio input
(e.g. a construction safety bot receiving photos).

Supported in PoC:
- PDF → extract text using PyMuPDF (fitz), append to context.raw_input
- Image → base64 encode, call LLM vision via self._llm.call(), append description
- Audio → stub: logs that ASR is out of scope, returns context unchanged

On any failure, returns context unchanged — never crashes caller.
"""

from __future__ import annotations

import base64
import logging
import os
import time
from typing import Optional

from src.base import KnowledgeBlock, KEContext, LLMWrapperBase

logger = logging.getLogger(__name__)

_VISION_PROMPT = """Describe the content of this image in clear, plain text.
Focus on any text, numbers, job-related information, or official document content visible.
Be concise and factual. Do not include personal opinions."""

_SUPPORTED_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
_SUPPORTED_PDF_EXTENSIONS = {".pdf"}


class MultimodalInputHandlerBlock(KnowledgeBlock):
    """
    Extracts text from non-text inputs attached to a user message.

    YAML config section read: knowledge.blocks.multimodal_input_handler

    After this block runs (when enabled and input contains file references):
        context.raw_input — appended with extracted text or image description
    """

    def process(
        self,
        context: KEContext,
        llm: LLMWrapperBase,
        config: dict,
    ) -> KEContext:
        """
        Process any non-text input referenced in context.raw_input.
        Returns context unchanged if disabled or if no file references found.
        Never raises.
        """
        start = time.time()

        block_cfg = (
            config.get("knowledge", {})
            .get("blocks", {})
            .get("multimodal_input_handler", {})
        )

        if not block_cfg.get("enabled", False):
            logger.info(
                "multimodal.skipped",
                extra={
                    "operation": "multimodal.process",
                    "status": "skipped",
                    "session_id": context.session_id,
                    "latency_ms": int((time.time() - start) * 1000),
                },
            )
            return context

        supported_types = block_cfg.get("supported_types", ["pdf", "image"])
        audio_enabled = block_cfg.get("audio_enabled", False)
        max_file_size_mb = block_cfg.get("max_file_size_mb", 10)
        image_model = block_cfg.get("image_model", "claude-haiku-4-5-20251001")

        # Look for file paths embedded in the raw_input
        # Convention: file references are passed as "FILE:<path>" tokens in context
        # In production, the Reach Layer would set these before calling KE.
        file_paths = self._extract_file_references(context.raw_input)

        if not file_paths:
            return context

        try:
            for file_path in file_paths:
                ext = os.path.splitext(file_path)[1].lower()

                if not os.path.exists(file_path):
                    logger.warning(
                        "multimodal.file_not_found",
                        extra={
                            "operation": "multimodal.process",
                            "status": "skipped",
                            "session_id": context.session_id,
                            "path": file_path,
                        },
                    )
                    continue

                # Check file size
                file_size_mb = os.path.getsize(file_path) / (1024 * 1024)
                if file_size_mb > max_file_size_mb:
                    logger.warning(
                        "multimodal.file_too_large",
                        extra={
                            "operation": "multimodal.process",
                            "status": "skipped",
                            "session_id": context.session_id,
                            "path": file_path,
                            "size_mb": round(file_size_mb, 2),
                            "limit_mb": max_file_size_mb,
                        },
                    )
                    continue

                if ext in _SUPPORTED_PDF_EXTENSIONS and "pdf" in supported_types:
                    extracted = self._extract_pdf_text(file_path)
                    if extracted:
                        context.raw_input += f"\n\n[Extracted from PDF]\n{extracted}"

                elif ext in _SUPPORTED_IMAGE_EXTENSIONS and "image" in supported_types:
                    description = self._describe_image(file_path, llm, image_model)
                    if description:
                        context.raw_input += f"\n\n[Image description]\n{description}"

                elif ext in {".mp3", ".wav", ".ogg", ".m4a"}:
                    if not audio_enabled:
                        logger.info(
                            "multimodal.audio_stub",
                            extra={
                                "operation": "multimodal.process",
                                "status": "skipped",
                                "session_id": context.session_id,
                                "reason": "ASR pipeline is out of scope for this framework. "
                                          "Voice conversion is handled upstream.",
                            },
                        )
                else:
                    logger.warning(
                        "multimodal.unsupported_type",
                        extra={
                            "operation": "multimodal.process",
                            "status": "skipped",
                            "session_id": context.session_id,
                            "ext": ext,
                        },
                    )

            logger.info(
                "multimodal.process",
                extra={
                    "operation": "multimodal.process",
                    "status": "success",
                    "session_id": context.session_id,
                    "files_processed": len(file_paths),
                    "latency_ms": int((time.time() - start) * 1000),
                },
            )

        except Exception as e:
            logger.error(
                "multimodal.error",
                extra={
                    "operation": "multimodal.process",
                    "status": "failure",
                    "session_id": context.session_id,
                    "error": f"{type(e).__name__}: {e}",
                    "latency_ms": int((time.time() - start) * 1000),
                },
            )
            # Return context as-is — must not crash

        return context

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _extract_file_references(self, text: str) -> list[str]:
        """
        Extract file path references from text.
        Convention: "FILE:/path/to/document.pdf" tokens embedded by Reach Layer.
        Returns list of absolute or relative file paths found.
        """
        if not text:
            return []
        paths = []
        for token in text.split():
            if token.startswith("FILE:"):
                path = token[5:]  # strip "FILE:" prefix
                if path:
                    paths.append(path)
        return paths

    def _extract_pdf_text(self, file_path: str) -> str:
        """Extract plain text from a PDF file using PyMuPDF."""
        import fitz  # PyMuPDF

        doc = fitz.open(file_path)
        pages_text = []
        for page in doc:
            text = page.get_text()
            if text.strip():
                pages_text.append(text.strip())
        doc.close()

        return "\n".join(pages_text)

    def _describe_image(
        self,
        file_path: str,
        llm: LLMWrapperBase,
        image_model: str,
    ) -> str:
        """
        Call the LLM with a vision prompt to get a text description of the image.
        Returns empty string on LLM failure.
        """
        with open(file_path, "rb") as f:
            image_data = base64.standard_b64encode(f.read()).decode("utf-8")

        ext = os.path.splitext(file_path)[1].lower().lstrip(".")
        media_type_map = {
            "jpg": "image/jpeg",
            "jpeg": "image/jpeg",
            "png": "image/png",
            "gif": "image/gif",
            "webp": "image/webp",
        }
        media_type = media_type_map.get(ext, "image/jpeg")

        messages = [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": image_data,
                        },
                    },
                    {"type": "text", "text": _VISION_PROMPT},
                ],
            }
        ]

        response = llm.call(
            messages=messages,
            tools=[],
            system="",
            model_override=image_model,
        )

        if response.stop_reason == "error" or not response.content:
            logger.warning(
                "multimodal.image_description_failed",
                extra={
                    "operation": "multimodal._describe_image",
                    "status": "failure",
                    "stop_reason": response.stop_reason,
                },
            )
            return ""

        return response.content
