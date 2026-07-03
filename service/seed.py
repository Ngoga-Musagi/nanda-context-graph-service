"""Seed corpus: ~30 realistic prior decisions, loaded idempotently at startup.

A precedent service with an empty graph reads as useless in a cold demo, so the
store is seeded with believable decisions across several domains (discount
approvals, refunds, access grants, vendor selection, content moderation, fraud
holds), including multi-hop causal chains. IDs and timestamps are fixed, so
seeding is deterministic and idempotent (MERGE by ``trace_id``).
"""

from __future__ import annotations

from typing import Any

from schema.models import DecisionTrace, ReasoningStep

from service.precedent import build_precedent_text


def trace_to_record(trace: DecisionTrace) -> dict[str, Any]:
    """Flatten a :class:`DecisionTrace` into the store's record dict (+ precedent_text)."""
    record: dict[str, Any] = {
        "trace_id": trace.trace_id,
        "agent_id": trace.agent_id,
        "agent_handle": trace.agent_handle,
        "parent_trace_id": trace.parent_trace_id,
        "a2a_msg_id": trace.a2a_msg_id,
        "outcome": trace.outcome,
        "timestamp_ms": trace.timestamp_ms,
        "duration_ms": trace.duration_ms,
        "inputs": trace.inputs,
        "output": trace.output,
        "steps": [s.model_dump() for s in trace.steps],
        "embedding": None,
    }
    record["precedent_text"] = build_precedent_text(record)
    return record


def _t(
    tid: str,
    agent: str,
    handle: str,
    inputs: dict[str, Any],
    steps: list[dict[str, Any]],
    output: dict[str, Any],
    outcome: str,
    ts: int,
    parent: str | None = None,
) -> DecisionTrace:
    return DecisionTrace(
        trace_id=tid,
        agent_id=agent,
        agent_handle=handle,
        parent_trace_id=parent,
        inputs=inputs,
        steps=[
            ReasoningStep(
                step_id=f"{tid}-s{i}",
                step_type=s["type"],
                thought=s["thought"],
                tool_name=s.get("tool"),
                confidence=s.get("conf", 1.0),
            )
            for i, s in enumerate(steps)
        ],
        output=output,
        outcome=outcome,
        timestamp_ms=ts,
    )


_BASE = 1_775_000_000_000  # fixed epoch-ms base so seeding is deterministic


