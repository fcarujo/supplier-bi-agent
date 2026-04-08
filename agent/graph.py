"""
Supplier BI Agent — LangGraph Graph
=====================================
Full pipeline — all 7 nodes wired.

  discover → pull → analyse → generate → validate → review → publish

Phase 4 additions:
  - validate_node: semantic validation against BigQuery ground truth
  - review_node: policy engine → auto_approve / queue / escalate
  - publish_node: GCS + BigQuery approved_reports + agent_runs update

AgentState extended with:
  - validation: semantic validation summary
  - policy_outcome: policy engine decision and rule results
  - gcs_path: published report location
"""

import uuid
from typing import Optional
from typing_extensions import TypedDict

from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver

from agent.nodes.discover  import discover_node
from agent.nodes.pull      import pull_node
from agent.nodes.analyse   import analyse_node
from agent.nodes.generate  import generate_node
from agent.nodes.validate  import validate_node
from agent.nodes.review    import review_node
from agent.nodes.publish   import publish_node


# ── Agent state ───────────────────────────────────────────────────────────────

class AgentState(TypedDict):
    # Input
    report_type:    str
    audience:       str
    supplier_id:    Optional[str]
    date_from:      Optional[str]
    date_to:        Optional[str]
    goal:           str

    # Discover output
    selected_tables:      Optional[list]
    table_schemas:        Optional[dict]
    discover_reasoning:   Optional[str]

    # Pull output
    queries:          Optional[dict]
    query_results:    Optional[dict]
    row_counts:       Optional[dict]
    pull_validation:  Optional[dict]

    # Analyse output
    analysis:         Optional[dict]
    confidence:       Optional[float]
    flags:            Optional[list]

    # Generate output
    report_narrative: Optional[str]
    report_json:      Optional[dict]

    # Validate output (Phase 4)
    validation:       Optional[dict]

    # Review output (Phase 4)
    approved:         Optional[bool]
    reviewer:         Optional[str]
    review_notes:     Optional[str]
    approved_at:      Optional[str]
    policy_outcome:   Optional[dict]

    # Publish output (Phase 4)
    gcs_path:         Optional[str]

    # Pipeline metadata
    run_id:           Optional[str]
    errors:           Optional[list]
    current_node:     Optional[str]


# ── Conditional edge — skip publish if not approved ───────────────────────────

def should_publish(state: AgentState) -> str:
    """Only proceed to publish if the report was approved."""
    if state.get("approved"):
        return "publish"
    return END


# ── Graph builder ─────────────────────────────────────────────────────────────

def build_graph():
    graph = StateGraph(AgentState)

    graph.add_node("discover",  discover_node)
    graph.add_node("pull",      pull_node)
    graph.add_node("analyse",   analyse_node)
    graph.add_node("generate",  generate_node)
    graph.add_node("validate",  validate_node)
    graph.add_node("review",    review_node)
    graph.add_node("publish",   publish_node)

    graph.set_entry_point("discover")
    graph.add_edge("discover",  "pull")
    graph.add_edge("pull",      "analyse")
    graph.add_edge("analyse",   "generate")
    graph.add_edge("generate",  "validate")
    graph.add_edge("validate",  "review")

    # Conditional edge — only publish if approved
    graph.add_conditional_edges(
        "review",
        should_publish,
        {"publish": "publish", END: END}
    )
    graph.add_edge("publish", END)

    checkpointer = MemorySaver()
    return graph.compile(checkpointer=checkpointer)


# ── Convenience runner ────────────────────────────────────────────────────────

def run_agent(
    report_type: str,
    goal:        str,
    audience:    str  = "business",
    supplier_id: str  = None,
    date_from:   str  = None,
    date_to:     str  = None,
    thread_id:   str  = None,
) -> AgentState:
    graph     = build_graph()
    thread_id = thread_id or str(uuid.uuid4())
    run_id    = str(uuid.uuid4())

    initial_state: AgentState = {
        "report_type":        report_type,
        "audience":           audience,
        "supplier_id":        supplier_id,
        "date_from":          date_from,
        "date_to":            date_to,
        "goal":               goal,
        "selected_tables":    None,
        "table_schemas":      None,
        "discover_reasoning": None,
        "queries":            None,
        "query_results":      None,
        "row_counts":         None,
        "pull_validation":    None,
        "analysis":           None,
        "confidence":         None,
        "flags":              None,
        "report_narrative":   None,
        "report_json":        None,
        "validation":         None,
        "approved":           None,
        "reviewer":           None,
        "review_notes":       None,
        "approved_at":        None,
        "policy_outcome":     None,
        "gcs_path":           None,
        "run_id":             run_id,
        "errors":             [],
        "current_node":       None,
    }

    config = {"configurable": {"thread_id": thread_id}}

    print(f"\n{'='*60}")
    print(f"Agent run starting")
    print(f"  Report type: {report_type}")
    print(f"  Audience:    {audience}")
    print(f"  Goal:        {goal}")
    if supplier_id:
        print(f"  Supplier:    {supplier_id}")
    print(f"  Run ID:      {run_id}")
    print(f"{'='*60}\n")

    return graph.invoke(initial_state, config=config)
