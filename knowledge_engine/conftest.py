"""
knowledge_engine/conftest.py

Ensures that knowledge_engine/src is resolved before any other installed
package named 'src' (e.g. agent_core's editable install) when running
pytest from the knowledge_engine directory.
"""

import sys
import os

# Prepend the knowledge_engine root so that `from src.X import Y`
# resolves to knowledge_engine/src/X, not agent_core/src/X.
_ke_root = os.path.dirname(os.path.abspath(__file__))
if _ke_root not in sys.path:
    sys.path.insert(0, _ke_root)