# --- Discount approvals (the demo theme — richest set) ---------------------
_DISCOUNT = [
    _t(
        "seed-discount-001", "discount-approval", "@billing:discount-approval",
        {"request": "Gold-tier member requests 15% discount on a 5-day SUV ski-trip rental"},
        [
            {"type": "retrieve", "thought": "Policy: discounts up to 10% auto-approve; above 10% need manager sign-off", "tool": "policy-db"},
            {"type": "evaluate", "thought": "Requested 15% exceeds the 10% auto-approval ceiling for Gold tier", "conf": 0.97},
            {"type": "decide", "thought": "Rejected: 15% exceeds the 10% auto-approval ceiling; routed to manager queue"},
        ],
        {"approved": False, "reason": "exceeds_auto_approval_ceiling"}, "failure", _BASE + 1000,
    ),
    _t(
        "seed-discount-002", "discount-approval", "@billing:discount-approval",
        {"request": "Silver-tier customer asks for 8% loyalty discount on annual subscription"},
        [
            {"type": "retrieve", "thought": "Policy: up to 10% auto-approve", "tool": "policy-db"},
            {"type": "evaluate", "thought": "8% is within the 10% ceiling and customer is in good standing", "conf": 0.95},
            {"type": "decide", "thought": "Approved: 8% is within the auto-approval ceiling"},
        ],
        {"approved": True, "discount_pct": 8}, "success", _BASE + 2000,
    ),
    _t(
        "seed-discount-003", "discount-approval", "@billing:discount-approval",
        {"request": "Platinum member requests 12% discount; cites a competitor quote"},
        [
            {"type": "retrieve", "thought": "Policy: above 10% needs manager sign-off; Platinum has a retention exception up to 15%", "tool": "policy-db"},
            {"type": "evaluate", "thought": "12% is above 10% but within the Platinum retention exception", "conf": 0.9},
            {"type": "decide", "thought": "Approved under the Platinum retention exception to match competitor pricing"},
        ],
        {"approved": True, "discount_pct": 12, "basis": "platinum_retention_exception"}, "success", _BASE + 3000,
    ),
    _t(
        "seed-discount-004", "discount-approval", "@billing:discount-approval",
        {"request": "New (no-tier) customer requests 20% first-time discount"},
        [
            {"type": "retrieve", "thought": "Policy: first-time promo capped at 10%", "tool": "policy-db"},
            {"type": "evaluate", "thought": "20% is double the first-time promo cap", "conf": 0.96},
            {"type": "decide", "thought": "Rejected: 20% exceeds the 10% first-time promo cap; offered 10% instead"},
        ],
        {"approved": False, "counter_offer_pct": 10}, "failure", _BASE + 4000,
    ),
    _t(
        "seed-discount-005", "discount-approval", "@billing:discount-approval",
        {"request": "Gold-tier member requests 10% discount on a fleet booking of 6 cars"},
        [
            {"type": "retrieve", "thought": "Policy: up to 10% auto-approve; fleet bookings get +5% volume allowance", "tool": "policy-db"},
            {"type": "evaluate", "thought": "10% is within ceiling; volume allowance gives headroom", "conf": 0.93},
            {"type": "decide", "thought": "Approved: 10% within ceiling and supported by fleet volume allowance"},
        ],
        {"approved": True, "discount_pct": 10}, "success", _BASE + 5000,
    ),
    _t(
        "seed-discount-006", "discount-approval", "@billing:discount-approval",
        {"request": "Silver-tier member requests 18% discount after a service complaint"},
        [
            {"type": "retrieve", "thought": "Policy: goodwill credits up to 15% allowed for verified service failures", "tool": "policy-db"},
            {"type": "retrieve", "thought": "CRM shows a verified late-delivery incident on the prior booking", "tool": "crm"},
            {"type": "evaluate", "thought": "18% exceeds even the 15% goodwill ceiling", "conf": 0.88},
            {"type": "decide", "thought": "Partially approved: 15% goodwill credit applied (capped), not the full 18%"},
        ],
        {"approved": True, "discount_pct": 15, "note": "capped_at_goodwill_ceiling"}, "success", _BASE + 6000,
    ),
    _t(
        "seed-discount-007", "discount-approval", "@billing:discount-approval",
        {"request": "Gold-tier member requests 9% discount on a weekend city rental"},
        [
            {"type": "retrieve", "thought": "Policy: up to 10% auto-approve", "tool": "policy-db"},
            {"type": "decide", "thought": "Approved: 9% within the auto-approval ceiling"},
        ],
        {"approved": True, "discount_pct": 9}, "success", _BASE + 7000,
    ),
    _t(
        "seed-discount-008", "discount-approval", "@billing:discount-approval",
        {"request": "Corporate account requests 25% bulk discount on a 50-seat license renewal"},
        [
            {"type": "retrieve", "thought": "Policy: enterprise bulk discounts above 20% require finance approval", "tool": "policy-db"},
            {"type": "delegate", "thought": "Delegating to finance for sign-off on the 25% bulk request", "tool": "a2a-delegate"},
        ],
        {"approved": None, "delegated_to": "finance-approval"}, "delegated", _BASE + 8000,
    ),
]

