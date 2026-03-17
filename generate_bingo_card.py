from __future__ import annotations

import argparse
import html
import random
import shutil
import webbrowser
from datetime import datetime
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


DEFAULT_TEMPLATE = "Bingo-Card-Template.jpg"
DEFAULT_CHALLENGES = "Challenge.txt"
DEFAULT_HARD_CHALLENGES = "Hard-Challenge.txt"
DEFAULT_OUTPUT = "generated-bingo-card.png"
DEFAULT_WEB_OUTPUT = "generated-bingo-card.html"
DEFAULT_ARCHIVE_DIR = "Old Generations"
GRID_SIZE = 5
FREE_SPACE = (GRID_SIZE // 2, GRID_SIZE // 2)
MIN_HARD_PER_BINGO = 1
MAX_HARD_PER_BINGO = 2
FULL_LENGTH_COVERAGE = 0.9
LINE_THRESHOLD = 180
FONT_CANDIDATES = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
)


def build_playable_positions(free_center: bool) -> list[tuple[int, int]]:
    return [
        (row_index, col_index)
        for row_index in range(GRID_SIZE)
        for col_index in range(GRID_SIZE)
        if not free_center or (row_index, col_index) != FREE_SPACE
    ]


def build_bingo_lines(
    playable_position_to_index: dict[tuple[int, int], int],
    free_center: bool,
) -> list[tuple[int, ...]]:
    lines: list[tuple[int, ...]] = []

    for row_index in range(GRID_SIZE):
        lines.append(
            tuple(
                playable_position_to_index[(row_index, col_index)]
                for col_index in range(GRID_SIZE)
                if not free_center or (row_index, col_index) != FREE_SPACE
            )
        )

    for col_index in range(GRID_SIZE):
        lines.append(
            tuple(
                playable_position_to_index[(row_index, col_index)]
                for row_index in range(GRID_SIZE)
                if not free_center or (row_index, col_index) != FREE_SPACE
            )
        )

    lines.append(
        tuple(
            playable_position_to_index[(offset, offset)]
            for offset in range(GRID_SIZE)
            if not free_center or (offset, offset) != FREE_SPACE
        )
    )
    lines.append(
        tuple(
            playable_position_to_index[(offset, GRID_SIZE - 1 - offset)]
            for offset in range(GRID_SIZE)
            if not free_center or (offset, GRID_SIZE - 1 - offset) != FREE_SPACE
        )
    )

    return lines


def compute_layout(
    free_center: bool,
) -> tuple[
    list[tuple[int, int]],
    dict[tuple[int, int], int],
    list[tuple[int, ...]],
    list[list[int]],
    int,
]:
    playable_positions = build_playable_positions(free_center)
    playable_position_to_index = {
        position: index for index, position in enumerate(playable_positions)
    }
    bingo_lines = build_bingo_lines(playable_position_to_index, free_center)
    playable_cell_count = len(playable_positions)
    lines_by_cell = [
        [line_index for line_index, line in enumerate(bingo_lines) if cell_index in line]
        for cell_index in range(playable_cell_count)
    ]
    return (
        playable_positions,
        playable_position_to_index,
        bingo_lines,
        lines_by_cell,
        playable_cell_count,
    )


PLAYABLE_POSITIONS, PLAYABLE_POSITION_TO_INDEX, BINGO_LINES, LINES_BY_CELL, PLAYABLE_CELL_COUNT = (
    compute_layout(free_center=True)
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate a bingo card image from a template and challenge list."
    )
    parser.add_argument(
        "--template",
        default=DEFAULT_TEMPLATE,
        help="Path to the bingo card template image.",
    )
    parser.add_argument(
        "--challenges",
        default=DEFAULT_CHALLENGES,
        help="Path to the challenge list text file.",
    )
    parser.add_argument(
        "--hard-challenges",
        default=DEFAULT_HARD_CHALLENGES,
        help="Path to the hard challenge list text file.",
    )
    parser.add_argument(
        "--output",
        default=DEFAULT_OUTPUT,
        help="Output image path.",
    )
    parser.add_argument(
        "--archive-dir",
        default=DEFAULT_ARCHIVE_DIR,
        help="Folder where previous generations are moved before overwriting output.",
    )
    parser.add_argument(
        "--web-output",
        default=DEFAULT_WEB_OUTPUT,
        help="Output HTML path for the interactive web bingo card.",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help="Optional random seed for reproducible cards.",
    )
    parser.add_argument(
        "--open-browser",
        dest="open_browser",
        action="store_true",
        default=True,
        help="Open the generated web bingo card in your default browser.",
    )
    parser.add_argument(
        "--no-open-browser",
        dest="open_browser",
        action="store_false",
        help="Generate the web card but do not open a browser window.",
    )
    parser.add_argument(
        "--free-center",
        dest="free_center",
        action="store_true",
        default=True,
        help="Use a free center tile (default).",
    )
    parser.add_argument(
        "--no-free-center",
        dest="free_center",
        action="store_false",
        help="Fill the center square from the challenge lists (default).",
    )
    parser.add_argument(
        "--padding",
        type=int,
        default=18,
        help="Inner padding for each bingo square.",
    )
    return parser.parse_args()


def load_challenges(path: Path) -> list[str]:
    challenges = [line.strip() for line in path.read_text(encoding="utf-8").splitlines()]
    return [line for line in challenges if line]


def choose_next_cell(
    assignments: list[int | None],
    line_unassigned_counts: list[int],
) -> int | None:
    best_cell: int | None = None
    best_score: tuple[int, int] | None = None

    for cell_index, assigned in enumerate(assignments):
        if assigned is not None:
            continue

        score = (
            sum(line_unassigned_counts[line_index] for line_index in LINES_BY_CELL[cell_index]),
            -len(LINES_BY_CELL[cell_index]),
        )
        if best_score is None or score < best_score:
            best_cell = cell_index
            best_score = score

    return best_cell


