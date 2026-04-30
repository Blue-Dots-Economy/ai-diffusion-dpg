"""Neutral Pydantic types for chat_provider.

Imported by ChatProviderBase and every concrete provider. Callers in
agent_core build these types directly; concrete providers translate
to/from their SDK shapes via _to_wire / _from_wire.
"""
