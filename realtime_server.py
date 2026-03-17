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


def merge_state(
    existing_state: dict[str, Any] | None,
    incoming_state: dict[str, Any],
) -> dict[str, Any]:
    """Merge updates so concurrent clicks do not clobber each other.

    Rules:
    - Merge players by id (incoming metadata wins).
    - For marks, accept all non-null incoming marks.
    - For clears (incoming null), only clear if the mark owner is present in
      the incoming player list.
    """
    if existing_state is None:
        return incoming_state

    merged = dict(existing_state)

    # Build set of player IDs in the incoming state.
    incoming_player_ids = set()
    if "players" in incoming_state:
        incoming_players = incoming_state.get("players", [])
        if isinstance(incoming_players, list):
            for player in incoming_players:
                if isinstance(player, dict) and "id" in player:
                    incoming_player_ids.add(player["id"])

    # Merge player list by ID.
    if "players" in incoming_state:
        existing_players = existing_state.get("players", [])
        incoming_players = incoming_state.get("players", [])

        if isinstance(existing_players, list) and isinstance(incoming_players, list):
            players_by_id = {}
            for player in existing_players:
                if isinstance(player, dict) and "id" in player:
                    players_by_id[player["id"]] = player

            for player in incoming_players:
                if isinstance(player, dict) and "id" in player:
                    players_by_id[player["id"]] = player

            merged["players"] = [players_by_id[pid] for pid in sorted(players_by_id.keys())]

    # Merge marks without letting stale snapshots erase concurrent marks.
    if "marks" in incoming_state and "marks" in existing_state:
        existing_marks = existing_state.get("marks", [])
        incoming_marks = incoming_state.get("marks", [])

        if isinstance(existing_marks, list) and isinstance(incoming_marks, list):
            merged_marks = list(existing_marks)

            for i, incoming_mark in enumerate(incoming_marks):
                if i < len(merged_marks):
                    existing_mark = merged_marks[i]

                    if incoming_mark is not None:
                        merged_marks[i] = incoming_mark
                    elif existing_mark is not None:
                        if existing_mark in incoming_player_ids:
                            merged_marks[i] = None

            merged["marks"] = merged_marks

    return merged


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
            # Merge the incoming state with existing state to prevent race conditions
            room_record["state"] = merge_state(room_record["state"], state)

        # Broadcast canonical merged state to everyone in the room, including
        # the sender, so all clients converge after concurrent clicks.
        emit("state_update", {"room": room, "state": room_record["state"]}, to=room)

    @socketio.on("cell_update")
    def on_cell_update(payload: Any) -> None:
        # Reuse the same budget as state updates.
        if not check_rate_limit(request.sid, "state_update", MAX_STATE_UPDATES_PER_MIN):
            emit(
                "error",
                {"message": "Too many updates. Please slow down."},
            )
            return

        if not isinstance(payload, dict):
            return

        room = normalize_room(payload.get("room"))
        cell_index = payload.get("cell_index")
        marked_by = payload.get("marked_by")
        players = payload.get("players")

        if not isinstance(cell_index, int) or cell_index < 0:
            return
        if marked_by is not None and not isinstance(marked_by, str):
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

            room_state = room_record.get("state")
            if not isinstance(room_state, dict):
                room_state = {"players": [], "marks": []}

            marks = room_state.get("marks")
            if not isinstance(marks, list):
                marks = []

            if cell_index >= len(marks):
                marks.extend([None] * (cell_index + 1 - len(marks)))

            marks[cell_index] = marked_by
            room_state["marks"] = marks

            # Keep player metadata fresh without touching marks based on snapshots.
            if isinstance(players, list):
                room_state["players"] = players

            room_record["state"] = room_state

        emit("state_update", {"room": room, "state": room_record["state"]}, to=room)

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
