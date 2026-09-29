# -*- coding: utf-8 -*-
import io
import os
import shutil
import tempfile
import unittest
from unittest import mock

from PIL import Image

from frappe_s3_attachment import image_check
from frappe_s3_attachment.image_check import is_allowed_public_image


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

    def test_accepts_png_jpeg_and_webp(self) -> None:
        for image_format in ("PNG", "JPEG", "WEBP"):
            with self.subTest(image_format=image_format):
                name = "logo." + image_format.lower()
                path = self._write(name, _image_bytes(image_format))
                self.assertTrue(is_allowed_public_image(path))

    def test_rejects_other_image_formats(self) -> None:
        for image_format in ("GIF", "BMP", "TIFF"):
            with self.subTest(image_format=image_format):
                path = self._write("logo.png", _image_bytes(image_format))
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
