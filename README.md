# Phone-a-Friend MCP

An MCP server enabling Claude Code instances to communicate in real-time. One instance enters "listening mode" while another initiates contact with a specific intent.

## Architecture

```
┌─────────────────────────────────────────┐
│      paf-hub (TCP server)               │
│      localhost:7777                     │
└─────────────────────────────────────────┘
         ▲                    ▲
         │ TCP                │ TCP
         │                    │
   Claude Tab 1          Claude Tab 2
   (Listener)            (Caller)
```

## Installation

```bash
cd ~/.claude/mcps/phone-a-friend
pip install -e .
```

## Usage

### Step 1: Start the Hub

```bash
paf-hub
```

### Step 2: Configure Claude Code

Add to `~/.claude/settings.json`:

```json
{
  "mcpServers": {
    "phone-a-friend": {
      "command": "python",
      "args": ["-m", "phone_a_friend.server"],
      "cwd": "/Users/YOUR_USERNAME/.claude/mcps/phone-a-friend/src"
    }
  }
}
```

### Step 3: Use It

**Tab 1 (Listener):**
```
"Listen for questions. You're the auth expert."
```
→ `paf(action="listen", session_name="auth-expert", description="Expert on auth")`
→ Blocks waiting...

**Tab 2 (Caller):**
```
"Ask the auth expert about token refresh."
```
→ `paf(action="list_sessions")` → sees "auth-expert"
→ `paf(action="connect", target_session="auth-expert", intent="Token refresh", my_name="feature-dev")`
→ `paf(action="send", target_session="auth-expert", message="How handle expired tokens?", my_name="feature-dev")`
→ `paf(action="wait_response", my_name="feature-dev")` → blocks for answer

**Tab 1 receives, responds:**
→ Listener unblocks with question
→ `paf(action="respond", session_name="auth-expert", message="Use sliding window...")`
→ `paf(action="listen", ...)` → back to waiting

## Tool Reference

Single tool `paf` with actions:

| Action | Parameters | Description |
|--------|------------|-------------|
| `listen` | session_name, description | Block waiting for messages |
| `list_sessions` | (none) | List active listeners |
| `connect` | target_session, intent, my_name | Connect with intent |
| `send` | target_session, message, my_name | Send message |
| `wait_response` | my_name | Block for reply |
| `respond` | session_name, message | Reply to caller |
| `end_session` | session_name | Close session |

## Conversation Flow

```
Listener                              Caller
────────                              ──────
listen() [blocks]
                                      connect(intent)
                                      send(question)
[receives question]
respond(answer)
                                      wait_response() → answer
listen() [blocks]                     send(follow_up)
[receives follow_up]
...
```
