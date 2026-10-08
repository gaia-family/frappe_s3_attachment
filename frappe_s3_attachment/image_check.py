# -*- coding: utf-8 -*-
"""Checks that a file on disk is a small raster image we are willing to serve publicly,
and rewrites an accepted image so it carries nothing but its pixels.

Kept free of Frappe imports so it can be unit tested on its own.
"""
import os
import struct
from typing import Any, Final

from PIL import Image, ImageOps, ImageSequence, JpegImagePlugin

# Formats Pillow decodes: the pixels are verified before a file is accepted. MPO is how
# Pillow names a JPEG that carries extra pictures after the first (many cameras write
# one), and is stored as a plain JPEG of that first picture.
PUBLIC_IMAGE_FORMATS: Final = frozenset({"PNG", "JPEG", "MPO", "WEBP", "GIF", "AVIF"})
PUBLIC_IMAGE_MAX_BYTES: Final = 5 * 1024 * 1024

# AVIF is an ISO base media file: a sequence of boxes, the first an `ftyp` box whose
# brands say which kind of file it is.
AVIF_BRANDS: Final = frozenset({b"avif", b"avis"})
AVIF_REQUIRED_BOXES: Final = frozenset({b"meta", b"mdat"})
# Item types that hold metadata rather than pixels: EXIF, and XMP (stored as a `mime`
# item). Pillow 10 cannot re-encode AVIF to drop them, so a file carrying one is
# rejected instead.
AVIF_METADATA_ITEM_TYPES: Final = frozenset({b"Exif", b"mime"})
_BOX_HEADER: Final = struct.Struct(">I4s")
_LARGE_SIZE: Final = struct.Struct(">Q")
_FULL_BOX_HEADER_SIZE: Final = 4  # version (1 byte) and flags (3 bytes)

# Re-encoding a JPEG that has to be rotated upright cannot reuse its quantization
# tables, so it is saved at this quality instead.
JPEG_REENCODE_QUALITY: Final = 95
WEBP_LOSSY_QUALITY: Final = 95
EXIF_ORIENTATION_TAG: Final = 0x0112
# Metadata Pillow copies from a source image into a re-encode unless told not to.
COPIED_METADATA_KEYS: Final = ("comment", "exif", "xmp", "XML:com.adobe.xmp")


def is_allowed_public_image(file_path: str) -> bool:
    """True only for a PNG, JPEG, WebP, GIF or AVIF of at most PUBLIC_IMAGE_MAX_BYTES.

    The format comes from the bytes, never from the file name or extension, so a PDF or
    an HTML page renamed to logo.png is rejected. SVG is not accepted: it is a document
    that can carry script.

    Pillow decodes every format it can read. An AVIF must also be a well formed
    container: an AVIF `ftyp` box first, then boxes that exactly fill the file (so nothing
    is appended after the image), including the `meta` and `mdat` an AVIF image must
    have, and no EXIF or XMP item. Pillow 10, which Frappe v15 pins, cannot read AVIF, so
    there the container check is the whole check.

    Bytes appended after a PNG, JPEG, WebP or GIF image are not rejected here: phones
    and cameras add some (a "motion photo" appends a video), and sanitize_public_image
    drops them before the file is stored.
    """
    try:
        if os.path.getsize(file_path) > PUBLIC_IMAGE_MAX_BYTES:
            return False
        if _has_avif_file_type(file_path):
            if not _is_clean_avif_container(file_path):
                return False
            return not _pillow_reads_avif() or _decodes_as_public_format(file_path)
        return _decodes_as_public_format(file_path)
    except (
        OSError, SyntaxError, ValueError, struct.error, Image.DecompressionBombError
    ):
        return False


