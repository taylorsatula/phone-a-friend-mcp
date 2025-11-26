# Phone-a-Friend MCP

An MCP (Model Context Protocol) server that enables Claude Code instances to communicate with each other in real-time. One Claude instance enters "listening mode" while another initiates contact with a specific intent/focus.

## Why?

When working with multiple Claude Code sessions, you often discover insights in one conversation that would be valuable in another. Previously, transferring this knowledge required manually exporting to markdown and pasting—hoping the context transfers. Phone-a-Friend enables **iterative, real-time dialogue** between Claude instances.

## Architecture

```
┌─────────────────────────────────────────┐
│      paf-hub (TCP server)               │  ← Start once, runs in background
│      localhost:7777                     │
└─────────────────────────────────────────┘
         ▲                    ▲
         │ TCP                │ TCP
         │                    │
   Claude Tab 1          Claude Tab 2
   (MCP Instance)        (MCP Instance)
```

The hub server runs independently and handles real-time message routing between Claude instances.

## Installation

```bash
git clone https://github.com/YOUR_USERNAME/phone-a-friend-mcp.git
cd phone-a-friend-mcp
pip install -e .
```

## Configuration

Add to your Claude Code settings (`~/.claude/settings.json`):

```json
{
  "mcpServers": {
    "phone-a-friend": {
      "command": "python",
      "args": ["-m", "phone_a_friend.server"],
      "cwd": "/path/to/phone-a-friend-mcp/src"
    }
  }
}
```

Replace `/path/to/phone-a-friend-mcp` with the actual path where you cloned the repo.

## Usage

### Step 1: Start the Hub Server

In a dedicated terminal:

```bash
paf-hub
```

You'll see:
```
==================================================
  Phone-a-Friend Hub Server
==================================================
[HUB] Phone-a-Friend hub running on 127.0.0.1:7777
[HUB] Idle timeout: 60 minutes
[HUB] Waiting for connections...
```

Keep this running.

### Step 2: Set Up a Listener (Tab 1)

In one Claude Code session:

```
"Listen for questions from other Claude sessions. You're the expert on [topic]."
```

Claude will register as a listener and wait for incoming connections.

### Step 3: Connect from Another Session (Tab 2)

In another Claude Code session:

```
"Ask the [session-name] about [your question]."
```

Claude will:
1. List available sessions
2. Connect with a stated intent/focus
3. Send your question
4. Wait for and relay the response

### Example Conversation Flow

```
Tab 1 (Listener)                          Tab 2 (Caller)
─────────────────                         ────────────────
paf_listen() [blocks]
                                          paf_list_sessions()
                                          paf_connect("expert", intent, "caller")
                                          paf_send("expert", question, "caller")
[receives question]
paf_respond("expert", answer)
                                          paf_wait_response("caller") → answer
                                          paf_send("expert", follow_up, "caller")
paf_listen() [blocks]
[receives follow_up]
paf_respond("expert", answer2)
...continues until topic resolved...
```

## Features

- **Real-time messaging** via TCP sockets (no polling)
- **Intent-focused conversations** - Human provides a focus that both Claudes see
- **Natural conversation ending** - Guideline to conclude when topic is resolved
- **60-minute idle timeout** - Auto-disconnect for inactive sessions
- **Pretty-printed output** - Human-readable message formatting

## Tools Reference

| Tool | Description |
|------|-------------|
| `paf_listen` | Open a listener and block until a message arrives |
| `paf_list_sessions` | List all active listening sessions |
| `paf_connect` | Initiate a conversation with an intent/focus |
| `paf_send` | Send a message to a listener |
| `paf_wait_response` | Block until a response arrives |
| `paf_respond` | Send a response back to the caller |
| `paf_refocus` | Update the conversation focus mid-conversation |
| `paf_end_session` | Close a listening session |

## How It Works

1. **Hub server** runs on `localhost:7777` and handles message routing
2. **MCP instances** (one per Claude Code session) connect to the hub via TCP
3. **Messages are delivered instantly** - no file polling or delays
4. Each Claude maintains its own conversation context - the MCP just passes messages

## Requirements

- Python 3.10+
- `mcp` package (installed automatically)

## License

MIT
