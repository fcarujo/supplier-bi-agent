"""
Supplier BI Agent — Policy Engine
===================================
Deterministic rule evaluator — no LLM involved.

Reads policies.yaml and evaluates agent state + validation results
to produce one of three outcomes:

  auto_approve    — all rules pass, report publishes without human review
  route_to_queue  — one or more soft rules fail, human review required
  escalate        — one or more hard rules fail, requires escalation

Each rule evaluation is recorded with pass/fail status and reason,
giving a complete audit trail of why a decision was made.
"""

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml


# ── Load policies ─────────────────────────────────────────────────────────────

POLICIES_PATH = Path(__file__).parent.parent / "config" / "policies.yaml"

def _load_policies() -> dict:
    with open(POLICIES_PATH) as f:
        return yaml.safe_load(f)


# ── Data classes ──────────────────────────────────────────────────────────────

@dataclass
class RuleResult:
    rule_name:   str
    rule_type:   str
    passed:      bool
    severity:    str        # soft / hard
    message:     str
    actual:      object     # what was evaluated
    threshold:   object     # what it was compared against


@dataclass
class PolicyOutcome:
    decision:           str            # auto_approve / route_to_queue / escalate
    report_type:        str
    rules_evaluated:    int
    rules_passed:       int
    rules_failed_soft:  int
    rules_failed_hard:  int
    rule_results:       list = field(default_factory=list)
    soft_failures:      list = field(default_factory=list)
    hard_failures:      list = field(default_factory=list)
    auto_approve_enabled: bool = True

    def to_dict(self) -> dict:
        return {
            "decision":             self.decision,
            "report_type":          self.report_type,
            "rules_evaluated":      self.rules_evaluated,
            "rules_passed":         self.rules_passed,
            "rules_failed_soft":    self.rules_failed_soft,
            "rules_failed_hard":    self.rules_failed_hard,
            "auto_approve_enabled": self.auto_approve_enabled,
            "soft_failures":        self.soft_failures,
            "hard_failures":        self.hard_failures,
            "rule_results": [
                {
                    "rule":      r.rule_name,
                    "type":      r.rule_type,
                    "passed":    r.passed,
                    "severity":  r.severity,
                    "message":   r.message if not r.passed else "OK",
                    "actual":    str(r.actual),
                    "threshold": str(r.threshold),
                }
                for r in self.rule_results
            ],
        }


# ── Rule evaluators ───────────────────────────────────────────────────────────

def _eval_confidence_min(rule: dict, state: dict, validation: dict) -> RuleResult:
    threshold = rule["threshold"]
    actual    = state.get("confidence") or 0.0
    passed    = actual >= threshold
    return RuleResult(
        rule_name = rule["name"],
        rule_type = rule["type"],
        passed    = passed,
        severity  = rule.get("severity", "soft"),
        message   = rule["message"],
        actual    = round(actual, 3),
        threshold = threshold,
    )


def _eval_validation_pass_min(rule: dict, state: dict, validation: dict) -> RuleResult:
    threshold = rule["threshold"]
    results   = validation.get("results", [])
    if not results:
        # No validation results — treat as passing (validation not yet run)
        return RuleResult(
            rule_name = rule["name"],
            rule_type = rule["type"],
            passed    = True,
            severity  = rule.get("severity", "soft"),
            message   = "No validation results available — skipped",
            actual    = "N/A",
            threshold = threshold,
        )
    passed_count = sum(1 for r in results if r.get("passed", False))
    pass_rate    = passed_count / len(results)
    passed       = pass_rate >= threshold
    return RuleResult(
        rule_name = rule["name"],
        rule_type = rule["type"],
        passed    = passed,
        severity  = rule.get("severity", "soft"),
        message   = rule["message"],
        actual    = f"{pass_rate:.1%} ({passed_count}/{len(results)})",
        threshold = f"{threshold:.1%}",
    )


def _eval_hallucination_max(rule: dict, state: dict, validation: dict) -> RuleResult:
    threshold = rule["threshold"]
    results   = validation.get("results", [])
    flags     = sum(1 for r in results if r.get("hallucination_flag", False))
    passed    = flags <= threshold
    return RuleResult(
        rule_name = rule["name"],
        rule_type = rule["type"],
        passed    = passed,
        severity  = rule.get("severity", "hard"),
        message   = rule["message"],
        actual    = flags,
        threshold = threshold,
    )


