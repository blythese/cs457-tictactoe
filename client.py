#!/usr/bin/env python3
"""Terminal client for the CS 457 networked Tic-Tac-Toe project.

Uses Sprint 1 Option A NDJSON framing and the server-authoritative game state.
"""

import argparse
import json
import queue
import socket
import sys
import threading
import time

MAX_MESSAGE_SIZE = 8192
RECV_SIZE = 4096


def valid_port(value: str) -> int:
    try:
        port = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("port must be an integer") from exc
    if not 1024 <= port <= 65535:
        raise argparse.ArgumentTypeError("port must be between 1024 and 65535")
    return port


def make_message(msg_type: str, player_id: str, payload=None) -> dict:
    message = {
        "msg_type": msg_type,
        "player_id": player_id,
        "timestamp": int(time.time()),
    }
    if payload is not None:
        message["payload"] = payload
    return message


def send_message(sock: socket.socket, message: dict):
    data = json.dumps(
        message, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8") + b"\n"
    if len(data) > MAX_MESSAGE_SIZE:
        raise ValueError("message exceeds 8192 bytes")
    sock.sendall(data)


def receive_messages(sock: socket.socket, events: queue.Queue):
    """Background receiver that reconstructs complete NDJSON messages."""
    buffer = bytearray()
    try:
        while True:
            chunk = sock.recv(RECV_SIZE)
            if not chunk:
                events.put(None)
                return
            buffer.extend(chunk)
            while b"\n" in buffer:
                line, _, remainder = buffer.partition(b"\n")
                buffer = bytearray(remainder)
                if not line:
                    events.put({"msg_type": "LOCAL_ERROR",
                                "message": "Server sent an empty message."})
                    continue
                if len(line) + 1 > MAX_MESSAGE_SIZE:
                    events.put({"msg_type": "LOCAL_ERROR",
                                "message": "Server message exceeded 8192 bytes."})
                    return
                try:
                    message = json.loads(line.decode("utf-8"))
                    if not isinstance(message, dict):
                        raise ValueError("message must be a JSON object")
                    events.put(message)
                except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
                    events.put({"msg_type": "LOCAL_ERROR",
                                "message": "Received malformed server JSON."})
                    return
            if len(buffer) >= MAX_MESSAGE_SIZE:
                events.put({"msg_type": "LOCAL_ERROR",
                            "message": "Server message exceeded 8192 bytes."})
                return
    except OSError as exc:
        events.put({"msg_type": "LOCAL_ERROR",
                    "message": f"Network connection error: {exc}"})


def display_board(board):
    print("\n       1   2   3")
    for row in range(3):
        cells = board[row * 3:row * 3 + 3]
        print(f"    {row + 1}  " + " | ".join(cells))
        if row < 2:
            print("      ---+---+---")
    print()


def prompt_move(sock, alias):
    """Read a human-friendly 1-based row,column and send 0-based protocol data."""
    while True:
        try:
            entry = input("Your Turn - enter row,col (1-3), or q to quit: ").strip()
        except (EOFError, KeyboardInterrupt):
            entry = "q"
        if entry.lower() in ("q", "quit", "exit"):
            send_message(sock, make_message(
                "DISCONNECT", alias, {"reason": "QUIT"}
            ))
            return False
        try:
            parts = entry.replace(" ", "").split(",")
            if len(parts) != 2:
                raise ValueError
            row, col = (int(part) for part in parts)
            if not (1 <= row <= 3 and 1 <= col <= 3):
                raise ValueError
        except ValueError:
            print("Invalid input. Enter two numbers from 1-3, e.g. 2,3.")
            continue
        send_message(sock, make_message(
            "MOVE", alias, {"row": row - 1, "col": col - 1}
        ))
        return True


def main():
    parser = argparse.ArgumentParser(
        description="CS 457 networked Tic-Tac-Toe client"
    )
    parser.add_argument(
        "-i", "--ip", dest="hostname", required=True,
        help="server DNS hostname or IP address (example: server.blythe.edu)"
    )
    parser.add_argument(
        "-p", "--port", required=True, type=valid_port,
        help="server TCP port (1024-65535)"
    )
    args = parser.parse_args()

    # Required DNS behavior: resolve hostnames and handle DNS failures cleanly.
    try:
        server_ip = socket.gethostbyname(args.hostname)
    except socket.gaierror:
        print(f"[ERROR] Unable to resolve DNS hostname '{args.hostname}'.")
        print("Please check your Cisco router DNS configuration "
              "('ip dns server' / 'ip host') or verify domain spelling.")
        sys.exit(1)

    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.connect((server_ip, args.port))
    except OSError as exc:
        print(f"[ERROR] Unable to connect to server {args.hostname}:{args.port}.")
        print(f"Details: {exc}")
        print("Check that the server is running, the port is correct, "
              "and the network/firewall allows TCP connections.")
        sys.exit(1)

    print(f"Connected to {args.hostname} ({server_ip}) on port {args.port}.")
    alias = input("Enter your player name (1-32 characters): ").strip()
    while not 1 <= len(alias) <= 32:
        alias = input("Name must be 1-32 characters. Enter player name: ").strip()

    events = queue.Queue()
    receiver = threading.Thread(
        target=receive_messages, args=(sock, events), daemon=True
    )
    receiver.start()

    try:
        send_message(sock, make_message(
            "CONNECT", alias, {"player_alias": alias}
        ))
    except OSError as exc:
        print(f"[ERROR] Could not send CONNECT message: {exc}")
        sock.close()
        return

    role = None
    symbol = None
    board = ["-"] * 9
    can_move = False
    game_started = False

    try:
        while True:
            if can_move:
                can_move = False
                if not prompt_move(sock, alias):
                    print("You left the game.")
                    break
                continue

            try:
                message = events.get(timeout=0.5)
            except queue.Empty:
                continue

            if message is None:
                print("\n[INFO] Server closed the connection.")
                break

            msg_type = message.get("msg_type")
            payload = message.get("payload", {})

            if msg_type == "LOCAL_ERROR":
                print(f"[ERROR] {message.get('message')}")
                break
            if msg_type == "LOBBY_WAIT":
                print("[WAITING] Waiting for Player 2 to connect...")
            elif msg_type == "GAME_START":
                if alias == payload.get("player_1"):
                    role, symbol = "Player_1", "X"
                elif alias == payload.get("player_2"):
                    role, symbol = "Player_2", "O"
                else:
                    print("[ERROR] Server did not include your player name.")
                    break
                game_started = True
                print("\n[GAME START] You are", role, f"({symbol}).")
                print("Player 1 is X and moves first.")
                display_board(board)
                if role == "Player_1":
                    can_move = True
                else:
                    print("Waiting for Player 1...")
            elif msg_type == "STATE_UPDATE":
                board = payload.get("board", board)
                display_board(board)
                print("Score: Player 1 (X) = "
                      f"{payload.get('score_player_1', 0)} | "
                      "Player 2 (O) = "
                      f"{payload.get('score_player_2', 0)}")
                active = payload.get("active_player")
                if active == role:
                    print("Your Turn.")
                    can_move = True
                else:
                    print("Waiting for Player 2..." if active == "Player_2"
                          else "Waiting for Player 1...")
            elif msg_type == "ERROR":
                print(f"[SERVER ERROR] {payload.get('error_code')}: "
                      f"{payload.get('message')}")
                if (game_started and role == payload.get("active_player")):
                    can_move = True
                # Invalid moves do not change the turn. The player can retry.
                if payload.get("error_code") in ("INVALID_MOVE",):
                    can_move = (role is not None)
            elif msg_type == "GAME_OVER":
                print("\n========== GAME OVER ==========")
                result = payload.get("result")
                winner = payload.get("winner")
                if result == "DRAW":
                    print("The game is a draw!")
                elif result == "FORFEIT":
                    print(f"{winner} wins by forfeit.")
                elif result == "WINNER":
                    print(f"{winner} wins!")
                print("Final score: Player 1 = "
                      f"{payload.get('final_score_player_1', 0)} | "
                      "Player 2 = "
                      f"{payload.get('final_score_player_2', 0)}")
                break
    except (KeyboardInterrupt, OSError) as exc:
        print(f"\n[INFO] Client stopping: {exc}")
        try:
            send_message(sock, make_message(
                "DISCONNECT", alias, {"reason": "QUIT"}
            ))
        except OSError:
            pass
    finally:
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        sock.close()


if __name__ == "__main__":
    main()
