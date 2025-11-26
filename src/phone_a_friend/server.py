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

    # Format incoming messages nicely
    if msg_type == "message":
        parts = []
        if result.get("intent_banner"):
            parts.append(result["intent_banner"])
        parts.append(f"FROM: {result.get('from', 'unknown')}")
        parts.append("")
        parts.append(result.get("message", ""))
        return "\n".join(parts)

    # Format responses nicely
    if msg_type == "response":
        parts = [f"FROM: {result.get('from', 'unknown')}", ""]
        parts.append(result.get("message", ""))
        return "\n".join(parts)

    # Format refocus notifications
    if msg_type == "refocus":
        parts = ["CONVERSATION REFOCUSED"]
        if result.get("intent_banner"):
            parts.append(result["intent_banner"])
        return "\n".join(parts)

    # Format timeout notifications
    if msg_type == "timeout":
        return f"TIMEOUT: {result.get('message', 'Session timed out')}"

    # Format connection confirmations
    if result.get("connected"):
        parts = ["CONNECTED"]
        if result.get("intent_banner"):
            parts.append(result["intent_banner"])
        return "\n".join(parts)

    # Format session lists nicely
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

    # Default: simple confirmations
    if result.get("sent"):
        return f"✓ Sent to {result.get('to', 'recipient')}"
    if result.get("closed"):
        return f"✓ Session '{result.get('session')}' closed"
    if result.get("updated"):
        return f"✓ Focus updated"

    # Fallback to JSON
    return json.dumps(result, indent=2)


class HubClient:
    """Client for connecting to the phone-a-friend hub."""

    def __init__(self):
        self.reader: asyncio.StreamReader | None = None
        self.writer: asyncio.StreamWriter | None = None
        self._lock = asyncio.Lock()

    async def connect(self) -> bool:
        """Connect to the hub server."""
        if self.writer is not None:
            return True

        try:
            self.reader, self.writer = await asyncio.open_connection(HUB_HOST, HUB_PORT)
            return True
        except Exception as e:
            return False

    async def ensure_connected(self) -> bool:
        """Ensure we're connected, reconnecting if needed."""
        if self.writer is None or self.writer.is_closing():
            self.reader = None
            self.writer = None
            return await self.connect()
        return True

    async def send_request(self, action: str, params: dict) -> dict:
        """Send a request to the hub and wait for response."""
        async with self._lock:
            if not await self.ensure_connected():
                return {"error": "Cannot connect to hub. Is it running? Start with: python -m phone_a_friend.hub"}

            request = {"action": action, "params": params}
            data = json.dumps(request) + "\n"

            try:
                self.writer.write(data.encode())
                await self.writer.drain()

                response_data = await self.reader.readline()
                if not response_data:
                    return {"error": "Hub connection closed"}

                return json.loads(response_data.decode())
            except Exception as e:
                self.writer = None
                self.reader = None
                return {"error": f"Hub communication error: {e}"}

    async def wait_for_message(self, action: str, params: dict) -> dict:
        """Send request and wait for an async message (for listen/wait_response)."""
        async with self._lock:
            if not await self.ensure_connected():
                return {"error": "Cannot connect to hub. Is it running? Start with: python -m phone_a_friend.hub"}

            request = {"action": action, "params": params}
            data = json.dumps(request) + "\n"

            try:
                self.writer.write(data.encode())
                await self.writer.drain()

                # For listen, first we get the "listening" confirmation
                # Then we wait for the actual message
                response_data = await self.reader.readline()
                if not response_data:
                    return {"error": "Hub connection closed"}

                response = json.loads(response_data.decode())

                # If it's just a status (listening), wait for the actual message
                if response.get("status") == "listening":
                    message_data = await self.reader.readline()
                    if not message_data:
                        return {"error": "Hub connection closed while waiting"}
                    return json.loads(message_data.decode())

                return response
            except Exception as e:
                self.writer = None
                self.reader = None
                return {"error": f"Hub communication error: {e}"}


# Global hub client
hub = HubClient()

# Create the MCP server
server = Server("phone-a-friend")


