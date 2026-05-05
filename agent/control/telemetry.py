"""
Supplier BI Agent — Telemetry Module
======================================
Shared module for collecting node-level performance telemetry.

Used by all pipeline nodes. Three functions, three lines per node:

    state = node_start(state, "discover")          # at top of node function
    state = record_tokens(state, "discover", resp) # after each LLM response
    return node_end(state, "discover", {...})       # wraps the return dict

Design decisions:
  - Functions take state in and return state out — no side effects, no globals.
    This is safe for LangGraph's state passing model.
  - record_tokens accumulates — calling it multiple times (e.g. pull with
    retries) adds to existing counts rather than overwriting.
  - node_end merges timing into the return dict automatically so nodes do
    not need to write {**state.get("node_timings", {}), "node": time} manually.
  - All functions are safe to call even if state is missing keys — they
    default gracefully so a node that forgets to call node_start will not crash.
  - sql_retries and validation_queries are NOT handled here — they are
    node-specific counters set directly in pull.py and validate.py.
"""

import time
from typing import Optional


# ── Internal state key names ──────────────────────────────────────────────────
# Centralised here so a rename only needs to happen in one place.
_TIMINGS_KEY  = "node_timings"
_TOKENS_KEY   = "token_counts"
_STARTS_KEY   = "_node_starts"   # internal — not written to BigQuery


def node_start(state: dict, node_name: str) -> dict:
    """
    Record the wall-clock start time for a node.

    Call this as the first line of a node function:
        state = node_start(state, "discover")

    Stores start times in a private _node_starts key that is not
    written to BigQuery — it is only used by node_end() to compute elapsed time.
    """
    starts = dict(state.get(_STARTS_KEY) or {})
    starts[node_name] = time.time()
    return {**state, _STARTS_KEY: starts}


def record_tokens(state: dict, node_name: str, response) -> dict:
    """
    Extract token usage from an Anthropic response object and accumulate
    into state["token_counts"][node_name].

    Call this immediately after each client.messages.create() call:
        state = record_tokens(state, "discover", response)

    Accumulates — safe to call multiple times for the same node (e.g. pull
    with SQL retries). Each call adds to the existing count.

    Silently no-ops if response has no usage attribute — safe to call
    without checking whether the LLM call succeeded.
    """
    try:
        input_tokens  = response.usage.input_tokens
        output_tokens = response.usage.output_tokens
    except Exception:
        return state  # no usage data — do nothing

    counts = dict(state.get(_TOKENS_KEY) or {})
    existing = counts.get(node_name, {"input": 0, "output": 0})
    counts[node_name] = {
        "input":  existing["input"]  + input_tokens,
        "output": existing["output"] + output_tokens,
    }
    return {**state, _TOKENS_KEY: counts}


def node_end(state: dict, node_name: str, return_dict: dict) -> dict:
    """
    Compute elapsed time for a node and merge telemetry into the return dict.

    Call this as the return value of a node function:
        return node_end(state, "discover", {
            "selected_tables": ...,
            "errors": ...,
        })

    Reads the start time set by node_start(), computes elapsed seconds,
    and adds both node_timings and token_counts to the return dict so
    LangGraph carries them forward to the next node.

    Safe to call even if node_start() was never called — defaults to 0.0s.
    """
    starts    = state.get(_STARTS_KEY) or {}
    start_at  = starts.get(node_name, time.time())
    elapsed   = round(time.time() - start_at, 2)

    print(f"  [{node_name}] Complete in {elapsed}s")

    # Merge timing into existing timings dict
    timings = dict(state.get(_TIMINGS_KEY) or {})
    timings[node_name] = elapsed

    # Carry forward current token counts (may have been updated by record_tokens)
    token_counts = state.get(_TOKENS_KEY) or {}

    return {
        **return_dict,
        _TIMINGS_KEY: timings,
        _TOKENS_KEY:  token_counts,
    }