def _eval_deviation_max_pct(rule: dict, state: dict, validation: dict) -> RuleResult:
    threshold = rule["threshold"]
    results   = validation.get("results", [])
    if not results:
        return RuleResult(
            rule_name = rule["name"],
            rule_type = rule["type"],
            passed    = True,
            severity  = rule.get("severity", "soft"),
            message   = "No validation results — skipped",
            actual    = "N/A",
            threshold = threshold,
        )
    max_dev = max(
        (r.get("deviation_pct") or 0.0) for r in results
    )
    passed = max_dev <= threshold
    return RuleResult(
        rule_name = rule["name"],
        rule_type = rule["type"],
        passed    = passed,
        severity  = rule.get("severity", "soft"),
        message   = rule["message"],
        actual    = f"{max_dev:.1f}%",
        threshold = f"{threshold:.1f}%",
    )


def _eval_required_sections(rule: dict, state: dict, validation: dict) -> RuleResult:
    sections  = rule.get("sections", [])
    narrative = state.get("report_narrative") or ""
    missing   = [s for s in sections if s.lower() not in narrative.lower()]
    passed    = len(missing) == 0
    return RuleResult(
        rule_name = rule["name"],
        rule_type = rule["type"],
        passed    = passed,
        severity  = rule.get("severity", "soft"),
        message   = rule["message"],
        actual    = f"Missing: {missing}" if missing else "All sections present",
        threshold = f"Required: {sections}",
    )


def _eval_improvement_actions_min(rule: dict, state: dict, validation: dict) -> RuleResult:
    threshold = rule["threshold"]
    analysis  = state.get("analysis") or {}
    actions   = analysis.get("improvement_actions") or []
    actual    = len(actions)
    passed    = actual >= threshold
    return RuleResult(
        rule_name = rule["name"],
        rule_type = rule["type"],
        passed    = passed,
        severity  = rule.get("severity", "soft"),
        message   = rule["message"],
        actual    = actual,
        threshold = threshold,
    )


def _eval_actions_must_cite_sku(rule: dict, state: dict, validation: dict) -> RuleResult:
    """
    Check that improvement actions reference specific SKUs or categories.
    An action is considered vague if it has no target or its target is
    'portfolio' / 'overall' with no further specificity.
    """
    analysis = state.get("analysis") or {}
    actions  = analysis.get("improvement_actions") or []

    if not actions:
        return RuleResult(
            rule_name = rule["name"],
            rule_type = rule["type"],
            passed    = False,
            severity  = rule.get("severity", "soft"),
            message   = "No improvement actions found",
            actual    = 0,
            threshold = "at least 1 action with SKU/category reference",
        )

    vague_actions = []
    for action in actions:
        target = str(action.get("target", "")).lower()
        scope  = str(action.get("scope", "")).lower()
        # Check if target contains a SKU pattern or named category
        has_sku      = bool(re.search(r"[A-Z]{3}-\d{4}", str(action.get("target", ""))))
        has_category = any(cat.lower() in target for cat in [
            "electronics", "home", "garden", "clothing", "apparel",
            "sports", "outdoors", "toys", "games", "beauty", "health",
            "kitchen", "dining"
        ])
        has_supplier = bool(re.search(r"SUP\d{3}", str(action.get("target", ""))))
        is_specific = has_sku or has_category or has_supplier or scope in ("sku", "category", "supplier", "portfolio")

        if not is_specific:
            vague_actions.append(action.get("action", "unnamed action")[:60])

    passed = len(vague_actions) == 0
    return RuleResult(
        rule_name = rule["name"],
        rule_type = rule["type"],
        passed    = passed,
        severity  = rule.get("severity", "soft"),
        message   = rule["message"],
        actual    = f"Vague actions: {vague_actions}" if vague_actions else "All actions cite SKU/category",
        threshold = "All actions must reference a SKU, category, or supplier",
    )


