from __future__ import annotations

import argparse
import hashlib
import time
from collections import defaultdict
from pathlib import Path
from threading import Lock
from typing import Any

from flask import Flask, Response, abort, request
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_socketio import SocketIO, emit, join_room, leave_room

DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 8000
DEFAULT_WEB_CARD = "generated-bingo-card.html"
DEFAULT_ROOM = "default"
MAX_ROOM_LENGTH = 80
MAX_PASSWORD_LENGTH = 200

# Rate limiting constants
MAX_JOIN_ROOM_PER_MIN = 30  # Max 30 join attempts per minute per client
MAX_STATE_UPDATES_PER_MIN = 100  # Max 100 state updates per minute per client
MAX_HTTP_REQUESTS_PER_MIN = 600  # Max 600 HTTP requests per minute per IP


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
    
    # Configure Flask-Limiter for HTTP endpoints
    limiter = Limiter(
        app=app,
        key_func=get_remote_address,
        default_limits=[f"{MAX_HTTP_REQUESTS_PER_MIN}/minute"],
        storage_uri="memory://",
    )
    
    socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")

    room_records: dict[str, dict[str, Any]] = {}
    sid_to_room: dict[str, str] = {}
    state_lock = Lock()
    
    # Rate limiting for WebSocket events
    event_timestamps: dict[str, list[float]] = defaultdict(list)
    
    def check_rate_limit(sid: str, event_type: str, max_per_min: int) -> bool:
        """Check if a client has exceeded rate limit for an event type."""
        now = time.time()
        key = f"{sid}:{event_type}"
        
        # Clean old timestamps (older than 60 seconds)
        event_timestamps[key] = [ts for ts in event_timestamps[key] if now - ts < 60]
        
        if len(event_timestamps[key]) >= max_per_min:
            return False
        
        event_timestamps[key].append(now)
        return True

    @app.get("/")
    @limiter.limit(f"{MAX_HTTP_REQUESTS_PER_MIN}/minute")
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
    @limiter.limit(f"{MAX_HTTP_REQUESTS_PER_MIN}/minute")
    def health() -> Response:
        return Response("ok", mimetype="text/plain")

    @socketio.on("join_room")
    def on_join_room(payload: Any) -> None:
        # Rate limit join attempts
        if not check_rate_limit(request.sid, "join_room", MAX_JOIN_ROOM_PER_MIN):
            emit(
                "error",
                {"message": "Too many join attempts. Please slow down."},
            )
            return
        
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
        # Rate limit state updates
        if not check_rate_limit(request.sid, "state_update", MAX_STATE_UPDATES_PER_MIN):
            emit(
                "error",
                {"message": "Too many updates. Please slow down."},
            )
            return
        
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
        
        # Clean up rate limit records for this session
        for key in list(event_timestamps.keys()):
            if key.startswith(f"{request.sid}:"):
                del event_timestamps[key]

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
