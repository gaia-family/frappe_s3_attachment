# -*- coding: utf-8 -*-
"""Decides whether an uploaded File may be stored as a public S3 object.

Ticking "Is Private" off is not enough on its own. A File becomes public only when all of
these hold, and is uploaded as private otherwise:

1. It is attached to a (DocType, field) pair that an installed app lists in the
   `s3_public_asset_fields` hook, for example in that app's hooks.py:

       s3_public_asset_fields = {
           "Clinic": ["logo", "image"],
       }

   With no hook configured, no File is ever public.
2. The record it is attached to exists and the uploading user may write to it (or, for a
   record that has not been saved yet, may create that DocType).
3. It is a local upload, not a reference to a remote URL.
4. Its bytes are a PNG, JPEG, WebP, GIF or AVIF image of at most 5 MB. A File that
   fails only this check is rejected outright, so an uploader who meant to publish a
   logo sees why.

A File that passes is re-encoded before upload (image_check.sanitize_public_image), so
the public object carries no EXIF, GPS, comments or bytes appended after the image.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Final

import frappe

from frappe_s3_attachment.image_check import (
    PUBLIC_IMAGE_MAX_BYTES,
    is_allowed_public_image,
)

if TYPE_CHECKING:
    from frappe.core.doctype.file.file import File

PUBLIC_ASSET_FIELDS_HOOK: Final = "s3_public_asset_fields"
REMOTE_URL_PREFIXES: Final = ("http://", "https://", "/api/method/")
# Frappe Desk names an unsaved record "new-<doctype-slug>-<random>" while it is being
# filled in, and file uploads made on it carry that name.
UNSAVED_RECORD_PREFIX: Final = "new-"


def get_public_asset_fields() -> dict[str, set[str]]:
    """Return {doctype: set(fieldnames)} merged from every app's hook."""
    configured = frappe.get_hooks(PUBLIC_ASSET_FIELDS_HOOK, default={}) or {}
    return {
        doctype: set(fields)
        for doctype, fields in configured.items()
        if isinstance(fields, (list, tuple, set))
    }


def is_public_asset_field(doctype: str | None, fieldname: str | None) -> bool:
    if not doctype or not fieldname:
        return False
    return fieldname in get_public_asset_fields().get(doctype, set())


def is_remote_reference(file_url: str | None) -> bool:
    return file_url is not None and file_url.startswith(REMOTE_URL_PREFIXES)


def uploader_can_edit_target(doctype: str, name: str | None) -> bool:
    if not name:
        return False
    meta = frappe.get_meta(doctype)
    if meta.issingle:
        return bool(frappe.has_permission(doctype, "write"))
    if frappe.db.exists(doctype, name):
        return bool(frappe.has_permission(doctype, "write", doc=name))
    if name.startswith(UNSAVED_RECORD_PREFIX):
        return bool(frappe.has_permission(doctype, "create"))
    return False


def should_store_public(doc: File, file_path: str) -> bool:
    """True if this not-private File may be uploaded as a public S3 object.

    Throws when the File is headed for an allowlisted field but is not an acceptable
    image, rather than quietly storing a file the uploader expects to be visible.
    """
    if doc.is_private:
        return False
    if is_remote_reference(doc.file_url):
        return False
    if not is_public_asset_field(doc.attached_to_doctype, doc.attached_to_field):
        return False
    if not uploader_can_edit_target(doc.attached_to_doctype, doc.attached_to_name):
        return False
    if not is_allowed_public_image(file_path):
        frappe.throw(
            frappe._(
                "Public images must be PNG, JPEG, WebP, GIF or AVIF files of {0} MB "
                "or less."
            ).format(PUBLIC_IMAGE_MAX_BYTES // (1024 * 1024)),
            title=frappe._("Invalid Image"),
        )
    return True


def is_public_s3_object(file_url: str | None, key: str | None) -> bool:
    """True if file_url is the public URL of the S3 object stored under key.

    A public upload's URL is the object's own S3 address, ending in its key, which the
    File keeps in content_hash.
    """
    return bool(
        file_url
        and key
        and file_url.startswith("https://")
        and file_url.endswith("/" + key)
    )