def sanitize_public_image(file_path: str) -> None:
    """Rewrite an accepted image in place so it holds only its pixels.

    Re-encoding drops everything the decoder does not need to draw the image: EXIF
    (including GPS coordinates), XMP, comments and text chunks, and any bytes appended
    after the image's end marker. The ICC color profile, transparency and animation are
    kept, and a photo whose EXIF says it is rotated is turned upright first, since
    the tag that said so is gone.

    Call only on a file is_allowed_public_image accepted. An AVIF is left as it is: its
    container check already rejected appended bytes and metadata items.
    """
    if _has_avif_file_type(file_path):
        return

    sanitized_path = file_path + ".sanitized"
    with Image.open(file_path) as image:
        if image.format in ("JPEG", "MPO"):
            _save_jpeg(image, sanitized_path)
        elif getattr(image, "is_animated", False):
            _save_animation(image, sanitized_path)
        else:
            _save_still(image, sanitized_path)
    os.replace(sanitized_path, file_path)


def _save_jpeg(image: Image.Image, target: str) -> None:
    icc_profile = image.info.get("icc_profile")
    upright = image.getexif().get(EXIF_ORIENTATION_TAG, 1) == 1
    if upright and isinstance(image, JpegImagePlugin.JpegImageFile):
        # Reusing the source's quantization tables and chroma subsampling keeps the
        # re-encode as close to lossless as JPEG allows. Only the first picture of an
        # MPO (a JpegImageFile subclass) is written.
        # Read through getattr: Pillow 10's type hints omit both, though they exist at
        # runtime in every version.
        options: dict[str, Any] = {"qtables": getattr(image, "quantization")}
        subsampling = getattr(JpegImagePlugin, "get_sampling")(image)
        if subsampling != -1:
            options["subsampling"] = subsampling
        _without_metadata(image).save(
            target, format="JPEG", icc_profile=icc_profile, **options
        )
        return
    rotated = ImageOps.exif_transpose(image)
    _without_metadata(rotated).save(
        target,
        format="JPEG",
        quality=JPEG_REENCODE_QUALITY,
        icc_profile=icc_profile,
    )


def _save_still(image: Image.Image, target: str) -> None:
    image_format = image.format
    options = _format_options(image)
    if image.getexif().get(EXIF_ORIENTATION_TAG, 1) != 1:
        image = ImageOps.exif_transpose(image)
    _without_metadata(image).save(target, format=image_format, **options)


def _save_animation(image: Image.Image, target: str) -> None:
    image_format = image.format
    options = _format_options(image)
    frames: list[Image.Image] = []
    durations: list[int] = []
    for frame in ImageSequence.Iterator(image):
        # copy() decodes the frame, which is when some formats set its duration.
        copied = frame.copy()
        durations.append(int(copied.info.get("duration") or 0))
        frames.append(_without_metadata(copied))
    frames[0].save(
        target,
        format=image_format,
        save_all=True,
        append_images=frames[1:],
        duration=durations,
        loop=image.info.get("loop", 0),
        **options,
    )


def _without_metadata(image: Image.Image) -> Image.Image:
    """The image, with the metadata Pillow would otherwise copy into a save removed."""
    for key in COPIED_METADATA_KEYS:
        image.info.pop(key, None)
    return image


def _format_options(image: Image.Image) -> dict[str, Any]:
    """Save options that keep how the image looks, and nothing else.

    Pillow writes EXIF, XMP and text only when asked to, so leaving them out of these
    options is what drops them.
    """
    options: dict[str, Any] = {}
    icc_profile = image.info.get("icc_profile")
    if icc_profile:
        options["icc_profile"] = icc_profile
    if image.format == "WEBP":
        lossless = _is_lossless_webp(image)
        options["lossless"] = lossless
        if not lossless:
            options["quality"] = WEBP_LOSSY_QUALITY
    if image.format == "GIF" and "transparency" in image.info:
        options["transparency"] = image.info["transparency"]
    return options


def _is_lossless_webp(image: Image.Image) -> bool:
    # A lossless WebP stores its image in a VP8L chunk; a lossy one uses VP8.
    file_path = getattr(image, "filename", "")
    if not file_path:
        return False
    with open(file_path, "rb") as handle:
        header = handle.read(64)
    return b"VP8L" in header


def _decodes_as_public_format(file_path: str) -> bool:
    with Image.open(file_path) as image:
        if image.format not in PUBLIC_IMAGE_FORMATS:
            return False
        # verify() walks the image data, so a valid header followed by junk fails.
        image.verify()
    # verify() leaves the image unusable; reopen and decode the pixels to catch
    # truncated data that verify() alone lets through.
    with Image.open(file_path) as image:
        image.load()
    return True


