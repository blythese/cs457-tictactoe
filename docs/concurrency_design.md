# Sprint 2: Server Concurrency Architecture Design

## 1. Purpose and Design Choice

**Selected architecture: Option A — Multi-threading with `threading.Thread`.**

The server uses one acceptor thread and one worker thread for each accepted client socket. The acceptor accepts connections and starts workers; each worker handles its client's incoming TCP byte stream and complete protocol messages. Blocking `recv()` calls are isolated to client worker threads, so one idle client cannot prevent the server from accepting or servicing the other client.

This architecture fits the project because the game has exactly two active players and the server must keep both client connections open at the same time.

The server remains authoritative for the Tic-Tac-Toe board, player roles, active turn, round result, and cumulative match scoreboard.

## 2. Sprint 0 Rules Preserved

The implementation carries forward these rules:

- Game: console-based Tic-Tac-Toe on a fixed 3x3 board.
- The first registered client becomes `Player_1`, uses `X`, and moves first.
- The second player becomes `Player_2` and uses `O`.
- Rows and columns are zero-indexed and must each be in the range `0..2`.
- A move is legal only when it is the active player's turn and the chosen cell is empty.
- A malformed, out-of-range, occupied-cell, or out-of-turn move returns `ERROR`; it does not consume the player's turn.
- Three symbols in a horizontal, vertical, or diagonal line wins.
- A full board with no winner is a draw.
- An intentional or unexpected disconnect during an active round causes a forfeit win for the remaining player.

## 3. Server Components

### 3.1 Acceptor thread

The acceptor creates the listening TCP socket on the configured server interface for `server.blythe.edu`, calls `listen()`, and repeatedly calls `accept()`.

For each accepted socket, it creates a client-session record and starts a worker thread. The acceptor does not call the client's blocking receive loop itself.

The server permits two active player sessions. A connection beyond the two-player capacity is rejected with an `ERROR` message where possible, then closed.

The exact TCP port is a deployment/runtime configuration value; it is not fixed by this design document.

### 3.2 One worker thread per client

Each worker owns and services one client connection. Its responsibilities are:

1. Receive bytes from that client's socket.
2. Append bytes to a persistent buffer.
3. Extract complete newline-delimited JSON messages only when `\n` is present.
4. Decode UTF-8 and validate the message schema.
5. Process complete messages through the synchronized game-state handling path.
6. Handle `CONNECT`, `MOVE`, and `DISCONNECT` messages.
7. Detect EOF and socket exceptions.
8. Close and clean up its own socket when the session ends.

TCP message framing must follow Sprint 1's `protocol_blueprint.md`: one compact UTF-8 JSON object terminated by one newline byte (`0x0A`). One `recv()` call is not assumed to equal one application message.

### 3.3 Shared game coordinator

The coordinator stores the authoritative game state and ensures that each state-changing event is processed as one serialized transaction. Worker threads may read from sockets concurrently, but they must not independently mutate the board, active turn, score totals, or player registry.

## 4. Shared Data and Protection

| Shared resource | Examples | Protection |
|---|---|---|
| Game phase | `WAITING_FOR_PLAYERS`, `PLAYER_TURN`, `GAME_OVER` | `game_lock` |
| Board | 3 rows × 3 columns, each cell empty, `X`, or `O` | `game_lock` |
| Active turn | `Player_1` or `Player_2` | `game_lock` |
| Player/session registry | Role, alias, socket, connection status | `game_lock` |
| Round result | Winner, draw, forfeit, result-recorded flag | `game_lock` |
| Cumulative scoreboard | Player 1 wins, Player 2 wins, draws | `game_lock` |
| Round number and current roles | Current round/session metadata | `game_lock` |
| Ordering of state changes and broadcasts | Move validation → mutation → sends | `broadcast_lock` |
| Outgoing writes to an individual socket | Framed JSON messages | Per-session `send_lock` |

### Lock definitions

```python
game_lock = threading.Lock()
broadcast_lock = threading.Lock()
```

Each client-session object also owns its own `send_lock = threading.Lock()`.

## 5. Exact Lock Acquisition Strategy

### 5.1 Required lock order

For any transaction that can change game state or broadcast an authoritative state update, the lock order is:

1. Acquire `broadcast_lock`.
2. Acquire `game_lock`.
3. Validate the event and update shared state while holding `game_lock`.
4. Create immutable message data/snapshots and copy the recipient list while still holding `game_lock`.
5. Release `game_lock`.
6. Send messages while still holding `broadcast_lock`; each socket write also acquires that session's `send_lock`.
7. Release `broadcast_lock` after the ordered sends finish.

