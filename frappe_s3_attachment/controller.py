from __future__ import unicode_literals

import datetime
import os
import random
import re
import string
import uuid
from urllib.parse import quote

import boto3

from botocore.client import Config
from botocore.exceptions import BotoCoreError, ClientError

import frappe


import magic

from frappe_s3_attachment.image_check import sanitize_public_image
from frappe_s3_attachment.public_assets import (
    is_public_s3_object,
    should_store_public,
)

PRIVATE_FILE_METHOD = "frappe_s3_attachment.controller.generate_file"
ATTACHMENT_FIELD_TYPES = ("Attach", "Attach Image")
# CloudFront allows 3,000 paths in progress per distribution; a form needs far fewer.
MAX_INVALIDATION_KEYS = 100


class S3Operations(object):

    def __init__(self):
        """
        Function to initialise the aws settings from frappe S3 File attachment
        doctype.
        """
        self.s3_settings_doc = frappe.get_doc(
            'S3 File Attachment',
            'S3 File Attachment',
        )
        if (
            self.s3_settings_doc.aws_key and
            self.s3_settings_doc.aws_secret
        ):
            self.S3_CLIENT = boto3.client(
                's3',
                aws_access_key_id=self.s3_settings_doc.aws_key,
                aws_secret_access_key=self.s3_settings_doc.aws_secret,
                region_name=self.s3_settings_doc.region_name,
                config=Config(signature_version='s3v4')
            )
        else:
            self.S3_CLIENT = boto3.client(
                's3',
                region_name=self.s3_settings_doc.region_name,
                config=Config(signature_version='s3v4')
            )
        self.BUCKET = self.s3_settings_doc.bucket_name
        self.folder_name = self.s3_settings_doc.folder_name
        # Public images go to their own bucket, behind a CDN, once both are configured.
        # Until then they are public-read objects in BUCKET, as before v1.2.0.
        self.PUBLIC_BUCKET = (
            self.s3_settings_doc.get("public_bucket_name") or ""
        ).strip()
        self.PUBLIC_BASE_URL = (
            self.s3_settings_doc.get("public_base_url") or ""
        ).strip().rstrip("/")
        self.PUBLIC_DISTRIBUTION_ID = (
            self.s3_settings_doc.get("public_distribution_id") or ""
        ).strip()

    @property
    def uses_public_assets_bucket(self):
        return bool(self.PUBLIC_BUCKET and self.PUBLIC_BASE_URL)

    def is_in_public_assets_bucket(self, file_url):
        """True if file_url is served from the public-assets bucket's CDN."""
        return bool(
            self.uses_public_assets_bucket
            and file_url
            and file_url.startswith(self.PUBLIC_BASE_URL + "/")
        )

    def public_url(self, key):
        """The URL a public object stored under key is served from."""
        if self.uses_public_assets_bucket:
            return "{}/{}".format(self.PUBLIC_BASE_URL, key)
        return "{}/{}/{}".format(self.S3_CLIENT.meta.endpoint_url, self.BUCKET, key)

    def strip_special_chars(self, file_name):
        """
        Strips file charachters which doesnt match the regex.
        """
        regex = re.compile('[^0-9a-zA-Z._-]')
        file_name = regex.sub('', file_name)
        return file_name

    def key_generator(self, file_name, parent_doctype, parent_name):
        """
        Generate keys for s3 objects uploaded with file name attached.
        """
        hook_cmd = frappe.get_hooks().get("s3_key_generator")
        if hook_cmd:
            try:
                k = frappe.get_attr(hook_cmd[0])(
                    file_name=file_name,
                    parent_doctype=parent_doctype,
                    parent_name=parent_name
                )
                if k:
                    return k.rstrip('/').lstrip('/')
            except:
                pass

        file_name = file_name.replace(' ', '_')
        file_name = self.strip_special_chars(file_name)
        key = ''.join(
            random.choice(
                string.ascii_uppercase + string.digits) for _ in range(8)
        )

        today = datetime.datetime.now()
        year = today.strftime("%Y")
        month = today.strftime("%m")
        day = today.strftime("%d")

        doc_path = None

        if not doc_path:
            if self.folder_name:
                final_key = self.folder_name + "/" + year + "/" + month + \
                    "/" + day + "/" + parent_doctype + "/" + key + "_" + \
                    file_name
            else:
                final_key = year + "/" + month + "/" + day + "/" + \
                    parent_doctype + "/" + key + "_" + file_name
            return final_key
        else:
            final_key = doc_path + '/' + key + "_" + file_name
            return final_key

    def upload_files_to_s3_with_key(
            self, file_path, file_name, is_private, parent_doctype, parent_name
    ):
        """
        Uploads a new file to S3.
        Strips the file extension to set the content_type in metadata.
        """
        mime_type = magic.from_file(file_path, mime=True)
        key = self.key_generator(file_name, parent_doctype, parent_name)
        content_type = mime_type
        try:
            if is_private:
                self.S3_CLIENT.upload_file(
                    file_path, self.BUCKET, key,
                    ExtraArgs={
                        "ContentType": content_type,
                        "Metadata": {
                            "ContentType": content_type,
                            "file_name": file_name
                        }
                    }
                )
            elif self.uses_public_assets_bucket:
                # No ACL: the bucket has ACLs disabled and only the CDN can read it.
                self.S3_CLIENT.upload_file(
                    file_path, self.PUBLIC_BUCKET, key,
                    ExtraArgs={
                        "ContentType": content_type,
                        "Metadata": {
                            "ContentType": content_type,
                        }
                    }
                )
            else:
                self.S3_CLIENT.upload_file(
                    file_path, self.BUCKET, key,
                    ExtraArgs={
                        "ContentType": content_type,
                        "ACL": 'public-read',
                        "Metadata": {
                            "ContentType": content_type,

                        }
                    }
                )

        except boto3.exceptions.S3UploadFailedError:
            frappe.throw(frappe._("File Upload Failed. Please try again."))
        return key

    def make_object_private(self, key):
        """Remove public read access from an existing object."""
        try:
            self.S3_CLIENT.put_object_acl(
                Bucket=self.BUCKET, Key=key, ACL='private'
            )
        except ClientError:
            frappe.throw(frappe._("Could not make the file private. Please try again."))

    def move_to_private_bucket(self, key):
        """Move an object from the public-assets bucket into BUCKET, under the same key.

        Copied first, so a failure leaves the public object where it was, at worst with
        an unused private copy. The public bucket keeps the deleted version for its
        retention period, readable only from inside the AWS account, not by the CDN.
        """
        try:
            self.S3_CLIENT.copy_object(
                Bucket=self.BUCKET, Key=key,
                CopySource={"Bucket": self.PUBLIC_BUCKET, "Key": key},
            )
            self.S3_CLIENT.delete_object(Bucket=self.PUBLIC_BUCKET, Key=key)
        except ClientError:
            frappe.throw(frappe._("Could not make the file private. Please try again."))

    def invalidate_public_cache(self, key):
        """Ask the CDN to drop its cached copy of key. Returns False if it could not."""
        return self.invalidate_public_paths([key]) is not None

    def invalidate_public_paths(self, keys):
        """Ask the CDN to drop its cached copies of keys, each of which may end in *
        to cover every key with that prefix. Returns the invalidation ID, or None if
        no distribution is configured or the request failed."""
        if not self.PUBLIC_DISTRIBUTION_ID:
            return None
        paths = [cdn_path(key) for key in keys]
        try:
            response = self._client("cloudfront").create_invalidation(
                DistributionId=self.PUBLIC_DISTRIBUTION_ID,
                InvalidationBatch={
                    "Paths": {"Quantity": len(paths), "Items": paths},
                    "CallerReference": uuid.uuid4().hex,
                },
            )
        except (BotoCoreError, ClientError):
            frappe.log_error(
                title="frappe_s3_attachment: CloudFront invalidation failed",
                message=frappe.get_traceback(),
            )
            return None
        return response["Invalidation"]["Id"]

    def _client(self, service):
        if self.s3_settings_doc.aws_key and self.s3_settings_doc.aws_secret:
            return boto3.client(
                service,
                aws_access_key_id=self.s3_settings_doc.aws_key,
                aws_secret_access_key=self.s3_settings_doc.aws_secret,
                region_name=self.s3_settings_doc.region_name,
            )
        return boto3.client(service, region_name=self.s3_settings_doc.region_name)

    def delete_from_s3(self, key, file_url=None):
        """Delete file from s3, from whichever bucket file_url says holds it."""
        if self.s3_settings_doc.delete_file_from_cloud:
            if self.is_in_public_assets_bucket(file_url):
                bucket = self.PUBLIC_BUCKET
            else:
                bucket = self.BUCKET
            try:
                self.S3_CLIENT.delete_object(Bucket=bucket, Key=key)
            except ClientError:
                frappe.throw(frappe._("Access denied: Could not delete file"))

    def read_file_from_s3(self, key):
        """
        Function to read file from a s3 file.
        """
        return self.S3_CLIENT.get_object(Bucket=self.BUCKET, Key=key)

    def get_url(self, key, file_name=None):
        """
        Return url.

        :param bucket: s3 bucket name
        :param key: s3 object key
        """
        if self.s3_settings_doc.signed_url_expiry_time:
            self.signed_url_expiry_time = self.s3_settings_doc.signed_url_expiry_time # noqa
        else:
            self.signed_url_expiry_time = 120
        params = {
                'Bucket': self.BUCKET,
                'Key': key,

        }

        url = self.S3_CLIENT.generate_presigned_url(
            'get_object',
            Params=params,
            ExpiresIn=self.signed_url_expiry_time,
        )

        return url