# --- Refund decisions ------------------------------------------------------
_REFUND = [
    _t(
        "seed-refund-001", "refund-agent", "@billing:refund",
        {"request": "Customer requests full refund 40 days after purchase, item unused"},
        [
            {"type": "retrieve", "thought": "Policy: full refunds within 30 days; 30-60 days store credit only", "tool": "policy-db"},
            {"type": "decide", "thought": "Store credit issued: past the 30-day full-refund window but within 60 days"},
        ],
        {"refund_type": "store_credit"}, "success", _BASE + 11000,
    ),
    _t(
        "seed-refund-002", "refund-agent", "@billing:refund",
        {"request": "Customer requests refund for a defective product within 10 days"},
        [
            {"type": "retrieve", "thought": "Policy: defective items get full refund regardless of window", "tool": "policy-db"},
            {"type": "retrieve", "thought": "Photos confirm the defect", "tool": "evidence-store"},
            {"type": "decide", "thought": "Full refund approved: verified defect within policy"},
        ],
        {"refund_type": "full"}, "success", _BASE + 12000,
    ),
    _t(
        "seed-refund-003", "refund-agent", "@billing:refund",
        {"request": "Customer requests refund 6 months after purchase, no defect"},
        [
            {"type": "retrieve", "thought": "Policy: no refunds past 60 days without a defect", "tool": "policy-db"},
            {"type": "decide", "thought": "Rejected: 6 months is well past the 60-day window and no defect reported"},
        ],
        {"refund_type": "none"}, "failure", _BASE + 13000,
    ),
    _t(
        "seed-refund-004", "refund-agent", "@billing:refund",
        {"request": "Customer disputes a duplicate charge on the same order"},
        [
            {"type": "retrieve", "thought": "Ledger shows two identical charges seconds apart", "tool": "ledger"},
            {"type": "decide", "thought": "Full refund of the duplicate charge: confirmed billing error"},
        ],
        {"refund_type": "full", "reason": "duplicate_charge"}, "success", _BASE + 14000,
    ),
    _t(
        "seed-refund-005", "refund-agent", "@billing:refund",
        {"request": "Customer requests refund after using a service for 25 days of a 30-day trial"},
        [
            {"type": "retrieve", "thought": "Policy: pro-rated refund only if usage under 50% of trial", "tool": "policy-db"},
            {"type": "evaluate", "thought": "25 of 30 days is 83% usage, far above the 50% threshold", "conf": 0.92},
            {"type": "decide", "thought": "Rejected: usage exceeds the 50% pro-rated-refund threshold"},
        ],
        {"refund_type": "none"}, "failure", _BASE + 15000,
    ),
]

# --- Access / permission grants -------------------------------------------
_ACCESS = [
    _t(
        "seed-access-001", "access-control", "@iam:access",
        {"request": "Contractor requests read access to the production analytics dataset"},
        [
            {"type": "retrieve", "thought": "Policy: contractors get read-only to non-PII datasets after NDA", "tool": "iam-policy"},
            {"type": "retrieve", "thought": "NDA on file; dataset flagged as containing PII", "tool": "data-catalog"},
            {"type": "decide", "thought": "Rejected: dataset contains PII, which contractors may not access even read-only"},
        ],
        {"granted": False, "reason": "pii_dataset"}, "failure", _BASE + 21000,
    ),
    _t(
        "seed-access-002", "access-control", "@iam:access",
        {"request": "Engineer requests temporary write access to the staging database"},
        [
            {"type": "retrieve", "thought": "Policy: time-boxed staging write grants allowed for engineers", "tool": "iam-policy"},
            {"type": "decide", "thought": "Granted: 24-hour time-boxed write access to staging"},
        ],
        {"granted": True, "ttl_hours": 24, "scope": "staging:write"}, "success", _BASE + 22000,
    ),
    _t(
        "seed-access-003", "access-control", "@iam:access",
        {"request": "Analyst requests admin role on the billing system"},
        [
            {"type": "retrieve", "thought": "Policy: admin on billing restricted to the finance-ops group", "tool": "iam-policy"},
            {"type": "evaluate", "thought": "Requester is in analytics, not finance-ops", "conf": 0.99},
            {"type": "decide", "thought": "Rejected: admin on billing is limited to finance-ops members"},
        ],
        {"granted": False, "reason": "role_scope_violation"}, "failure", _BASE + 23000,
    ),
    _t(
        "seed-access-004", "access-control", "@iam:access",
        {"request": "On-call engineer requests emergency prod access during an incident"},
        [
            {"type": "retrieve", "thought": "Policy: break-glass prod access allowed during declared incidents with audit logging", "tool": "iam-policy"},
            {"type": "retrieve", "thought": "Incident INC-4821 is active and the requester is on-call", "tool": "incident-db"},
            {"type": "decide", "thought": "Granted: break-glass prod access for the active incident, fully audit-logged"},
        ],
        {"granted": True, "scope": "prod:break-glass", "audit": True}, "success", _BASE + 24000,
    ),
    _t(
        "seed-access-005", "access-control", "@iam:access",
        {"request": "Intern requests access to the source code repository"},
        [
            {"type": "retrieve", "thought": "Policy: interns get repo read access after onboarding completion", "tool": "iam-policy"},
            {"type": "retrieve", "thought": "Onboarding checklist complete", "tool": "hr-system"},
            {"type": "decide", "thought": "Granted: read access to the repository post-onboarding"},
        ],
        {"granted": True, "scope": "repo:read"}, "success", _BASE + 25000,
    ),
]

