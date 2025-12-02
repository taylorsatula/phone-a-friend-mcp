"""MCP server that connects to the phone-a-friend hub."""

import asyncio
import json
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

HUB_HOST = "127.0.0.1"
HUB_PORT = 7777


def format_result(result: dict) -> str:
    """Format result for human-readable output."""
    if "error" in result:
        return f"ERROR: {result['error']}"

    msg_type = result.get("type")

    if msg_type == "message":
        parts = []
        if result.get("intent_banner"):
            parts.append(result["intent_banner"])
        parts.append(f"FROM: {result.get('from', 'unknown')}")
        parts.append("")
        parts.append(result.get("message", ""))
        return "\n".join(parts)

    if msg_type == "response":
        return f"FROM {result.get('from', 'unknown')}:\n\n{result.get('message', '')}"

    if result.get("connected"):
        parts = ["CONNECTED"]
        if result.get("intent_banner"):
            parts.append(result["intent_banner"])
        return "\n".join(parts)

    if "sessions" in result:
        sessions = result["sessions"]
        if not sessions:
            return "No active sessions"
        parts = ["ACTIVE SESSIONS:", ""]
        for s in sessions:
            status = "(busy)" if s.get("busy") else "(available)"
            parts.append(f"  • {s['name']} {status}")
            parts.append(f"    {s['description']}")
        return "\n".join(parts)

    if result.get("sent"):
        return f"✓ Sent to {result.get('to', 'recipient')}"
    if result.get("closed"):
        return f"✓ Session '{result.get('session')}' closed"

    return json.dumps(result, indent=2)


class HubClient:
    """Client for connecting to the phone-a-friend hub."""

    def __init__(self):
        self.reader: asyncio.StreamReader | None = None
        self.writer: asyncio.StreamWriter | None = None
        self._lock = asyncio.Lock()

    async def ensure_connected(self) -> bool:
        """Ensure we're connected to the hub."""
        if self.writer is None or self.writer.is_closing():
            self.reader = None
            self.writer = None
            try:
                self.reader, self.writer = await asyncio.open_connection(HUB_HOST, HUB_PORT)
                return True
            except Exception:
                return False
        return True

    async def send_request(self, action: str, params: dict) -> dict:
        """Send a request and get response."""
        async with self._lock:
            if not await self.ensure_connected():
                return {"error": "Cannot connect to hub. Start with: python -m phone_a_friend.hub"}

            request = {"action": action, "params": params}
            try:
                self.writer.write((json.dumps(request) + "\n").encode())
                await self.writer.drain()
                response = await self.reader.readline()
                if not response:
                    return {"error": "Hub connection closed"}
                return json.loads(response.decode())
            except Exception as e:
                self.writer = None
                self.reader = None
                return {"error": f"Hub error: {e}"}

    async def wait_for_message(self, action: str, params: dict) -> dict:
        """Send request and wait for async message (for listen)."""
        async with self._lock:
            if not await self.ensure_connected():
                return {"error": "Cannot connect to hub. Start with: python -m phone_a_friend.hub"}

            request = {"action": action, "params": params}
            try:
                self.writer.write((json.dumps(request) + "\n").encode())
                await self.writer.drain()

                # First response is "listening" confirmation
                response = await self.reader.readline()
                if not response:
                    return {"error": "Hub connection closed"}

                result = json.loads(response.decode())
                if result.get("status") == "listening":
                    # Wait for actual message
                    message = await self.reader.readline()
                    if not message:
                        return {"error": "Hub connection closed while waiting"}
                    return json.loads(message.decode())

                return result
            except Exception as e:
                self.writer = None
                self.reader = None
                return {"error": f"Hub error: {e}"}


hub = HubClient()
server = Server("phone-a-friend")


@server.list_tools()
async def list_tools() -> list[Tool]:
    """List available tools."""
    return [
        Tool(
            name="paf",
            description="Phone-a-Friend: Claude-to-Claude communication. Actions: listen (block waiting for messages), list_sessions, connect (initiate with intent), send, wait_response (block for reply), respond, end_session.",
            inputSchema={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["listen", "list_sessions", "connect", "send", "wait_response", "respond", "end_session"],
                        "description": "Operation: listen/list_sessions/connect/send/wait_response/respond/end_session"
                    },
                    "session_name": {"type": "string", "description": "Your session name (listen/respond/end_session)"},
                    "target_session": {"type": "string", "description": "Target session (connect/send)"},
                    "message": {"type": "string", "description": "Message content (send/respond)"},
                    "my_name": {"type": "string", "description": "Your identifier (connect/send/wait_response)"},
                    "intent": {"type": "string", "description": "Conversation focus (connect)"},
                    "description": {"type": "string", "description": "What you can help with (listen)"}
                },
                "required": ["action"]
            }
        )
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    """Handle tool calls."""
    if name != "paf":
        return [TextContent(type="text", text=f"ERROR: Unknown tool: {name}")]

    action = arguments.get("action")
    if not action:
        return [TextContent(type="text", text="ERROR: action parameter required")]

    try:
        if action == "listen":
            result = await hub.wait_for_message("listen", {
                "session_name": arguments["session_name"],
                "description": arguments["description"]
            })
        elif action == "list_sessions":
            result = await hub.send_request("list_sessions", {})
        elif action == "connect":
            result = await hub.send_request("connect", {
                "target_session": arguments["target_session"],
                "intent": arguments["intent"],
                "my_name": arguments["my_name"]
            })
        elif action == "send":
            result = await hub.send_request("send", {
                "target_session": arguments["target_session"],
                "message": arguments["message"],
                "my_name": arguments["my_name"]
            })
        elif action == "wait_response":
            if not await hub.ensure_connected():
                result = {"error": "Cannot connect to hub"}
            else:
                try:
                    response = await hub.reader.readline()
                    result = json.loads(response.decode()) if response else {"error": "Connection closed"}
                except Exception as e:
                    result = {"error": f"Error waiting: {e}"}
        elif action == "respond":
            result = await hub.send_request("respond", {
                "session_name": arguments["session_name"],
                "message": arguments["message"]
            })
        elif action == "end_session":
            result = await hub.send_request("end_session", {
                "session_name": arguments["session_name"]
            })
        else:
            result = {"error": f"Unknown action: {action}"}

        return [TextContent(type="text", text=format_result(result))]

    except KeyError as e:
        return [TextContent(type="text", text=f"ERROR: Missing parameter: {e}")]
    except Exception as e:
        return [TextContent(type="text", text=f"ERROR: {e}")]


def main():
    """Run the MCP server."""
    async def run():
        async with stdio_server() as (read_stream, write_stream):
            await server.run(read_stream, write_stream, server.create_initialization_options())

    asyncio.run(run())


if __name__ == "__main__":
    main()
