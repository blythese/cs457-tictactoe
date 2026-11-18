#!/usr/bin/env python3
"""Threaded TCP server for the CS 457 networked Tic-Tac-Toe project.

Protocol: Sprint 1 Option A (UTF-8 NDJSON, one JSON object per line).
The server is authoritative for player roles, turns, board state, and results.
"""

import argparse
import json
import logging
import socket
import threading
import time
from typing import Dict, Optional

HOST = "0.0.0.0"
MAX_MESSAGE_SIZE = 8192
RECV_SIZE = 4096
WIN_LINES = (
    (0, 1, 2), (3, 4, 5), (6, 7, 8),
    (0, 3, 6), (1, 4, 7), (2, 5, 8),
    (0, 4, 8), (2, 4, 6),
)


def valid_port(value: str) -> int:
    """Parse and validate a TCP port in the assignment's allowed range."""
    try:
        port = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("port must be an integer") from exc
    if not 1024 <= port <= 65535:
        raise argparse.ArgumentTypeError("port must be between 1024 and 65535")
    return port


def make_message(msg_type: str, player_id: str = "SERVER",
                 payload: Optional[dict] = None) -> dict:
    """Build a protocol message with the required common fields."""
    message = {
        "msg_type": msg_type,
        "player_id": player_id,
        "timestamp": int(time.time()),
    }
    if payload is not None:
        message["payload"] = payload
    return message


