# Sprint 2: State and Score Broadcast Sequence Diagram

## 1. Purpose

These Mermaid sequence diagrams show the message flow for the Sprint 0 Tic-Tac-Toe game using the Sprint 1 newline-delimited JSON protocol and the Sprint 2 threaded server architecture.

The diagrams cover every application message type in the protocol: `CONNECT`, `LOBBY_WAIT`, `GAME_START`, `MOVE`, `STATE_UPDATE`, `ERROR`, `DISCONNECT`, and `GAME_OVER`. TCP EOF/RST events are transport events, not application messages.

Player 1 is always `X` and moves first. Player 2 is always `O`. The server is authoritative for the board, active turn, win/draw result, and cumulative scoreboard.

---

## 2. Connection, Turn Enforcement, and State Broadcast

```mermaid
sequenceDiagram
    autonumber
    actor C1 as Client 1 (Player 1 / X)
    participant Server as Game Server
    actor C2 as Client 2 (Player 2 / O)

    C1->>Server: CONNECT(player_alias=Alice)
    Note over Server: Worker 1 parses the complete newline-delimited JSON message
    Server-->>C1: LOBBY_WAIT(players_connected=1, players_required=2)

    C2->>Server: CONNECT(player_alias=Bob)
    Note over Server: Register both roles under the state lock; Player 1 is X and starts
    Server-->>C1: GAME_START(Player 1=Alice/X, Player 2=Bob/O, starter=Player 1)
    Server-->>C2: GAME_START(Player 1=Alice/X, Player 2=Bob/O, starter=Player 1)

    alt Player 2 tries to move before Player 1
        C2->>Server: MOVE(row=0, col=2)
        Note over Server: Validate sender against active_player=Player 1
        Server-->>C2: ERROR(error_code=OUT_OF_TURN)
        Note over Server: Board, scores, and active turn remain unchanged
    else Player 1 submits a valid move
        C1->>Server: MOVE(row=0, col=2)
        Note over Server: Acquire broadcast_lock then game_lock; validate bounds and empty cell
        Server->>Server: Place X; check win/draw; set active player to Player 2
        Server-->>C1: STATE_UPDATE(board, active_player=Player 2, scoreboard)
        Server-->>C2: STATE_UPDATE(board, active_player=Player 2, scoreboard)
        Note over Server: Send the same state snapshot to both clients before releasing broadcast_lock
    end
```

### Flow notes

1. Each client has a worker thread, so one worker waiting for socket data does not block the other client.
2. The server sends `GAME_START` only after both players are registered. Player 1 is assigned `X` and moves first; Player 2 is assigned `O`.
3. The `alt` branch shows that an out-of-turn move returns `ERROR(OUT_OF_TURN)` and makes no state change. The other branch shows a valid move and the state broadcast.
4. The server validates every move, updates its authoritative board, and broadcasts the same `STATE_UPDATE` to both clients.
5. The server holds `broadcast_lock` across the ordered sends so another state-changing transaction cannot overtake the broadcast. It releases `game_lock` before network I/O.

---

## 3. Invalid or Occupied-Cell Move

This is a separate example of a request received when it is Player 2's turn and cell `(0,2)` already contains Player 1's `X` from an earlier valid move.

```mermaid
sequenceDiagram
    autonumber
    actor C1 as Client 1 (Player 1 / X)
    participant Server as Game Server
    actor C2 as Client 2 (Player 2 / O)

    Note over C1,C2: Prior STATE_UPDATE says Player 2 is active; cell (0,2) contains X
    C2->>Server: MOVE(row=0, col=2)
    Note over Server: Validate active turn, board bounds, and cell occupancy
    Server-->>C2: ERROR(error_code=INVALID_MOVE)
    Note over Server: Do not overwrite X; Player 2 keeps the turn
```

Malformed JSON is rejected with `ERROR(INVALID_MESSAGE)` and an unknown message type with `ERROR(UNKNOWN_MSG_TYPE)`. No invalid request modifies the board or scoreboard.

---

## 4. Round Completion and Scoreboard Synchronization

```mermaid
sequenceDiagram
    autonumber
    actor C1 as Client 1 (Player 1 / X)
    participant Server as Game Server
    actor C2 as Client 2 (Player 2 / O)

    C2->>Server: MOVE(final legal row/col)
    Note over Server: Acquire broadcast_lock then game_lock; validate and apply the move
    Server->>Server: Detect win or full-board draw
    Server->>Server: Increment winner counter or draws_total exactly once
    Server-->>C1: STATE_UPDATE(final board, score_player_1, score_player_2, draws_total)
    Server-->>C2: STATE_UPDATE(final board, score_player_1, score_player_2, draws_total)
    Server-->>C1: GAME_OVER(result, winner, reason, final scores, draws_total)
    Server-->>C2: GAME_OVER(result, winner, reason, final scores, draws_total)
    Note over Server: Both clients receive the final state and score table before round cleanup
```

### Scoreboard rules

The server stores these cumulative totals in memory:

- `score_player_1`: rounds won by Player 1 (`X`).
- `score_player_2`: rounds won by Player 2 (`O`).
- `draws_total`: rounds ending in a draw.

A normal win increments the winner's total; a draw increments `draws_total`; a forfeit increments the remaining player's win total. The scoreboard is updated once per completed round, then the same totals are broadcast in the final `STATE_UPDATE` and `GAME_OVER`. Invalid moves never affect the scoreboard.

---

## 5. Intentional and Unexpected Disconnects

```mermaid
sequenceDiagram
    autonumber
    actor C1 as Client 1 (Player 1 / X)
    participant Server as Game Server
    actor C2 as Client 2 (Player 2 / O)

    alt Intentional departure during an active round
        C2->>Server: DISCONNECT(reason=QUIT)
        Note over Server: Serialize disconnect; mark Player 2 disconnected
        Server->>Server: Award Player 1 a forfeit win once
        Server-->>C1: STATE_UPDATE(final board, updated scoreboard)
        Server-->>C1: GAME_OVER(result=FORFEIT, winner=Player 1, reason=DISCONNECT)
    else Unexpected TCP EOF, reset, or write failure
        Note over Server: Worker detects zero-byte recv or a socket exception
        Server->>Server: Mark Player 2 disconnected; award Player 1 a forfeit win once
        Server-->>C1: STATE_UPDATE(final board, updated scoreboard)
        Server-->>C1: GAME_OVER(result=FORFEIT, winner=Player 1, reason=DISCONNECT)
    end
    Server->>Server: Close dead socket, clean session state, prepare next round
```

A disconnect after a round has already ended must not award a second win. The server's round-result guard and `game_lock` prevent duplicate scoreboard changes if multiple disconnect/error events arrive close together.

---

## 6. Broadcast and Thread-Safety Guarantees

- The server validates and mutates state inside `game_lock`.
- The server releases `game_lock` before any socket write.
- The server keeps `broadcast_lock` for the full ordered state-update broadcast.
- Each session's `send_lock` protects framed writes to that client's socket.
- Both client writes are attempted before the broadcast transaction finishes. A failed write is detected and handled as a disconnect.
- TCP `sendall()` cannot confirm that the remote application's UI has processed a message; it ensures the server attempts to hand the full framed message to its local TCP stack. Application-level acknowledgement is not part of the current protocol.

The Mermaid source is embedded in this Markdown file, so GitHub can render it directly. No exported image or PDF is required.
