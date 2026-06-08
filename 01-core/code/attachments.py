# ================================================================================
# attachments.py
#
# Purpose
# Handles file attachment CRUD for scored jobs.
#
# Key Responsibilities
# - Upload attachments as base64 JSON (no signed URLs or multipart)
# - Download attachments as base64 JSON
# - Delete attachments from S3 and from the job's DynamoDB array
# - Enforce a 5-attachment cap per job and a 10 MB per-file size limit
#
# DynamoDB
#   Attachments are stored as a List attribute on the job item.
#   Delete uses read-modify-write to avoid equality fragility on dicts.
#
# S3 Layout
#   users/USER#<user_id>/jobs/JOB#<job_id>/attachments/<att_id>/<filename>
# ================================================================================

import base64
import json
import os
import uuid
from datetime import datetime, timezone
from decimal import Decimal

import boto3

# ------------------------------------------------------------------------------
# AWS clients
# ------------------------------------------------------------------------------

table = boto3.resource("dynamodb").Table(os.environ["TABLE_NAME"])
s3 = boto3.client("s3")

BACKEND_BUCKET = os.environ["BACKEND_BUCKET_NAME"]

MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024   # 10 MB
MAX_ATTACHMENTS      = 5


# ------------------------------------------------------------------------------
# Helpers (self-contained copies to avoid circular imports)
# ------------------------------------------------------------------------------

def utc_now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def get_user_id(event):
    claims = (
        event.get("requestContext", {})
        .get("authorizer", {})
        .get("jwt", {})
        .get("claims", {})
    )
    return claims.get("cognito:username") or claims.get("sub") or "demo"


def json_serializer(value):
    if isinstance(value, Decimal):
        return int(value) if value % 1 == 0 else float(value)
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def response(code, body):
    return {
        "statusCode": code,
        "body": json.dumps(body, default=json_serializer)
    }


def get_job_item(user_id, job_id):
    return table.get_item(
        Key={"pk": f"USER#{user_id}", "sk": f"JOB#{job_id}"}
    ).get("Item")


# ------------------------------------------------------------------------------
# GET /jobs/{job_id}/attachments
# ------------------------------------------------------------------------------

# --------------------------------------------------------------------------------
# Function: list_attachments
#
# Purpose
# Returns the attachments array stored on the job's DynamoDB item.
#
# Arguments
# - event : API Gateway Lambda event dict
#
# Returns
# - List of attachment metadata dicts
# --------------------------------------------------------------------------------
def list_attachments(event):
    user_id = get_user_id(event)
    job_id  = (event.get("pathParameters") or {}).get("job_id", "")

    item = get_job_item(user_id, job_id)
    if not item:
        return response(404, {"error": "job not found"})

    return response(200, list(item.get("attachments") or []))


# ------------------------------------------------------------------------------
# POST /jobs/{job_id}/attachments
# ------------------------------------------------------------------------------

# --------------------------------------------------------------------------------
# Function: upload_attachment
#
# Purpose
# Decodes base64 file data from the request body, writes it to S3, and
# appends an attachment metadata dict to the job's DynamoDB item.
#
# Arguments
# - event : API Gateway Lambda event dict
#
# Returns
# - Attachment metadata dict on success
# --------------------------------------------------------------------------------
def upload_attachment(event):
    user_id = get_user_id(event)
    job_id  = (event.get("pathParameters") or {}).get("job_id", "")
    body    = json.loads(event.get("body") or "{}")

    item = get_job_item(user_id, job_id)
    if not item:
        return response(404, {"error": "job not found"})

    existing = list(item.get("attachments") or [])
    if len(existing) >= MAX_ATTACHMENTS:
        return response(400, {"error": "attachment limit reached (5 max)"})

    filename     = (body.get("filename") or "").strip()
    content_type = (body.get("content_type") or "application/octet-stream").strip()
    data_b64     = body.get("data") or ""

    if not filename or not data_b64:
        return response(400, {"error": "filename and data are required"})

    try:
        raw = base64.b64decode(data_b64)
    except Exception:
        return response(400, {"error": "invalid base64 data"})

    if len(raw) > MAX_ATTACHMENT_BYTES:
        return response(400, {"error": "file exceeds 10 MB limit"})

    att_id  = str(uuid.uuid4())
    pk      = f"USER#{user_id}"
    s3_key  = f"users/{pk}/jobs/JOB#{job_id}/attachments/{att_id}/{filename}"

    s3.put_object(
        Bucket=BACKEND_BUCKET,
        Key=s3_key,
        Body=raw,
        ContentType=content_type
    )

    att = {
        "attachment_id": att_id,
        "filename":      filename,
        "content_type":  content_type,
        "size":          len(raw),
        "uploaded_at":   utc_now(),
    }
    existing.append(att)

    table.update_item(
        Key={"pk": pk, "sk": f"JOB#{job_id}"},
        UpdateExpression="SET attachments = :atts, updated_at = :ts",
        ExpressionAttributeValues={
            ":atts": existing,
            ":ts":   utc_now(),
        }
    )

    return response(200, att)


