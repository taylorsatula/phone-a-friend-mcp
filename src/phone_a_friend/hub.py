"""
Central hub server for phone-a-friend.

This runs as a standalone process that MCP instances connect to.
Start with: python -m phone_a_friend.hub
"""

import asyncio
import json
import time
from dataclasses import dataclass, field
from typing import Optional
import signal
import sys

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 7777
IDLE_TIMEOUT_SECONDS = 60 * 60  # 60 minutes


@dataclass
class Session:
    """An active listening session."""
    name: str
    description: str
    writer: asyncio.StreamWriter
    current_caller: Optional[str] = None
    current_intent: Optional[str] = None
    last_activity: float = field(default_factory=time.time)


@dataclass
class HubState:
    """Global state for the hub."""
    sessions: dict[str, Session] = field(default_factory=dict)
    # Maps caller_name -> their StreamWriter
    callers: dict[str, asyncio.StreamWriter] = field(default_factory=dict)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


state = HubState()


def format_intent_banner(intent: str, include_directive: bool = True) -> str:
    """Format the intent as a visible banner with conversation guidelines."""
    border = "=" * 60
    banner = f"\n{border}\nCONVERSATION FOCUS: {intent}\n{border}"

    if include_directive:
        banner += """

GUIDELINES:
- Stay focused on the topic above
- When the topic is fully understood and resolved, naturally conclude the conversation
- Either party can end by saying the discussion is complete
- Idle conversations will auto-disconnect after 60 minutes
"""

    return banner


async def send_response(writer: asyncio.StreamWriter, response: dict) -> None:
    """Send a JSON response to a client."""
    data = json.dumps(response) + "\n"
    writer.write(data.encode())
    await writer.drain()


async def handle_listen(params: dict, writer: asyncio.StreamWriter) -> dict:
    """Handle a listen request - register session and wait for messages."""
    session_name = params["session_name"]
    description = params["description"]

    async with state.lock:
        if session_name in state.sessions:
            return {"error": f"Session '{session_name}' already exists"}

        session = Session(
            name=session_name,
            description=description,
            writer=writer
        )
        state.sessions[session_name] = session

    print(f"[HUB] Session '{session_name}' registered and listening")
    return {"status": "listening", "session": session_name}


async def handle_list_sessions(params: dict, writer: asyncio.StreamWriter) -> dict:
    """List all active sessions."""
    async with state.lock:
        sessions = [
            {
                "name": s.name,
                "description": s.description,
                "busy": s.current_caller is not None
            }
            for s in state.sessions.values()
        ]
    return {"sessions": sessions}


async def handle_connect(params: dict, writer: asyncio.StreamWriter) -> dict:
    """Connect to a listening session."""
    target = params["target_session"]
    intent = params["intent"]
    my_name = params["my_name"]

    async with state.lock:
        session = state.sessions.get(target)
        if not session:
            return {"error": f"Session '{target}' not found"}

        if session.current_caller and session.current_caller != my_name:
            return {"error": f"Session '{target}' is busy"}

        session.current_caller = my_name
        session.current_intent = intent
        state.callers[my_name] = writer

    print(f"[HUB] '{my_name}' connected to '{target}' with intent: {intent[:50]}...")
    return {
        "connected": True,
        "target": target,
        "intent": intent,
        "intent_banner": format_intent_banner(intent)
    }


async def handle_send(params: dict, writer: asyncio.StreamWriter) -> dict:
    """Send a message to a listener."""
    target = params["target_session"]
    message = params["message"]
    my_name = params["my_name"]

    async with state.lock:
        session = state.sessions.get(target)
        if not session:
            return {"error": f"Session '{target}' not found"}

        # Update caller tracking and activity timestamp
        state.callers[my_name] = writer
        session.last_activity = time.time()

        # Forward message to listener (only include directive on first message)
        include_directive = session.current_caller != my_name  # First contact
        msg = {
            "type": "message",
            "from": my_name,
            "message": message,
            "intent": session.current_intent,
            "intent_banner": format_intent_banner(session.current_intent, include_directive) if session.current_intent else None
        }

        try:
            await send_response(session.writer, msg)
        except Exception as e:
            return {"error": f"Failed to send to listener: {e}"}

    return {"sent": True, "to": target}


async def handle_respond(params: dict, writer: asyncio.StreamWriter) -> dict:
    """Send a response back to a caller."""
    session_name = params["session_name"]
    message = params["message"]

    async with state.lock:
        session = state.sessions.get(session_name)
        if not session:
            return {"error": f"Session '{session_name}' not found"}

        if not session.current_caller:
            return {"error": "No caller connected"}

        caller_writer = state.callers.get(session.current_caller)
        if not caller_writer:
            return {"error": "Caller connection lost"}

        # Update activity timestamp
        session.last_activity = time.time()

        msg = {
            "type": "response",
            "from": session_name,
            "message": message
        }

        try:
            await send_response(caller_writer, msg)
        except Exception as e:
            return {"error": f"Failed to send response: {e}"}

    return {"sent": True, "to": session.current_caller}