def build_hard_layout(
    target_hard_count: int,
    randomizer: random.Random | None,
) -> list[int] | None:
    assignments: list[int | None] = [None] * PLAYABLE_CELL_COUNT
    line_hard_counts = [0] * len(BINGO_LINES)
    line_unassigned_counts = [len(line) for line in BINGO_LINES]

    def is_feasible(
        cell_index: int,
        value: int,
        hard_used: int,
        assigned_count: int,
    ) -> bool:
        new_hard_used = hard_used + value
        remaining_cells_after = PLAYABLE_CELL_COUNT - (assigned_count + 1)

        if new_hard_used > target_hard_count:
            return False
        if new_hard_used + remaining_cells_after < target_hard_count:
            return False

        for line_index in LINES_BY_CELL[cell_index]:
            new_line_hard_count = line_hard_counts[line_index] + value
            new_line_unassigned_count = line_unassigned_counts[line_index] - 1

            if new_line_hard_count > MAX_HARD_PER_BINGO:
                return False
            if new_line_hard_count + new_line_unassigned_count < MIN_HARD_PER_BINGO:
                return False

        return True

    def solve(hard_used: int, assigned_count: int) -> bool:
        if assigned_count == PLAYABLE_CELL_COUNT:
            return hard_used == target_hard_count and all(
                MIN_HARD_PER_BINGO <= count <= MAX_HARD_PER_BINGO
                for count in line_hard_counts
            )

        cell_index = choose_next_cell(assignments, line_unassigned_counts)
        if cell_index is None:
            return False

        values = [0, 1]
        if randomizer is not None:
            randomizer.shuffle(values)

        for value in values:
            if not is_feasible(cell_index, value, hard_used, assigned_count):
                continue

            assignments[cell_index] = value
            for line_index in LINES_BY_CELL[cell_index]:
                line_unassigned_counts[line_index] -= 1
                line_hard_counts[line_index] += value

            if solve(hard_used + value, assigned_count + 1):
                return True

            for line_index in LINES_BY_CELL[cell_index]:
                line_unassigned_counts[line_index] += 1
                line_hard_counts[line_index] -= value
            assignments[cell_index] = None

        return False

    if not solve(hard_used=0, assigned_count=0):
        return None

    return [value for value in assignments if value is not None]


@lru_cache(maxsize=1)
def get_valid_hard_counts() -> tuple[int, ...]:
    valid_counts: list[int] = []

    for target_hard_count in range(PLAYABLE_CELL_COUNT + 1):
        if build_hard_layout(target_hard_count, randomizer=None) is not None:
            valid_counts.append(target_hard_count)

    return tuple(valid_counts)


def configure_layout(free_center: bool) -> None:
    global PLAYABLE_POSITIONS
    global PLAYABLE_POSITION_TO_INDEX
    global BINGO_LINES
    global LINES_BY_CELL
    global PLAYABLE_CELL_COUNT

    (
        PLAYABLE_POSITIONS,
        PLAYABLE_POSITION_TO_INDEX,
        BINGO_LINES,
        LINES_BY_CELL,
        PLAYABLE_CELL_COUNT,
    ) = compute_layout(free_center)
    get_valid_hard_counts.cache_clear()


def select_challenges(
    standard_challenges: list[str],
    hard_challenges: list[str],
    randomizer: random.Random,
) -> tuple[list[str], list[int], int]:
    valid_counts = [
        hard_count
        for hard_count in get_valid_hard_counts()
        if hard_count <= len(hard_challenges)
        and PLAYABLE_CELL_COUNT - hard_count <= len(standard_challenges)
    ]

    if not valid_counts:
        raise ValueError(
            "The challenge files do not contain enough entries to satisfy the bingo constraints. "
            f"Need at least {MIN_HARD_PER_BINGO} and at most {MAX_HARD_PER_BINGO} hard challenges "
            "in every possible bingo line."
        )

    hard_count = randomizer.choice(valid_counts)
    hard_layout = build_hard_layout(hard_count, randomizer=randomizer)
    if hard_layout is None:
        raise ValueError("Could not build a valid hard challenge layout for the bingo card.")

    chosen_standard = iter(
        randomizer.sample(standard_challenges, PLAYABLE_CELL_COUNT - hard_count)
    )
    chosen_hard = iter(randomizer.sample(hard_challenges, hard_count))

    selected_challenges: list[str] = []
    for is_hard in hard_layout:
        if is_hard:
            selected_challenges.append(next(chosen_hard))
        else:
            selected_challenges.append(next(chosen_standard))

    return selected_challenges, hard_layout, hard_count


def cluster_positions(positions: list[int]) -> list[tuple[int, int]]:
    if not positions:
        return []

    clusters: list[tuple[int, int]] = []
    start = positions[0]
    previous = positions[0]

    for position in positions[1:]:
        if position == previous + 1:
            previous = position
            continue
        clusters.append((start, previous))
        start = previous = position

    clusters.append((start, previous))
    return clusters


def detect_grid_bands(template: Image.Image) -> tuple[list[tuple[int, int]], list[tuple[int, int]]]:
    grayscale = template.convert("L")
    pixels = grayscale.load()
    width, height = grayscale.size

    row_positions = [
        y
        for y in range(height)
        if sum(1 for x in range(width) if pixels[x, y] < LINE_THRESHOLD) > width * FULL_LENGTH_COVERAGE
    ]
    col_positions = [
        x
        for x in range(width)
        if sum(1 for y in range(height) if pixels[x, y] < LINE_THRESHOLD) > height * FULL_LENGTH_COVERAGE
    ]

    row_bands = cluster_positions(row_positions)
    col_bands = cluster_positions(col_positions)

    expected_bands = GRID_SIZE + 1
    if len(row_bands) != expected_bands or len(col_bands) != expected_bands:
        raise ValueError(
            "Could not detect the bingo grid from the template image. "
            f"Expected {expected_bands} row and column bands, found "
            f"{len(row_bands)} rows and {len(col_bands)} columns."
        )

    return row_bands, col_bands


