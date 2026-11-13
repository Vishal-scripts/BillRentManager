"""Read the Tata Power-DDL pay-bill captcha (two digits + plus, strikethrough noise)."""

from __future__ import annotations

import io
import json
import os
import random
import re
import shutil
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytesseract
from PIL import Image, ImageFilter, ImageOps

_WHITELIST = "0123456789+"
_MAX_OPERAND = 99
_PLUS_CLUSTER = {"44", "45", "54", "55"}


class BillCheckStopped(Exception):
    """The owner pressed Stop before this CA was checked."""
def captcha_debug_dir() -> Path:
    """Captcha images go under the data folder, not the source tree."""
    raw = os.environ.get("NARELA_HOME", "").strip().strip('"')
    root = Path(raw) if raw else Path(__file__).resolve().parents[1]
    return root / "data" / "captcha-debug"


def captcha_tries_from_env(default: int = 6) -> int:
    """How many captcha images to try for one CA (``BRM_CAPTCHA_TRIES`` or ``NDPL_CAPTCHA_TRIES``)."""
    raw = ""
    for key in ("BRM_CAPTCHA_TRIES", "NDPL_CAPTCHA_TRIES"):
        raw = os.environ.get(key, "").strip()
        if raw:
            break
    if not raw:
        raw = str(default)
    try:
        parsed = int(raw)
    except ValueError:
        parsed = default
    return max(1, min(12, parsed))


def captcha_pause(
    attempt: int = 0,
    *,
    min_s: float | None = None,
    max_s: float | None = None,
    should_stop=None,
) -> bool:
    """Random delay between captcha attempts; slightly longer on later tries.

    Returns True when ``should_stop`` asks to end the wait early.
    """
    base_min = 1.5 if min_s is None else min_s
    base_max = 4.0 if max_s is None else max_s
    extra = min(2.0, 0.35 * max(0, attempt))
    delay = random.uniform(base_min + extra * 0.25, base_max + extra)
    if should_stop is None:
        time.sleep(delay)
        return False
    end = time.monotonic() + delay
    while True:
        if should_stop():
            return True
        left = end - time.monotonic()
        if left <= 0:
            return False
        time.sleep(min(0.2, left))


def _bundled_tesseract_candidates() -> list[str]:
    found = []
    home = os.environ.get("NARELA_HOME", "").strip().strip('"')
    roots = []
    if home:
        roots.append(home)
    roots.append(str(Path(__file__).resolve().parents[1]))
    for root in roots:
        found.append(os.path.join(root, "tesseract", "tesseract.exe"))
    return found


def _windows_tesseract_candidates() -> list[str]:
    """UB Mannheim install folders. Winget per-user uses LocalAppData, not Program Files.

    The per-user folder is added whenever ``LOCALAPPDATA`` or ``USERPROFILE`` is
    set, so discovery does not depend on flipping ``os.name``.
    """
    found = []
    if os.name == "nt":
        found.extend(
            (
                r"C:\Program Files\Tesseract-OCR\tesseract.exe",
                r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
            )
        )
    local = os.environ.get("LOCALAPPDATA", "").strip().strip('"')
    if not local:
        profile = os.environ.get("USERPROFILE", "").strip().strip('"')
        if profile:
            local = os.path.join(profile, "AppData", "Local")
    if local:
        found.append(os.path.join(local, "Programs", "Tesseract-OCR", "tesseract.exe"))
    return found


def configure_tesseract() -> str | None:
    configured = os.environ.get("TESSERACT_CMD", "").strip().strip('"')
    candidates = [configured] if configured else []
    candidates.extend(_bundled_tesseract_candidates())
    candidates.extend(_windows_tesseract_candidates())
    which = shutil.which("tesseract")
    if which:
        candidates.append(which)
    chosen = next((path for path in candidates if path and os.path.isfile(path)), None)
    if not chosen:
        return None
    pytesseract.pytesseract.tesseract_cmd = chosen
    tessdata = os.path.join(os.path.dirname(chosen), "tessdata")
    if os.path.isdir(tessdata) and not os.environ.get("TESSDATA_PREFIX"):
        os.environ["TESSDATA_PREFIX"] = tessdata + os.sep
    return chosen