@frappe.whitelist()
def file_upload_to_s3(doc, method):
    """
    check and upload files to s3. the path check and
    """
    s3_upload = S3Operations()
    path = doc.file_url
    site_path = frappe.utils.get_site_path()
    parent_doctype = doc.attached_to_doctype or 'File'
    parent_name = doc.attached_to_name
    ignore_s3_upload_for_doctype = frappe.local.conf.get('ignore_s3_upload_for_doctype') or ['Data Import']
    if parent_doctype not in ignore_s3_upload_for_doctype:
        # Where Frappe wrote the bytes depends on the flag the File arrived with.
        if not doc.is_private:
            file_path = site_path + '/public' + path
        else:
            file_path = site_path + path
        is_private = 0 if should_store_public(doc, file_path) else 1
        if not is_private:
            sanitize_public_image(file_path)
        key = s3_upload.upload_files_to_s3_with_key(
            file_path, doc.file_name,
            is_private, parent_doctype,
            parent_name
        )

        if is_private:
            file_url = private_file_url(key, doc.file_name)
        else:
            file_url = s3_upload.public_url(key)
        os.remove(file_path)
        frappe.db.sql("""UPDATE `tabFile` SET file_url=%s, folder=%s,
            old_parent=%s, content_hash=%s, is_private=%s WHERE name=%s""", (
            file_url, 'Home/Attachments', 'Home/Attachments', key, is_private, doc.name))

        doc.file_url = file_url
        doc.is_private = is_private

        if parent_doctype and frappe.get_meta(parent_doctype).get('image_field'):
            frappe.db.set_value(parent_doctype, parent_name, frappe.get_meta(parent_doctype).get('image_field'), file_url)

        frappe.db.commit()