def build_cell_boxes(
    row_bands: list[tuple[int, int]],
    col_bands: list[tuple[int, int]],
    padding: int,
) -> list[tuple[int, int, int, int]]:
    boxes: list[tuple[int, int, int, int]] = []

    for row_index in range(GRID_SIZE):
        top = row_bands[row_index][1] + 1 + padding
        bottom = row_bands[row_index + 1][0] - 1 - padding
        for col_index in range(GRID_SIZE):
            left = col_bands[col_index][1] + 1 + padding
            right = col_bands[col_index + 1][0] - 1 - padding
            boxes.append((left, top, right, bottom))

    return boxes


def find_font_path() -> Path | None:
    for candidate in FONT_CANDIDATES:
        path = Path(candidate)
        if path.exists():
            return path
    return None


def text_width(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont) -> int:
    bbox = draw.textbbox((0, 0), text, font=font)
    return bbox[2] - bbox[0]


def split_long_word(
    draw: ImageDraw.ImageDraw,
    word: str,
    font: ImageFont.ImageFont,
    max_width: int,
) -> list[str]:
    pieces: list[str] = []
    current = ""

    for character in word:
        candidate = f"{current}{character}"
        if current and text_width(draw, candidate, font) > max_width:
            pieces.append(current)
            current = character
        else:
            current = candidate

    if current:
        pieces.append(current)

    return pieces


def wrap_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.ImageFont,
    max_width: int,
) -> list[str]:
    words = " ".join(text.split()).split(" ")
    lines: list[str] = []
    current_line = ""

    for word in words:
        candidate = word if not current_line else f"{current_line} {word}"
        if text_width(draw, candidate, font) <= max_width:
            current_line = candidate
            continue

        if current_line:
            lines.append(current_line)
            current_line = ""

        if text_width(draw, word, font) <= max_width:
            current_line = word
            continue

        split_word = split_long_word(draw, word, font, max_width)
        lines.extend(split_word[:-1])
        current_line = split_word[-1]

    if current_line:
        lines.append(current_line)

    return lines


