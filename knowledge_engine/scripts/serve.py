"""
knowledge_engine/scripts/serve.py

Container entrypoint for the Knowledge Engine: ingest-if-empty, then serve.

If the ChromaDB store has not been populated yet, runs the offline ingestion
pipeline (scripts/ingest.py) once, then replaces this process with the
Knowledge Engine server (main.py). A populated store skips straight to the
server.

This used to be a `sh -c` one-liner in docker-compose.yml. The runtime image
is a Docker Hardened Image with no shell, so the logic lives here instead:

    python -m scripts.serve --config config/knowledge_engine.yaml
"""

import argparse
import logging
import os
import sys
from pathlib import Path

from scripts.ingest import main as ingest_main

logger = logging.getLogger(__name__)

DEFAULT_CONFIG = "config/knowledge_engine.yaml"
DEFAULT_CHROMA_DIR = "/app/chroma_db"

# ChromaDB's persistent client creates this file on first write, so its
# presence is what marks the store as already ingested.
_CHROMA_MARKER = "chroma.sqlite3"


def main(argv: list[str] | None = None) -> None:
    """Ingest documents if the vector store is empty, then start the server.

    Args:
        argv: Command-line arguments (without the program name). ``None`` reads
            ``sys.argv``. Accepts ``--config`` (ingestion config YAML) and
            ``--chroma-dir`` (ChromaDB persist directory; defaults to
            ``$CHROMA_PERSIST_DIR``, then ``/app/chroma_db``).

    Raises:
        SystemExit: If ingestion fails. The server is not started, matching
            the previous ``ingest && python main.py`` shell chain.
    """
    parser = argparse.ArgumentParser(
        description="Ingest into ChromaDB if empty, then start the Knowledge Engine."
    )
    parser.add_argument("--config", default=DEFAULT_CONFIG)
    parser.add_argument(
        "--chroma-dir",
        default=os.getenv("CHROMA_PERSIST_DIR", DEFAULT_CHROMA_DIR),
    )
    args = parser.parse_args(argv)

    if (Path(args.chroma_dir) / _CHROMA_MARKER).exists():
        logger.info(
            "serve.ingest_skipped",
            extra={
                "operation": "serve.main",
                "status": "skipped",
                "reason": "chroma store already populated",
            },
        )
    else:
        logger.info(
            "serve.ingest_start",
            extra={"operation": "serve.main", "reason": "chroma store empty"},
        )
        ingest_main(config_path=args.config)

    # exec rather than import-and-run so the server is PID 1's direct
    # successor: signals reach uvicorn and the ingest pipeline's memory
    # (embedding model, loaded documents) is released.
    os.execv(sys.executable, [sys.executable, "main.py"])


if __name__ == "__main__":
    main()
