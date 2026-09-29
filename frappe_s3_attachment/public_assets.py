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
4. Its bytes decode as a PNG, JPEG or WebP image of at most 5 MB. A File that fails only
   this check is rejected outright, so an uploader who meant to publish a logo sees why.
"""
from __future__ import unicode_literals

import frappe

from frappe_s3_attachment.image_check import (
    PUBLIC_IMAGE_MAX_BYTES,
    is_allowed_public_image,
)

PUBLIC_ASSET_FIELDS_HOOK = "s3_public_asset_fields"
REMOTE_URL_PREFIXES = ("http://", "https://", "/api/method/")
# Frappe Desk names an unsaved record "new-<doctype-slug>-<random>" while it is being
# filled in, and file uploads made on it carry that name.
UNSAVED_RECORD_PREFIX = "new-"


def get_public_asset_fields():
    """Return {doctype: set(fieldnames)} merged from every app's hook."""
    configured = frappe.get_hooks(PUBLIC_ASSET_FIELDS_HOOK, default={}) or {}
    return {
        doctype: set(fields)
        for doctype, fields in configured.items()
        if isinstance(fields, (list, tuple, set))
    }


def is_public_asset_field(doctype, fieldname):
    if not doctype or not fieldname:
        return False
    return fieldname in get_public_asset_fields().get(doctype, set())


def is_remote_reference(file_url):
    return bool(file_url) and file_url.startswith(REMOTE_URL_PREFIXES)


def uploader_can_edit_target(doctype, name):
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


def should_store_public(doc, file_path):
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
                "Public images must be PNG, JPEG or WebP files of {0} MB or less."
            ).format(PUBLIC_IMAGE_MAX_BYTES // (1024 * 1024)),
            title=frappe._("Invalid Image"),
        )
    return True


def prevent_making_file_public(doc, method=None):
    """File validate hook: an existing private File can never be switched to public.

    Public or private is decided once, at upload. Flipping the flag later would not move
    the S3 object anyway, so the File would claim to be public while its object stayed
    private (or the reverse). Upload the image again instead.
    """
    previous = doc.get_doc_before_save()
    if previous and previous.is_private and not doc.is_private:
        frappe.throw(
            frappe._(
                "A private file cannot be made public. Upload the image again instead."
            ),
            title=frappe._("File Must Stay Private"),
        )
