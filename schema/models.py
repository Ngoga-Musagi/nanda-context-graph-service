"""Pydantic v2 models for nanda-context-graph decision traces."""

import time
import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ReasoningStep(BaseModel):
    """A single reasoning step within a decision trace."""

    model_config = ConfigDict(extra="ignore")

    step_id: str
    step_type: Literal["retrieve", "evaluate", "decide", "delegate", "execute", "error"]
    thought: str
    tool_name: str | None = None
    tool_input: dict | None = None
    tool_output: dict | None = None
    confidence: float = 1.0
    duration_ms: int | None = None


class DecisionTrace(BaseModel):
    """Core event: a complete decision trace emitted by an agent."""

    model_config = ConfigDict(extra="ignore")

    trace_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    agent_id: str
    agent_handle: str | None = None
    parent_trace_id: str | None = None
    a2a_msg_id: str | None = None
    inputs: dict
    steps: list[ReasoningStep] = []
    output: dict
    outcome: Literal["success", "failure", "delegated", "error"]
    timestamp_ms: int = Field(default_factory=lambda: int(time.time() * 1000))
    duration_ms: int | None = None

    @classmethod
    def from_a2a(
        cls,
        agent_id: str,
        conversation_id: str,
        message: dict,
        response: dict,
    ) -> "DecisionTrace":
        """Create a DecisionTrace from an A2A message exchange."""
        return cls(
            agent_id=agent_id,
            a2a_msg_id=conversation_id,
            inputs={"message": message},
            output={"response": response},
            outcome="success",
        )