def operands_from_ocr(text: str) -> tuple[int, int] | None:
    raw = text or ""
    compact = re.sub(r"\s+", "", raw)
    if "+" in compact:
        if re.search(r"\d{3,}", compact):
            return None
        match = re.fullmatch(r"(\d{1,2})\+(\d{1,2})", compact)
        if match:
            left, right = int(match.group(1)), int(match.group(2))
            if 0 <= left <= _MAX_OPERAND and 0 <= right <= _MAX_OPERAND:
                return left, right
    if "+" not in raw and not re.search(r"\s", raw):
        return _relaxed_pair(compact)
    return None


def _relaxed_pair(compact: str) -> tuple[int, int] | None:
    """Fallback when OCR drops the plus sign (e.g. ``2022`` → ``20+22``)."""
    digits = re.sub(r"\D", "", compact or "")
    if len(digits) != 4:
        return None
    left, right = int(digits[:2]), int(digits[2:])
    if 0 <= left <= _MAX_OPERAND and 0 <= right <= _MAX_OPERAND:
        return left, right
    left, right = int(digits[0]), int(digits[1:])
    if 0 <= left <= _MAX_OPERAND and 0 <= right <= _MAX_OPERAND:
        return left, right
    return None


def _pair_ok(pair: tuple[int, int]) -> bool:
    return 0 <= pair[0] <= _MAX_OPERAND and 0 <= pair[1] <= _MAX_OPERAND


def _captcha_pair(text: str) -> tuple[str, tuple[int, int]] | None:
    """Read one OCR string as ``NN+NN``.

    A 4 or a 5 in the plus position is the plus. ``10528`` and ``10428`` are
    both ``10+28``. A doubled ``44``, ``45``, ``54``, or ``55`` is the same
    plus, kept only as support for a pair already seen another way.
    """
    compact = re.sub(r"\s+", "", text or "")
    compact = re.sub(r"\+{2,}", "+", compact)
    if not compact:
        return None
    match = re.fullmatch(r"(\d{1,2})\+(\d{1,2})", compact)
    if match:
        pair = (int(match.group(1)), int(match.group(2)))
        return ("exact", pair) if _pair_ok(pair) else None
    if not re.fullmatch(r"\d+", compact):
        return None
    if len(compact) == 5 and compact[2] in "45":
        pair = (int(compact[:2]), int(compact[3:]))
        kind = "plus4" if compact[2] == "4" else "plus5"
        return (kind, pair) if _pair_ok(pair) else None
    if len(compact) == 6 and compact[2:4] in _PLUS_CLUSTER:
        pair = (int(compact[:2]), int(compact[4:]))
        return ("doubled", pair) if _pair_ok(pair) else None
    if len(compact) == 4:
        pair = (int(compact[:2]), int(compact[2:]))
        return ("dropped", pair) if _pair_ok(pair) else None
    return None


def _votes_from_reads(reads: list[str]) -> tuple[Counter[tuple[int, int]], dict[tuple[int, int], set[str]]]:
    """Count operand pairs. A dropped or doubled plus only confirms a pair."""
    votes: Counter[tuple[int, int]] = Counter()
    channels: dict[tuple[int, int], set[str]] = {}
    doubled: list[tuple[int, int]] = []
    dropped: list[tuple[int, int]] = []
    for text in reads:
        parsed = _captcha_pair(text)
        if not parsed:
            continue
        kind, pair = parsed
        if kind == "doubled":
            doubled.append(pair)
            continue
        if kind == "dropped":
            dropped.append(pair)
            continue
        votes[pair] += 1
        channels.setdefault(pair, set()).add(kind)
    for pair in doubled:
        if pair in votes:
            votes[pair] += 1
    for pair in dropped:
        if pair in votes:
            votes[pair] += 1
    return votes, channels