async def handle_refocus(params: dict, writer: asyncio.StreamWriter) -> dict:
    """Update conversation intent."""
    session_name = params["session_name"]
    new_intent = params["new_intent"]

    async with state.lock:
        session = state.sessions.get(session_name)
        if not session:
            return {"error": f"Session '{session_name}' not found"}

        old_intent = session.current_intent
        session.current_intent = new_intent

        # Notify caller of refocus
        if session.current_caller:
            caller_writer = state.callers.get(session.current_caller)
            if caller_writer:
                msg = {
                    "type": "refocus",
                    "new_intent": new_intent,
                    "intent_banner": format_intent_banner(new_intent)
                }
                try:
                    await send_response(caller_writer, msg)
                except:
                    pass

    print(f"[HUB] Session '{session_name}' refocused")
    return {
        "updated": True,
        "old_intent": old_intent,
        "new_intent": new_intent,
        "intent_banner": format_intent_banner(new_intent)
    }


async def handle_end_session(params: dict, writer: asyncio.StreamWriter) -> dict:
    """End a listening session."""
    session_name = params["session_name"]

    async with state.lock:
        if session_name in state.sessions:
            del state.sessions[session_name]

    print(f"[HUB] Session '{session_name}' ended")
    return {"closed": True, "session": session_name}


HANDLERS = {
    "listen": handle_listen,
    "list_sessions": handle_list_sessions,
    "connect": handle_connect,
    "send": handle_send,
    "respond": handle_respond,
    "refocus": handle_refocus,
    "end_session": handle_end_session,
}


async def handle_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
    """Handle a connected MCP client."""
    addr = writer.get_extra_info('peername')
    print(f"[HUB] Client connected from {addr}")

    try:
        while True:
            data = await reader.readline()
            if not data:
                break

            try:
                request = json.loads(data.decode())
                action = request.get("action")
                params = request.get("params", {})

                handler = HANDLERS.get(action)
                if handler:
                    response = await handler(params, writer)
                else:
                    response = {"error": f"Unknown action: {action}"}

                # Only send response for non-listen actions or errors
                # (listen blocks and waits for messages)
                if action != "listen" or "error" in response:
                    await send_response(writer, response)

            except json.JSONDecodeError:
                await send_response(writer, {"error": "Invalid JSON"})
            except Exception as e:
                await send_response(writer, {"error": str(e)})

    except asyncio.CancelledError:
        pass
    except Exception as e:
        print(f"[HUB] Client error: {e}")
    finally:
        # Clean up any sessions owned by this connection
        async with state.lock:
            to_remove = [
                name for name, session in state.sessions.items()
                if session.writer == writer
            ]
            for name in to_remove:
                del state.sessions[name]
                print(f"[HUB] Session '{name}' cleaned up (client disconnected)")

        writer.close()
        await writer.wait_closed()
        print(f"[HUB] Client {addr} disconnected")


async def check_idle_sessions():
    """Background task to disconnect idle sessions."""
    while True:
        await asyncio.sleep(60)  # Check every minute
        now = time.time()

        async with state.lock:
            to_remove = []
            for name, session in state.sessions.items():
                idle_time = now - session.last_activity
                if idle_time > IDLE_TIMEOUT_SECONDS:
                    to_remove.append((name, session))

            for name, session in to_remove:
                print(f"[HUB] Session '{name}' timed out after 60 minutes of inactivity")
                # Notify the session it's being disconnected
                try:
                    await send_response(session.writer, {
                        "type": "timeout",
                        "message": "Session disconnected due to 60 minutes of inactivity"
                    })
                    session.writer.close()
                except:
                    pass
                del state.sessions[name]


async def run_server(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT):
    """Run the hub server."""
    server = await asyncio.start_server(handle_client, host, port)
    addr = server.sockets[0].getsockname()
    print(f"[HUB] Phone-a-Friend hub running on {addr[0]}:{addr[1]}")
    print(f"[HUB] Idle timeout: 60 minutes")
    print(f"[HUB] Waiting for connections...")

    # Start idle checker in background
    asyncio.create_task(check_idle_sessions())

    async with server:
        await server.serve_forever()


def main():
    """Entry point."""
    # Handle Ctrl+C gracefully
    def signal_handler(sig, frame):
        print("\n[HUB] Shutting down...")
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)

    print("=" * 50)
    print("  Phone-a-Friend Hub Server")
    print("=" * 50)

    try:
        asyncio.run(run_server())
    except KeyboardInterrupt:
        print("\n[HUB] Shutting down...")


if __name__ == "__main__":
    main()
