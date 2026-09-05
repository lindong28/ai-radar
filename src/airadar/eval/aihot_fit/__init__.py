"""aihot-fit: evaluate the content pipeline against AIHOT historical outputs.

Four slots (see ~/.claude/references/llm-eval-system.md): the production
prefilter / score / enrich-v2 stages are the object under evaluation, AIHOT
items matched to our ``items`` table are the questions, a DeepSeek judge scores
summary / reason closeness, and deterministic metrics with bootstrap CIs make
runs comparable. Nothing here writes ``item_evaluations`` or opens
``radar.db`` for writing.
"""
