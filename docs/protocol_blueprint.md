# Sprint 1: Application Protocol Blueprint

## 1. Purpose

This document defines the application-layer messaging protocol for the turn-based two-player game. The protocol is designed to run over TCP and provides explicit message types, field names, data types, directions, validation rules, and wire-format examples.

The protocol uses **Option A: Newline-Delimited JSON (NDJSON)** for TCP message framing.

The implementation must follow this document exactly. Client and server code must not invent additional message formats or assume that one `recv()` call equals one application message.

---

## 2. Transport and Serialization

| Property | Specification |
|---|---|
| Transport protocol | TCP |
| Serialization | JSON |
| Character encoding | UTF-8 |
| Framing | One JSON object per line |
| Message terminator | ASCII newline `\n` / byte `0x0A` |
| Message size limit | 8192 bytes including the terminating newline |
| Byte order | Not applicable to framing; this protocol is text based |
| Required top-level fields | `msg_type`, `player_id`, `timestamp` |
| Payload field | Required for message types that carry application data |

JSON messages must be serialized as a single physical line. Pretty-printed JSON is not permitted because the protocol uses a newline as the message boundary.

Newline characters that are part of a JSON string value must be represented using the JSON escape sequence `\n`; a literal byte `0x0A` is reserved as the message terminator.

---

# 3. TCP Packet Framing Mechanism — Option A

## 3.1 Framing Rule

Every application message is a JSON object encoded as UTF-8 and terminated by exactly one newline character:

```text
<JSON object><0x0A>
```

The sender must transmit the complete encoded message followed by the newline.

The receiver must maintain a byte stream buffer. Incoming bytes are appended to the buffer until a newline byte is found. The receiver then:

1. Extracts all bytes through the first `0x0A`.
2. Removes the newline terminator.
3. Decodes the remaining bytes as UTF-8.
4. Parses the resulting text as one JSON object.
5. Validates the `msg_type`, required fields, field types, and message-specific payload.
6. Leaves any bytes after the newline in the buffer for the next message.

This rule handles both TCP fragmentation and TCP coalescing.

### TCP fragmentation

A single logical message can arrive in multiple `recv()` calls:

```text
recv #1:
{"msg_type":"MO

recv #2:
VE","player_id":"Player_1","payload":{"row":0,"col":2},"timestamp":1727000005}\n
```

The receiver must not attempt to parse `recv #1` as a complete message. It waits until a newline is present.

### TCP coalescing

Multiple logical messages can arrive in one `recv()` call:

```text
{"msg_type":"CONNECT","player_id":"Alice","timestamp":1727000000}\n{"msg_type":"MOVE","player_id":"Alice","payload":{"row":0,"col":2},"timestamp":1727000005}\n
```

The receiver must process this as two separate messages.

## 3.2 Receiver Buffer Algorithm

Reference pseudocode:

```text
stream_buffer = empty byte buffer

while connection is open:
    chunk = socket.recv(4096)

    if chunk is empty:
        handle TCP EOF
        stop receiving

    append chunk to stream_buffer

    while newline byte exists in stream_buffer:
        line = bytes before first newline
        remove line and newline from stream_buffer

        if line is empty:
            send ERROR(INVALID_MESSAGE)
            continue

        decode line as UTF-8
        parse line as JSON
        validate message
        dispatch message to the FSM
```

The implementation must never assume that one `recv()` corresponds to one application message.

## 3.3 Maximum Message Size

A client or server must reject a message whose complete framed size exceeds 8192 bytes. This prevents an unbounded receive buffer caused by a peer that never sends a newline.

An oversized message must produce an `ERROR` message with:

```text
error_code = MESSAGE_TOO_LARGE
```

and the connection may then be closed by the receiver as a protocol violation.

---

# 4. Common JSON Fields

All protocol messages use the following common fields unless explicitly noted otherwise:

| Field | JSON type | Required | Description |
|---|---|---:|---|
| `msg_type` | string | Yes | Exact message type name |
| `player_id` | string | Yes | Player alias associated with the message; use `SERVER` for server-originated messages |
| `timestamp` | integer | Yes | Unix epoch timestamp in seconds |
| `payload` | object | Message-specific | Data associated with the message |

### Player ID rules

A `player_id` must be a non-empty string of 1–32 characters. The server assigns the roles `Player_1` and `Player_2` when the game starts.

The reserved value `SERVER` is used only for server-originated messages.

---

# 5. Message Type Specifications

## 5.1 CONNECT

**Direction:** Client → Server

**Purpose:** Requests entry into the game room using a player alias.

### Schema

```json
{
  "msg_type": "CONNECT",
  "player_id": "Alice",
  "timestamp": 1727000000,
  "payload": {
    "player_alias": "Alice"
  }
}
```

| Field | Type | Rules |
|---|---|---|
| `msg_type` | string | Must equal `"CONNECT"` |
| `player_id` | string | Client alias, 1–32 characters |
| `timestamp` | integer | Unix epoch seconds |
| `payload` | object | Required |
| `payload.player_alias` | string | Must match `player_id` |

### Server behavior

