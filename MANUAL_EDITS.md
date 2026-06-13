# Two manual edits (safer by hand than by anchor)

The `apply_fixes.py` script handles everything that can be matched to a single
unique anchor. Two edits touch a return-dict / initial-state literal whose exact
surrounding text varies, so apply these two by hand in VS Code. Both are tiny.

---

## Edit A — `agent/graph.py`: seed `analysis_month` into the initial state

In `run_agent(...)`, find where the initial state dict is built and passed to
the graph (the dict that contains `"report_type": report_type`, `"goal": goal`,
etc.). Add two keys to it:

```python
        "analysis_month":  analysis_month or current_analysis_month(),
        "grounding_skus":  [],
```

So a state literal that looked like:

```python
    initial_state = {
        "report_type": report_type,
        "audience":    audience,
        "supplier_id": supplier_id,
        "goal":        goal,
        # ...
    }
```

becomes:

```python
    initial_state = {
        "report_type":     report_type,
        "audience":        audience,
        "supplier_id":     supplier_id,
        "goal":            goal,
        "analysis_month":  analysis_month or current_analysis_month(),
        "grounding_skus":  [],
        # ...
    }
```

(`current_analysis_month` is already imported by the script.)

If your `run_agent` passes arguments straight into `graph.invoke({...})` rather
than a named `initial_state` variable, add the same two keys to that inline dict.

---

## Edit B — `agent/nodes/generate.py`: return the grounding manifest

The supplier-report builder already computes `comment_intelligence` (the loaded
CI rows). Where the supplier-report path returns its state dict (the `return {...}`
that includes `"report_narrative"` / `"report_json"`), add the grounding manifest
so `validate` can use it:

```python
    grounding_skus = [c.get("productSKU") for c in (comment_intelligence or []) if c.get("productSKU")]
    min_ci_conf = min(
        [c.get("confidence", 1.0) for c in (comment_intelligence or [])],
        default=None,
    )
```

then in the returned dict add:

```python
        "grounding_skus":    grounding_skus,
        "min_ci_confidence": min_ci_conf,
```

### Optional but recommended (bug 3 — confidence propagation)

If a supplier report's Customer Voice rests on low-confidence CI, the overall
confidence should not auto-publish. After `analyse` sets `confidence`, clamp it
by the CI confidence. The cleanest place is in `review`/policy, but a one-liner
in generate's returned state works too:

```python
        # pull the existing confidence down to the weakest CI input it relies on
        "confidence": (
            min(state.get("confidence", 1.0), min_ci_conf)
            if min_ci_conf is not None else state.get("confidence")
        ),
```

Only add this if `generate` already has access to `state["confidence"]`
(it receives `state`). If not, do the clamp in `review.py` instead, reading
`state.get("min_ci_confidence")`.

### Also recommended (bug 1 tail — empty CI should not silently auto-publish)

For `monthly_supplier_account`, an empty `comment_intelligence` means the report
is missing its core section. Add a flag so it routes to human review instead of
auto-publishing:

```python
    if report_type == "monthly_supplier_account" and not comment_intelligence:
        errors.append("No comment intelligence available for supplier report — review required")
```

(`errors` is already part of the returned state in this node.)
