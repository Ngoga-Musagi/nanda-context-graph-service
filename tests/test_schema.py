"""Tests for schema.models — DecisionTrace and ReasoningStep."""

import uuid

from schema.models import DecisionTrace, ReasoningStep


class TestReasoningStep:
    def test_minimal(self):
        step = ReasoningStep(
            step_id="s1",
            step_type="decide",
            thought="picked option A",
        )
        assert step.step_id == "s1"
        assert step.tool_name is None
        assert step.confidence == 1.0

    def test_extra_fields_ignored(self):
        step = ReasoningStep(
            step_id="s2",
            step_type="execute",
            thought="ran tool",
            unknown_field="should be dropped",
        )
        assert not hasattr(step, "unknown_field")


class TestDecisionTrace:
    def test_minimal_required_fields(self):
        trace = DecisionTrace(
            agent_id="agent-001",
            inputs={"message": "hello"},
            output={"response": "world"},
            outcome="success",
        )
        assert trace.agent_id == "agent-001"
        assert trace.steps == []
        assert trace.agent_handle is None
        assert trace.duration_ms is None

    def test_trace_id_auto_generated(self):
        trace = DecisionTrace(
            agent_id="agent-001",
            inputs={},
            output={},
            outcome="success",
        )
        # Should be a valid UUID4 string
        parsed = uuid.UUID(trace.trace_id)
        assert parsed.version == 4

    def test_timestamp_ms_auto_generated(self):
        trace = DecisionTrace(
            agent_id="agent-001",
            inputs={},
            output={},
            outcome="success",
        )
        assert isinstance(trace.timestamp_ms, int)
        assert trace.timestamp_ms > 0

    def test_extra_fields_ignored(self):
        trace = DecisionTrace(
            agent_id="agent-001",
            inputs={},
            output={},
            outcome="success",
            legacy_field="v0.0.1",
            some_new_thing=42,
        )
        assert not hasattr(trace, "legacy_field")
        assert not hasattr(trace, "some_new_thing")

    def test_from_a2a_factory(self):
        message = {"text": "what is the weather?", "type": "text"}
        response = {"text": "sunny", "type": "text"}
        trace = DecisionTrace.from_a2a(
            agent_id="weather-agent",
            conversation_id="conv-abc-123",
            message=message,
            response=response,
        )
        assert trace.agent_id == "weather-agent"
        assert trace.a2a_msg_id == "conv-abc-123"
        assert trace.inputs == {"message": message}
        assert trace.output == {"response": response}
        assert trace.outcome == "success"
        assert trace.steps == []
        # trace_id and timestamp_ms should be auto-filled
        uuid.UUID(trace.trace_id)
        assert trace.timestamp_ms > 0

    def test_explicit_trace_id_preserved(self):
        trace = DecisionTrace(
            trace_id="my-custom-id",
            agent_id="agent-001",
            inputs={},
            output={},
            outcome="failure",
        )
        assert trace.trace_id == "my-custom-id"