@server.list_tools()
async def list_tools() -> list[Tool]:
    """List available tools."""
    return [
        Tool(
            name="paf_listen",
            description="Open a named listener and block until a message arrives from another Claude instance. Call this in a loop to keep listening. REQUIRES: Hub server running (python -m phone_a_friend.hub)",
            inputSchema={
                "type": "object",
                "properties": {
                    "session_name": {
                        "type": "string",
                        "description": "A friendly name for this session (e.g., 'auth-expert', 'debug-helper')"
                    },
                    "description": {
                        "type": "string",
                        "description": "What this session knows about or can help with"
                    }
                },
                "required": ["session_name", "description"]
            }
        ),
        Tool(
            name="paf_list_sessions",
            description="List all active listening sessions available to connect to.",
            inputSchema={
                "type": "object",
                "properties": {}
            }
        ),
        Tool(
            name="paf_connect",
            description="Initiate a conversation with a listening session. The intent will be shown to both parties to keep the conversation focused.",
            inputSchema={
                "type": "object",
                "properties": {
                    "target_session": {
                        "type": "string",
                        "description": "The name of the session to connect to"
                    },
                    "intent": {
                        "type": "string",
                        "description": "The focus/purpose of this conversation - what you want to discuss"
                    },
                    "my_name": {
                        "type": "string",
                        "description": "How you want to identify yourself to the other session"
                    }
                },
                "required": ["target_session", "intent", "my_name"]
            }
        ),
        Tool(
            name="paf_send",
            description="Send a message to a listening session.",
            inputSchema={
                "type": "object",
                "properties": {
                    "target_session": {
                        "type": "string",
                        "description": "The session to send the message to"
                    },
                    "message": {
                        "type": "string",
                        "description": "The message content"
                    },
                    "my_name": {
                        "type": "string",
                        "description": "Your session identifier"
                    }
                },
                "required": ["target_session", "message", "my_name"]
            }
        ),
        Tool(
            name="paf_wait_response",
            description="Wait for a response from the session you're talking to. This blocks until a response arrives.",
            inputSchema={
                "type": "object",
                "properties": {
                    "my_name": {
                        "type": "string",
                        "description": "Your session identifier (same as used in send)"
                    }
                },
                "required": ["my_name"]
            }
        ),
        Tool(
            name="paf_respond",
            description="Send a response back to whoever is connected to your listening session.",
            inputSchema={
                "type": "object",
                "properties": {
                    "session_name": {
                        "type": "string",
                        "description": "Your listener session name"
                    },
                    "message": {
                        "type": "string",
                        "description": "The response content"
                    }
                },
                "required": ["session_name", "message"]
            }
        ),
        Tool(
            name="paf_refocus",
            description="Update the conversation focus/intent. Both parties will see the new focus.",
            inputSchema={
                "type": "object",
                "properties": {
                    "session_name": {
                        "type": "string",
                        "description": "The session whose conversation to refocus"
                    },
                    "new_intent": {
                        "type": "string",
                        "description": "The new focus for the conversation"
                    }
                },
                "required": ["session_name", "new_intent"]
            }
        ),
        Tool(
            name="paf_end_session",
            description="Close a listening session and unregister from the network.",
            inputSchema={
                "type": "object",
                "properties": {
                    "session_name": {
                        "type": "string",
                        "description": "The session to close"
                    }
                },
                "required": ["session_name"]
            }
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    """Handle tool calls by forwarding to hub."""

    try:
        if name == "paf_listen":
            result = await hub.wait_for_message("listen", {
                "session_name": arguments["session_name"],
                "description": arguments["description"]
            })
        elif name == "paf_list_sessions":
            result = await hub.send_request("list_sessions", {})
        elif name == "paf_connect":
            result = await hub.send_request("connect", {
                "target_session": arguments["target_session"],
                "intent": arguments["intent"],
                "my_name": arguments["my_name"]
            })
        elif name == "paf_send":
            result = await hub.send_request("send", {
                "target_session": arguments["target_session"],
                "message": arguments["message"],
                "my_name": arguments["my_name"]
            })
        elif name == "paf_wait_response":
            # Wait for response is a blocking call - read from the connection
            if not await hub.ensure_connected():
                result = {"error": "Cannot connect to hub"}
            else:
                try:
                    response_data = await hub.reader.readline()
                    if response_data:
                        result = json.loads(response_data.decode())
                    else:
                        result = {"error": "Connection closed while waiting"}
                except Exception as e:
                    result = {"error": f"Error waiting for response: {e}"}
        elif name == "paf_respond":
            result = await hub.send_request("respond", {
                "session_name": arguments["session_name"],
                "message": arguments["message"]
            })
        elif name == "paf_refocus":
            result = await hub.send_request("refocus", {
                "session_name": arguments["session_name"],
                "new_intent": arguments["new_intent"]
            })
        elif name == "paf_end_session":
            result = await hub.send_request("end_session", {
                "session_name": arguments["session_name"]
            })
        else:
            result = {"error": f"Unknown tool: {name}"}

        return [TextContent(type="text", text=format_result(result))]

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