If the connection is accepted, the server registers the player. If this is the first connected player, the server sends `LOBBY_WAIT`. If the second player joins, the server transitions to `GAME_START`.

---

## 5.2 LOBBY_WAIT

**Direction:** Server → Client

**Purpose:** Tells the first connected client that the server is waiting for the second player.

### Schema

```json
{
  "msg_type": "LOBBY_WAIT",
  "player_id": "SERVER",
  "timestamp": 1727000001,
  "payload": {
    "players_connected": 1,
    "players_required": 2
  }
}
```

| Field | Type | Rules |
|---|---|---|
| `msg_type` | string | Must equal `"LOBBY_WAIT"` |
| `player_id` | string | Must equal `"SERVER"` |
| `timestamp` | integer | Unix epoch seconds |
| `payload.players_connected` | integer | Must be `1` |
| `payload.players_required` | integer | Must be `2` |

---

## 5.3 GAME_START

**Direction:** Server → Clients

**Purpose:** Announces that two players are present, assigns roles, and identifies the starting player.

### Schema

```json
{
  "msg_type": "GAME_START",
  "player_id": "SERVER",
  "timestamp": 1727000002,
  "payload": {
    "player_1": "Alice",
    "player_2": "Bob",
    "starting_player": "Player_1",
    "player_1_symbol": "X",
    "player_2_symbol": "O"
  }
}
```

| Field | Type | Rules |
|---|---|---|
| `msg_type` | string | Must equal `"GAME_START"` |
| `player_id` | string | Must equal `"SERVER"` |
| `timestamp` | integer | Unix epoch seconds |
| `payload.player_1` | string | Alias assigned to Player 1 |
| `payload.player_2` | string | Alias assigned to Player 2 |
| `payload.player_1_symbol` | string | Must equal `"X"` |
| `payload.player_2_symbol` | string | Must equal `"O"` |
| `payload.starting_player` | string | Must be `Player_1` because Player 1 always moves first |

The same `GAME_START` message is sent to both clients so each client knows the complete role assignment. Player 1 always receives symbol `X` and moves first; Player 2 always receives symbol `O` and moves second.

---

## 5.4 MOVE

**Direction:** Client → Server

**Purpose:** Submits the active player's move coordinates or answer selection.

### Schema for a grid move

```json
{
  "msg_type": "MOVE",
  "player_id": "Alice",
  "timestamp": 1727000005,
  "payload": {
    "row": 0,
    "col": 2
  }
}
```

| Field | Type | Rules |
|---|---|---|
| `msg_type` | string | Must equal `"MOVE"` |
| `player_id` | string | Must identify the connected player |
| `timestamp` | integer | Unix epoch seconds |
| `payload` | object | Required |
| `payload.row` | integer | Zero-indexed grid row |
| `payload.col` | integer | Zero-indexed grid column |

The server validates the coordinates against the fixed 3x3 board and current board state.

A `MOVE` is valid only when:

1. The sender is one of the two connected players.
2. The sender is the current active player.
3. `row` and `col` are integers.
4. The coordinates are inside the valid board range.
5. The selected location or answer is legal for the current game state.

---

## 5.5 STATE_UPDATE

**Direction:** Server → Clients

**Purpose:** Broadcasts the authoritative game state after a valid move.

### Schema

```json
{
  "msg_type": "STATE_UPDATE",
  "player_id": "SERVER",
  "timestamp": 1727000006,
  "payload": {
    "board": [
      "X",
      "-",
      "-",
      "O",
      "-",
      "-",
      "-",
      "-",
      "-"
    ],
    "score_player_1": 1,
    "score_player_2": 0,
    "active_player": "Player_2",
    "symbol_player_1": "X",
    "symbol_player_2": "O"
  }
}
```

| Field | Type | Rules |
|---|---|---|
| `msg_type` | string | Must equal `"STATE_UPDATE"` |
| `player_id` | string | Must equal `"SERVER"` |
| `timestamp` | integer | Unix epoch seconds |
| `payload.board` | array | Authoritative board/state representation |
| `payload.score_player_1` | integer | Player 1 score |
| `payload.score_player_2` | integer | Player 2 score |
| `payload.active_player` | string | `Player_1` or `Player_2` |
| `payload.symbol_player_1` | string | Must equal `"X"` |
| `payload.symbol_player_2` | string | Must equal `"O"` |

The game board is always a 3x3 grid. Rows and columns are zero-indexed from 0 through 2. The fixed symbols are `X` for Player 1 and `O` for Player 2.

The server is authoritative. Clients must not independently declare the board state or score as final.

---

## 5.6 ERROR

**Direction:** Server → Client

**Purpose:** Reports a rejected message without crashing the server or advancing the game state.

### Schema

```json
{
  "msg_type": "ERROR",
  "player_id": "SERVER",
  "timestamp": 1727000007,
  "payload": {
    "error_code": "OUT_OF_TURN",
    "message": "It is not your turn.",
    "related_msg_type": "MOVE"
  }
}
```

### Standard error codes

