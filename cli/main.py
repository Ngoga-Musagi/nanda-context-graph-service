"""nanda-context-graph CLI — emit traces, query decisions, check health."""

import json
import os
import time
import uuid

import click
import requests

NCG_INGEST_URL = os.getenv("NCG_INGEST_URL", "http://localhost:7200")
NCG_GRAPH_URL = os.getenv("NCG_GRAPH_API_URL", "http://localhost:7201")


@click.group()
def cli():
    """nanda-context-graph CLI"""
    pass


@cli.command()
@click.option("--agent-id", required=True, help="Agent identifier")
@click.option("--message", required=True, help="Message text to trace")
@click.option("--ingest-url", default=None, help="Override NCG_INGEST_URL")
def emit(agent_id, message, ingest_url):
    """Manually emit a test DecisionTrace."""
    url = ingest_url or NCG_INGEST_URL
    trace = {
        "trace_id": str(uuid.uuid4()),
        "agent_id": agent_id,
        "inputs": {"message": message},
        "steps": [],
        "output": {"response": "test"},
        "outcome": "success",
        "timestamp_ms": int(time.time() * 1000),
    }
    try:
        resp = requests.post(f"{url}/ingest/trace", json=trace, timeout=5)
        click.echo(f"Status: {resp.status_code}  trace_id: {trace['trace_id']}")
    except requests.ConnectionError:
        click.echo(f"Connection error: could not reach {url}", err=True)
        click.echo(f"trace_id: {trace['trace_id']}")
        raise SystemExit(1)


@cli.command()
@click.argument("trace_id")
def trace(trace_id):
    """Fetch and display a full decision trace."""
    resp = requests.get(f"{NCG_GRAPH_URL}/api/v1/trace/{trace_id}", timeout=5)
    click.echo(json.dumps(resp.json(), indent=2))


@cli.command()
@click.option("--agent-id", required=True, help="Agent identifier")
def why(agent_id):
    """Ask why an agent made its last decision."""
    resp = requests.get(
        f"{NCG_GRAPH_URL}/api/v1/why", params={"agent_id": agent_id}, timeout=5
    )
    click.echo(json.dumps(resp.json(), indent=2))


@cli.command()
@click.option("--agent-id", required=True, help="Agent identifier")
@click.option("--limit", default=20, help="Max traces to return")
@click.option(
    "--outcome",
    type=click.Choice(["success", "failure", "error", "delegated"]),
    default=None,
    help="Filter by outcome",
)
def history(agent_id, limit, outcome):
    """Show decision history for an agent."""
    params = {"agent_id": agent_id, "limit": limit}
    if outcome:
        params["outcome"] = outcome
    resp = requests.get(
        f"{NCG_GRAPH_URL}/api/v1/agent/{agent_id}/history",
        params=params,
        timeout=5,
    )
    click.echo(json.dumps(resp.json(), indent=2))


@cli.command()
@click.option("--ingest-url", default=None, help="Override NCG_INGEST_URL")
@click.option("--graph-url", default=None, help="Override NCG_GRAPH_API_URL")
def health(ingest_url, graph_url):
    """Check health of ingest and graph API services."""
    ingest = ingest_url or NCG_INGEST_URL
    graph = graph_url or NCG_GRAPH_URL

    for name, url in [("ingest", ingest), ("graph", graph)]:
        try:
            resp = requests.get(f"{url}/health", timeout=3)
            data = resp.json()
            click.echo(f"{name:>6}: {url} — {data.get('status', 'unknown')}")
        except requests.ConnectionError:
            click.echo(f"{name:>6}: {url} — unreachable", err=True)
        except Exception as e:
            click.echo(f"{name:>6}: {url} — error: {e}", err=True)


if __name__ == "__main__":
    cli()