No code may acquire `broadcast_lock` while holding `game_lock`. No code may hold `game_lock` while waiting for a network send. This avoids lock-order deadlocks and prevents a slow network write from freezing all access to the shared board data.

### 5.2 Why two global locks are used

- `game_lock` protects the data structures themselves and keeps a board/turn/score mutation atomic.
- `broadcast_lock` keeps state-changing transactions and their corresponding outgoing messages in a consistent order. A second move must not mutate the board or send a later `STATE_UPDATE` between the first client's and second client's sends for the previous update.
- Per-session `send_lock` prevents two worker threads from interleaving bytes on the same TCP socket.

A parser-level error that does not modify state can send an `ERROR` through the target session's `send_lock`. That code must not acquire `broadcast_lock` while holding `send_lock`.

### 5.3 Critical sections

**Inside `game_lock`:**

- Register/remove a client.
- Check the registered player's role and current turn.
- Validate row/column values and cell occupancy.
- Apply a legal mark.
- Detect a win/draw.
- Change `active_player`.
- Update the cumulative scoreboard exactly once when a round ends.
- Change phase and set the `round_result_recorded` guard.
- Snapshot the client sessions and authoritative state for outgoing messages.

**Outside `game_lock`:**

- `recv()`, blocking sends, logging, JSON serialization when it does not require mutable shared state, and socket close operations.
- Network writes happen while the transaction still holds `broadcast_lock`, but never while holding `game_lock`.

## 6. Move Processing Transaction

When a worker receives a complete `MOVE` message, the coordinator processes it as follows:

1. Acquire `broadcast_lock`, then `game_lock`.
2. Confirm the game is in `PLAYER_TURN`.
3. Confirm the message identifies a connected player.
4. Confirm the sender is the active player. Otherwise prepare `ERROR(OUT_OF_TURN)`.
5. Confirm `row` and `col` are integers from 0 through 2. Otherwise prepare `ERROR(INVALID_MOVE)`.
6. Confirm the selected cell is empty. Otherwise prepare `ERROR(INVALID_MOVE)`.
7. If valid, place `X` or `O`, check win/draw, and update the active turn if the game continues.
8. If the round has ended, update the cumulative win/draw counter exactly once and set the result-recorded guard.
9. Build the appropriate `STATE_UPDATE` snapshot; if the round has ended, also build `GAME_OVER`.
10. Release `game_lock`.
11. Send the prepared response or broadcast, in order, using `sendall()` and the per-client `send_lock`.
12. Release `broadcast_lock`.

If a move is rejected, board contents, scoreboard, and active turn remain unchanged. The rejection does not advance the round.

Clients should wait for the server's `STATE_UPDATE` indicating their role is active before submitting their next move. The server still validates every request; client behavior is not a substitute for server-side turn enforcement.

## 7. Synchronous State Broadcast

After a valid continuing move, the server sends the same authoritative `STATE_UPDATE` snapshot to both players before releasing `broadcast_lock`:

1. Send `STATE_UPDATE` to Player 1 using `sendall()`.
2. Send the same `STATE_UPDATE` to Player 2 using `sendall()`.
3. Do not allow another state-changing transaction to overtake this broadcast.
4. Only then release `broadcast_lock`.

When a round ends, the server first updates the cumulative scoreboard under `game_lock`. It then sends a final `STATE_UPDATE` containing the final board and updated scoreboard to both clients, followed by `GAME_OVER` to both clients.

The sends are synchronous from the server's control-flow perspective: the server attempts both writes in a defined order and handles write errors before completing the broadcast transaction. TCP `sendall()` does not prove that the other application's UI has processed the message; it sends the bytes to the local TCP stack. A failed write is treated as a connection failure and routed through disconnect/forfeit handling.

A short socket write timeout should be configured so an unresponsive peer cannot hold up broadcasts indefinitely. A failed recipient is marked disconnected and handled using the same cleanup/forfeit rules as other transport failures.

## 8. Score and Leaderboard Synchronization

The scoreboard is kept in server memory and is authoritative. It contains:

- `score_player_1`: cumulative number of rounds won by Player 1 (`X`).
- `score_player_2`: cumulative number of rounds won by Player 2 (`O`).
- `draws_total`: cumulative number of rounds that ended in a draw.

At server startup, all three totals start at zero. A normal win increments only the winner's total. A draw increments `draws_total`. A forfeit increments the remaining player's win total. The scoreboard is updated once per completed round; invalid moves do not affect it.

The score totals persist across rounds while the server process and the same two-player match are running. They are reset when the server process restarts. `STATE_UPDATE` carries the current totals so clients can refresh their score table. On round completion, both clients receive a final `STATE_UPDATE` with the scoreboard and then `GAME_OVER` with the final result and totals.

