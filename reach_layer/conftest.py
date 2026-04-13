"""pytest bootstrap for reach_layer.

Ensures auth env vars are populated before server.py is imported, so the
module-level FastAPI app singleton can be created even when tests run
against a domain config that has auth.enabled=true (e.g. KKB).
"""

import os

os.environ.setdefault("REACH_SESSION_SECRET", "test-secret-" + "x" * 32)
os.environ.setdefault("GOOGLE_CLIENT_ID", "test-client.apps.googleusercontent.com")
