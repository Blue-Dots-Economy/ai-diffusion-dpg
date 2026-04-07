"""
knowledge_engine/src/blocks/static_knowledge_base.py

Block 4 — Static Knowledge Base (RAG)

Retrieves the most relevant knowledge chunks from ChromaDB for the user's
normalised query. This is the core grounding block — it provides the context
the LLM uses to answer accurately about jobs, schemes, and training.

Two modes:
- ingest(): Offline, called from scripts/ingest.py once before first run.
            Loads documents → chunks → embeds → stores in ChromaDB.
- process(): Runtime, called on every turn. Embeds query → similarity search.

Embedding providers (from YAML config):
- sentence_transformers (default): Local model, no API key required.
- openai: API-based, requires OPENAI_API_KEY env var.

On any ChromaDB failure: logs error, returns empty chunks — does not crash caller.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any, Optional

from src.base import KnowledgeBlock, KEContext, LLMWrapperBase

logger = logging.getLogger(__name__)

# Intent → doc_type filter mapping is loaded from config at runtime.
# See knowledge.blocks.static_knowledge_base.intent_filters in config/config.yaml.


class StaticKnowledgeBaseBlock(KnowledgeBlock):
    """
    ChromaDB-backed RAG retrieval block.

    Instantiated once at engine startup. The ChromaDB collection is loaded
    (or created empty) at construction time. Documents are added via ingest().

    YAML config section read: knowledge.blocks.static_knowledge_base

    After this block runs:
        context.retrieval_chunks        — top-k relevant chunks above similarity threshold
        context.always_include_chunks   — all chunks with doc_type=always_include
    """

    def __init__(self) -> None:
        self._collection = None
        self._embedding_fn = None

    def warmup(self, block_cfg: dict) -> None:
        """Pre-load the embedding model and open the ChromaDB collection at startup.
        Prevents cold-start latency on the first real request."""
        if not block_cfg.get("enabled", True):
            return
        try:
            self._get_collection(block_cfg)
            logger.info(
                "static_kb.warmup_complete",
                extra={"operation": "static_kb.warmup", "status": "success"},
            )
        except Exception as e:
            logger.warning(
                "static_kb.warmup_failed",
                extra={
                    "operation": "static_kb.warmup",
                    "status": "failure",
                    "error": f"{type(e).__name__}: {e}",
                },
            )

    def process(
        self,
        context: KEContext,
        llm: LLMWrapperBase,
        config: dict,
    ) -> KEContext:
        """
        Query ChromaDB for chunks relevant to context.normalised_input.
        Returns context with retrieval_chunks and always_include_chunks populated.
        Never raises — on any failure, returns empty chunks.
        """
        start = time.time()

        block_cfg = (
            config.get("knowledge", {})
            .get("blocks", {})
            .get("static_knowledge_base", {})
        )

        if not block_cfg.get("enabled", True):
            logger.info(
                "static_kb.skipped",
                extra={
                    "operation": "static_kb.process",
                    "status": "skipped",
                    "session_id": context.session_id,
                    "latency_ms": int((time.time() - start) * 1000),
                },
            )
            return context

        try:
            collection = self._get_collection(block_cfg)
        except Exception as e:
            logger.error(
                "static_kb.collection_init_failed",
                extra={
                    "operation": "static_kb.process",
                    "status": "failure",
                    "session_id": context.session_id,
                    "error": f"{type(e).__name__}: {e}",
                    "latency_ms": int((time.time() - start) * 1000),
                },
            )
            return context  # empty chunks

        try:
            top_k = block_cfg.get("top_k", 3)
            similarity_threshold = block_cfg.get("similarity_threshold", 0.65)
            use_intent_filter = block_cfg.get("metadata_filters", {}).get("use_intent_filter", True)
            use_location_filter = block_cfg.get("metadata_filters", {}).get("use_location_filter", True)
            intent_filters: dict[str, list[str]] = block_cfg.get("intent_filters", {})

            # Build metadata filters
            where_filter = self._build_where_filter(
                context=context,
                use_intent_filter=use_intent_filter,
                use_location_filter=use_location_filter,
                intent_filters=intent_filters,
            )

            # Query for relevant chunks
            query_text = context.normalised_input or context.raw_input
            retrieval_chunks = self._query_collection(
                collection=collection,
                query_text=query_text,
                top_k=top_k,
                similarity_threshold=similarity_threshold,
                where_filter=where_filter,
            )

            # Fetch always_include chunks (bypass similarity filter)
            always_include_chunks = self._fetch_always_include(collection)

            context.retrieval_chunks = retrieval_chunks
            context.always_include_chunks = always_include_chunks

            logger.info(
                "static_kb.process",
                extra={
                    "operation": "static_kb.process",
                    "status": "success",
                    "session_id": context.session_id,
                    "retrieval_count": len(retrieval_chunks),
                    "always_include_count": len(always_include_chunks),
                    "latency_ms": int((time.time() - start) * 1000),
                },
            )

        except Exception as e:
            logger.error(
                "static_kb.retrieval_failed",
                extra={
                    "operation": "static_kb.process",
                    "status": "failure",
                    "session_id": context.session_id,
                    "error": f"{type(e).__name__}: {e}",
                    "latency_ms": int((time.time() - start) * 1000),
                },
            )
            context.retrieval_chunks = []
            context.always_include_chunks = []

        return context

    def ingest(self, config: dict) -> None:
        """
        Load documents from YAML-configured sources into ChromaDB.
        Called from scripts/ingest.py — not from the response path.

        Clears the existing collection and rebuilds from scratch on every run.
        """
        block_cfg = (
            config.get("knowledge", {})
            .get("blocks", {})
            .get("static_knowledge_base", {})
        )

        collection = self._get_collection(block_cfg, recreate=True)
        sources = block_cfg.get("sources", [])

        if not sources:
            logger.warning(
                "static_kb.ingest_no_sources",
                extra={"operation": "static_kb.ingest", "status": "skipped"},
            )
            return

        total_chunks = 0
        for source in sources:
            path = source.get("path", "")
            doc_type = source.get("doc_type", "unknown")

            if not os.path.exists(path):
                logger.warning(
                    "static_kb.ingest_source_missing",
                    extra={
                        "operation": "static_kb.ingest",
                        "status": "skipped",
                        "path": path,
                    },
                )
                continue

            try:
                chunks = self._load_and_chunk(path, doc_type)
                if chunks:
                    self._add_chunks_to_collection(collection, chunks, doc_type)
                    total_chunks += len(chunks)
                    logger.info(
                        "static_kb.ingest_source",
                        extra={
                            "operation": "static_kb.ingest",
                            "status": "success",
                            "path": path,
                            "doc_type": doc_type,
                            "chunk_count": len(chunks),
                        },
                    )
            except Exception as e:
                logger.error(
                    "static_kb.ingest_source_failed",
                    extra={
                        "operation": "static_kb.ingest",
                        "status": "failure",
                        "path": path,
                        "error": f"{type(e).__name__}: {e}",
                    },
                )

        logger.info(
            "static_kb.ingest_complete",
            extra={
                "operation": "static_kb.ingest",
                "status": "success",
                "total_chunks": total_chunks,
            },
        )

    # ------------------------------------------------------------------
    # Private: collection management
    # ------------------------------------------------------------------

    def _get_collection(self, block_cfg: dict, recreate: bool = False):
        """Get or create the ChromaDB collection with the configured embedding function."""
        import chromadb

        if self._collection is not None and not recreate:
            return self._collection

        persist_dir = (
            os.environ.get("CHROMA_PERSIST_DIR")
            or block_cfg.get("chroma_persist_dir")
            or "./data/chroma_db"
        )
        collection_name = block_cfg.get("collection_name", "dpg_knowledge")

        os.makedirs(persist_dir, exist_ok=True)
        client = chromadb.PersistentClient(path=persist_dir)

        embedding_fn = self._get_embedding_function(block_cfg)

        if recreate:
            try:
                client.delete_collection(name=collection_name)
            except Exception:
                pass  # collection may not exist yet

        self._collection = client.get_or_create_collection(
            name=collection_name,
            embedding_function=embedding_fn,
            metadata={"hnsw:space": "cosine"},
        )
        return self._collection

    def _get_embedding_function(self, block_cfg: dict):
        """Return the embedding function based on YAML config."""
        provider = block_cfg.get("embedding_provider", "sentence_transformers")
        model = block_cfg.get("embedding_model", "paraphrase-multilingual-MiniLM-L12-v2")

        if provider == "openai":
            api_key = os.environ.get("OPENAI_API_KEY")
            if not api_key:
                raise ValueError(
                    "OPENAI_API_KEY environment variable is not set. "
                    "Set it in .env or switch to embedding_provider: sentence_transformers."
                )
            from chromadb.utils.embedding_functions import OpenAIEmbeddingFunction
            return OpenAIEmbeddingFunction(api_key=api_key, model_name=model)

        elif provider == "sentence_transformers":
            from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction
            return SentenceTransformerEmbeddingFunction(model_name=model)

        else:
            raise ValueError(
                f"Unknown embedding_provider '{provider}'. "
                "Valid options: openai | sentence_transformers"
            )

    # ------------------------------------------------------------------
    # Private: retrieval
    # ------------------------------------------------------------------

    def _build_where_filter(
        self,
        context: KEContext,
        use_intent_filter: bool,
        use_location_filter: bool,
        intent_filters: dict[str, list[str]] | None = None,
    ) -> Optional[dict]:
        """
        Build a ChromaDB `where` filter dict from context.intent and entities.
        Returns None if no meaningful filter can be constructed.

        intent_filters: mapping of intent name → list of doc_types to restrict search to.
                        Loaded from knowledge.blocks.static_knowledge_base.intent_filters in config.
                        Empty list for an intent means no doc_type filter (search all).
        """
        if intent_filters is None:
            intent_filters = {}

        conditions = []

        # Intent → doc_type filter
        if use_intent_filter and context.intent:
            doc_types = intent_filters.get(context.intent, [])
            if doc_types:
                # Exclude always_include from similarity search (fetched separately)
                conditions.append({"doc_type": {"$in": doc_types}})

        # Location filter
        if use_location_filter:
            location = context.entities.get("location", "")
            if location:
                # Filter by district metadata field if present
                conditions.append({"district": {"$eq": location}})

        if not conditions:
            # No intent filter: still exclude always_include from similarity search
            return {"doc_type": {"$ne": "always_include"}}

        if len(conditions) == 1:
            return conditions[0]

        return {"$and": conditions}

    def _query_collection(
        self,
        collection,
        query_text: str,
        top_k: int,
        similarity_threshold: float,
        where_filter: Optional[dict],
    ) -> list[dict]:
        """Query ChromaDB and return chunks above the similarity threshold."""
        if not query_text:
            return []

        kwargs: dict = {
            "query_texts": [query_text],
            "n_results": top_k,
            "include": ["documents", "metadatas", "distances"],
        }
        if where_filter:
            kwargs["where"] = where_filter

        try:
            results = collection.query(**kwargs)
        except Exception as e:
            logger.error(
                "static_kb.query_failed",
                extra={
                    "operation": "static_kb._query_collection",
                    "status": "failure",
                    "error": f"{type(e).__name__}: {e}",
                },
            )
            return []

        chunks = []
        documents = (results.get("documents") or [[]])[0]
        metadatas = (results.get("metadatas") or [[]])[0]
        distances = (results.get("distances") or [[]])[0]

        for doc, meta, dist in zip(documents, metadatas, distances):
            # ChromaDB cosine distance: 0 = identical, 2 = opposite
            # Convert to similarity score: similarity = 1 - (distance / 2)
            similarity = 1.0 - (dist / 2.0)
            if similarity >= similarity_threshold:
                chunks.append({
                    "text": doc,
                    "metadata": meta or {},
                    "similarity": round(similarity, 4),
                })

        return chunks

    def _fetch_always_include(self, collection) -> list[dict]:
        """Fetch all chunks with doc_type=always_include (bypass similarity filter)."""
        try:
            results = collection.get(
                where={"doc_type": {"$eq": "always_include"}},
                include=["documents", "metadatas"],
            )
            chunks = []
            documents = results.get("documents") or []
            metadatas = results.get("metadatas") or []
            for doc, meta in zip(documents, metadatas):
                chunks.append({"text": doc, "metadata": meta or {}})
            return chunks
        except Exception as e:
            logger.error(
                "static_kb.always_include_fetch_failed",
                extra={
                    "operation": "static_kb._fetch_always_include",
                    "status": "failure",
                    "error": f"{type(e).__name__}: {e}",
                },
            )
            return []

    # ------------------------------------------------------------------
    # Private: document loading + chunking
    # ------------------------------------------------------------------

    def _load_and_chunk(self, path: str, doc_type: str) -> list[dict]:
        """
        Load a document from path and split into chunks.
        Returns list of {"text": str, "metadata": dict} dicts.
        Supports: .pdf, .csv, .md, .txt
        """
        ext = os.path.splitext(path)[1].lower()

        if ext == ".pdf":
            return self._chunk_pdf(path, doc_type)
        elif ext == ".csv":
            return self._chunk_csv(path, doc_type)
        elif ext in (".md", ".txt"):
            return self._chunk_text_file(path, doc_type)
        elif ext == ".json":
            return self._chunk_json(path, doc_type)
        else:
            logger.warning(
                "static_kb.unsupported_format",
                extra={
                    "operation": "static_kb._load_and_chunk",
                    "status": "skipped",
                    "path": path,
                    "ext": ext,
                },
            )
            return []

    def _chunk_pdf(self, path: str, doc_type: str) -> list[dict]:
        """Extract text from PDF and split into chunks."""
        import fitz  # PyMuPDF
        from langchain_text_splitters import RecursiveCharacterTextSplitter

        doc = fitz.open(path)
        full_text = ""
        for page in doc:
            full_text += page.get_text()
        doc.close()

        if not full_text.strip():
            return []

        splitter = RecursiveCharacterTextSplitter(
            chunk_size=400,
            chunk_overlap=50,
            separators=["\n\n", "\n", ". ", " ", ""],
        )
        chunks_text = splitter.split_text(full_text)

        return [
            {
                "text": chunk,
                "metadata": {
                    "doc_type": doc_type,
                    "source": os.path.basename(path),
                },
            }
            for chunk in chunks_text
            if chunk.strip()
        ]

    def _chunk_csv(self, path: str, doc_type: str) -> list[dict]:
        """Convert each CSV row into a text chunk."""
        import csv

        chunks = []
        with open(path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                text = " | ".join(f"{k}: {v}" for k, v in row.items() if v)
                if text.strip():
                    metadata: dict = {"doc_type": doc_type, "source": os.path.basename(path)}
                    # Promote district column to metadata for location filtering
                    if "district" in row:
                        metadata["district"] = row["district"]
                    chunks.append({"text": text, "metadata": metadata})

        return chunks

    def _chunk_json(self, path: str, doc_type: str) -> list[dict]:
        """Load a JSON file and convert each item to a searchable text chunk.

        Supports a JSON array of objects. Each object is serialised as
        ``key: value`` lines and stored as one chunk. Non-array JSON is
        treated as a single chunk.

        Args:
            path: Path to the .json file.
            doc_type: Document type tag stored in ChromaDB metadata.

        Returns:
            List of chunk dicts with ``text`` and ``metadata`` keys.
        """
        import json

        with open(path, encoding="utf-8") as f:
            data = json.load(f)

        items = data if isinstance(data, list) else [data]
        chunks = []
        for item in items:
            if isinstance(item, dict):
                lines = []
                for k, v in item.items():
                    if isinstance(v, list):
                        lines.append(f"{k}: {', '.join(str(x) for x in v)}")
                    elif v is not None:
                        lines.append(f"{k}: {v}")
                text = "\n".join(lines)
            else:
                text = str(item)

            if text.strip():
                chunks.append({
                    "text": text,
                    "metadata": {
                        "doc_type": doc_type,
                        "source": os.path.basename(path),
                    },
                })
        return chunks

    def _chunk_text_file(self, path: str, doc_type: str) -> list[dict]:
        """Split a plain text or markdown file into chunks."""
        from langchain_text_splitters import RecursiveCharacterTextSplitter

        with open(path, "r", encoding="utf-8") as f:
            content = f.read()

        if not content.strip():
            return []

        splitter = RecursiveCharacterTextSplitter(
            chunk_size=400,
            chunk_overlap=50,
            separators=["\n\n", "\n", ". ", " ", ""],
        )
        chunks_text = splitter.split_text(content)

        return [
            {
                "text": chunk,
                "metadata": {
                    "doc_type": doc_type,
                    "source": os.path.basename(path),
                },
            }
            for chunk in chunks_text
            if chunk.strip()
        ]

    def _add_chunks_to_collection(
        self,
        collection,
        chunks: list[dict],
        doc_type: str,
    ) -> None:
        """Add a list of chunk dicts to the ChromaDB collection."""
        import hashlib

        documents = []
        metadatas = []
        ids = []

        for i, chunk in enumerate(chunks):
            text = chunk.get("text", "")
            if not text:
                continue
            # Deterministic ID based on content hash
            chunk_id = hashlib.md5(f"{doc_type}_{i}_{text[:50]}".encode()).hexdigest()
            documents.append(text)
            metadatas.append(chunk.get("metadata", {"doc_type": doc_type}))
            ids.append(chunk_id)

        if documents:
            collection.add(documents=documents, metadatas=metadatas, ids=ids)