The `round_result_recorded` flag is reset when a new round begins. The coordinator checks this flag under `game_lock` before changing the scoreboard, preventing two nearly simultaneous disconnect events from awarding duplicate forfeit wins.

No additional message type is needed for the leaderboard. It is included in the existing `STATE_UPDATE` and `GAME_OVER` payloads.

## 9. Connection Lifecycle

1. **Listen:** Acceptor creates the TCP listening socket and waits for connections.
2. **Accept:** For a new socket, create the session record and start a dedicated worker thread.
3. **Connect/register:** Worker reads framed messages and validates `CONNECT`. The first registered player is Player 1/X; the second is Player 2/O. Once both have registered, send `GAME_START` to both and set Player 1 as the active player.
4. **Wait/process:** Worker receives complete messages. `MOVE` is processed through the serialized transaction. `DISCONNECT` enters the forfeit path during an active round.
5. **Round completion:** Send final `STATE_UPDATE` and `GAME_OVER` to both clients; do not process further moves for that completed round.
6. **Clean up:** Remove failed/disconnected sockets and release session resources. Reset the board/turn/result flags for the next round. Keep the cumulative scoreboard in memory while the server remains running.
7. **Return to lobby:** If the server continues running, wait for the two-player session to be established for the next round.

The acceptor continues accepting/handling connections independently of individual workers blocked in `recv()`. Because the room capacity is two, any additional connection is rejected rather than added to the active game.

## 10. Disconnect and Failure Handling

### Intentional application disconnect

When a player sends `DISCONNECT` during an active round:

- The coordinator serializes the event with `broadcast_lock` and `game_lock`.
- The other player is declared the forfeit winner.
- The winner's cumulative score is incremented once.
- The server sends final `STATE_UPDATE` and `GAME_OVER` to the remaining connected client.
- The departed socket is closed and session state is cleaned up.

### Unexpected disconnect

A worker treats `recv()` returning `b""` as TCP EOF. It also catches `ConnectionResetError`, `ConnectionAbortedError`, `BrokenPipeError`, and configured socket timeouts. It submits the same disconnect event to the coordinator. If a round is active, the opponent wins by forfeit. If the game has already ended or the session was already removed, the event must not award another win.

### Malformed messages

Malformed JSON, incorrect field types, unknown message types, oversized lines, or invalid player identities produce the matching `ERROR` response where possible. They do not mutate the board or scoreboard. A protocol-violating or unresponsive peer may be closed after the error is sent or the send timeout expires.

## 11. Pseudocode for the State-Changing Path

This pseudocode describes the lock discipline, not a complete implementation:

```python
def handle_move(session, message):
    with broadcast_lock:
        with game_lock:
            response = validate_and_apply_move(session, message)
            # response contains immutable message data and recipient snapshots.
            # Invalid moves prepare ERROR and leave game state unchanged.
            # A completed round updates the scoreboard exactly once.

        # game_lock has been released before any network I/O.
        # broadcast_lock keeps this transaction's sends ordered.
        for recipient, outgoing_message in response.ordered_sends:
            try:
                send_framed_message(recipient, outgoing_message)
            except (BrokenPipeError, ConnectionResetError,
                    ConnectionAbortedError, TimeoutError):
                response.failed_recipients.add(recipient)

        # Mark failed recipients and trigger disconnect/forfeit handling
        # in the same serialized transaction or a serialized coordinator event.
```

The production implementation must ensure that failed recipients are removed and the disconnect/forfeit event is processed exactly once. Do not recursively reacquire `broadcast_lock` from inside a transaction that already holds it.

## 12. Concurrency Test Checklist

- [ ] The acceptor starts a worker thread for each of two client sockets.
- [ ] A blocked `recv()` for one client does not prevent the other worker from receiving a message.
- [ ] A third client is rejected when both player slots are occupied.
- [ ] Player 1 is assigned `X` and moves first; Player 2 is assigned `O`.
- [ ] Two move messages submitted close together cannot both mutate the same prior board state.
- [ ] An out-of-turn move produces `ERROR` without changing board or turn.
- [ ] An out-of-range or occupied-cell move produces `ERROR` and does not consume the turn.
- [ ] Every continuing valid move broadcasts an identical `STATE_UPDATE` to both clients.
- [ ] The next state-changing broadcast cannot overtake the previous broadcast.
- [ ] A win increments one winner counter exactly once.
- [ ] A draw increments `draws_total` exactly once.
- [ ] Graceful disconnect, TCP EOF, reset, broken pipe, and timeout use the forfeit/cleanup path.
- [ ] Both clients receive the final scoreboard and `GAME_OVER` when reachable.
- [ ] All sockets and worker threads are cleaned up after a session ends.
