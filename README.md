<a href="https://zerodha.tech"><img src="https://zerodha.tech/static/images/github-badge.svg" align="right" /></a>

## Frappe S3 Attachment

Frappe app to make file upload automatically upload and read from s3.

[![Whatsapp Video](https://img.youtube.com/vi/hutJkHf8e2o/0.jpg)](https://www.youtube.com/watch?v=hutJkHf8e2o)

#### Features.

1. Upload both public and private files to s3.
2. Stream files from S3, when file is viewed everytime.
3. Lets you add S3 credentials
    (aws key, aws secret, bucket name, folder name) through ui and migrate existing
    files.
4. Deletes from s3 whenever a file is deleted in ui.
5. Files are uploaded categorically in the format.
    {s3_folder_path}/{year}/{month}/{day}/{doctype}/{file_hash}

#### Installation.

1. bench get-app git@github.com:gaia-family/frappe_s3_attachment.git
2. bench install-app frappe_s3_attachment

#### Configuration Setup.

1. Open single doctype "s3 File Attachment"
2. Enter (Bucket Name, AWS key, AWS secret, S3 bucket Region name, Folder Name)
    Folder Name- folder name is the default folder path in s3.
3. Migrate existing files lets all the existing files in private and public folders
    to be migrated to s3.
4. Delete From Cloud when selected deletes the file form s3 bucket whenever a file
    is deleted from ui. By default the Delete from cloud will be unchecked.

#### Public files

Unticking "Is Private" does not by itself make a file public. A File is uploaded as a
public object only when every one of these holds; otherwise it is stored private, with a
signed-URL `file_url`:

1. An installed app lists its (DocType, field) pair in the `s3_public_asset_fields` hook.
   With no hook configured, nothing is ever public.

    ```python
    # your_app/hooks.py
    s3_public_asset_fields = {
        "Clinic": ["logo", "image"],
        "Letter Head": ["image", "footer_image"],
    }
    ```

2. The record it is attached to exists and the uploader can write to it (for a record
   not yet saved, the uploader can create that DocType).
3. It is a local upload, not a reference to a remote URL.
4. Its bytes decode as a PNG, JPEG or WebP image of 5 MB or less. SVG is not accepted.
   An upload to an allowlisted field that fails this check is rejected with an error.

An existing private File cannot be switched to public; upload it again instead.

##### S3 Configuration

1. Permission Overview (Based on requirements)

* When createing the bucket make sure that ACL is enabled
* Objects can be public
  * The bucket is not public but anyone with appropriate permissions can grant public access to objects.

2. [Block public access (bucket settings)](https://docs.aws.amazon.com/console/s3/publicaccess)

* Block all public Access - `Off`

    * Block public access to buckets and objects granted through new access control lists (ACLs) - `Off`

    * Block public access to buckets and objects granted through any access control lists (ACLs) - `Off`

    * Block public access to buckets and objects granted through any access control lists (ACLs) - `Off`

    * Block public and cross-account access to buckets and objects through any public bucket or access point policies - `Off`

3. [Bucket policy](https://docs.aws.amazon.com/console/s3/access-policy-language-overview)

`JSON{ "Version": "2012-10-17", "Statement": [ { "Sid": "AddCannedAcl", "Effect": "Allow", "Principal": { "AWS": "arn:aws:iam::<xyz>:user/<S3_USERNAME>" }, "Action": [ "s3:PutObject", "s3:PutObjectAcl" ], "Resource": "arn:aws:s3:::<BUCKET_NAME>/*", "Condition": { "StringEquals": { "s3:x-amz-acl": "public-read" } } } ]}`

#### License

MIT