# --- Vendor / supplier selection ------------------------------------------
_VENDOR = [
    _t(
        "seed-vendor-001", "procurement-agent", "@ops:procurement",
        {"request": "Select a cloud GPU vendor for a 3-month training run, budget $40k"},
        [
            {"type": "retrieve", "thought": "Three quotes: A $38k/99.9% SLA, B $31k/99.0% SLA, C $45k/99.95% SLA", "tool": "rfq-system"},
            {"type": "evaluate", "thought": "B is cheapest but its SLA risks training interruptions; A fits budget with a strong SLA", "conf": 0.85},
            {"type": "decide", "thought": "Selected vendor A: best SLA-within-budget tradeoff for a long training run"},
        ],
        {"vendor": "A", "cost": 38000}, "success", _BASE + 31000,
    ),
    _t(
        "seed-vendor-002", "procurement-agent", "@ops:procurement",
        {"request": "Choose a payments processor; priority is lowest per-transaction fee"},
        [
            {"type": "retrieve", "thought": "Processor X 2.9%+30c, Y 2.5%+25c, Z 3.1% flat", "tool": "rfq-system"},
            {"type": "decide", "thought": "Selected Y: lowest effective per-transaction fee for our average ticket size"},
        ],
        {"vendor": "Y"}, "success", _BASE + 32000,
    ),
    _t(
        "seed-vendor-003", "procurement-agent", "@ops:procurement",
        {"request": "Pick a logistics partner; the incumbent had two late shipments"},
        [
            {"type": "retrieve", "thought": "Incumbent on-time 88%; challenger on-time 96% at 6% higher cost", "tool": "vendor-scorecard"},
            {"type": "evaluate", "thought": "Reliability gap outweighs the 6% cost delta for time-sensitive freight", "conf": 0.8},
            {"type": "decide", "thought": "Switched to the challenger: higher on-time reliability justifies the modest cost increase"},
        ],
        {"vendor": "challenger"}, "success", _BASE + 33000,
    ),
    _t(
        "seed-vendor-004", "procurement-agent", "@ops:procurement",
        {"request": "Approve a sole-source purchase from an unvetted vendor"},
        [
            {"type": "retrieve", "thought": "Policy: sole-source above $10k requires a vetting exception", "tool": "policy-db"},
            {"type": "decide", "thought": "Rejected: unvetted sole-source above threshold without an approved exception"},
        ],
        {"vendor": None, "reason": "vetting_required"}, "failure", _BASE + 34000,
    ),
]

# --- Content moderation ----------------------------------------------------
_MOD = [
    _t(
        "seed-mod-001", "moderation-agent", "@trust:moderation",
        {"request": "Review a flagged post reported for hate speech"},
        [
            {"type": "retrieve", "thought": "Policy: slurs targeting protected groups are removable", "tool": "policy-db"},
            {"type": "evaluate", "thought": "Post contains a targeted slur, not a quoted counter-speech context", "conf": 0.9},
            {"type": "decide", "thought": "Removed: targeted slur violates the hate-speech policy"},
        ],
        {"action": "remove"}, "success", _BASE + 41000,
    ),
    _t(
        "seed-mod-002", "moderation-agent", "@trust:moderation",
        {"request": "Review a post flagged for spam with 5 outbound links"},
        [
            {"type": "retrieve", "thought": "Policy: link count alone is not spam; intent and repetition matter", "tool": "policy-db"},
            {"type": "evaluate", "thought": "Links are to cited sources in a genuine discussion, not promotional", "conf": 0.82},
            {"type": "decide", "thought": "Kept: links are citations in good-faith discussion, not spam"},
        ],
        {"action": "keep"}, "success", _BASE + 42000,
    ),
    _t(
        "seed-mod-003", "moderation-agent", "@trust:moderation",
        {"request": "Review an account flagged for coordinated inauthentic behavior"},
        [
            {"type": "retrieve", "thought": "Signals: 40 accounts posting identical text within 2 minutes", "tool": "signal-db"},
            {"type": "decide", "thought": "Suspended: identical synchronized posting indicates coordinated inauthentic behavior"},
        ],
        {"action": "suspend"}, "success", _BASE + 43000,
    ),
]

