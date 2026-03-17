from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
from threading import Lock
from typing import Any

from flask import Flask, Response, abort, request
from flask_socketio import SocketIO, emit, join_room, leave_room

DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 8000
DEFAULT_WEB_CARD = "generated-bingo-card.html"
DEFAULT_ROOM = "default"
MAX_ROOM_LENGTH = 80
MAX_PASSWORD_LENGTH = 200


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Host the generated bingo card with realtime multiplayer sync."
    )
    parser.add_argument(
        "--web-card",
        default=DEFAULT_WEB_CARD,
        help="Path to the generated HTML bingo card.",
    )
    parser.add_argument(
        "--host",
        default=DEFAULT_HOST,
        help="Host/IP to bind the server to.",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=DEFAULT_PORT,
        help="Port to run the server on.",
    )
    return parser.parse_args()


def normalize_room(raw_room: Any) -> str:
    if not isinstance(raw_room, str):
        return DEFAULT_ROOM

    room = raw_room.strip()
    if not room:
        return DEFAULT_ROOM

    return room[:MAX_ROOM_LENGTH]


def normalize_password(raw_password: Any) -> str:
    if not isinstance(raw_password, str):
        return ""
    return raw_password[:MAX_PASSWORD_LENGTH]


def hash_password(password: str) -> str | None:
    if not password:
        return None
    return hashlib.sha256(password.encode("utf-8")).hexdigest()


def build_app(web_card_path: Path) -> tuple[Flask, SocketIO]:
    app = Flask(__name__)
    socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")

    room_records: dict[str, dict[str, Any]] = {}
    sid_to_room: dict[str, str] = {}
    state_lock = Lock()

    @app.get("/")
    def index() -> Response:
        if not web_card_path.exists():
            abort(
                404,
                description=(
                    f"Web card not found at '{web_card_path}'. "
                    "Run generate_bingo_card.py first."
                ),
            )
        return Response(web_card_path.read_text(encoding="utf-8"), mimetype="text/html")

    @app.get("/health")
    def health() -> Response:
        return Response("ok", mimetype="text/plain")

    @socketio.on("join_room")
    def on_join_room(payload: Any) -> None:
        room = normalize_room(payload.get("room") if isinstance(payload, dict) else None)
        password = normalize_password(payload.get("password") if isinstance(payload, dict) else None)
        password_hash = hash_password(password)

        with state_lock:
            room_record = room_records.get(room)
            if room_record is None:
                room_record = {
                    "password_hash": password_hash,
                    "state": None,
                }
                room_records[room] = room_record
            elif room_record["password_hash"] != password_hash:
                emit(
                    "auth_error",
                    {"room": room, "message": "Incorrect room password."},
                )
                return

            previous_room = sid_to_room.get(request.sid)
            sid_to_room[request.sid] = room

        if previous_room and previous_room != room:
            leave_room(previous_room)

        join_room(room)
        emit(
            "joined_room",
            {
                "room": room,
                "protected": room_record["password_hash"] is not None,
            },
        )

        room_state = room_record["state"]

        if room_state is None:
            emit("state_missing", {"room": room})
        else:
            emit("state_snapshot", {"room": room, "state": room_state})

    @socketio.on("state_update")
    def on_state_update(payload: Any) -> None:
        if not isinstance(payload, dict):
            return

        room = normalize_room(payload.get("room"))
        state = payload.get("state")
        if not isinstance(state, dict):
            return

        with state_lock:
            joined_room_name = sid_to_room.get(request.sid)
            if joined_room_name != room:
                emit(
                    "auth_error",
                    {"room": room, "message": "Join the room before sending updates."},
                )
                return

            room_record = room_records.setdefault(
                room,
                {"password_hash": None, "state": None},
            )
            room_record["state"] = state

        emit("state_update", {"room": room, "state": state}, to=room, include_self=False)

    @socketio.on("disconnect")
    def on_disconnect() -> None:
        with state_lock:
            sid_to_room.pop(request.sid, None)

    return app, socketio


def main() -> None:
    args = parse_args()
    web_card_path = Path(args.web_card).resolve()
    app, socketio = build_app(web_card_path)

    socketio.run(
        app,
        host=args.host,
        port=args.port,
        allow_unsafe_werkzeug=True,
    )


if __name__ == "__main__":
    main()