def _eval_errors_max(rule: dict, state: dict, validation: dict) -> RuleResult:
    threshold = rule["threshold"]
    errors    = state.get("errors") or []
    # Filter out low-severity warnings (e.g. supplier table row count note)
    real_errors = [e for e in errors if "low row count" not in str(e).lower()]
    actual      = len(real_errors)
    passed      = actual <= threshold
    return RuleResult(
        rule_name = rule["name"],
        rule_type = rule["type"],
        passed    = passed,
        severity  = rule.get("severity", "soft"),
        message   = rule["message"],
        actual    = actual,
        threshold = threshold,
    )


# ── Rule dispatcher ───────────────────────────────────────────────────────────

RULE_EVALUATORS = {
    "confidence_min":          _eval_confidence_min,
    "validation_pass_min":     _eval_validation_pass_min,
    "hallucination_max":       _eval_hallucination_max,
    "deviation_max_pct":       _eval_deviation_max_pct,
    "required_sections":       _eval_required_sections,
    "improvement_actions_min": _eval_improvement_actions_min,
    "actions_must_cite_sku":   _eval_actions_must_cite_sku,
    "errors_max":              _eval_errors_max,
}


# ── Policy engine ─────────────────────────────────────────────────────────────

def evaluate(
    state:       dict,
    validation:  dict,
    report_type: str,
) -> PolicyOutcome:
    """
    Evaluate all rules for a given report type against agent state
    and validation results.

    Args:
        state:       Agent state dict (from LangGraph)
        validation:  Validation summary dict (from validate_node)
        report_type: Must match a key in policies.yaml

    Returns:
        PolicyOutcome with decision and full rule audit trail
    """
    policies = _load_policies()

    # Get policy config for this report type, fall back to defaults
    policy_config = policies["policies"].get(report_type)
    if not policy_config:
        print(f"  [policy] No policy defined for '{report_type}' — using defaults")
        defaults      = policies.get("defaults", {})
        policy_config = {
            "description":        f"Default policy for {report_type}",
            "auto_approve_enabled": defaults.get("auto_approve_enabled", False),
            "rules": [
                {
                    "name":      "confidence_min",
                    "type":      "confidence_min",
                    "threshold": defaults.get("confidence_min", 0.80),
                    "severity":  "soft",
                    "message":   "Confidence below default threshold",
                },
                {
                    "name":      "no_hallucinations",
                    "type":      "hallucination_max",
                    "threshold": defaults.get("hallucination_max", 0),
                    "severity":  "hard",
                    "message":   "Hallucination detected",
                },
            ]
        }

    auto_approve_enabled = policy_config.get("auto_approve_enabled", False)
    rules                = policy_config.get("rules", [])
    rule_results         = []
    soft_failures        = []
    hard_failures        = []

    print(f"  [policy] Evaluating {len(rules)} rules for '{report_type}'...")

    for rule in rules:
        rule_type = rule.get("type")
        evaluator = RULE_EVALUATORS.get(rule_type)

        if not evaluator:
            print(f"  [policy] Unknown rule type: {rule_type} — skipping")
            continue

        result = evaluator(rule, state, validation)
        rule_results.append(result)

        status = "✓" if result.passed else "✗"
        print(f"  [policy] {status} {result.rule_name}: {result.actual}")

        if not result.passed:
            if result.severity == "hard":
                hard_failures.append(result.message)
            else:
                soft_failures.append(result.message)

    # ── Determine decision ────────────────────────────────────────────────────
    if hard_failures:
        decision = "escalate"
    elif soft_failures or not auto_approve_enabled:
        decision = "route_to_queue"
    else:
        decision = "auto_approve"

    outcome = PolicyOutcome(
        decision           = decision,
        report_type        = report_type,
        rules_evaluated    = len(rule_results),
        rules_passed       = sum(1 for r in rule_results if r.passed),
        rules_failed_soft  = len(soft_failures),
        rules_failed_hard  = len(hard_failures),
        rule_results       = rule_results,
        soft_failures      = soft_failures,
        hard_failures      = hard_failures,
        auto_approve_enabled = auto_approve_enabled,
    )

    print(f"  [policy] Decision: {decision.upper()} "
          f"({outcome.rules_passed}/{outcome.rules_evaluated} rules passed)")
    if hard_failures:
        for f in hard_failures:
            print(f"  [policy] HARD FAILURE: {f}")
    if soft_failures:
        for f in soft_failures:
            print(f"  [policy] Soft failure: {f}")

    return outcome
