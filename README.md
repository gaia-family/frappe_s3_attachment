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
    to be migrated to s3. Only a System Manager can run it.
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
4. Its bytes are a PNG, JPEG, WebP, GIF or AVIF image of 5 MB or less. SVG is not accepted.
   AVIF is decoded when the installed Pillow can read it; on Pillow 10 (Frappe v15) its
   container structure is checked instead, and an AVIF carrying EXIF or XMP, or with
   bytes after its last box, is refused.
   An upload to an allowlisted field that fails this check is rejected with an error.

A file that passes is re-encoded before it is uploaded, so the public object holds only
the image. EXIF (including GPS), XMP, comments, text chunks and any bytes appended after
the image (a phone's "motion photo" video, or a polyglot payload) are dropped. The color
profile, transparency and animation are kept, a photo whose EXIF says it is rotated is
turned upright, and a JPEG is re-encoded with its own quantization tables. AVIF is
uploaded as it is, having passed the checks above.

Changing "Is Private" on a stored file:

* Private to public is refused for every file. Upload the image again instead, so it goes
  through the checks above.
* Public to private, on a public S3 object, makes the object private, switches the File's
  `file_url` to the signed private URL, and updates any attachment field on the attached
  record that held the old URL. An object in the public-assets bucket (below) is moved
  into the attachments bucket and its cached copy is cleared from the CDN; if clearing
  fails, the file is still made private and the user is warned that cached copies may
  stay reachable until they expire. A public-read object in the attachments bucket has
  its ACL set to private.

#### Public-assets bucket

Public images can live in their own bucket, served through a CDN, so that the attachments
bucket never needs to allow public access. Set these in "S3 File Attachment":

* Public Bucket Name: a bucket with ACLs disabled (`BucketOwnerEnforced`) and all Block
  Public Access settings on, readable only by the CDN (for CloudFront, through Origin
  Access Control).
* Public Base URL: the CDN address, e.g. `https://assets.example.com`. A public file's
  `file_url` is this followed by its key.
* CloudFront Distribution ID (optional): lets the app clear a cached copy when a public
  file is made private.

With both the bucket and the base URL set, public uploads go to that bucket with no ACL.
With either missing, they are public-read objects in the attachments bucket, as before.
Deletes go to whichever bucket the file's URL points at, so files from both setups keep
working side by side.

The credentials the app runs with need, on the public-assets bucket, `s3:PutObject`,
`s3:GetObject` and `s3:DeleteObject` (plus `s3:ListBucket` on the bucket), and
`cloudfront:CreateInvalidation` on the distribution.

##### S3 Configuration (attachments bucket, without a public-assets bucket)

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