# --- Fraud / risk holds ----------------------------------------------------
_FRAUD = [
    _t(
        "seed-fraud-001", "risk-agent", "@trust:risk",
        {"request": "Score a $4,200 order shipping to a freight forwarder, new account"},
        [
            {"type": "retrieve", "thought": "Signals: new account, high value, reshipper address, mismatched billing geo", "tool": "risk-engine"},
            {"type": "evaluate", "thought": "Multiple high-risk signals stack above the manual-review threshold", "conf": 0.87},
            {"type": "decide", "thought": "Held for manual review: stacked high-risk signals on a high-value new-account order"},
        ],
        {"action": "hold_for_review"}, "success", _BASE + 51000,
    ),
    _t(
        "seed-fraud-002", "risk-agent", "@trust:risk",
        {"request": "Score a $60 reorder from a 3-year customer with consistent history"},
        [
            {"type": "retrieve", "thought": "Signals: established account, low value, address matches prior orders", "tool": "risk-engine"},
            {"type": "decide", "thought": "Auto-approved: low value and consistent history place this well below the risk threshold"},
        ],
        {"action": "approve"}, "success", _BASE + 52000,
    ),
    _t(
        "seed-fraud-003", "risk-agent", "@trust:risk",
        {"request": "Score a login from a new country immediately after a password reset"},
        [
            {"type": "retrieve", "thought": "Signals: impossible-travel velocity, password reset 4 minutes prior", "tool": "risk-engine"},
            {"type": "decide", "thought": "Step-up auth required: impossible-travel plus recent reset is a likely account-takeover pattern"},
        ],
        {"action": "require_mfa"}, "success", _BASE + 53000,
    ),
]

# --- A multi-hop causal chain (broker -> pricing -> approval) ---------------
_CHAIN = [
    _t(
        "seed-chain-broker", "rental-broker", "@rental:broker",
        {"user_request": "Need a car in Boston for 5 days, 4 people, ski trip, Gold member"},
        [
            {"type": "evaluate", "thought": "4 passengers + ski gear implies an SUV with roof capacity", "conf": 0.92},
            {"type": "retrieve", "thought": "Inventory: Toyota RAV4 SUV available at $85/day", "tool": "inventory-api"},
            {"type": "decide", "thought": "Recommend the RAV4: SUV capacity fits a 4-person ski trip"},
            {"type": "delegate", "thought": "Delegating to pricing for the Gold-tier rate", "tool": "a2a-delegate"},
        ],
        {"recommendation": "Toyota RAV4", "delegated_to": "rental-pricing"}, "delegated", _BASE + 61000,
    ),
    _t(
        "seed-chain-pricing", "rental-pricing", "@rental:pricing",
        {"car": "Toyota RAV4", "days": 5, "tier": "Gold"},
        [
            {"type": "retrieve", "thought": "Base rate $85/day x 5 = $425", "tool": "rate-engine"},
            {"type": "retrieve", "thought": "Gold tier carries a 15% loyalty discount", "tool": "crm"},
            {"type": "evaluate", "thought": "15% off $425 = $361.25", "conf": 0.99},
            {"type": "delegate", "thought": "Delegating to approval: 15% exceeds the 10% auto-approval ceiling", "tool": "a2a-delegate"},
        ],
        {"quoted_total": 361.25, "discount_pct": 15, "delegated_to": "rental-approval"},
        "delegated", _BASE + 62000, parent="seed-chain-broker",
    ),
    _t(
        "seed-chain-approval", "rental-approval", "@rental:approval",
        {"discount_pct": 15, "tier": "Gold", "total": 361.25},
        [
            {"type": "retrieve", "thought": "Policy: discounts above 10% require approval", "tool": "policy-db"},
            {"type": "evaluate", "thought": "15% exceeds the 10% auto-approval ceiling for Gold tier", "conf": 0.95},
            {"type": "decide", "thought": "Rejected: 15% exceeds the 10% ceiling; offer a compliant 10% instead"},
        ],
        {"approved": False, "counter_offer_pct": 10}, "failure", _BASE + 63000, parent="seed-chain-pricing",
    ),
]


SEED_TRACES: list[DecisionTrace] = [
    *_DISCOUNT,
    *_REFUND,
    *_ACCESS,
    *_VENDOR,
    *_MOD,
    *_FRAUD,
    *_CHAIN,
]


def seed_records() -> list[dict[str, Any]]:
    """Return all seed decisions as store records (with precedent_text built)."""
    return [trace_to_record(t) for t in SEED_TRACES]
