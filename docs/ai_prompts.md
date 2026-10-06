# Sprint 1: AI Prompting & Constraint Strategy

## 1. Purpose

This document records the constrained prompts that should be used with AI coding assistants during implementation.

The objective is to prevent generic socket boilerplate from silently inventing a different protocol. AI-generated code must implement the exact schemas, framing rules, and state transitions defined in:

- `protocol_blueprint.md`
- `fsm_specification.md`

The AI assistant should treat those documents as the specification and should not replace them with a different protocol design.

---

# 2. Global System Constraint

Use this as the system/developer instruction for an AI coding assistant:

```text
You are implementing a networked two-player game.

You MUST follow the supplied Application Protocol Blueprint and Game FSM Specification exactly.

Protocol constraints:
1. Transport is TCP.
2. Serialization is UTF-8 JSON.
3. Framing is newline-delimited JSON (NDJSON).
4. Every complete JSON object must end with exactly one byte 0x0A.
5. This is the Sprint 0 Tic-Tac-Toe game: a fixed 3x3 board.
6. The first connected client is always Player_1 with symbol X and moves first; the second connected client is Player_2 with symbol O.
7. Do not randomize or otherwise change the starting player or symbols.
8. TCP recv() boundaries are NOT application-message boundaries.
9. The receiver must maintain a persistent byte buffer and extract complete messages only when newline is present.
10. Do not use fixed-size recv() calls as message boundaries.
11. Do not introduce length-prefix framing.
12. Do not introduce a different delimiter.
13. Do not invent or rename message types.
14. Do not add fields unless the protocol specification explicitly requires them.
12. Validate message type, required fields, data types, and message-specific payload before dispatch.
13. Invalid messages must produce ERROR and must not crash the server.
14. Out-of-turn MOVE messages must produce ERROR(OUT_OF_TURN) and keep the server in PLAYER_TURN.
15. Intentional DISCONNECT and abrupt TCP disconnects must follow the FSM's forfeit/cleanup behavior.
16. The server is authoritative for player roles, board state, scores, turns, and final results.

Required message types:
CONNECT
LOBBY_WAIT
GAME_START
MOVE
STATE_UPDATE
ERROR
DISCONNECT
GAME_OVER

Do not write generic protocol code first and adapt it later. Implement the exact schema and FSM rules supplied by the project documents.
```

---

# 3. Prompt for the Framing/Parser Implementation

```text
Using the attached protocol_blueprint.md as the single source of truth, implement the NDJSON TCP framing layer.

Requirements:
- Use a persistent bytearray/bytes buffer per connection.
- Append every recv() result to the buffer.
- Locate the first newline byte 0x0A.
- Extract one complete line at a time.
- Preserve any bytes after the extracted newline for the next message.
- Decode only complete lines as UTF-8.
- Parse each complete line as exactly one JSON object.
- Never assume one recv() equals one message.
- Correctly handle:
  A. one message split across multiple recv() calls
  B. multiple messages in one recv() call
  C. multiple messages where the last message is fragmented
  D. TCP EOF (empty recv result)
- Enforce the 8192-byte framed-message limit.
- Reject malformed JSON using ERROR(INVALID_MESSAGE).
- Do not use length-prefix framing.
- Do not create a second framing protocol.

Before producing code, list the exact parser invariants you are implementing.
Then provide the implementation.
Then provide tests for fragmentation, coalescing, malformed JSON, EOF, and oversized input.
```

---

# 4. Prompt for the Serialization/Sender Implementation

```text
Implement the protocol serializer from protocol_blueprint.md.

The serializer must:
1. Accept a message object that matches one of the eight allowed message types.
2. Validate required top-level fields.
3. Validate message-specific payload fields.
4. Serialize using JSON.
5. Encode the serialized JSON as UTF-8.
6. Append exactly one newline byte b"\\n".
7. Send with sendall().
8. Reject/raise a protocol validation error if the message does not match the documented schema.

Use compact JSON serialization where practical so each message stays within the documented 8192-byte limit.

Do not:
- add length headers
- add custom delimiters
- pretty-print multi-line JSON
- silently rename fields
- silently coerce invalid field types
- invent undocumented message types
```

---

# 5. Prompt for Message Validation

```text
Implement a message validator for the exact schemas in protocol_blueprint.md.

The validator must explicitly check:
- msg_type exists and is one of the eight allowed values
- player_id exists and is a string
- timestamp exists and is an integer
- payload exists when required
- every required message-specific field exists
- every required field has the documented JSON type
- MOVE row and col are integers
- GAME_START starting_player is Player_1 or Player_2
- ERROR error_code is present
- DISCONNECT reason is present
- GAME_OVER result, reason, and final scores are present

Return a structured validation result rather than allowing malformed data to reach the game state engine.

Do not replace explicit validation with permissive dictionary access such as message.get(...) without checking required fields.
```

---

# 6. Prompt for FSM Implementation

