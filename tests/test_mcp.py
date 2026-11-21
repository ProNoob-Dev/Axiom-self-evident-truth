"""MCP server: stdio JSON-RPC — initialize, tools/list, tools/call."""

import json
import subprocess
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
SERVER = BASE_DIR / "app" / "mcp_server.py"


def _run_mcp(requests: list[dict]) -> list[dict]:
    proc = subprocess.run(
        [sys.executable, str(SERVER)], input="\n".join(json.dumps(r) for r in requests),
        capture_output=True, text=True, timeout=60, cwd=str(BASE_DIR))
    assert proc.returncode == 0, proc.stderr
    return [json.loads(line) for line in proc.stdout.splitlines() if line.strip()]


def test_initialize_and_tools_list():
    replies = _run_mcp([
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
    ])
    assert replies[0]["result"]["serverInfo"]["name"] == "axiom"
    names = [t["name"] for t in replies[1]["result"]["tools"]]
    assert names == ["search_codebase", "ask_analytics", "resolve_dispute", "work_ticket"]


def test_tools_call_search_codebase():
    replies = _run_mcp([
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
         "params": {"name": "search_codebase", "arguments": {"query": "refund paise rupees", "k": 2}}},
    ])
    assert "error" not in replies[0]
    hits = json.loads(replies[0]["result"]["content"][0]["text"])
    assert hits and hits[0]["file"] == "billing.py"


def test_tools_call_unknown_tool_is_error_not_crash():
    replies = _run_mcp([
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
         "params": {"name": "nope", "arguments": {}}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
    ])
    assert "error" in replies[0]
    assert replies[1]["result"]["tools"], "server keeps serving after a bad call"