def fit_text_to_box(
    draw: ImageDraw.ImageDraw,
    text: str,
    box: tuple[int, int, int, int],
    font_path: Path | None,
) -> tuple[ImageFont.ImageFont, str, int]:
    max_width = box[2] - box[0]
    max_height = box[3] - box[1]

    if font_path is None:
        font = ImageFont.load_default()
        wrapped_text = "\n".join(wrap_text(draw, text, font, max_width))
        return font, wrapped_text, 4

    max_font_size = min(72, max(18, min(max_width, max_height) // 2))
    min_font_size = 12
    fallback_font: ImageFont.ImageFont | None = None
    fallback_text = text
    fallback_spacing = 4

    for size in range(max_font_size, min_font_size - 1, -1):
        font = ImageFont.truetype(str(font_path), size=size)
        wrapped_lines = wrap_text(draw, text, font, max_width)
        wrapped_text = "\n".join(wrapped_lines)
        spacing = max(4, size // 6)
        bbox = draw.multiline_textbbox(
            (0, 0),
            wrapped_text,
            font=font,
            spacing=spacing,
            align="center",
        )
        width = bbox[2] - bbox[0]
        height = bbox[3] - bbox[1]

        fallback_font = font
        fallback_text = wrapped_text
        fallback_spacing = spacing

        if width <= max_width and height <= max_height:
            return font, wrapped_text, spacing

    if fallback_font is None:
        raise ValueError("Unable to load a usable font for drawing challenge text.")

    return fallback_font, fallback_text, fallback_spacing


def draw_centered_text(
    draw: ImageDraw.ImageDraw,
    box: tuple[int, int, int, int],
    text: str,
    font_path: Path | None,
) -> None:
    font, wrapped_text, spacing = fit_text_to_box(draw, text, box, font_path)
    bbox = draw.multiline_textbbox(
        (0, 0),
        wrapped_text,
        font=font,
        spacing=spacing,
        align="center",
    )
    text_width_value = bbox[2] - bbox[0]
    text_height_value = bbox[3] - bbox[1]
    box_width = box[2] - box[0]
    box_height = box[3] - box[1]
    x = box[0] + (box_width - text_width_value) / 2 - bbox[0]
    y = box[1] + (box_height - text_height_value) / 2 - bbox[1]
    draw.multiline_text(
        (x, y),
        wrapped_text,
        fill="black",
        font=font,
        spacing=spacing,
        align="center",
    )


def build_archive_path(output_path: Path, archive_dir: Path) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    candidate = archive_dir / f"{output_path.stem}-{timestamp}{output_path.suffix}"
    counter = 1

    while candidate.exists():
        candidate = archive_dir / f"{output_path.stem}-{timestamp}-{counter}{output_path.suffix}"
        counter += 1

    return candidate


def archive_existing_output(output_path: Path, archive_dir_name: str) -> None:
    if not output_path.exists():
        return

    archive_dir = output_path.parent / archive_dir_name
    archive_dir.mkdir(parents=True, exist_ok=True)
    archived_output = build_archive_path(output_path, archive_dir)
    shutil.move(str(output_path), str(archived_output))


def build_web_card_html(
    selected_challenges: list[str],
    hard_layout: list[int],
    free_center: bool,
) -> str:
    cells: list[str] = []
    challenge_index = 0

    for _row_index in range(GRID_SIZE):
        for _col_index in range(GRID_SIZE):
            if free_center and (_row_index, _col_index) == FREE_SPACE:
                cells.append(
                    '<button type="button" class="cell free completed" title="Free space">'
                    '<span class="cell-label">FREE</span>'
                    '</button>'
                )
                continue

            challenge_text = html.escape(selected_challenges[challenge_index])
            is_hard = bool(hard_layout[challenge_index])
            hard_class = " hard" if is_hard else ""
            hard_badge = '<span class="hard-badge">HARD</span>' if is_hard else ""
            cells.append(
                '<button type="button" class="cell{hard_class}" title="Click to mark for active player">'
                '<span class="cell-label">{challenge_text}</span>{hard_badge}'
                '</button>'.format(
                    hard_class=hard_class,
                    challenge_text=challenge_text,
                    hard_badge=hard_badge,
                )
            )
            challenge_index += 1

    return f"""<!doctype html>
<html lang=\"en\">
<head>
    <meta charset=\"utf-8\">
    <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">
    <title>Bingo Card</title>
    <style>
        :root {{
            --bg-top: #f7efe3;
            --bg-bottom: #e6dac6;
            --board-bg: #fffef9;
            --panel-bg: #fdf6e9;
            --board-line: #3f2d1d;
            --text: #2f2419;
            --done-bg: #ffe082;
            --hard-bg: #fbe6d5;
            --hard-pill: #7a2f0b;
            --accent: #2a7f62;
        }}

        * {{ box-sizing: border-box; }}

        body {{
            margin: 0;
            min-height: 100vh;
            font-family: "Trebuchet MS", "Segoe UI", sans-serif;
            color: var(--text);
            background: linear-gradient(180deg, var(--bg-top), var(--bg-bottom));
            padding: 1.5rem;
        }}

        .page {{
            width: min(1300px, 100%);
            margin: 0 auto;
            display: grid;
            grid-template-columns: minmax(240px, 300px) 1fr;
            gap: 1.25rem;
            align-items: start;
        }}

        .sidebar {{
            background: var(--panel-bg);
            border: 3px solid var(--board-line);
            border-radius: 14px;
            box-shadow: 0 12px 24px rgba(63, 45, 29, 0.2);
            padding: 1rem;
            position: sticky;
            top: 1rem;
        }}

        .sidebar h2 {{
            margin: 0;
            text-transform: uppercase;
            letter-spacing: 0.05em;
            font-size: 1.2rem;
        }}

        .sidebar-hint {{
            margin: 0.45rem 0 0.9rem;
            font-size: 0.9rem;
            line-height: 1.35;
        }}

        .players-list {{
            display: grid;
            gap: 0.75rem;
            margin-bottom: 0.85rem;
        }}

        .player-card {{
            border: 2px solid #ccb79a;
            border-radius: 10px;
            background: #fffdfa;
            padding: 0.65rem;
            display: grid;
            gap: 0.35rem;
        }}

        .player-card.active {{
            border-color: var(--board-line);
            box-shadow: inset 0 0 0 1px rgba(63, 45, 29, 0.25);
        }}

        .player-top {{
            display: flex;
            align-items: center;
            gap: 0.45rem;
            font-size: 0.85rem;
            font-weight: 700;
        }}

        .field-label {{
            font-size: 0.75rem;
            font-weight: 700;
            text-transform: uppercase;
            letter-spacing: 0.05em;
            opacity: 0.85;
        }}

        .name-input {{
            width: 100%;
            border: 1px solid #9f8463;
            border-radius: 7px;
            padding: 0.35rem 0.5rem;
            font: inherit;
            font-size: 0.92rem;
            color: inherit;
            background: #fff;
        }}

        .color-input {{
            width: 100%;
            height: 2rem;
            border: 1px solid #9f8463;
            border-radius: 7px;
            padding: 0.15rem;
            background: #fff;
            cursor: pointer;
        }}

        .remove-button,
        .remove-btn {{
            align-self: start;
            border: 1px solid #9f8463;
            background: #fff5e6;
            color: var(--text);
            border-radius: 6px;
            padding: 0.35rem 0.5rem;
            font-size: 0.8rem;
            font-weight: 600;
            cursor: pointer;
            transition: background-color 0.12s ease;
        }}

        .remove-button:hover,
        .remove-btn:hover {{
            background: #ffd699;
        }}

        .wrap {{
            width: 100%;
            display: grid;
            gap: 1rem;
            justify-items: center;
        }}

        h1 {{
            margin: 0;
            letter-spacing: 0.06em;
            text-transform: uppercase;
            font-size: clamp(1.3rem, 2vw + 1rem, 2rem);
        }}

        .controls {{
            display: flex;
            gap: 0.75rem;
            flex-wrap: wrap;
            justify-content: center;
        }}

        .control-btn {{
            border: 2px solid var(--board-line);
            background: var(--board-bg);
            color: var(--text);
            border-radius: 999px;
            padding: 0.45rem 0.95rem;
            font-size: 0.95rem;
            cursor: pointer;
            transition: transform 0.12s ease, background-color 0.12s ease;
        }}

        .control-btn:hover {{
            transform: translateY(-1px);
            background: #fdf2df;
        }}

        .room-panel {{
            width: min(900px, 100%);
            display: grid;
            grid-template-columns: minmax(170px, 1.2fr) minmax(170px, 1.2fr) auto auto;
            gap: 0.75rem;
            align-items: end;
        }}

        .room-field {{
            display: grid;
            gap: 0.3rem;
        }}

        .room-input {{
            width: 100%;
            border: 2px solid #9f8463;
            border-radius: 10px;
            padding: 0.55rem 0.7rem;
            font: inherit;
            font-size: 0.95rem;
            color: inherit;
            background: #fffdfa;
        }}

        .room-message {{
            width: min(900px, 100%);
            margin: 0;
            font-size: 0.9rem;
            text-align: center;
            min-height: 1.3rem;
            opacity: 0.92;
        }}

        .room-message.success {{
            color: #0f5132;
        }}

        .room-message.error {{
            color: #842029;
        }}

        .connection-status {{
            border: 2px solid var(--board-line);
            border-radius: 999px;
            padding: 0.4rem 0.8rem;
            font-size: 0.85rem;
            font-weight: 700;
            background: #fffdfa;
        }}

        .connection-status.connected {{
            background: #d8f4e8;
            color: #0f5132;
            border-color: #0f5132;
        }}

        .connection-status.offline {{
            background: #ffe5e1;
            color: #842029;
            border-color: #842029;
        }}

        .board {{
            width: min(86vmin, 900px);
            aspect-ratio: 1 / 1;
            background: var(--board-bg);
            border: 5px solid var(--board-line);
            border-radius: 14px;
            display: grid;
            grid-template-columns: repeat(5, 1fr);
            box-shadow: 0 16px 30px rgba(63, 45, 29, 0.22);
            overflow: hidden;
        }}

        .cell {{
            border: 1px solid var(--board-line);
            background: #fff;
            color: var(--text);
            padding: 0.55rem;
            cursor: pointer;
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: center;
            text-align: center;
            gap: 0.35rem;
            font-weight: 600;
            font-size: clamp(0.62rem, 0.66vw + 0.48rem, 1.02rem);
            line-height: 1.25;
            transition: background-color 0.14s ease, transform 0.08s ease;
        }}

        .cell:hover {{
            transform: scale(0.995);
            background: #fff6e7;
        }}

        .cell.hard {{ background: var(--hard-bg); }}

        .cell.completed {{
            background: var(--done-bg);
            text-decoration: line-through;
        }}

        .cell.marked {{
            box-shadow: inset 0 0 0 2px rgba(63, 45, 29, 0.32);
        }}

        .cell.free {{
            background: #eaf6f0;
            border-width: 2px;
            font-size: clamp(0.8rem, 0.85vw + 0.55rem, 1.2rem);
            color: var(--accent);
            text-decoration: none;
        }}

        .hard-badge {{
            font-size: 0.68em;
            font-weight: 700;
            letter-spacing: 0.06em;
            border: 1px solid var(--hard-pill);
            color: var(--hard-pill);
            border-radius: 999px;
            padding: 0.08rem 0.42rem;
        }}

        .hint {{
            margin: 0;
            font-size: 0.9rem;
            opacity: 0.9;
            text-align: center;
        }}

        @media (max-width: 980px) {{
            .page {{
                grid-template-columns: 1fr;
            }}

            .sidebar {{
                position: static;
            }}

            .board {{
                width: min(92vmin, 900px);
            }}

            .room-panel {{
                grid-template-columns: 1fr;
            }}
        }}
    </style>
</head>
<body>
    <div class=\"page\">
        <aside class=\"sidebar\" aria-label=\"Players\">
            <h2>Players</h2>
            <p class=\"sidebar-hint\">Choose the active player, edit their name and color, then click a square to shade it.</p>
            <section id=\"players-list\" class=\"players-list\" aria-label=\"Player list\"></section>
            <button id=\"add-player\" type=\"button\" class=\"control-btn\">Add Player</button>
        </aside>

        <main class=\"wrap\">
            <h1>Bingo Card</h1>
            <div class="room-panel" aria-label="Room controls">
                <label class="room-field" for="room-name">
                    <span class="field-label">Room</span>
                    <input id="room-name" class="room-input" type="text" maxlength="80" placeholder="default">
                </label>
                <label class="room-field" for="room-password">
                    <span class="field-label">Password</span>
                    <input id="room-password" class="room-input" type="password" maxlength="200" placeholder="Optional room password">
                </label>
                <button id="join-room" type="button" class="control-btn">Join Room</button>
                <button id="copy-room-link" type="button" class="control-btn">Copy Room Link</button>
            </div>
            <p id="room-message" class="room-message">Copying the room link shares the room name only. Share the password separately.</p>
            <div class=\"controls\">
                <button id=\"clear-completed\" type=\"button\" class=\"control-btn\">Clear Marks</button>
                <span id="connection-status" class="connection-status offline">Offline</span>
            </div>
            <section class=\"board\" id=\"bingo-board\" aria-label=\"Interactive bingo card\">
                {''.join(cells)}
            </section>
            <p class=\"hint\">Click any square to mark or unmark it for the active player.</p>
        </main>
    </div>
    <script src="https://cdn.socket.io/4.7.5/socket.io.min.js" crossorigin="anonymous"></script>
    <script>
        const board = document.getElementById('bingo-board');
        const clearButton = document.getElementById('clear-completed');
        const playersList = document.getElementById('players-list');
        const addPlayerButton = document.getElementById('add-player');
        const roomNameInput = document.getElementById('room-name');
        const roomPasswordInput = document.getElementById('room-password');
        const joinRoomButton = document.getElementById('join-room');
        const copyRoomLinkButton = document.getElementById('copy-room-link');
        const roomMessage = document.getElementById('room-message');
        const connectionStatus = document.getElementById('connection-status');
        const starterColors = ['#4f7cff', '#e56b6f', '#2a9d8f', '#f4a261', '#6a4c93', '#118ab2'];

        const initialRoomId = new URLSearchParams(window.location.search).get('room') || 'default';
        let playerCounter = 0;
        let activePlayerId = null;
        let socket = null;
        let applyingRemoteState = false;
        let pendingJoin = false;
        let desiredRoomId = initialRoomId;
        let desiredRoomPassword = '';
        let joinedRoomId = null;

        const cells = Array.from(board.querySelectorAll('.cell'));
        for (let index = 0; index < cells.length; index += 1) {{
            cells[index].dataset.cellIndex = String(index);
        }}

        function setConnectionStatus(message, variant) {{
            connectionStatus.classList.remove('connected', 'offline');
            connectionStatus.classList.add(variant);
            connectionStatus.textContent = message;
        }}

        function setRoomMessage(message, variant) {{
            roomMessage.textContent = message;
            roomMessage.classList.remove('success', 'error');
            if (variant) {{
                roomMessage.classList.add(variant);
            }}
        }}

        function getSelectedRoomId() {{
            const trimmed = roomNameInput.value.trim();
            const roomName = trimmed || 'default';
            roomNameInput.value = roomName;
            return roomName;
        }}

        function buildRoomUrl(roomName) {{
            const url = new URL(window.location.href);
            url.searchParams.set('room', roomName);
            return url.toString();
        }}

        async function copyText(text) {{
            if (navigator.clipboard && window.isSecureContext) {{
                await navigator.clipboard.writeText(text);
                return;
            }}

            const tempInput = document.createElement('textarea');
            tempInput.value = text;
            tempInput.setAttribute('readonly', 'readonly');
            tempInput.style.position = 'absolute';
            tempInput.style.left = '-9999px';
            document.body.appendChild(tempInput);
            tempInput.select();
            document.execCommand('copy');
            tempInput.remove();
        }}

        function preferredTextColor(hexColor) {{
            const clean = hexColor.replace('#', '');
            const value = Number.parseInt(clean, 16);
            if (Number.isNaN(value)) return '#1d1308';
            const r = (value >> 16) & 255;
            const g = (value >> 8) & 255;
            const b = value & 255;
            const luminance = (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255;
            return luminance > 0.62 ? '#1d1308' : '#ffffff';
        }}

        function getPlayerCard(playerId) {{
            return playersList.querySelector('.player-card[data-player-id="' + playerId + '"]');
        }}

        function refreshActivePlayerStyles() {{
            for (const card of playersList.querySelectorAll('.player-card')) {{
                card.classList.toggle('active', card.dataset.playerId === activePlayerId);
            }}
        }}

        function getPlayerState(playerId) {{
            const card = getPlayerCard(playerId);
            if (!card) return null;
            const nameInput = card.querySelector('.name-input');
            const colorInput = card.querySelector('.color-input');
            return {{
                id: playerId,
                name: nameInput.value.trim() || 'Player',
                color: colorInput.value,
            }};
        }}

        function clearCellMark(cell) {{
            delete cell.dataset.markedBy;
            cell.classList.remove('marked');
            cell.style.backgroundColor = '';
            cell.style.color = '';
            cell.title = 'Click to mark for active player';
        }}

        function updateMarkedCellTitles(playerId, playerName) {{
            for (const cell of board.querySelectorAll('.cell[data-marked-by="' + playerId + '"]')) {{
                cell.title = 'Marked by ' + playerName;
            }}
        }}

        function applyPlayerColorToExistingMarks(playerId, color) {{
            const textColor = preferredTextColor(color);
            for (const cell of board.querySelectorAll('.cell[data-marked-by="' + playerId + '"]')) {{
                cell.style.backgroundColor = color;
                cell.style.color = textColor;
            }}
        }}

        function markCellForPlayer(cell, playerId) {{
            const player = getPlayerState(playerId);
            if (!player) return;
            cell.dataset.markedBy = player.id;
            cell.classList.add('marked');
            cell.style.backgroundColor = player.color;
            cell.style.color = preferredTextColor(player.color);
            cell.title = 'Marked by ' + player.name;
        }}

        function createPlayer(name, color, canRemove, forcedPlayerId) {{
            let playerId = forcedPlayerId;
            if (!playerId) {{
                playerCounter += 1;
                playerId = 'player-' + playerCounter;
            }} else {{
                const suffix = Number.parseInt(playerId.replace('player-', ''), 10);
                if (!Number.isNaN(suffix)) {{
                    playerCounter = Math.max(playerCounter, suffix);
                }}
            }}

            const activeId = 'active-' + playerId;
            const nameId = 'name-' + playerId;
            const colorId = 'color-' + playerId;

            const card = document.createElement('article');
            card.className = 'player-card';
            card.dataset.playerId = playerId;

            let htmlContent =
                '<div class="player-top">' +
                    '<input type="radio" class="player-active" name="active-player" id="' + activeId + '" value="' + playerId + '">' +
                    '<label for="' + activeId + '">Active</label>' +
                '</div>' +
                '<label class="field-label" for="' + nameId + '">Name</label>' +
                '<input id="' + nameId + '" class="name-input" type="text" value="' + name + '">' +
                '<label class="field-label" for="' + colorId + '">Color</label>' +
                '<input id="' + colorId + '" class="color-input" type="color" value="' + color + '">';

            if (canRemove) {{
                htmlContent += '<button type="button" class="remove-btn" aria-label="Remove player">Remove</button>';
            }}

            card.innerHTML = htmlContent;
            playersList.appendChild(card);

            const radio = card.querySelector('.player-active');
            const nameInput = card.querySelector('.name-input');
            const colorInput = card.querySelector('.color-input');
            const removeBtn = card.querySelector('.remove-btn');

            radio.addEventListener('change', () => {{
                if (radio.checked) {{
                    activePlayerId = playerId;
                    refreshActivePlayerStyles();
                }}
            }});

            nameInput.addEventListener('input', () => {{
                const nextName = nameInput.value.trim() || 'Player';
                updateMarkedCellTitles(playerId, nextName);
            }});

            nameInput.addEventListener('change', () => {{
                broadcastState();
            }});

            nameInput.addEventListener('blur', () => {{
                broadcastState();
            }});

            colorInput.addEventListener('input', () => {{
                applyPlayerColorToExistingMarks(playerId, colorInput.value);
                broadcastState();
            }});

            card.addEventListener('click', (event) => {{
                if (event.target.classList.contains('name-input') || event.target.classList.contains('color-input')) {{
                    return;
                }}
                if (event.target.classList.contains('remove-btn')) {{
                    return;
                }}
                radio.checked = true;
                activePlayerId = playerId;
                refreshActivePlayerStyles();
            }});

            if (removeBtn) {{
                removeBtn.addEventListener('click', () => {{
                    for (const cell of board.querySelectorAll('.cell[data-marked-by="' + playerId + '"]')) {{
                        clearCellMark(cell);
                    }}
                    if (activePlayerId === playerId) {{
                        const remainingRadios = playersList.querySelectorAll('.player-active');
                        if (remainingRadios.length > 1) {{
                            const next = remainingRadios[0].value === playerId ? remainingRadios[1] : remainingRadios[0];
                            next.checked = true;
                            activePlayerId = next.value;
                        }} else {{
                            activePlayerId = null;
                        }}
                    }}
                    card.remove();
                    refreshActivePlayerStyles();
                    broadcastState();
                }});
            }}

            if (activePlayerId === null) {{
                radio.checked = true;
                activePlayerId = playerId;
                refreshActivePlayerStyles();
            }}
        }}

        function collectState() {{
            const players = [];
            for (const card of playersList.querySelectorAll('.player-card')) {{
                const playerId = card.dataset.playerId;
                const name = card.querySelector('.name-input').value;
                const color = card.querySelector('.color-input').value;
                players.push({{ id: playerId, name: name, color: color }});
            }}

            const marks = [];
            for (const cell of board.querySelectorAll('.cell')) {{
                marks.push(cell.dataset.markedBy || null);
            }}

            // activePlayerId is intentionally excluded — each device tracks
            // its own active player independently.
            return {{
                players: players,
                marks: marks,
            }};
        }}

        function applyState(state) {{
            if (!state || !Array.isArray(state.players)) {{
                return;
            }}

            // Remember which player this device had selected so we can
            // restore it after rebuilding the list from shared state.
            const savedActivePlayerId = activePlayerId;

            playersList.innerHTML = '';
            playerCounter = 0;
            activePlayerId = null;

            if (state.players.length === 0) {{
                createPlayer('Player 1', starterColors[0], false, null);
            }} else {{
                for (let index = 0; index < state.players.length; index += 1) {{
                    const player = state.players[index];
                    const fallbackName = 'Player ' + (index + 1);
                    const fallbackColor = starterColors[index % starterColors.length];
                    createPlayer(
                        player.name || fallbackName,
                        player.color || fallbackColor,
                        index > 0,
                        player.id || null
                    );
                }}
            }}

            for (const cell of board.querySelectorAll('.cell[data-marked-by]')) {{
                clearCellMark(cell);
            }}

            if (Array.isArray(state.marks)) {{
                const cells = board.querySelectorAll('.cell');
                const maxLength = Math.min(cells.length, state.marks.length);
                for (let index = 0; index < maxLength; index += 1) {{
                    const markedBy = state.marks[index];
                    if (!markedBy) {{
                        continue;
                    }}
                    if (getPlayerCard(markedBy)) {{
                        markCellForPlayer(cells[index], markedBy);
                    }}
                }}
            }}

            // Restore this device's own active-player selection if that
            // player still exists; otherwise keep whichever player
            // createPlayer() defaulted to (the first one).
            if (savedActivePlayerId && getPlayerCard(savedActivePlayerId)) {{
                activePlayerId = savedActivePlayerId;
            }}

            if (activePlayerId) {{
                const activeRadio = playersList.querySelector('.player-active[value="' + activePlayerId + '"]');
                if (activeRadio) {{
                    activeRadio.checked = true;
                }}
            }}

            refreshActivePlayerStyles();
        }}

        function broadcastState() {{
            if (applyingRemoteState || !socket || !socket.connected || !joinedRoomId) {{
                return;
            }}
            socket.emit('state_update', {{ room: joinedRoomId, state: collectState() }});
        }}

        function emitCellUpdate(cellIndex, markedBy) {{
            if (applyingRemoteState || !socket || !socket.connected || !joinedRoomId) {{
                return;
            }}
            const state = collectState();
            socket.emit('cell_update', {{
                room: joinedRoomId,
                cell_index: cellIndex,
                marked_by: markedBy,
                players: state.players,
            }});
        }}

        function applyRemoteState(state) {{
            applyingRemoteState = true;
            try {{
                applyState(state);
            }} finally {{
                applyingRemoteState = false;
            }}
        }}

        function emitJoinRequest() {{
            if (!socket || !socket.connected) {{
                return;
            }}
            pendingJoin = false;
            setConnectionStatus('Joining ' + desiredRoomId + '...', 'offline');
            socket.emit('join_room', {{
                room: desiredRoomId,
                password: desiredRoomPassword,
            }});
        }}

        function queueRoomJoin() {{
            desiredRoomId = getSelectedRoomId();
            desiredRoomPassword = roomPasswordInput.value;
            pendingJoin = true;
            window.history.replaceState(null, '', buildRoomUrl(desiredRoomId));
            setRoomMessage('Connecting to room ' + desiredRoomId + '.', null);

            if (socket && socket.connected) {{
                emitJoinRequest();
            }} else {{
                setConnectionStatus('Connecting...', 'offline');
            }}
        }}

        function setupRealtime() {{
            if (typeof io !== 'function') {{
                setConnectionStatus('Realtime unavailable', 'offline');
                setRoomMessage('Socket.IO failed to load, so live sync is unavailable.', 'error');
                return;
            }}

            socket = io();
            socket.on('connect', () => {{
                setConnectionStatus('Connected to server', 'connected');
                if (!pendingJoin) {{
                    pendingJoin = true;
                }}
                emitJoinRequest();
            }});

            socket.on('disconnect', () => {{
                joinedRoomId = null;
                setConnectionStatus('Offline', 'offline');
            }});

            socket.on('connect_error', () => {{
                joinedRoomId = null;
                setConnectionStatus('Connection failed', 'offline');
            }});

            socket.on('joined_room', (payload) => {{
                if (!payload || payload.room !== desiredRoomId) {{
                    return;
                }}
                joinedRoomId = payload.room;
                roomNameInput.value = payload.room;
                setConnectionStatus(
                    payload.protected
                        ? 'Live Room: ' + payload.room + ' (Locked)'
                        : 'Live Room: ' + payload.room,
                    'connected'
                );
                setRoomMessage(
                    payload.protected
                        ? 'Joined protected room. Share the password separately.'
                        : 'Joined room successfully.',
                    'success'
                );
            }});

            socket.on('auth_error', (payload) => {{
                if (!payload || payload.room !== desiredRoomId) {{
                    return;
                }}
                joinedRoomId = null;
                setConnectionStatus('Room access denied', 'offline');
                setRoomMessage(payload.message || 'Incorrect room password.', 'error');
            }});

            socket.on('state_snapshot', (payload) => {{
                if (!payload || payload.room !== joinedRoomId) {{
                    return;
                }}
                applyRemoteState(payload.state);
            }});

            socket.on('state_update', (payload) => {{
                if (!payload || payload.room !== joinedRoomId) {{
                    return;
                }}
                applyRemoteState(payload.state);
            }});

            socket.on('state_missing', (payload) => {{
                if (!payload || payload.room !== joinedRoomId) {{
                    return;
                }}
                broadcastState();
            }});
        }}

        joinRoomButton.addEventListener('click', () => {{
            queueRoomJoin();
        }});

        copyRoomLinkButton.addEventListener('click', async () => {{
            const roomName = getSelectedRoomId();
            await copyText(buildRoomUrl(roomName));
            setRoomMessage('Room link copied. Share the password separately.', 'success');
        }});

        roomNameInput.addEventListener('keydown', (event) => {{
            if (event.key === 'Enter') {{
                queueRoomJoin();
            }}
        }});

        roomPasswordInput.addEventListener('keydown', (event) => {{
            if (event.key === 'Enter') {{
                queueRoomJoin();
            }}
        }});

        addPlayerButton.addEventListener('click', () => {{
            const nextPlayerNumber = playerCounter + 1;
            const colorIndex = (nextPlayerNumber - 1) % starterColors.length;
            createPlayer('Player ' + nextPlayerNumber, starterColors[colorIndex], true, null);
            broadcastState();
        }});

        board.addEventListener('click', (event) => {{
            const cell = event.target.closest('.cell');
            if (!cell) return;
            if (!activePlayerId) return;

            const cellIndex = Number.parseInt(cell.dataset.cellIndex || '', 10);
            if (Number.isNaN(cellIndex)) return;

            if (cell.dataset.markedBy === activePlayerId) {{
                clearCellMark(cell);
                emitCellUpdate(cellIndex, null);
            }} else {{
                markCellForPlayer(cell, activePlayerId);
                emitCellUpdate(cellIndex, activePlayerId);
            }}
        }});

        clearButton.addEventListener('click', () => {{
            for (const cell of board.querySelectorAll('.cell[data-marked-by]')) {{
                const cellIndex = Number.parseInt(cell.dataset.cellIndex || '', 10);
                clearCellMark(cell);
                if (!Number.isNaN(cellIndex)) {{
                    emitCellUpdate(cellIndex, null);
                }}
            }}
        }});

        createPlayer('Player 1', starterColors[0], false, null);
        roomNameInput.value = initialRoomId;
        setConnectionStatus('Offline', 'offline');
        setupRealtime();
    </script>
</body>
</html>
"""


def generate_web_card(
    selected_challenges: list[str],
    hard_layout: list[int],
    free_center: bool,
    web_output_path: Path,
    archive_dir_name: str,
    open_browser: bool,
) -> None:
    web_output_path.parent.mkdir(parents=True, exist_ok=True)
    archive_existing_output(web_output_path, archive_dir_name)
    web_output_path.write_text(
        build_web_card_html(selected_challenges, hard_layout, free_center),
        encoding="utf-8",
    )

    if open_browser:
        webbrowser.open(web_output_path.resolve().as_uri())


def generate_card(
    template_path: Path,
    challenge_path: Path,
    hard_challenge_path: Path,
    output_path: Path,
    web_output_path: Path,
    archive_dir_name: str,
    seed: int | None,
    padding: int,
    open_browser: bool,
    free_center: bool,
) -> None:
    configure_layout(free_center)
    template = Image.open(template_path).convert("RGB")
    draw = ImageDraw.Draw(template)

    challenges = load_challenges(challenge_path)
    hard_challenges = load_challenges(hard_challenge_path)
    randomizer = random.Random(seed)
    selected_challenges, hard_layout, _ = select_challenges(
        standard_challenges=challenges,
        hard_challenges=hard_challenges,
        randomizer=randomizer,
    )

    row_bands, col_bands = detect_grid_bands(template)
    boxes = build_cell_boxes(row_bands, col_bands, padding)
    font_path = find_font_path()

    challenge_index = 0
    for row_index in range(GRID_SIZE):
        for col_index in range(GRID_SIZE):
            if free_center and (row_index, col_index) == FREE_SPACE:
                continue
            box = boxes[row_index * GRID_SIZE + col_index]
            draw_centered_text(draw, box, selected_challenges[challenge_index], font_path)
            challenge_index += 1

    output_path.parent.mkdir(parents=True, exist_ok=True)
    archive_existing_output(output_path, archive_dir_name)
    template.save(output_path)
    generate_web_card(
        selected_challenges=selected_challenges,
        hard_layout=hard_layout,
        free_center=free_center,
        web_output_path=web_output_path,
        archive_dir_name=archive_dir_name,
        open_browser=open_browser,
    )


def main() -> None:
    args = parse_args()
    generate_card(
        template_path=Path(args.template),
        challenge_path=Path(args.challenges),
        hard_challenge_path=Path(args.hard_challenges),
        output_path=Path(args.output),
        web_output_path=Path(args.web_output),
        archive_dir_name=args.archive_dir,
        seed=args.seed,
        padding=args.padding,
        open_browser=args.open_browser,
        free_center=args.free_center,
    )


if __name__ == "__main__":
    main()