# ------------------------------------------------------------------------------
# GET /jobs/{job_id}/attachments/{attachment_id}
# ------------------------------------------------------------------------------

# --------------------------------------------------------------------------------
# Function: download_attachment
#
# Purpose
# Reads the attachment from S3 and returns it base64-encoded in the
# response body so the browser can reconstruct and trigger a download.
#
# Arguments
# - event : API Gateway Lambda event dict
#
# Returns
# - {"attachment_id", "filename", "content_type", "data"} where data is b64
# --------------------------------------------------------------------------------
def download_attachment(event):
    user_id = get_user_id(event)
    params  = event.get("pathParameters") or {}
    job_id  = params.get("job_id", "")
    att_id  = params.get("attachment_id", "")

    item = get_job_item(user_id, job_id)
    if not item:
        return response(404, {"error": "job not found"})

    att = next(
        (a for a in (item.get("attachments") or [])
         if a.get("attachment_id") == att_id),
        None
    )
    if not att:
        return response(404, {"error": "attachment not found"})

    pk     = f"USER#{user_id}"
    s3_key = f"users/{pk}/jobs/JOB#{job_id}/attachments/{att_id}/{att['filename']}"

    try:
        raw = s3.get_object(Bucket=BACKEND_BUCKET, Key=s3_key)["Body"].read()
    except Exception:
        return response(404, {"error": "attachment file not found in storage"})

    return response(200, {
        "attachment_id": att_id,
        "filename":      att["filename"],
        "content_type":  att.get("content_type", "application/octet-stream"),
        "data":          base64.b64encode(raw).decode("utf-8"),
    })


# ------------------------------------------------------------------------------
# DELETE /jobs/{job_id}/attachments/{attachment_id}
# ------------------------------------------------------------------------------

# --------------------------------------------------------------------------------
# Function: delete_attachment
#
# Purpose
# Removes the attachment from S3 then filters it out of the job's
# DynamoDB attachments list using read-modify-write (not list_remove,
# which requires exact dict equality).
#
# Arguments
# - event : API Gateway Lambda event dict
#
# Returns
# - {"deleted": <attachment_id>}
# --------------------------------------------------------------------------------
def delete_attachment(event):
    user_id = get_user_id(event)
    params  = event.get("pathParameters") or {}
    job_id  = params.get("job_id", "")
    att_id  = params.get("attachment_id", "")

    item = get_job_item(user_id, job_id)
    if not item:
        return response(404, {"error": "job not found"})

    attachments = list(item.get("attachments") or [])
    att = next(
        (a for a in attachments if a.get("attachment_id") == att_id),
        None
    )
    if not att:
        return response(404, {"error": "attachment not found"})

    pk     = f"USER#{user_id}"
    s3_key = f"users/{pk}/jobs/JOB#{job_id}/attachments/{att_id}/{att['filename']}"

    try:
        s3.delete_object(Bucket=BACKEND_BUCKET, Key=s3_key)
    except Exception:
        pass  # Already gone from S3; still clean up Firestore

    updated = [a for a in attachments if a.get("attachment_id") != att_id]
    table.update_item(
        Key={"pk": pk, "sk": f"JOB#{job_id}"},
        UpdateExpression="SET attachments = :atts, updated_at = :ts",
        ExpressionAttributeValues={
            ":atts": updated,
            ":ts":   utc_now(),
        }
    )

    return response(200, {"deleted": att_id})
