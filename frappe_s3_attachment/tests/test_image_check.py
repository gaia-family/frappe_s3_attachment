# -*- coding: utf-8 -*-
import io
import os
import shutil
import struct
import tempfile
import unittest
from unittest import mock

from typing import Any

from PIL import Image, ImageCms, JpegImagePlugin, PngImagePlugin

from frappe_s3_attachment import image_check
from frappe_s3_attachment.image_check import (
    is_allowed_public_image,
    sanitize_public_image,
)

# A real 16x16 AVIF written by Pillow 12 (ftyp, meta and mdat boxes). Pillow 10, which
# Frappe v15 pins, cannot write or read AVIF, so the bytes are committed.
FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")
AVIF_FIXTURE = os.path.join(FIXTURES, "logo.avif")
# The same image with an EXIF item, also written by Pillow 12.
AVIF_WITH_EXIF_FIXTURE = os.path.join(FIXTURES, "logo-exif.avif")

# Bytes a polyglot would append after a valid image, and the start of the MP4 a phone
# "motion photo" appends after its JPEG.
APPENDED_HTML = b"<html><script>alert(1)</script></html>"
APPENDED_MP4 = b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom" + b"\x00" * 64
CAMERA_MAKE = "TestCam"
EXIF_MAKE_TAG = 0x010F
EXIF_ORIENTATION_TAG = 0x0112
EXIF_GPS_IFD_TAG = 0x8825


def _image_bytes(image_format: str) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (16, 16), (200, 30, 90)).save(buffer, format=image_format)
    return buffer.getvalue()


