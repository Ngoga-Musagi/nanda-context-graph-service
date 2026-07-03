"""MCP middleware shim — wraps any MCP client to auto-emit ReasoningStep traces.

Drop-in replacement for mcp.call_tool(). Fire-and-forget emission to
NCG_INGEST_URL/ingest/step. Completely silent when NCG_INGEST_URL is unset.

Usage:
    from middleware import traced_mcp
    result = await traced_mcp.call_tool("search", parent_trace_id="t-1", query="NANDA")
"""

import os
import threading
import time
import uuid
from typing import Any


class TracedMCP:
    """Drop-in wrapper for an MCP client that emits step traces."""

    def __init__(self, mcp_client=None):
        self._client = mcp_client
        self._ingest_url = os.getenv("NCG_INGEST_URL")

    async def call_tool(
        self, tool_name: str, parent_trace_id: str | None = None, **kwargs
    ) -> Any:
        """Call an MCP tool and emit a ReasoningStep trace."""
        step_id = str(uuid.uuid4())
        start_ms = int(time.time() * 1000)
        step: dict = {}
        try:
            result = await self._client.call_tool(tool_name, **kwargs)
            step = {
                "step_id": step_id,
                "step_type": "execute",
                "thought": f"Calling MCP tool: {tool_name}",
                "tool_name": tool_name,
                "tool_input": kwargs,
                "tool_output": result,
                "confidence": 1.0,
                "duration_ms": int(time.time() * 1000) - start_ms,
            }
            return result
        except Exception as e:
            step = {
                "step_id": step_id,
                "step_type": "error",
                "thought": f"MCP tool {tool_name} failed: {e}",
                "tool_name": tool_name,
                "tool_input": kwargs,
                "confidence": 0.0,
            }
            raise
        finally:
            self._emit_step(step, parent_trace_id)

    def _emit_step(self, step: dict, parent_trace_id: str | None = None) -> None:
        if not self._ingest_url:
            return
        payload = {"step": step, "parent_trace_id": parent_trace_id}
        url = f"{self._ingest_url}/ingest/step"

        def _post():
            try:
                import requests as req

                req.post(url, json=payload, timeout=2)
            except Exception:
                pass  # fire-and-forget — never crash the caller

        threading.Thread(target=_post, daemon=True).start()


traced_mcp = TracedMCP()
