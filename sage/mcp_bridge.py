"""
sage.mcp_bridge - Standard I/O MCP Server providing verification tools and ACK steering bridge.
"""
import json
import sys

from sage.mcp_bridge_helpers import (
    git_read, grep_search, run_command, sage_send, view_file,
)
from sage.mcp_bridge_wait import sage_wait

TOOLS = [
    {
        "name": "view_file",
        "description": "Read file contents with optional line range slice.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path to file"},
                "start": {"type": "integer", "description": "Start line (1-indexed)"},
                "end": {"type": "integer", "description": "End line (1-indexed)"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "grep_search",
        "description": "Search regular expression pattern in files.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Regex pattern"},
                "path": {"type": "string", "description": "Target path or directory"},
            },
            "required": ["pattern"],
        },
    },
    {
        "name": "git_read",
        "description": "Read-only git command (status, diff, log, show).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "args": {
                    "description": "Git subcommand and arguments",
                },
            },
            "required": ["args"],
        },
    },
    {
        "name": "sage_send",
        "description": "Queue steering or directive message to agent inbox.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "conv_id": {"type": "string", "description": "Conversation ID"},
                "message": {"type": "string", "description": "Steering message"},
            },
            "required": ["conv_id", "message"],
        },
    },
    {
        "name": "sage_wait",
        "description": "Wait for drain receipt and agent reaction in transcript.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "conv_id": {"type": "string", "description": "Conversation ID"},
                "seq": {"type": "integer", "description": "Sequence number"},
                "timeout_s": {"type": "number", "description": "Timeout in seconds"},
            },
            "required": ["conv_id", "seq"],
        },
    },
    {
        "name": "run_command",
        "description": "Execute shell command (active only when SAGE_MCP_EXEC=1).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "cmd": {"type": "string", "description": "Shell command to run"},
            },
            "required": ["cmd"],
        },
    },
]


def dispatch_tool_call(name, args):
    args = args or {}
    if name == "view_file":
        return str(view_file(args.get("path", ""), args.get("start"), args.get("end")))
    if name == "grep_search":
        return str(grep_search(args.get("pattern", ""), args.get("path", ".")))
    if name == "git_read":
        return str(git_read(args.get("args", "")))
    if name == "sage_send":
        res = sage_send(args.get("conv_id", ""), args.get("message", ""))
        return json.dumps(res)
    if name == "sage_wait":
        timeout = float(args.get("timeout_s", 10.0))
        res = sage_wait(args.get("conv_id", ""), int(args.get("seq", 0)), timeout_s=timeout)
        return json.dumps(res)
    if name == "run_command":
        res = run_command(args.get("cmd", ""))
        return json.dumps(res)
    return json.dumps({"error": f"Unknown tool: {name}"})


def handle_rpc_request(req):
    if not isinstance(req, dict):
        return None
    method = req.get("method")
    req_id = req.get("id")
    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "sage-mcp-bridge", "version": "0.1.0"},
            },
        }
    if method in ("notifications/initialized", "initialized"):
        return None
    if method in ("tools/list", "tools/listTools"):
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {"tools": TOOLS},
        }
    if method in ("tools/call", "tools/callTool"):
        params = req.get("params") or {}
        tool_name = params.get("name") or ""
        tool_args = params.get("arguments") or {}
        output = dispatch_tool_call(tool_name, tool_args)
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "content": [{"type": "text", "text": output}],
                "isError": False,
            },
        }
    if method == "ping":
        return {"jsonrpc": "2.0", "id": req_id, "result": {}}
    if req_id is not None:
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {"code": -32601, "message": f"Method not found: {method}"},
        }
    return None


def run_stdio_server():
    while True:
        try:
            line = sys.stdin.readline()
            if not line:
                break
            if not line.strip():
                continue
            req = json.loads(line)
            resp = handle_rpc_request(req)
            if resp is not None:
                sys.stdout.write(json.dumps(resp) + "\n")
                sys.stdout.flush()
        except (EOFError, KeyboardInterrupt):
            break
        except Exception as exc:
            err_resp = {"jsonrpc": "2.0", "error": {"code": -32700, "message": str(exc)}}
            sys.stdout.write(json.dumps(err_resp) + "\n")
            sys.stdout.flush()


def main():
    if "--selfcheck" in sys.argv:
        names = [t["name"] for t in TOOLS]
        sys.stdout.write(f"Sage MCP Bridge tools: {', '.join(names)}\n")
        sys.exit(0)
    run_stdio_server()


if __name__ == "__main__":
    main()
