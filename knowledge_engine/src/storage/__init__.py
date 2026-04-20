"""
knowledge_engine/src/storage/__init__.py

Factory for storage backends.

Returns the Azure Blob backend when AZURE_STORAGE_ACCOUNT, AZURE_STORAGE_KEY,
and AZURE_CONTAINER_NAME are all set in the environment; otherwise falls back
to LocalPVCStorageBackend.

AzureBlobStorageBackend is imported lazily so this module loads cleanly before
Task 4 creates azure_blob.py. Once azure_blob.py exists the try/except at
module level exposes the class for patch() in tests.

Belongs to the Knowledge Engine block of the DPG framework.
"""
from __future__ import annotations

import os

from src.storage.base import StorageBackend, StorageError
from src.storage.local_pvc import LocalPVCStorageBackend

# Attempt a module-level import so the name is patchable via
# patch("src.storage.AzureBlobStorageBackend") in tests.
# This will be None until Task 4 creates azure_blob.py.
try:
    from src.storage.azure_blob import AzureBlobStorageBackend  # noqa: F401
except ImportError:
    AzureBlobStorageBackend = None  # type: ignore[assignment,misc]

__all__ = [
    "get_storage_backend",
    "StorageBackend",
    "StorageError",
    "LocalPVCStorageBackend",
    "AzureBlobStorageBackend",
]


def get_storage_backend() -> StorageBackend:
    """Return the appropriate storage backend based on environment variables.

    Selects the Azure Blob backend when AZURE_STORAGE_ACCOUNT, AZURE_STORAGE_KEY,
    and AZURE_CONTAINER_NAME are all present in the environment; otherwise returns
    a LocalPVCStorageBackend.

    Returns:
        A concrete StorageBackend instance ready for use.
    """
    acct = os.environ.get("AZURE_STORAGE_ACCOUNT")
    key = os.environ.get("AZURE_STORAGE_KEY")
    cont = os.environ.get("AZURE_CONTAINER_NAME")

    if acct and key and cont:
        # Resolve the class from the module namespace so tests can patch it via
        # patch("src.storage.AzureBlobStorageBackend").
        import src.storage as _mod
        azure_cls = getattr(_mod, "AzureBlobStorageBackend", None)
        if azure_cls is None:
            # azure_blob.py now exists (post-Task-4) but was not yet imported.
            from src.storage.azure_blob import AzureBlobStorageBackend as azure_cls  # type: ignore[no-redef]
        return azure_cls(acct, key, cont)

    return LocalPVCStorageBackend()