def _pillow_reads_avif() -> bool:
    Image.init()
    return "AVIF" in Image.ID


def _has_avif_file_type(file_path: str) -> bool:
    """True if the file opens with an `ftyp` box naming an AVIF brand."""
    with open(file_path, "rb") as handle:
        head = handle.read(64)
    if len(head) < _BOX_HEADER.size:
        return False
    size, box_type = _BOX_HEADER.unpack_from(head, 0)
    if box_type != b"ftyp" or not _BOX_HEADER.size <= size <= len(head):
        return False
    return _has_avif_brand(head[_BOX_HEADER.size:size])


def _is_clean_avif_container(file_path: str) -> bool:
    """True if the file is an AVIF whose boxes fill it exactly and hold no metadata."""
    with open(file_path, "rb") as handle:
        data = handle.read()

    boxes = _read_boxes(data, 0, len(data))
    if not boxes:
        return False
    first_type, first_start, first_end = boxes[0]
    if first_type != b"ftyp" or not _has_avif_brand(data[first_start:first_end]):
        return False
    if not AVIF_REQUIRED_BOXES.issubset(box_type for box_type, _, _ in boxes):
        return False

    for box_type, start, end in boxes:
        if box_type == b"meta":
            item_types = _avif_item_types(data, start, end)
            if item_types is None or item_types & AVIF_METADATA_ITEM_TYPES:
                return False
    return True


def _avif_item_types(data: bytes, start: int, end: int) -> set[bytes] | None:
    """The item types listed in a `meta` box's `iinf`, or None if it is malformed."""
    children = _read_boxes(data, start + _FULL_BOX_HEADER_SIZE, end)
    if children is None:
        return None
    item_types: set[bytes] = set()
    for box_type, child_start, child_end in children:
        if box_type != b"iinf":
            continue
        version = data[child_start]
        entry_count_size = 2 if version == 0 else 4
        entries = _read_boxes(
            data, child_start + _FULL_BOX_HEADER_SIZE + entry_count_size, child_end
        )
        if entries is None:
            return None
        for entry_type, entry_start, entry_end in entries:
            if entry_type != b"infe":
                continue
            entry_version = data[entry_start]
            if entry_version < 2:
                # Versions 0 and 1 predate item types and cannot name an Exif item.
                continue
            item_id_size = 2 if entry_version == 2 else 4
            # item_ID, then item_protection_index (2 bytes), then item_type.
            type_start = entry_start + _FULL_BOX_HEADER_SIZE + item_id_size + 2
            if type_start + 4 > entry_end:
                return None
            item_types.add(data[type_start:type_start + 4])
    return item_types


def _read_boxes(
    data: bytes, start: int, end: int
) -> list[tuple[bytes, int, int]] | None:
    """Parse the boxes between start and end as (type, payload start, end).

    Returns None unless the boxes exactly fill the range.
    """
    boxes: list[tuple[bytes, int, int]] = []
    offset = start
    while offset < end:
        if offset + _BOX_HEADER.size > end:
            return None
        size, box_type = _BOX_HEADER.unpack_from(data, offset)
        header_size = _BOX_HEADER.size
        if size == 1:
            if offset + header_size + _LARGE_SIZE.size > end:
                return None
            (size,) = _LARGE_SIZE.unpack_from(data, offset + header_size)
            header_size += _LARGE_SIZE.size
        elif size == 0:
            size = end - offset
        if size < header_size or offset + size > end:
            return None
        boxes.append((box_type, offset + header_size, offset + size))
        offset += size
    return boxes


def _has_avif_brand(file_type_payload: bytes) -> bool:
    # Major brand (4 bytes), minor version (4 bytes), then compatible brands (4 each).
    if len(file_type_payload) < 8 or len(file_type_payload) % 4:
        return False
    brands = {file_type_payload[0:4]} | {
        file_type_payload[i:i + 4] for i in range(8, len(file_type_payload), 4)
    }
    return bool(brands & AVIF_BRANDS)