def choose_from_guesses(guesses: list[str]) -> tuple[int, int] | None:
    """Pick one pair from raw OCR strings, or nothing if they do not agree.

    An explicit ``NN+NN`` that already leads is kept. The 4/5-as-plus reading
    is used only when those explicit reads do not agree, which is the usual
    case on the live captcha.
    """
    exact: Counter[tuple[int, int]] = Counter()
    for text in guesses:
        parsed = _captcha_pair(text)
        if parsed and parsed[0] == "exact":
            exact[parsed[1]] += 1
    chosen = _choose_pair(exact, {})
    if chosen is not None:
        return chosen
    votes, channels = _votes_from_reads(guesses)
    return _choose_pair(votes, channels)


def read_operands(image_bytes: bytes) -> tuple[int, int] | None:
    total, _meta = solve_captcha(image_bytes)
    if total is None:
        return None
    return _meta.get("chosen_pair")


def ocr_addition(image_bytes: bytes) -> int | None:
    total, _meta = solve_captcha(image_bytes)
    return total


def solve_captcha(image_bytes: bytes, should_stop=None) -> tuple[int | None, dict[str, Any]]:
    """OCR with several preprocessings. Submit only when the reads agree.

    ``should_stop`` is checked between Tesseract calls so Stop does not wait
    for every preprocessing of the current image.
    """
    configure_tesseract()
    image = _flatten_rgba(Image.open(io.BytesIO(image_bytes)))
    raw_reads: list[str] = []
    small = max(image.size) < 140
    scale = 8 if max(image.size) < 120 else (6 if small else 3)
    variants = [
        _plain_gray(image, scale=scale, threshold=120),
        _plain_gray(image, scale=scale, threshold=135),
        _plain_gray(image, scale=scale + 1, threshold=128),
        _inverted_variant(image, scale=scale),
        _gray_variant(image, scale=scale, threshold=140),
        _gray_variant(image, scale=scale, threshold=160),
        _otsu_variant(image, scale=scale),
        _green_variant(image, scale=scale),
        _sharpen_variant(image, scale=scale),
        _median_variant(image, scale=scale),
    ]
    if small:
        variants.extend(
            [
                _plain_gray(image, scale=scale + 2, threshold=125),
                _gray_variant(image, scale=scale + 1, threshold=130),
                _median_variant(image, scale=scale + 1),
            ]
        )

    def stopped() -> bool:
        return bool(should_stop and should_stop())

    for variant in variants:
        for psm in (7, 8, 13):
            if stopped():
                return _stopped_meta(raw_reads)
            text, _pair = _read_with_text(variant, psm)
            if text:
                raw_reads.append(text.strip())
    votes, _channels = _votes_from_reads(raw_reads)
    chosen = choose_from_guesses(raw_reads)
    meta: dict[str, Any] = {
        "guesses": raw_reads[:40],
        "votes": {f"{a}+{b}": count for (a, b), count in votes.most_common(12)},
        "chosen_pair": chosen,
        "chosen_sum": (chosen[0] + chosen[1]) if chosen else None,
        "stopped": False,
    }
    return meta["chosen_sum"], meta


def _stopped_meta(raw_reads: list[str]) -> tuple[None, dict[str, Any]]:
    return None, {
        "guesses": raw_reads[:40],
        "votes": {},
        "chosen_pair": None,
        "chosen_sum": None,
        "stopped": True,
    }


