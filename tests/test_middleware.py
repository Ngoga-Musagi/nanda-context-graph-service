"""Tests for middleware.mcp_shim — TracedMCP wrapper."""

import asyncio
import time
from unittest.mock import AsyncMock, patch

import pytest

from middleware.mcp_shim import TracedMCP


# ── Helpers ──────────────────────────────────────────────────────


class FakeMCPClient:
    """Minimal async MCP client stub."""

    async def call_tool(self, tool_name: str, **kwargs):
        return {"result": f"{tool_name} ok", **kwargs}


class FailingMCPClient:
    async def call_tool(self, tool_name: str, **kwargs):
        raise RuntimeError("tool exploded")


# ── Tests ────────────────────────────────────────────────────────


class TestStepPayloadShape:
    @pytest.mark.asyncio
    async def test_success_step_payload(self, monkeypatch):
        monkeypatch.setenv("NCG_INGEST_URL", "http://fake:7200")

        captured = {}

        def fake_post(url, json=None, timeout=None):
            captured["url"] = url
            captured["payload"] = json

        shim = TracedMCP(mcp_client=FakeMCPClient())
        shim._ingest_url = "http://fake:7200"

        with patch("middleware.mcp_shim.threading") as mock_threading:
            # Capture the thread target so we can call it synchronously
            def run_target(target, daemon=True):
                target()

            mock_threading.Thread.side_effect = (
                lambda target, daemon=True: type("T", (), {"start": lambda self: target()})()
            )

            with patch.dict("sys.modules", {}):
                import requests as real_requests

                with patch("requests.post", side_effect=fake_post):
                    # Re-import inside the thread target scope
                    with patch("builtins.__import__", wraps=__builtins__.__import__ if hasattr(__builtins__, '__import__') else __import__):
                        result = await shim.call_tool(
                            "search", parent_trace_id="t-abc", query="NANDA"
                        )

        assert result == {"result": "search ok", "query": "NANDA"}

    @pytest.mark.asyncio
    async def test_success_emits_correct_fields(self, monkeypatch):
        """Verify the step dict has all required ReasoningStep fields."""
        monkeypatch.setenv("NCG_INGEST_URL", "http://fake:7200")

        emitted = {}

        def capture_emit(self, step, parent_trace_id=None):
            emitted["step"] = step
            emitted["parent_trace_id"] = parent_trace_id

        shim = TracedMCP(mcp_client=FakeMCPClient())
        shim._ingest_url = "http://fake:7200"

        with patch.object(TracedMCP, "_emit_step", capture_emit):
            await shim.call_tool("search", parent_trace_id="t-123", query="test")

        step = emitted["step"]
        assert step["step_type"] == "execute"
        assert step["tool_name"] == "search"
        assert step["tool_input"] == {"query": "test"}
        assert step["tool_output"] == {"result": "search ok", "query": "test"}
        assert step["confidence"] == 1.0
        assert "step_id" in step
        assert "duration_ms" in step
        assert "thought" in step
        assert emitted["parent_trace_id"] == "t-123"

    @pytest.mark.asyncio
    async def test_error_step_payload(self, monkeypatch):
        monkeypatch.setenv("NCG_INGEST_URL", "http://fake:7200")

        emitted = {}

        def capture_emit(self, step, parent_trace_id=None):
            emitted["step"] = step

        shim = TracedMCP(mcp_client=FailingMCPClient())
        shim._ingest_url = "http://fake:7200"

        with patch.object(TracedMCP, "_emit_step", capture_emit):
            with pytest.raises(RuntimeError, match="tool exploded"):
                await shim.call_tool("bad_tool", parent_trace_id="t-err")

        step = emitted["step"]
        assert step["step_type"] == "error"
        assert step["confidence"] == 0.0
        assert "tool exploded" in step["thought"]


class TestSilentWhenUnset:
    @pytest.mark.asyncio
    async def test_no_exception_when_url_unset(self, monkeypatch):
        monkeypatch.delenv("NCG_INGEST_URL", raising=False)

        shim = TracedMCP(mcp_client=FakeMCPClient())
        shim._ingest_url = None

        # Must not raise, must not attempt any HTTP call
        result = await shim.call_tool("search", query="test")
        assert result == {"result": "search ok", "query": "test"}

    @pytest.mark.asyncio
    async def test_emit_step_noop_when_url_unset(self):
        shim = TracedMCP(mcp_client=FakeMCPClient())
        shim._ingest_url = None

        with patch("middleware.mcp_shim.threading") as mock_threading:
            shim._emit_step({"step_id": "s1"}, parent_trace_id="t-1")
            # Thread should never be created
            mock_threading.Thread.assert_not_called()


class TestFireAndForget:
    @pytest.mark.asyncio
    async def test_call_tool_does_not_block(self, monkeypatch):
        """call_tool must return promptly even if emission would be slow."""
        monkeypatch.setenv("NCG_INGEST_URL", "http://fake:7200")

        shim = TracedMCP(mcp_client=FakeMCPClient())
        shim._ingest_url = "http://fake:7200"

        start = time.perf_counter_ns()
        result = await shim.call_tool("fast_tool", parent_trace_id="t-1")
        elapsed_ms = (time.perf_counter_ns() - start) / 1_000_000

        assert result == {"result": "fast_tool ok"}
        # The call_tool itself (excluding network) must complete very fast.
        # The daemon thread handles emission asynchronously.
        assert elapsed_ms < 50, f"call_tool blocked for {elapsed_ms:.1f}ms"