```text
Implement the server-side FSM exactly as specified in fsm_specification.md.

Required states:
INIT
WAITING_FOR_PLAYERS
GAME_START
PLAYER_TURN
EVALUATE_MOVE
GAME_OVER
CLEANUP

The implementation must preserve these rules:
- INIT initializes server/game data and enters WAITING_FOR_PLAYERS.
- First CONNECT registers a player and sends LOBBY_WAIT.
- Second CONNECT assigns Player_1/Player_2 and enters GAME_START.
- GAME_START sends GAME_START to both clients, assigns X to Player_1 and O to Player_2, and always starts Player_1.
- Only the active player may move.
- Wrong-player MOVE produces ERROR(OUT_OF_TURN) and remains in PLAYER_TURN.
- Syntactically valid but illegal moves produce ERROR(INVALID_MOVE) and remain in PLAYER_TURN.
- Valid moves enter EVALUATE_MOVE.
- If the game continues, send STATE_UPDATE, switch the active player, and return to PLAYER_TURN.
- A move is valid only when row and col are integers from 0 through 2 and the selected cell is empty.
- If the game ends, send STATE_UPDATE followed by GAME_OVER.
- DISCONNECT during an active game causes opponent victory by forfeit.
- TCP EOF, ConnectionResetError, BrokenPipeError, and ConnectionAbortedError follow the disconnect path.
- GAME_OVER always transitions to CLEANUP.
- CLEANUP resets session/game state and returns to WAITING_FOR_PLAYERS.

Do not invent additional gameplay states unless they are represented as internal implementation details that do not change the documented FSM.
```

---

# 7. Prompt for Test Generation

```text
Generate tests directly from protocol_blueprint.md and fsm_specification.md.

At minimum, test:
1. CONNECT schema validation
2. LOBBY_WAIT generation
3. GAME_START role assignment
4. valid MOVE
5. out-of-turn MOVE
6. invalid coordinates
7. malformed JSON
8. unknown msg_type
9. invalid player_id
10. STATE_UPDATE contents
11. graceful DISCONNECT
12. TCP EOF
13. ConnectionResetError
14. BrokenPipeError
15. game completion and GAME_OVER
16. CLEANUP and return to WAITING_FOR_PLAYERS
17. one message fragmented across multiple recv() calls
18. multiple messages coalesced into one recv() call
19. oversized message rejection

Each test must identify the protocol rule or FSM transition it verifies.
Do not write tests that expect undocumented behavior.
```

---

# 8. Prompt to Prevent Protocol Drift

```text
Review the proposed implementation against protocol_blueprint.md and fsm_specification.md.

Create a compliance table with these columns:
- Requirement
- Expected behavior
- Implementation location
- Pass/Fail
- Evidence

Check every message type, every required field, every field type, the NDJSON newline framing rule, fragmentation handling, coalescing handling, EOF handling, socket exception handling, all required FSM states, out-of-turn behavior, invalid move behavior, disconnect/forfeit behavior, GAME_OVER behavior, and CLEANUP reset behavior.

Do not suggest a different protocol. Report mismatches against the existing protocol specification.
```

---

# 9. AI Usage Rules

When using an AI coding assistant:

- Provide the protocol and FSM documents to the assistant before requesting implementation.
- Ask the assistant to restate the relevant constraints before generating code.
- Request small components instead of unconstrained full-project generation.
- Require tests for every parser and FSM transition.
- Compare generated code against the exact schemas before accepting it.
- Reject generated code that changes framing from newline-delimited JSON to a different mechanism.
- Reject generated code that treats individual `recv()` calls as complete messages.
- Reject generated code that adds undocumented message types or fields.
- Keep the protocol documents under Git version control so they remain the authoritative design.

---

# 10. Acceptance Checklist for AI-Generated Code

```text
[ ] Uses TCP.
[ ] Uses UTF-8 JSON.
[ ] Uses newline-delimited framing.
[ ] Handles fragmentation.
[ ] Handles coalescing.
[ ] Enforces the message-size limit.
[ ] Defines all eight message types.
[ ] Validates required fields and types.
[ ] Rejects unknown message types.
[ ] Rejects malformed JSON without crashing.
[ ] Rejects out-of-turn moves with ERROR.
[ ] Rejects illegal moves with ERROR.
[ ] Handles application DISCONNECT.
[ ] Handles TCP EOF.
[ ] Handles ConnectionResetError.
[ ] Handles BrokenPipeError.
[ ] Handles ConnectionAbortedError.
[ ] Implements all seven required FSM states.
[ ] Sends STATE_UPDATE after valid continuing moves.
[ ] Sends GAME_OVER for normal completion/draw/forfeit.
[ ] Performs CLEANUP and returns to WAITING_FOR_PLAYERS.
[ ] Does not introduce an alternate framing protocol.
```


---

# 11. Sprint 0 Compatibility Requirement

This Sprint 1 implementation must remain consistent with the Sprint 0 Statement of Work:

- Game: Tic-Tac-Toe
- Players: exactly 2
- Board: 3x3
- Player 1: first client, symbol `X`, moves first
- Player 2: second client, symbol `O`
- Win: three matching symbols horizontally, vertically, or diagonally
- Draw: all 9 cells filled with no winner
- Invalid coordinates/occupied cells: error response, no loss of turn
- Unexpected disconnect: remaining player wins by forfeit

When generated code or suggestions conflict with these rules, the Sprint 0/Sprint 1 project documents take precedence over generic AI assumptions.
