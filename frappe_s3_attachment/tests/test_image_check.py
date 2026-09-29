# -*- coding: utf-8 -*-
import io
import os
import shutil
import struct
import tempfile
import unittest
from unittest import mock

from PIL import Image

from frappe_s3_attachment import image_check
from frappe_s3_attachment.image_check import is_allowed_public_image

# A real 16x16 AVIF written by Pillow 12 (ftyp, meta and mdat boxes). Pillow 10, which
# Frappe v15 pins, cannot write or read AVIF, so the bytes are committed.
AVIF_FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "logo.avif")


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


if __name__ == "__main__":
    unittest.main()
