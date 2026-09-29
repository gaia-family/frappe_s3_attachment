# -*- coding: utf-8 -*-
"""Checks that a file on disk is a small raster image we are willing to serve publicly.

Kept free of Frappe imports so it can be unit tested on its own.
"""
import os
from typing import Final

from PIL import Image

PUBLIC_IMAGE_FORMATS: Final = frozenset({"PNG", "JPEG", "WEBP"})
PUBLIC_IMAGE_MAX_BYTES: Final = 5 * 1024 * 1024


def is_allowed_public_image(file_path: str) -> bool:
    """True only for a PNG, JPEG or WebP of at most PUBLIC_IMAGE_MAX_BYTES that decodes.

    The format comes from decoding the bytes, never from the file name or extension, so a
    PDF or an HTML page renamed to logo.png is rejected. SVG is not accepted: it is a
    document that can carry script.
    """
    try:
        if os.path.getsize(file_path) > PUBLIC_IMAGE_MAX_BYTES:
            return False
        with Image.open(file_path) as image:
            if image.format not in PUBLIC_IMAGE_FORMATS:
                return False
            # verify() walks the whole file, so a valid header followed by junk fails.
            image.verify()
        # verify() leaves the image unusable; reopen and decode the pixels to catch
        # truncated data that verify() alone lets through.
        with Image.open(file_path) as image:
            image.load()
    except (OSError, SyntaxError, ValueError, Image.DecompressionBombError):
        return False
    return True