| Error code | Meaning |
|---|---|
| `INVALID_MESSAGE` | Malformed JSON, missing required fields, or incorrect field types |
| `INVALID_MOVE` | Move payload is syntactically valid but the move is illegal |
| `OUT_OF_TURN` | A valid player sent a move while it was the other player's turn |
| `MESSAGE_TOO_LARGE` | Complete framed message exceeded 8192 bytes |
| `UNKNOWN_MSG_TYPE` | `msg_type` is not defined by this protocol |
| `INVALID_PLAYER` | Player is not registered for the current game |

| Field | Type | Required |
|---|---|---:|
| `msg_type` | string | Yes |
| `player_id` | string | Yes; `SERVER` |
| `timestamp` | integer | Yes |
| `payload.error_code` | string | Yes |
| `payload.message` | string | Yes |
| `payload.related_msg_type` | string | Yes |

An `ERROR` does not by itself change the active player or end the game.

---

## 5.7 DISCONNECT

**Direction:** Client → Server

**Purpose:** Notifies the server that the client is intentionally leaving the game.

### Schema

```json
{
  "msg_type": "DISCONNECT",
  "player_id": "Alice",
  "timestamp": 1727000010,
  "payload": {
    "reason": "QUIT"
  }
}
```

| Field | Type | Rules |
|---|---|---|
| `msg_type` | string | Must equal `"DISCONNECT"` |
| `player_id` | string | Must identify the connected player |
| `timestamp` | integer | Unix epoch seconds |
| `payload.reason` | string | `QUIT` is the normal intentional-departure reason |

If a player disconnects during an active game, the remaining player wins by forfeit.

---

## 5.8 GAME_OVER

**Direction:** Server → Clients

**Purpose:** Announces the final result and final scores.

### Schema

```json
{
  "msg_type": "GAME_OVER",
  "player_id": "SERVER",
  "timestamp": 1727000011,
  "payload": {
    "result": "WINNER",
    "winner": "Player_1",
    "reason": "FORFEIT",
    "final_score_player_1": 1,
    "final_score_player_2": 0
  }
}
```

### Result values

| Result | Meaning |
|---|---|
| `WINNER` | One player won the game normally |
| `DRAW` | The game ended with equal results |
| `FORFEIT` | One player disconnected and the opponent won |

For a draw:

```json
{
  "msg_type": "GAME_OVER",
  "player_id": "SERVER",
  "timestamp": 1727000020,
  "payload": {
    "result": "DRAW",
    "winner": null,
    "reason": "GAME_COMPLETE",
    "final_score_player_1": 3,
    "final_score_player_2": 3
  }
}
```

---

# 6. Message Validation Order

The receiving side must process every complete framed message in this order:

1. Confirm the line does not exceed the maximum message size.
2. Decode bytes as UTF-8.
3. Parse JSON.
4. Confirm the result is a JSON object.
5. Confirm `msg_type`, `player_id`, and `timestamp` exist.
6. Validate the common field data types.
7. Validate the message-specific payload schema.
8. Validate whether that message is legal in the current FSM state.
9. Dispatch the message to the corresponding state transition.
10. Send an `ERROR` instead of crashing when validation fails.

Malformed or invalid messages must never be interpreted as valid game moves.

---

# 7. Wire Stream Examples

## 7.1 Two Messages Back-to-Back

```text
{"msg_type":"CONNECT","player_id":"Alice","timestamp":1727000000,"payload":{"player_alias":"Alice"}}\n{"msg_type":"MOVE","player_id":"Alice","timestamp":1727000005,"payload":{"row":0,"col":2}}\n
```

The receiver extracts two messages because two newline delimiters exist.

## 7.2 Fragmented Message

```text
chunk 1:
{"msg_type":"MOVE","player_id":"Alice","timestamp":1727000005,

chunk 2:
"payload":{"row":0,"col":2}}\n
```

The receiver concatenates both chunks before parsing.

## 7.3 Multiple Messages Plus Fragmentation

```text
chunk 1:
{"msg_type":"CONNECT","player_id":"Alice","timestamp":1727000000,"payload":{"player_alias":"Alice"}}\n{"msg_type":"MOVE",

chunk 2:
"player_id":"Alice","timestamp":1727000005,"payload":{"row":0,"col":2}}\n
```

The first message is processed immediately after its newline is found. The second message remains buffered until its own newline arrives.

---

# 8. Serialization Requirements

The sender should construct a dictionary/object matching the message schema, serialize it to JSON, encode it as UTF-8, append `b"\n"`, and transmit it.

Reference implementation pattern:

```python
message_bytes = json.dumps(
    message,
    separators=(",", ":"),
    ensure_ascii=False
).encode("utf-8") + b"\n"

sock.sendall(message_bytes)
```

The receiver must use a persistent stream buffer because TCP does not preserve application message boundaries.

---

# 9. Protocol Guarantees

This protocol guarantees that:

- Every application message has one deterministic boundary.
- Fragmented TCP data is reconstructed before JSON parsing.
- Coalesced TCP messages are separated using newline boundaries.
- Message schemas are explicit enough to implement a parser.
- Invalid and out-of-turn moves result in `ERROR` messages rather than server crashes.
- Intentional and abrupt disconnects have defined game-state behavior.
- The server remains authoritative for roles, turns, board state, scores, and final results.
