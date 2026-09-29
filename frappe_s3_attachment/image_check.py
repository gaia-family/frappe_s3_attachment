# -*- coding: utf-8 -*-
"""Checks that a file on disk is a small raster image we are willing to serve publicly.

Kept free of Frappe imports so it can be unit tested on its own.
"""
import os
import struct
from typing import Final

from PIL import Image

# Formats Pillow decodes: the pixels are verified before a file is accepted.
PUBLIC_IMAGE_FORMATS: Final = frozenset({"PNG", "JPEG", "WEBP", "GIF", "AVIF"})
PUBLIC_IMAGE_MAX_BYTES: Final = 5 * 1024 * 1024

# AVIF is an ISO base media file: a sequence of boxes, the first an `ftyp` box whose
# brands say which kind of file it is.
AVIF_BRANDS: Final = frozenset({b"avif", b"avis"})
AVIF_REQUIRED_BOXES: Final = frozenset({b"meta", b"mdat"})
_BOX_HEADER: Final = struct.Struct(">I4s")
_LARGE_SIZE: Final = struct.Struct(">Q")


def is_allowed_public_image(file_path: str) -> bool:
    """True only for a PNG, JPEG, WebP, GIF or AVIF of at most PUBLIC_IMAGE_MAX_BYTES.

    The format comes from the bytes, never from the file name or extension, so a PDF or
    an HTML page renamed to logo.png is rejected. SVG is not accepted: it is a document
    that can carry script.

    Pillow decodes every format it can read. Pillow 10, which Frappe v15 pins, cannot
    read AVIF, so there an AVIF is accepted only if its container is well formed: an AVIF
    `ftyp` box first, then boxes that exactly fill the file, including the `meta` and
    `mdat` an AVIF image must have.
    """
    try:
        if os.path.getsize(file_path) > PUBLIC_IMAGE_MAX_BYTES:
            return False
        if _is_avif_container(file_path) and not _pillow_reads_avif():
            return True
        return _decodes_as_public_format(file_path)
    except (
        OSError, SyntaxError, ValueError, struct.error, Image.DecompressionBombError
    ):
        return False


def _decodes_as_public_format(file_path: str) -> bool:
    with Image.open(file_path) as image:
        if image.format not in PUBLIC_IMAGE_FORMATS:
            return False
        # verify() walks the whole file, so a valid header followed by junk fails.
        image.verify()
    # verify() leaves the image unusable; reopen and decode the pixels to catch
    # truncated data that verify() alone lets through.
    with Image.open(file_path) as image:
        image.load()
    return True


def _pillow_reads_avif() -> bool:
    Image.init()
    return "AVIF" in Image.ID


def _is_avif_container(file_path: str) -> bool:
    """True if the file is a sequence of well-formed boxes that make up an AVIF image."""
    with open(file_path, "rb") as handle:
        data = handle.read()

    box_types: list[bytes] = []
    offset = 0
    while offset < len(data):
        size, box_type = _BOX_HEADER.unpack_from(data, offset)
        header_size = _BOX_HEADER.size
        if size == 1:
            (size,) = _LARGE_SIZE.unpack_from(data, offset + header_size)
            header_size += _LARGE_SIZE.size
        elif size == 0:
            size = len(data) - offset
        if size < header_size or offset + size > len(data):
            return False
        payload = data[offset + header_size:offset + size]
        if not box_types and not (box_type == b"ftyp" and _has_avif_brand(payload)):
            return False
        box_types.append(box_type)
        offset += size

    return AVIF_REQUIRED_BOXES.issubset(box_types)


def _has_avif_brand(file_type_payload: bytes) -> bool:
    # Major brand (4 bytes), minor version (4 bytes), then compatible brands (4 each).
    if len(file_type_payload) < 8 or len(file_type_payload) % 4:
        return False
    brands = {file_type_payload[0:4]} | {
        file_type_payload[i:i + 4] for i in range(8, len(file_type_payload), 4)
    }
    return bool(brands & AVIF_BRANDS)