def encode_message(message: dict) -> bytes:
    """Serialize one compact JSON object and append the NDJSON delimiter."""
    data = json.dumps(
        message, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8") + b"\n"
    if len(data) > MAX_MESSAGE_SIZE:
        raise ValueError("outgoing message exceeds 8192 bytes")
    return data


class Player:
    """One connected player's socket and assigned game identity."""

    def __init__(self, sock: socket.socket, address, alias: str, role: str):
        self.sock = sock
        self.address = address
        self.alias = alias
        self.role = role
        self.symbol = "X" if role == "Player_1" else "O"
        self.send_lock = threading.Lock()
        self.closed = False

    def send(self, message: dict) -> bool:
        """Serialize writes so concurrent server events cannot interleave."""
        try:
            data = encode_message(message)
            with self.send_lock:
                if self.closed:
                    return False
                self.sock.sendall(data)
            return True
        except (OSError, ValueError):
            return False

    def close(self):
        with self.send_lock:
            if self.closed:
                return
            self.closed = True
            try:
                self.sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                self.sock.close()
            except OSError:
                pass


class GameServer:
    """A single two-player game room managed by per-client worker threads."""

    def __init__(self):
        self.lock = threading.RLock()
        self.players: Dict[str, Player] = {}
        self.board = ["-"] * 9
        self.active_player = "Player_1"
        self.state = "INIT"
        self.game_over = False
        self.scores = {"Player_1": 0, "Player_2": 0}

    def transition(self, new_state: str):
        logging.info("FSM: %s -> %s", self.state, new_state)
        self.state = new_state

    def send_error(self, player: Player, code: str, message: str,
                   related: str):
        player.send(make_message("ERROR", payload={
            "error_code": code,
            "message": message,
            "related_msg_type": related,
        }))

    def register(self, player: Player) -> bool:
        """Register a CONNECT request; return False if the room is full."""
        with self.lock:
            if self.game_over or len(self.players) >= 2:
                self.send_error(
                    player, "INVALID_PLAYER", "The game room is full.",
                    "CONNECT"
                )
                return False

            self.players[player.role] = player
            if len(self.players) == 1:
                self.transition("WAITING_FOR_PLAYERS")
                player.send(make_message("LOBBY_WAIT", payload={
                    "players_connected": 1,
                    "players_required": 2,
                }))
                logging.info("%s joined; waiting for Player 2.", player.alias)
                return True

            self.transition("GAME_START")
            self.board = ["-"] * 9
            self.active_player = "Player_1"
            self.game_over = False
            p1 = self.players["Player_1"]
            p2 = self.players["Player_2"]
            start = make_message("GAME_START", payload={
                "player_1": p1.alias,
                "player_2": p2.alias,
                "starting_player": "Player_1",
                "player_1_symbol": "X",
                "player_2_symbol": "O",
            })
            p1.send(start)
            p2.send(start)
            self.transition("PLAYER_TURN")
            logging.info("Game started: %s (X) vs %s (O).",
                         p1.alias, p2.alias)
            return True

    def state_update(self):
        payload = {
            "board": list(self.board),
            "score_player_1": self.scores["Player_1"],
            "score_player_2": self.scores["Player_2"],
            "active_player": self.active_player,
            "symbol_player_1": "X",
            "symbol_player_2": "O",
        }
        message = make_message("STATE_UPDATE", payload=payload)
        for player in list(self.players.values()):
            player.send(message)

    def finish(self, result: str, winner: Optional[str], reason: str):
        """End the current game once and broadcast the authoritative result."""
        if self.game_over:
            return
        self.game_over = True
        self.transition("GAME_OVER")
        if winner in self.scores:
            self.scores[winner] += 1
        message = make_message("GAME_OVER", payload={
            "result": result,
            "winner": winner,
            "reason": reason,
            "final_score_player_1": self.scores["Player_1"],
            "final_score_player_2": self.scores["Player_2"],
        })
        for player in list(self.players.values()):
            player.send(message)
        self.transition("CLEANUP")

    def handle_message(self, player: Player, message: dict):
        """Validate and dispatch a complete client message into the FSM."""
        with self.lock:
            if self.players.get(player.role) is not player:
                self.send_error(player, "INVALID_PLAYER",
                                "You are not registered in this game.",
                                message.get("msg_type", "UNKNOWN"))
                return

            msg_type = message.get("msg_type")
            if msg_type == "DISCONNECT":
                self.disconnect(player, "QUIT")
                return

            if msg_type != "MOVE":
                code = "UNKNOWN_MSG_TYPE" if isinstance(msg_type, str) else "INVALID_MESSAGE"
                self.send_error(player, code, "Expected a MOVE message.",
                                str(msg_type))
                return

            if self.game_over or self.state != "PLAYER_TURN":
                self.send_error(player, "INVALID_MOVE",
                                "The game is not accepting moves.",
                                "MOVE")
                return

            if player.role != self.active_player:
                self.send_error(player, "OUT_OF_TURN",
                                "It is not your turn.", "MOVE")
                return

            payload = message.get("payload")
            if not isinstance(payload, dict):
                self.send_error(player, "INVALID_MOVE",
                                "MOVE payload must contain row and col.",
                                "MOVE")
                return

            row = payload.get("row")
            col = payload.get("col")
            # bool is a subclass of int in Python, so reject it explicitly.
            if (not isinstance(row, int) or isinstance(row, bool)
                    or not isinstance(col, int) or isinstance(col, bool)
                    or not 0 <= row <= 2 or not 0 <= col <= 2):
                self.send_error(player, "INVALID_MOVE",
                                "Row and column must each be 0, 1, or 2.",
                                "MOVE")
                return

            index = row * 3 + col
            if self.board[index] != "-":
                self.send_error(player, "INVALID_MOVE",
                                "That square is already occupied.", "MOVE")
                return

            self.transition("EVALUATE_MOVE")
            self.board[index] = player.symbol
            winner = self.find_winner()
            if winner:
                self.active_player = player.role
                self.state_update()
                self.finish("WINNER", winner, "LINE_COMPLETE")
                return

            if "-" not in self.board:
                self.state_update()
                self.finish("DRAW", None, "GAME_COMPLETE")
                return

            self.active_player = (
                "Player_2" if player.role == "Player_1" else "Player_1"
            )
            self.state_update()
            self.transition("PLAYER_TURN")

    def find_winner(self) -> Optional[str]:
        for a, b, c in WIN_LINES:
            if self.board[a] != "-" and self.board[a] == self.board[b] == self.board[c]:
                return "Player_1" if self.board[a] == "X" else "Player_2"
        return None

    def disconnect(self, player: Player, reason: str = "QUIT"):
        """Handle intentional or unexpected client departure."""
        with self.lock:
            if self.players.get(player.role) is not player:
                return
            was_active_game = len(self.players) == 2 and not self.game_over
            del self.players[player.role]
            logging.info("%s disconnected (%s).", player.alias, reason)

            if was_active_game:
                opponent_role = (
                    "Player_2" if player.role == "Player_1" else "Player_1"
                )
                opponent = self.players.get(opponent_role)
                self.finish("FORFEIT", opponent_role, "FORFEIT")
                if opponent is None:
                    self.game_over = True
            elif not self.players:
                self.transition("CLEANUP")

            # Once both clients have left, prepare the room for another match.
            if not self.players:
                self.board = ["-"] * 9
                self.active_player = "Player_1"
                self.game_over = False
                self.transition("WAITING_FOR_PLAYERS")


def read_messages(sock: socket.socket):
    """Yield newline-framed JSON objects, handling TCP fragmentation/coalescing."""
    buffer = bytearray()
    while True:
        chunk = sock.recv(RECV_SIZE)
        if not chunk:
            return
        buffer.extend(chunk)

        while b"\n" in buffer:
            line, _, remainder = buffer.partition(b"\n")
            buffer = bytearray(remainder)
            if not line:
                raise ValueError("empty NDJSON message")
            if len(line) + 1 > MAX_MESSAGE_SIZE:
                raise OverflowError("message exceeds 8192 bytes")
            try:
                message = json.loads(line.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ValueError("invalid JSON or UTF-8") from exc
            if not isinstance(message, dict):
                raise ValueError("top-level JSON value must be an object")
            yield message

        if len(buffer) >= MAX_MESSAGE_SIZE:
            raise OverflowError("message exceeds 8192 bytes")


def validate_connect(message: dict) -> str:
    """Validate common fields and the CONNECT-specific payload."""
    if (message.get("msg_type") != "CONNECT"
            or not isinstance(message.get("player_id"), str)
            or not 1 <= len(message["player_id"]) <= 32
            or not isinstance(message.get("timestamp"), int)
            or isinstance(message.get("timestamp"), bool)
            or not isinstance(message.get("payload"), dict)):
        raise ValueError("CONNECT is missing required fields or has invalid types")
    alias = message["payload"].get("player_alias")
    if alias != message["player_id"]:
        raise ValueError("player_alias must match player_id")
    return alias


def client_worker(conn: socket.socket, address, game: GameServer):
    """Handle one client connection in its own thread."""
    player = None
    try:
        conn.settimeout(30)
        iterator = read_messages(conn)
        try:
            first = next(iterator)
        except StopIteration:
            return
        alias = validate_connect(first)

        with game.lock:
            if len(game.players) == 0:
                role = "Player_1"
            elif len(game.players) == 1 and not game.game_over:
                role = "Player_2"
            else:
                role = "Player_1"
            player = Player(conn, address, alias, role)
            accepted = game.register(player)

        if not accepted:
            return

        conn.settimeout(None)
        for message in iterator:
            # Validate the common protocol envelope before dispatch.
            if (not isinstance(message.get("player_id"), str)
                    or not isinstance(message.get("timestamp"), int)
                    or isinstance(message.get("timestamp"), bool)):
                game.send_error(player, "INVALID_MESSAGE",
                                "Message common fields are invalid.",
                                str(message.get("msg_type", "UNKNOWN")))
                continue
            if message.get("player_id") != player.alias:
                game.send_error(player, "INVALID_PLAYER",
                                "Message player_id does not match your alias.",
                                str(message.get("msg_type", "UNKNOWN")))
                continue
            game.handle_message(player, message)

    except OverflowError:
        if player:
            game.send_error(player, "MESSAGE_TOO_LARGE",
                            "Message exceeded 8192 bytes.", "UNKNOWN")
    except (ValueError, socket.timeout) as exc:
        logging.warning("Invalid client input from %s: %s", address, exc)
        if player:
            game.send_error(player, "INVALID_MESSAGE",
                            "Malformed or incomplete protocol message.",
                            "UNKNOWN")
    except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError,
            OSError) as exc:
        logging.info("Connection problem from %s: %s", address, exc)
    finally:
        if player:
            game.disconnect(player, "CONNECTION_CLOSED")
        try:
            conn.close()
        except OSError:
            pass


def main():
    parser = argparse.ArgumentParser(
        description="CS 457 multiplayer Tic-Tac-Toe TCP server"
    )
    parser.add_argument(
        "-p", "--port", required=True, type=valid_port,
        help="TCP listening port (1024-65535)"
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="[SERVER] %(asctime)s %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    game = GameServer()
    game.transition("INIT")

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
            server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            server.bind((HOST, args.port))
            server.listen(10)
            game.transition("WAITING_FOR_PLAYERS")
            logging.info("Listening on %s:%d", HOST, args.port)
            while True:
                conn, address = server.accept()
                logging.info("TCP connection from %s:%d", *address)
                worker = threading.Thread(
                    target=client_worker,
                    args=(conn, address, game),
                    daemon=True,
                )
                worker.start()
    except KeyboardInterrupt:
        logging.info("Server shutting down.")
    except OSError as exc:
        logging.error("Could not start server: %s", exc)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