def save_captcha_attempt(
    ca_no: str,
    attempt: int,
    image_bytes: bytes,
    meta: dict[str, Any],
    *,
    outcome: str,
    directory: Path | None = None,
) -> Path | None:
    try:
        folder = Path(directory) if directory else captcha_debug_dir()
        folder.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        safe = "".join(ch for ch in ca_no if ch.isdigit()) or "unknown"
        base = f"{safe}_{stamp}_try{attempt}_{outcome}"
        png = folder / f"{base}.png"
        png.write_bytes(image_bytes)
        payload = {"ca_no": ca_no, "attempt": attempt, "outcome": outcome, **meta}
        (folder / f"{base}.json").write_text(
            json.dumps(payload, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        return png
    except OSError:
        return None


def _choose_pair(
    votes: Counter[tuple[int, int]],
    channels: dict[tuple[int, int], set[str]] | None = None,
) -> tuple[int, int] | None:
    """Return a pair only when the reads agree.

    The usual rule is unchanged: at least two votes, and a lead of two over
    every other reading combined. A single guess, a tie, or a one-vote lead
    is left unread.

    A second path covers the live captcha, where ``+`` is often read as 4 or
    5 and the other readings scatter. The leader is sent only when it has at
    least four votes, leads the next pair by two, and the same pair was seen
    both as a real plus and as a 4 or 5, or as both a 4 and a 5.
    """
    if not votes:
        return None
    channels = channels or {}
    ranked = votes.most_common()
    pair, count = ranked[0]
    others = sum(item[1] for item in ranked[1:])
    if count >= 2 and count >= others + 2:
        return pair
    second = ranked[1][1] if len(ranked) > 1 else 0
    kinds = channels.get(pair, set())
    confirmed = (
        {"exact", "plus4"} <= kinds
        or {"exact", "plus5"} <= kinds
        or {"plus4", "plus5"} <= kinds
    )
    if count >= 4 and count >= second + 2 and confirmed:
        return pair
    return None


def _read_with_text(image: Image.Image, psm: int) -> tuple[str, tuple[int, int] | None]:
    padded = _pad(image, 16)
    config = f"--oem 3 --psm {psm} -c tessedit_char_whitelist={_WHITELIST}"
    text = pytesseract.image_to_string(padded, config=config)
    return text, operands_from_ocr(text)


def _flatten_rgba(image: Image.Image) -> Image.Image:
    if image.mode != "RGBA":
        return image.convert("RGB")
    flat = Image.new("RGB", image.size, (255, 255, 255))
    flat.paste(image, mask=image.split()[3])
    return flat


def _pad(image: Image.Image, margin: int) -> Image.Image:
    canvas = Image.new("L", (image.width + 2 * margin, image.height + 2 * margin), 255)
    canvas.paste(image.convert("L"), (margin, margin))
    return canvas


def _otsu_threshold(gray: Image.Image) -> int:
    hist = gray.histogram()[:256]
    total = sum(hist)
    if total <= 0:
        return 140
    sum_total = sum(index * count for index, count in enumerate(hist))
    sum_bg = 0
    weight_bg = 0
    best_thresh = 128
    best_var = -1.0
    for thresh in range(256):
        weight_bg += hist[thresh]
        if weight_bg == 0:
            continue
        weight_fg = total - weight_bg
        if weight_fg == 0:
            break
        sum_bg += thresh * hist[thresh]
        mean_bg = sum_bg / weight_bg
        mean_fg = (sum_total - sum_bg) / weight_fg
        var_between = weight_bg * weight_fg * (mean_bg - mean_fg) ** 2
        if var_between > best_var:
            best_var = var_between
            best_thresh = thresh
    return best_thresh


def _inverted_variant(image: Image.Image, scale: int) -> Image.Image:
    gray = ImageOps.autocontrast(image.convert("L"))
    enlarged = gray.resize((gray.width * scale, gray.height * scale), Image.Resampling.LANCZOS)
    ink = enlarged.point(lambda pixel: 0 if pixel < 140 else 255)
    return ImageOps.invert(ink.convert("L"))


def _otsu_variant(image: Image.Image, scale: int) -> Image.Image:
    gray = ImageOps.autocontrast(image.convert("L"))
    enlarged = gray.resize((gray.width * scale, gray.height * scale), Image.Resampling.LANCZOS)
    limit = _otsu_threshold(enlarged)
    ink = enlarged.point(lambda pixel, limit=limit: 0 if pixel < limit else 255)
    return _denoise(_remove_strike(ink))


def _median_variant(image: Image.Image, scale: int) -> Image.Image:
    gray = ImageOps.autocontrast(image.convert("L"))
    smooth = gray.filter(ImageFilter.MedianFilter(size=3))
    enlarged = smooth.resize((smooth.width * scale, smooth.height * scale), Image.Resampling.LANCZOS)
    limit = _otsu_threshold(enlarged)
    ink = enlarged.point(lambda pixel, limit=limit: 0 if pixel < limit else 255)
    return _denoise(_remove_strike(ink))


def _sharpen_variant(image: Image.Image, scale: int) -> Image.Image:
    gray = ImageOps.autocontrast(image.convert("L"))
    sharp = gray.filter(ImageFilter.SHARPEN)
    enlarged = sharp.resize((sharp.width * scale, sharp.height * scale), Image.Resampling.LANCZOS)
    ink = enlarged.point(lambda pixel: 0 if pixel < 145 else 255)
    return _denoise(_remove_strike(ink))


def _plain_gray(image: Image.Image, scale: int, threshold: int) -> Image.Image:
    gray = ImageOps.autocontrast(image.convert("L"))
    enlarged = gray.resize((gray.width * scale, gray.height * scale), Image.Resampling.LANCZOS)
    return enlarged.point(lambda pixel, limit=threshold: 0 if pixel < limit else 255)


def _gray_variant(image: Image.Image, scale: int, threshold: int) -> Image.Image:
    gray = ImageOps.autocontrast(image.convert("L"))
    enlarged = gray.resize((gray.width * scale, gray.height * scale), Image.Resampling.LANCZOS)
    ink = enlarged.point(lambda pixel, limit=threshold: 0 if pixel < limit else 255)
    return _denoise(_remove_strike(ink))


def _green_variant(image: Image.Image, scale: int) -> Image.Image:
    rgb = image.convert("RGB")
    width, height = rgb.size
    source = rgb.load()
    mask = Image.new("L", (width, height), 255)
    target = mask.load()
    for y in range(height):
        for x in range(width):
            red, green, blue = source[x, y]
            greenish = (
                green + 10 >= red
                and green + 10 >= blue
                and max(red, green, blue) < 210
                and (green - red) + (green - blue) >= 8
            )
            near_black = red < 75 and green < 100 and blue < 75
            if greenish or near_black:
                target[x, y] = 0
    enlarged = mask.resize((width * scale, height * scale), Image.Resampling.NEAREST)
    return _denoise(_remove_strike(enlarged))


def _remove_strike(image: Image.Image) -> Image.Image:
    width, height = image.size
    pixels = image.load()
    row_run = [0] * height
    column_used = [False] * width
    for y in range(height):
        longest = run = 0
        for x in range(width):
            if pixels[x, y] < 128:
                run += 1
                if run > longest:
                    longest = run
                column_used[x] = True
            else:
                run = 0
        row_run[y] = longest
    used = [index for index, on in enumerate(column_used) if on]
    min_run = 5 if width < 200 else 8
    if not used or max(row_run) < min_run:
        return image
    text_width = used[-1] - used[0] + 1
    ink_runs = sorted(run for run in row_run if run >= 6)
    if not ink_runs:
        return image
    median = ink_runs[len(ink_runs) // 2]
    limit = max(int(median * 2.4), int(text_width * 0.55))
    strike_rows = [
        y
        for y, run in enumerate(row_run)
        if run >= limit and height * 0.12 < y < height * 0.88
    ]
    if not strike_rows:
        return image
    top, bottom = min(strike_rows), max(strike_rows)
    if bottom - top > max(3, height // 6):
        peak = max(strike_rows, key=lambda y: row_run[y])
        top = bottom = peak
    cleaned = image.copy()
    output = cleaned.load()
    for y in range(top, bottom + 1):
        for x in range(width):
            output[x, y] = 255
    if top > 0 and bottom + 1 < height:
        for x in range(width):
            if pixels[x, top - 1] < 128 and pixels[x, bottom + 1] < 128:
                for y in range(top, bottom + 1):
                    output[x, y] = 0
    return cleaned


def _denoise(image: Image.Image) -> Image.Image:
    width, height = image.size
    pixels = image.load()
    cleaned = image.copy()
    output = cleaned.load()
    for y in range(1, height - 1):
        for x in range(1, width - 1):
            if pixels[x, y] >= 128:
                continue
            neighbours = 0
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    if (dx or dy) and pixels[x + dx, y + dy] < 128:
                        neighbours += 1
            if neighbours < 1:
                output[x, y] = 255
    return cleaned