def private_file_url(key, file_name):
    """The URL Frappe serves a private S3 object from, through a signed redirect."""
    return """/api/method/{0}?key={1}&file_name={2}""".format(
        PRIVATE_FILE_METHOD, key, file_name
    )


def handle_privacy_change(doc, method=None):
    """File before_validate hook: keep is_private and the S3 object in step.

    Frappe's own handling moves a file between the public and private folders on disk,
    which S3 files are not in, so ticking or unticking "Is Private" on one would fail.

    - Private to public is refused for every File: whether a file may be public is
      decided once, at upload, by the allowlist. Upload the image again instead.
    - Public to private, for a public S3 object, makes the object private (moving it
      out of the public-assets bucket and clearing the CDN's cached copy, or, for a
      public-read object in the attachments bucket, removing its ACL), points the
      File and the record it is attached to at the signed private URL, and skips
      Frappe's File.validate for this save, since everything it would move is done.
      Other Files are left to Frappe.
    """
    if doc.is_new():
        return
    previous = doc.get_doc_before_save()
    if not previous or bool(previous.is_private) == bool(doc.is_private):
        return

    if previous.is_private:
        frappe.throw(
            frappe._(
                "A private file cannot be made public. Upload the image again instead."
            ),
            title=frappe._("File Must Stay Private"),
        )

    if not is_public_s3_object(previous.file_url, previous.content_hash):
        return

    key = previous.content_hash
    s3 = S3Operations()
    if s3.is_in_public_assets_bucket(previous.file_url):
        s3.move_to_private_bucket(key)
        if not s3.invalidate_public_cache(key):
            frappe.msgprint(
                frappe._(
                    "The file is now private, but copies cached by the CDN may stay "
                    "reachable at the old address for up to a day."
                ),
                title=frappe._("Cached Copies Not Cleared"),
                indicator="orange",
            )
    else:
        s3.make_object_private(key)
    new_url = private_file_url(key, doc.file_name)
    repoint_attached_fields(doc, previous.file_url, new_url)
    doc.file_url = new_url
    doc.flags.ignore_validate = True


