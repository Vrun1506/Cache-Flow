"""Bounds and safe handling for uploaded images and audio (Interface.md "Bound image/audio bytes").

Everything the iPad uploads is untrusted. These checks run before any model sees the bytes and
raise :class:`MediaError`, whose ``code`` maps onto an HTTP status (``too_large`` is 413).
"""

from __future__ import annotations

import io
import wave

from PIL import Image, ImageFile

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
MAX_PNG_BYTES = 4 * 1024 * 1024  # BoardSnapshot.maxBytes on the iPad
MAX_SIDE = 4096
MAX_PIXELS = 16_000_000  # 4096 x 4096 is the bound; a tiny file that expands past it is a bomb
MAX_WAV_BYTES = 2_000_000
MAX_WAV_SECONDS = 30.0
WAV_RATE = 16_000

# A truncated file must be an error, not a half-decoded image.
ImageFile.LOAD_TRUNCATED_IMAGES = False


class MediaError(Exception):
    """An upload that failed its bounds. ``code`` is machine-readable."""

    STATUS = {
        "too_large": 413,
        "not_png": 422,
        "bad_dimensions": 422,
        "decompression_bomb": 413,
        "not_wav": 422,
        "bad_format": 422,
        "too_long": 413,
    }

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail

    @property
    def http_status(self) -> int:
        return self.STATUS.get(self.code, 422)


def _open(data: bytes) -> Image.Image:
    return Image.open(io.BytesIO(data))


def _load(data: bytes) -> Image.Image:
    """Open and fully decode, mapping every failure onto a MediaError."""
    try:
        img = _open(data)
        img.load()
        return img
    except Image.DecompressionBombError as exc:
        raise MediaError("decompression_bomb") from exc
    except Exception as exc:
        raise MediaError("not_png", type(exc).__name__) from exc


def validate_png(
    data: bytes, *, max_bytes: int = MAX_PNG_BYTES, max_side: int = MAX_SIDE, max_pixels: int = MAX_PIXELS
) -> tuple[int, int]:
    """Check size, signature, dimensions and that the whole image decodes. Returns (width, height)."""
    if len(data) > max_bytes:
        raise MediaError("too_large", f"{len(data)} bytes")
    if not data.startswith(PNG_SIGNATURE):
        raise MediaError("not_png")
    try:
        img = _open(data)
        width, height = img.size  # header only: nothing is decoded yet
    except Image.DecompressionBombError as exc:  # Pillow's own limit, hit inside open()
        raise MediaError("decompression_bomb") from exc
    except Exception as exc:
        raise MediaError("not_png", type(exc).__name__) from exc
    if width <= 0 or height <= 0:
        raise MediaError("bad_dimensions", f"{width}x{height}")
    if width * height > max_pixels:
        raise MediaError("decompression_bomb", f"{width}x{height}")
    if width > max_side or height > max_side:
        raise MediaError("bad_dimensions", f"{width}x{height}")
    try:
        img.verify()  # chunk CRCs
        _open(data).load()  # verify() leaves the image unusable, so decode a fresh copy
    except Image.DecompressionBombError as exc:
        raise MediaError("decompression_bomb") from exc
    except Exception as exc:  # truncated or corrupt data
        raise MediaError("not_png", type(exc).__name__) from exc
    return width, height


def _flatten(img: Image.Image) -> Image.Image:
    """PencilKit snapshots can be transparent: put them on white so ink stays readable."""
    if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
        rgba = img.convert("RGBA")
        base = Image.new("RGB", rgba.size, (255, 255, 255))
        base.paste(rgba, mask=rgba.split()[3])
        return base
    return img.convert("RGB") if img.mode not in ("RGB", "L") else img


def _png(img: Image.Image) -> bytes:
    out = io.BytesIO()
    img.save(out, format="PNG", optimize=False)
    return out.getvalue()


def crop(data: bytes, bbox: tuple[float, float, float, float], image_region: tuple[float, float, float, float]) -> bytes:
    """Cut a board-space ``bbox`` (x, y, w, h) out of a PNG that covers ``image_region`` (x, y, w, h).

    The scale is pixels per board point, derived from the image and the region. The box is clamped
    to the image; a box wholly outside it is a ``bad_dimensions`` error.
    """
    rx, ry, rw, rh = image_region
    if rw <= 0 or rh <= 0:
        raise MediaError("bad_dimensions", "empty image region")
    img = _load(data)
    sx, sy = img.width / rw, img.height / rh
    bx, by, bw, bh = bbox
    left = max(0, int(round((bx - rx) * sx)))
    top = max(0, int(round((by - ry) * sy)))
    right = min(img.width, int(round((bx + bw - rx) * sx)))
    bottom = min(img.height, int(round((by + bh - ry) * sy)))
    if right - left < 1 or bottom - top < 1:
        raise MediaError("bad_dimensions", "box outside the image")
    return _png(_flatten(img.crop((left, top, right, bottom))))


def downscale(data: bytes, max_side: int = 1024) -> bytes:
    """Shrink so the longest side is at most ``max_side``, flattened onto white. Never enlarges."""
    img = _load(data)
    flat = _flatten(img)
    if max(flat.size) > max_side:
        scale = max_side / max(flat.size)
        flat = flat.resize((max(1, round(flat.width * scale)), max(1, round(flat.height * scale))), Image.LANCZOS)
    elif flat is img and data.startswith(PNG_SIGNATURE):
        return data  # already small and opaque: keep the original bytes
    return _png(flat)


def validate_wav(
    data: bytes, *, max_bytes: int = MAX_WAV_BYTES, max_seconds: float = MAX_WAV_SECONDS
) -> int:
    """Check a 16 kHz mono 16-bit PCM WAV. Returns the duration in milliseconds."""
    if len(data) > max_bytes:
        raise MediaError("too_large", f"{len(data)} bytes")
    try:
        with wave.open(io.BytesIO(data), "rb") as w:
            channels, width, rate, frames = w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()
            comp = w.getcomptype()
    except (wave.Error, EOFError, ValueError) as exc:
        raise MediaError("not_wav", type(exc).__name__) from exc
    if comp != "NONE" or channels != 1 or width != 2 or rate != WAV_RATE:
        raise MediaError("bad_format", f"{channels}ch {width * 8}bit {rate}Hz {comp}")
    duration_ms = frames * 1000 // rate
    if duration_ms > max_seconds * 1000:
        raise MediaError("too_long", f"{duration_ms} ms")
    if duration_ms == 0:
        raise MediaError("bad_format", "empty audio")
    return duration_ms
