"""Evaluate the user-visible content pipeline against AIHOT historical outputs.

The package implements the five L1 slots: production behavior, versioned AIHOT
questions, semantic title/summary/reason judgments, deterministic metrics, and
judge validation. L2 round assets and L3 attempt/identity governance are wired
through the append-only ledger. Evaluation never writes ``item_evaluations`` or
opens ``radar.db`` for writing.
"""