def repoint_attached_fields(doc, old_url, new_url):
    """Update every attachment field on the attached record that still holds old_url."""
    if not doc.attached_to_doctype or not doc.attached_to_name:
        return
    meta = frappe.get_meta(doc.attached_to_doctype)
    fieldnames = [
        field.fieldname for field in meta.fields
        if field.fieldtype in ATTACHMENT_FIELD_TYPES
    ]
    if not fieldnames:
        return

    if meta.issingle:
        for fieldname in fieldnames:
            if frappe.db.get_single_value(doc.attached_to_doctype, fieldname) == old_url:
                frappe.db.set_single_value(doc.attached_to_doctype, fieldname, new_url)
        return

    values = frappe.db.get_value(
        doc.attached_to_doctype, doc.attached_to_name, fieldnames, as_dict=True
    ) or {}
    for fieldname, value in values.items():
        if value == old_url:
            frappe.db.set_value(
                doc.attached_to_doctype, doc.attached_to_name, fieldname, new_url,
                update_modified=False,
            )


def cdn_path(key):
    """The CloudFront invalidation path for key. A trailing * stays a wildcard."""
    if key.endswith("*"):
        return "/" + quote(key[:-1]) + "*"
    return "/" + quote(key)


def keys_to_invalidate(text, base_url):
    """Parse one key or public URL per line into a deduplicated list of keys."""
    keys = []
    for line in (text or "").splitlines():
        value = line.strip()
        if not value:
            continue
        if base_url and value.startswith(base_url + "/"):
            value = value[len(base_url) + 1:]
        elif value.startswith(("http://", "https://")):
            frappe.throw(
                frappe._("{0} is not served from {1}.").format(value, base_url),
                title=frappe._("Not a Public Asset"),
            )
        value = value.lstrip("/")
        if "*" in value[:-1]:
            frappe.throw(
                frappe._("{0}: a * may only end a key.").format(value),
                title=frappe._("Invalid Key"),
            )
        if value not in keys:
            keys.append(value)
    if not keys:
        frappe.throw(frappe._("Enter at least one key or URL."))
    if len(keys) > MAX_INVALIDATION_KEYS:
        frappe.throw(
            frappe._("Clear at most {0} keys at a time, or use a key ending in *.")
            .format(MAX_INVALIDATION_KEYS)
        )
    return keys


