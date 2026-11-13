import io
import struct
import wave
import zlib

import pytest
from PIL import Image

from tutor.media.png import MediaError, crop, downscale, validate_png, validate_wav


def png(w=100, h=50, color=(255, 0, 0), mode="RGB") -> bytes:
    out = io.BytesIO()
    Image.new(mode, (w, h), color).save(out, format="PNG")
    return out.getvalue()


def wav(seconds=1.0, rate=16000, channels=1, width=2) -> bytes:
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(width)
        w.setframerate(rate)
        w.writeframes(b"\x00" * int(seconds * rate) * channels * width)
    return out.getvalue()


def fake_png(w, h) -> bytes:
    """A tiny, valid PNG that claims huge dimensions (a decompression bomb)."""

    def chunk(kind: bytes, body: bytes) -> bytes:
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))

    ihdr = struct.pack(">IIBBBBB", w, h, 1, 0, 0, 0, 0)
    raw = zlib.compress(b"\x00" * ((w + 7) // 8 + 1) * 4)  # far fewer rows than claimed
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", raw) + chunk(b"IEND", b"")


def code(fn, *a, **kw):
    with pytest.raises(MediaError) as e:
        fn(*a, **kw)
    return e.value


class TestValidatePng:
    def test_good_png_returns_dimensions(self):
        assert validate_png(png(120, 40)) == (120, 40)

    def test_too_large(self):
        e = code(validate_png, png(100, 100), max_bytes=50)
        assert e.code == "too_large" and e.http_status == 413

    def test_not_a_png(self):
        assert code(validate_png, b"GIF89a....").code == "not_png"
        assert code(validate_png, b"").code == "not_png"

    def test_jpeg_with_a_png_name_is_rejected(self):
        out = io.BytesIO()
        Image.new("RGB", (10, 10)).save(out, format="JPEG")
        assert code(validate_png, out.getvalue()).code == "not_png"

    def test_truncated_png_is_rejected(self):
        data = png(300, 300)
        assert code(validate_png, data[: len(data) // 2]).code == "not_png"

    def test_corrupt_png_is_rejected(self):
        data = bytearray(png(200, 200))
        for i in range(len(data) // 2, len(data) // 2 + 20):
            data[i] ^= 0xFF
        assert code(validate_png, bytes(data)).code == "not_png"

    def test_decompression_bomb_is_rejected_before_decoding(self):
        e = code(validate_png, fake_png(30000, 30000))
        assert e.code == "decompression_bomb" and e.http_status == 413

    def test_just_over_the_pixel_limit_is_a_bomb(self):
        assert code(validate_png, fake_png(4096, 4096)).code == "decompression_bomb"

    def test_long_thin_image_over_the_side_limit(self):
        assert code(validate_png, png(5000, 10)).code == "bad_dimensions"

    def test_limits_are_configurable(self):
        assert code(validate_png, png(100, 100), max_side=64).code == "bad_dimensions"


class TestCrop:
    def test_crop_scales_board_points_to_pixels(self):
        # 200x100 px covering a 100x50 point region at (10, 20): 2 px per point.
        img = Image.new("RGB", (200, 100), (255, 255, 255))
        img.paste((0, 0, 0), (40, 20, 80, 60))  # black square at points x 30..50, y 30..50
        out = io.BytesIO()
        img.save(out, format="PNG")
        cropped = Image.open(io.BytesIO(crop(out.getvalue(), (30, 30, 20, 20), (10, 20, 100, 50))))
        assert cropped.size == (40, 40)
        assert cropped.getpixel((20, 20)) == (0, 0, 0)

    def test_box_is_clamped_to_the_image(self):
        cropped = Image.open(io.BytesIO(crop(png(100, 100), (-50, -50, 200, 200), (0, 0, 100, 100))))
        assert cropped.size == (100, 100)

    def test_box_outside_the_image_is_an_error(self):
        assert code(crop, png(100, 100), (500, 500, 10, 10), (0, 0, 100, 100)).code == "bad_dimensions"

    def test_empty_region_is_an_error(self):
        assert code(crop, png(10, 10), (0, 0, 5, 5), (0, 0, 0, 10)).code == "bad_dimensions"

    def test_garbage_bytes(self):
        assert code(crop, b"nope", (0, 0, 1, 1), (0, 0, 10, 10)).code == "not_png"

    def test_transparent_ink_is_flattened_onto_white(self):
        cropped = Image.open(io.BytesIO(crop(png(10, 10, (0, 0, 0, 0), "RGBA"), (0, 0, 10, 10), (0, 0, 10, 10))))
        assert cropped.mode == "RGB" and cropped.getpixel((5, 5)) == (255, 255, 255)


class TestDownscale:
    def test_large_image_is_shrunk_keeping_aspect_ratio(self):
        out = Image.open(io.BytesIO(downscale(png(2000, 1000), 500)))
        assert out.size == (500, 250)

    def test_small_opaque_image_is_returned_unchanged(self):
        data = png(100, 50)
        assert downscale(data, 1024) == data

    def test_never_enlarges(self):
        assert Image.open(io.BytesIO(downscale(png(10, 10, (1, 2, 3), "RGBA"), 1024))).size == (10, 10)

    def test_transparent_becomes_white(self):
        out = Image.open(io.BytesIO(downscale(png(8, 8, (0, 0, 0, 0), "RGBA"), 1024)))
        assert out.getpixel((1, 1)) == (255, 255, 255)

    def test_garbage_bytes(self):
        assert code(downscale, b"x").code == "not_png"


class TestValidateWav:
    def test_good_wav_returns_duration(self):
        assert validate_wav(wav(1.5)) == 1500

    def test_too_large(self):
        assert code(validate_wav, wav(1), max_bytes=100).http_status == 413

    def test_too_long(self):
        assert code(validate_wav, wav(3), max_seconds=2).code == "too_long"

    def test_not_a_wav(self):
        assert code(validate_wav, b"RIFFxxxxnotawave").code == "not_wav"
        assert code(validate_wav, b"").code == "not_wav"

    @pytest.mark.parametrize("kw", [{"rate": 44100}, {"channels": 2}, {"width": 1}])
    def test_wrong_format(self, kw):
        assert code(validate_wav, wav(0.5, **kw)).code == "bad_format"

    def test_empty_audio_is_rejected(self):
        assert code(validate_wav, wav(0)).code == "bad_format"