class TestIsAllowedPublicImage(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.mkdtemp()

    def tearDown(self) -> None:
        shutil.rmtree(self.directory)

    def _write(self, name: str, content: bytes) -> str:
        path = os.path.join(self.directory, name)
        with open(path, "wb") as handle:
            handle.write(content)
        return path

    def test_accepts_png_jpeg_webp_and_gif(self) -> None:
        for image_format in ("PNG", "JPEG", "WEBP", "GIF"):
            with self.subTest(image_format=image_format):
                name = "logo." + image_format.lower()
                path = self._write(name, _image_bytes(image_format))
                self.assertTrue(is_allowed_public_image(path))

    def test_rejects_other_image_formats(self) -> None:
        for image_format in ("BMP", "TIFF"):
            with self.subTest(image_format=image_format):
                path = self._write("logo.png", _image_bytes(image_format))
                self.assertFalse(is_allowed_public_image(path))

    def _avif_bytes(self) -> bytes:
        with open(AVIF_FIXTURE, "rb") as handle:
            return handle.read()

    def test_accepts_avif_by_its_container_when_pillow_cannot_read_it(self) -> None:
        path = self._write("logo.avif", self._avif_bytes())
        with mock.patch.object(image_check, "_pillow_reads_avif", return_value=False):
            self.assertTrue(is_allowed_public_image(path))

    def test_accepts_avif_by_decoding_when_pillow_can_read_it(self) -> None:
        if not image_check._pillow_reads_avif():
            self.skipTest("this Pillow cannot read AVIF")
        path = self._write("logo.avif", self._avif_bytes())
        self.assertTrue(is_allowed_public_image(path))

    def test_rejects_malformed_avif_containers(self) -> None:
        avif = self._avif_bytes()
        file_type_box = avif[: struct.unpack(">I", avif[:4])[0]]
        other_brand = file_type_box[:8] + b"mp42" + file_type_box[12:16] + b"mp42" * (
            (len(file_type_box) - 16) // 4
        )
        cases = {
            "header then junk": file_type_box + os.urandom(64),
            "truncated": avif[:-10],
            "missing mdat": avif[: avif.index(b"mdat") - 4],
            "not an AVIF brand": other_brand + avif[len(file_type_box):],
            "ftyp only": file_type_box,
        }
        for label, content in cases.items():
            with self.subTest(case=label), mock.patch.object(
                image_check, "_pillow_reads_avif", return_value=False
            ):
                path = self._write("logo.avif", content)
                self.assertFalse(is_allowed_public_image(path))

    def test_rejects_svg(self) -> None:
        svg = (
            b'<svg xmlns="http://www.w3.org/2000/svg">'
            b"<script>alert(1)</script></svg>"
        )
        self.assertFalse(is_allowed_public_image(self._write("logo.png", svg)))

    def test_rejects_documents_named_like_images(self) -> None:
        for content in (b"%PDF-1.7\n1 0 obj\n", b"<html><body>hi</body></html>"):
            with self.subTest(content=content[:5]):
                path = self._write("logo.png", content)
                self.assertFalse(is_allowed_public_image(path))

    def test_rejects_valid_signature_followed_by_junk(self) -> None:
        junk = b"\x89PNG\r\n\x1a\n" + os.urandom(512)
        self.assertFalse(is_allowed_public_image(self._write("logo.png", junk)))

    def test_rejects_truncated_image(self) -> None:
        truncated = _image_bytes("PNG")[:-20]
        self.assertFalse(is_allowed_public_image(self._write("logo.png", truncated)))

    def test_rejects_file_over_size_limit(self) -> None:
        path = self._write("logo.png", _image_bytes("PNG"))
        limit = os.path.getsize(path) - 1
        with mock.patch.object(image_check, "PUBLIC_IMAGE_MAX_BYTES", limit):
            self.assertFalse(is_allowed_public_image(path))

    def test_rejects_missing_file(self) -> None:
        missing = os.path.join(self.directory, "missing.png")
        self.assertFalse(is_allowed_public_image(missing))

    def test_accepts_image_with_bytes_appended_for_sanitizing(self) -> None:
        # Phones append data after real photos, so this is cleaned rather than refused.
        for image_format in ("PNG", "JPEG", "WEBP", "GIF"):
            with self.subTest(image_format=image_format):
                content = _image_bytes(image_format) + APPENDED_HTML
                path = self._write("logo." + image_format.lower(), content)
                self.assertTrue(is_allowed_public_image(path))

    def test_rejects_avif_with_bytes_appended(self) -> None:
        path = self._write("logo.avif", self._avif_bytes() + APPENDED_HTML)
        for pillow_reads_avif in (False, True):
            with self.subTest(pillow_reads_avif=pillow_reads_avif), mock.patch.object(
                image_check, "_pillow_reads_avif", return_value=pillow_reads_avif
            ):
                self.assertFalse(is_allowed_public_image(path))

    def test_rejects_avif_carrying_exif(self) -> None:
        with open(AVIF_WITH_EXIF_FIXTURE, "rb") as handle:
            path = self._write("logo.avif", handle.read())
        with mock.patch.object(image_check, "_pillow_reads_avif", return_value=False):
            self.assertFalse(is_allowed_public_image(path))


def _exif(orientation: int = 1) -> Image.Exif:
    exif = Image.Exif()
    exif[EXIF_MAKE_TAG] = CAMERA_MAKE
    exif[EXIF_ORIENTATION_TAG] = orientation
    exif[EXIF_GPS_IFD_TAG] = {2: (40.0, 44.0, 54.0)}  # GPSLatitude
    return exif


def _srgb_profile() -> bytes:
    profile = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB"))
    # getattr: Pillow 10's type hints leave tobytes untyped.
    content = getattr(profile, "tobytes")()
    assert isinstance(content, bytes)
    return content


class TestSanitizePublicImage(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.mkdtemp()

    def tearDown(self) -> None:
        shutil.rmtree(self.directory)

    def _save(self, name: str, image: Image.Image, **options: Any) -> str:
        path = os.path.join(self.directory, name)
        image.save(path, **options)
        return path

    def _append(self, path: str, content: bytes) -> None:
        with open(path, "ab") as handle:
            handle.write(content)

    def _sanitized_bytes(self, path: str) -> bytes:
        sanitize_public_image(path)
        self.assertTrue(is_allowed_public_image(path))
        with open(path, "rb") as handle:
            return handle.read()

    def test_drops_appended_bytes(self) -> None:
        for image_format in ("PNG", "JPEG", "WEBP", "GIF"):
            with self.subTest(image_format=image_format):
                path = self._save(
                    "logo." + image_format.lower(),
                    Image.new("RGB", (16, 16), (200, 30, 90)),
                    format=image_format,
                )
                self._append(path, APPENDED_HTML)
                self.assertNotIn(b"<script>", self._sanitized_bytes(path))

    def test_drops_motion_photo_video(self) -> None:
        path = self._save(
            "photo.jpg", Image.new("RGB", (16, 16), (10, 120, 200)), format="JPEG"
        )
        self._append(path, APPENDED_MP4)
        self.assertNotIn(b"ftypmp42", self._sanitized_bytes(path))

    def test_drops_exif_and_gps(self) -> None:
        for image_format in ("JPEG", "PNG", "WEBP"):
            with self.subTest(image_format=image_format):
                path = self._save(
                    "photo." + image_format.lower(),
                    Image.new("RGB", (16, 16), (10, 120, 200)),
                    format=image_format,
                    exif=_exif().tobytes(),
                )
                content = self._sanitized_bytes(path)
                self.assertNotIn(CAMERA_MAKE.encode(), content)
                with Image.open(path) as image:
                    self.assertEqual(dict(image.getexif()), {})

    def test_drops_png_text_chunks(self) -> None:
        text = PngImagePlugin.PngInfo()
        text.add_text("Comment", "taken at 12 Main St")
        path = self._save(
            "logo.png", Image.new("RGB", (16, 16)), format="PNG", pnginfo=text
        )
        self.assertNotIn(b"Main St", self._sanitized_bytes(path))

    def test_drops_gif_comment(self) -> None:
        path = self._save(
            "logo.gif", Image.new("P", (16, 16)), format="GIF", comment=b"Main St"
        )
        self.assertNotIn(b"Main St", self._sanitized_bytes(path))

    def test_keeps_color_profile(self) -> None:
        profile = _srgb_profile()
        for image_format in ("JPEG", "PNG", "WEBP"):
            with self.subTest(image_format=image_format):
                path = self._save(
                    "logo." + image_format.lower(),
                    Image.new("RGB", (16, 16), (10, 120, 200)),
                    format=image_format,
                    icc_profile=profile,
                )
                self._sanitized_bytes(path)
                with Image.open(path) as image:
                    self.assertEqual(image.info.get("icc_profile"), profile)

    def test_turns_rotated_photo_upright(self) -> None:
        # Orientation 6 means "rotate 90 degrees clockwise to display".
        for image_format in ("JPEG", "PNG", "WEBP"):
            with self.subTest(image_format=image_format):
                path = self._save(
                    "photo." + image_format.lower(),
                    Image.new("RGB", (32, 16), (10, 120, 200)),
                    format=image_format,
                    exif=_exif(orientation=6).tobytes(),
                )
                self._sanitized_bytes(path)
                with Image.open(path) as image:
                    self.assertEqual(image.size, (16, 32))
                    self.assertEqual(dict(image.getexif()), {})

    def test_keeps_jpeg_quality_when_no_rotation_is_needed(self) -> None:
        source = Image.effect_noise((64, 64), 40).convert("RGB")
        path = self._save("photo.jpg", source, format="JPEG", quality=70)
        with Image.open(path) as image:
            assert isinstance(image, JpegImagePlugin.JpegImageFile)
            before = getattr(image, "quantization")
        self._sanitized_bytes(path)
        with Image.open(path) as image:
            assert isinstance(image, JpegImagePlugin.JpegImageFile)
            self.assertEqual(getattr(image, "quantization"), before)

    def test_keeps_png_transparency(self) -> None:
        image = Image.new("RGBA", (16, 16), (255, 0, 0, 0))
        path = self._save("logo.png", image, format="PNG")
        self._sanitized_bytes(path)
        with Image.open(path) as sanitized:
            self.assertEqual(sanitized.mode, "RGBA")
            pixel = sanitized.getpixel((0, 0))
            assert isinstance(pixel, tuple)
            self.assertEqual(pixel[3], 0)

    def test_keeps_gif_transparency(self) -> None:
        image = Image.new("P", (16, 16), 0)
        path = self._save("logo.gif", image, format="GIF", transparency=0)
        self._sanitized_bytes(path)
        with Image.open(path) as sanitized:
            self.assertEqual(sanitized.info.get("transparency"), 0)

    def test_keeps_animation(self) -> None:
        colors = [(255, 0, 0), (0, 255, 0), (0, 0, 255)]
        frames = [Image.new("RGB", (16, 16), color) for color in colors]
        for image_format in ("GIF", "WEBP", "PNG"):
            with self.subTest(image_format=image_format):
                path = self._save(
                    "logo." + image_format.lower(),
                    frames[0],
                    format=image_format,
                    save_all=True,
                    append_images=frames[1:],
                    duration=[100, 200, 300],
                    loop=0,
                )
                self._append(path, APPENDED_HTML)
                self.assertNotIn(b"<script>", self._sanitized_bytes(path))
                with Image.open(path) as image:
                    frame_count = getattr(image, "n_frames", 1)
                    self.assertEqual(frame_count, 3)
                    durations = []
                    for index in range(frame_count):
                        image.seek(index)
                        image.load()
                        durations.append(image.info.get("duration"))
                    self.assertEqual(durations, [100, 200, 300])

    def test_stores_first_picture_of_multi_picture_jpeg(self) -> None:
        pictures = [Image.new("RGB", (16, 16), (255, 0, 0)), Image.new("RGB", (8, 8))]
        path = self._save(
            "photo.jpg",
            pictures[0],
            format="MPO",
            save_all=True,
            append_images=pictures[1:],
        )
        self.assertTrue(is_allowed_public_image(path))
        self._sanitized_bytes(path)
        with Image.open(path) as image:
            self.assertEqual(image.format, "JPEG")
            self.assertEqual(getattr(image, "n_frames", 1), 1)
            self.assertEqual(image.size, (16, 16))

    def test_leaves_avif_untouched(self) -> None:
        with open(AVIF_FIXTURE, "rb") as handle:
            original = handle.read()
        path = os.path.join(self.directory, "logo.avif")
        with open(path, "wb") as handle:
            handle.write(original)
        sanitize_public_image(path)
        with open(path, "rb") as handle:
            self.assertEqual(handle.read(), original)


if __name__ == "__main__":
    unittest.main()