@frappe.whitelist(methods=["POST"])
def invalidate_public_assets(keys):
    """Clear the CDN's cached copies of public assets, given one key or URL per line.

    For an object replaced or removed outside the app, or a cached copy that outlived a
    failed invalidation. Only clears caches: it never deletes or changes an object.
    """
    frappe.only_for("System Manager")
    s3 = S3Operations()
    if not s3.PUBLIC_DISTRIBUTION_ID:
        frappe.throw(frappe._("Set the CloudFront Distribution ID first."))
    parsed = keys_to_invalidate(keys, s3.PUBLIC_BASE_URL)
    invalidation_id = s3.invalidate_public_paths(parsed)
    if not invalidation_id:
        frappe.throw(
            frappe._("CloudFront refused the request. See the Error Log for details.")
        )
    return {
        "invalidation_id": invalidation_id,
        "paths": [cdn_path(key) for key in parsed],
    }


@frappe.whitelist()
def generate_file(key=None, file_name=None):
    """
    Function to stream file from s3.
    """
    if key:
        s3_upload = S3Operations()
        signed_url = s3_upload.get_url(key, file_name)
        frappe.local.response["type"] = "redirect"
        frappe.local.response["location"] = signed_url
    else:
        frappe.local.response['body'] = "Key not found."
    return


def upload_existing_files_s3(name, file_name):
    """
    Function to upload all existing files.
    """
    file_doc_name = frappe.db.get_value('File', {'name': name})
    if file_doc_name:
        doc = frappe.get_doc('File', name)
        s3_upload = S3Operations()
        path = doc.file_url
        site_path = frappe.utils.get_site_path()
        parent_doctype = doc.attached_to_doctype
        parent_name = doc.attached_to_name
        if not doc.is_private:
            file_path = site_path + '/public' + path
        else:
            file_path = site_path + path
        is_private = 0 if should_store_public(doc, file_path) else 1
        if not is_private:
            sanitize_public_image(file_path)
        key = s3_upload.upload_files_to_s3_with_key(
            file_path, doc.file_name,
            is_private, parent_doctype,
            parent_name
        )

        if is_private:
            method = PRIVATE_FILE_METHOD
            file_url = """/api/method/{0}?key={1}""".format(method, key)
        else:
            file_url = s3_upload.public_url(key)
        os.remove(file_path)
        doc = frappe.db.sql("""UPDATE `tabFile` SET file_url=%s, folder=%s,
            old_parent=%s, content_hash=%s, is_private=%s WHERE name=%s""", (
            file_url, 'Home/Attachments', 'Home/Attachments', key, is_private, doc.name))
        frappe.db.commit()
    else:
        pass


def s3_file_regex_match(file_url):
    """
    Match the public file regex match.
    """
    return re.match(
        r'^(https:|/api/method/frappe_s3_attachment.controller.generate_file)',
        file_url
    )


@frappe.whitelist()
def migrate_existing_files():
    """
    Function to migrate the existing files to s3.
    """
    frappe.only_for("System Manager")
    # get_all_files_from_public_folder_and_upload_to_s3
    files_list = frappe.get_all(
        'File',
        fields=['name', 'file_url', 'file_name']
    )
    for file in files_list:
        if file['file_url']:
            if not s3_file_regex_match(file['file_url']):
                upload_existing_files_s3(file['name'], file['file_name'])
    return True


def delete_from_cloud(doc, method):
    """Delete file from s3"""
    s3 = S3Operations()
    s3.delete_from_s3(doc.content_hash, doc.file_url)


@frappe.whitelist()
def ping():
    """
    Test function to check if api function work.
    """
    return "pong"
