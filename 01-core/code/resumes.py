# ================================================================================
# Resumes API
#
# Handles CRUD operations for user resumes.
#
# Design
# - Resume text is stored in S3
# - Metadata is stored in DynamoDB
# - Partition key groups all user objects together
#
# DynamoDB Keys
#   pk = USER#<user_id>
#   sk = RESUME#<resume_id>
#
# S3 Path
#   users/USER#<user_id>/resumes/RESUME#<resume_id>.txt
# ================================================================================

import json
import os
import uuid
from datetime import datetime, timezone

import boto3
from boto3.dynamodb.conditions import Key

# --------------------------------------------------------------------------------
# AWS clients
# --------------------------------------------------------------------------------

table = boto3.resource("dynamodb").Table(os.environ["TABLE_NAME"])
s3 = boto3.client("s3")

BACKEND_BUCKET = os.environ["BACKEND_BUCKET_NAME"]


# --------------------------------------------------------------------------------
# Common helpers
# --------------------------------------------------------------------------------

# --------------------------------------------------------------------------------
# Function: utc_now
#
# Purpose
# Returns the current UTC timestamp in ISO 8601 format, truncated to
# second precision.
#
# Returns
# - ISO 8601 timestamp string (no microseconds)
# --------------------------------------------------------------------------------
def utc_now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


# --------------------------------------------------------------------------------
# Function: response
#
# Purpose
# Builds a standard API Gateway response dict.
#
# Arguments
# - status_code : HTTP status code integer
# - body        : Python object to serialize as the JSON response body
#
# Returns
# - dict with statusCode and body keys
# --------------------------------------------------------------------------------
def response(status_code, body):
    return {
        "statusCode": status_code,
        "body": json.dumps(body)
    }


# --------------------------------------------------------------------------------
# Function: get_user_id
#
# Purpose
# Extracts the authenticated user ID from the Cognito JWT claims on
# the API Gateway event.
#
# Arguments
# - event : API Gateway Lambda event dict
#
# Returns
# - cognito:username, sub, or "demo" as fallback
# --------------------------------------------------------------------------------
def get_user_id(event):
    claims = (
        event.get("requestContext", {})
        .get("authorizer", {})
        .get("jwt", {})
        .get("claims", {})
    )

    return claims.get("cognito:username") or claims.get("sub") or "demo"


# --------------------------------------------------------------------------------
# Function: get_resume_id
#
# Purpose
# Extracts the resume_id path parameter from the API Gateway event.
#
# Arguments
# - event : API Gateway Lambda event dict
#
# Returns
# - resume_id string or None if not present
# --------------------------------------------------------------------------------
def get_resume_id(event):
    path_params = event.get("pathParameters") or {}
    return path_params.get("resume_id")


# --------------------------------------------------------------------------------
# Function: build_keys
#
# Purpose
# Derives the DynamoDB partition key, sort key, and S3 object key for
# a given user and resume ID pair.
#
# Arguments
# - user_id   : authenticated user identifier
# - resume_id : UUID of the resume record
#
# Returns
# - tuple of (pk, sk, s3_key)
# --------------------------------------------------------------------------------
def build_keys(user_id, resume_id):
    pk = f"USER#{user_id}"
    sk = f"RESUME#{resume_id}"
    s3_key = f"users/{pk}/resumes/{sk}.txt"
    return pk, sk, s3_key


# --------------------------------------------------------------------------------
# Function: get_resume_item
#
# Purpose
# Fetches a single resume record from DynamoDB by its composite key.
#
# Arguments
# - pk : DynamoDB partition key (USER#<user_id>)
# - sk : DynamoDB sort key (RESUME#<resume_id>)
#
# Returns
# - DynamoDB item dict or None if not found
# --------------------------------------------------------------------------------
def get_resume_item(pk, sk):
    result = table.get_item(
        Key={
            "pk": pk,
            "sk": sk
        }
    )
    return result.get("Item")


# --------------------------------------------------------------------------------
# POST /resumes
#
# Creates a resume record. Stores text in S3 and metadata in DynamoDB.
# --------------------------------------------------------------------------------

def create_resume(event):

    user_id = get_user_id(event)

    body = json.loads(event["body"])
    name = body.get("name", "").strip()
    resume_text = body.get("resume", "").strip()

    if not name:
        return response(400, {"error": "name is required"})

    if not resume_text:
        return response(400, {"error": "resume is required"})

    resume_id = str(uuid.uuid4())
    pk, sk, s3_key = build_keys(user_id, resume_id)
    now = utc_now()

    s3.put_object(
        Bucket=BACKEND_BUCKET,
        Key=s3_key,
        Body=resume_text.encode("utf-8"),
        ContentType="text/plain"
    )

    table.put_item(
        Item={
            "pk": pk,
            "sk": sk,
            "name": name,
            "s3_key": s3_key,
            "created_at": now,
            "updated_at": now
        }
    )

    return response(
        200,
        {
            "resume_id": resume_id,
            "name": name
        }
    )


# --------------------------------------------------------------------------------
# GET /resumes
#
# Returns metadata for all resumes belonging to the authenticated user.
# --------------------------------------------------------------------------------

def list_resumes(event=None):

    user_id = get_user_id(event or {})
    pk = f"USER#{user_id}"

    result = table.query(
        KeyConditionExpression=Key("pk").eq(pk)
    )

    resumes = [
        {
            "resume_id": item["sk"].replace("RESUME#", "", 1),
            "name": item.get("name", ""),
            "created_at": item.get("created_at"),
            "updated_at": item.get("updated_at")
        }
        for item in result.get("Items", [])
        if item["sk"].startswith("RESUME#")
    ]

    return response(200, resumes)


# --------------------------------------------------------------------------------
# GET /resumes/{resume_id}
#
# Returns one resume with metadata and full resume text.
# --------------------------------------------------------------------------------

def get_resume(event):

    user_id = get_user_id(event)
    resume_id = get_resume_id(event)

    if not resume_id:
        return response(400, {"error": "resume_id is required"})

    pk, sk, _ = build_keys(user_id, resume_id)

    item = get_resume_item(pk, sk)
    if not item:
        return response(404, {"error": "resume not found"})

    s3_result = s3.get_object(
        Bucket=BACKEND_BUCKET,
        Key=item["s3_key"]
    )

    resume_text = s3_result["Body"].read().decode("utf-8")

    return response(
        200,
        {
            "resume_id": resume_id,
            "name": item.get("name", ""),
            "resume": resume_text,
            "created_at": item.get("created_at"),
            "updated_at": item.get("updated_at")
        }
    )


# --------------------------------------------------------------------------------
# PUT /resumes/{resume_id}
#
# Replaces resume metadata and text.
# --------------------------------------------------------------------------------

def update_resume(event):

    user_id = get_user_id(event)
    resume_id = get_resume_id(event)

    if not resume_id:
        return response(400, {"error": "resume_id is required"})

    body = json.loads(event["body"])
    name = body.get("name", "").strip()
    resume_text = body.get("resume", "").strip()

    if not name:
        return response(400, {"error": "name is required"})

    if not resume_text:
        return response(400, {"error": "resume is required"})

    pk, sk, s3_key = build_keys(user_id, resume_id)

    item = get_resume_item(pk, sk)
    if not item:
        return response(404, {"error": "resume not found"})

    s3.put_object(
        Bucket=BACKEND_BUCKET,
        Key=s3_key,
        Body=resume_text.encode("utf-8"),
        ContentType="text/plain"
    )

    table.update_item(
        Key={
            "pk": pk,
            "sk": sk
        },
        UpdateExpression=(
            "SET #name = :name, s3_key = :s3_key, updated_at = :updated_at"
        ),
        ExpressionAttributeNames={
            "#name": "name"
        },
        ExpressionAttributeValues={
            ":name": name,
            ":s3_key": s3_key,
            ":updated_at": utc_now()
        }
    )

    return response(
        200,
        {
            "resume_id": resume_id,
            "name": name
        }
    )


# --------------------------------------------------------------------------------
# DELETE /resumes/{resume_id}
#
# Deletes the metadata row from DynamoDB and the resume object from S3.
# --------------------------------------------------------------------------------

def delete_resume(event):

    user_id = get_user_id(event)
    resume_id = get_resume_id(event)

    if not resume_id:
        return response(400, {"error": "resume_id is required"})

    pk, sk, _ = build_keys(user_id, resume_id)

    item = get_resume_item(pk, sk)
    if not item:
        return response(404, {"error": "resume not found"})

    s3.delete_object(
        Bucket=BACKEND_BUCKET,
        Key=item["s3_key"]
    )

    table.delete_item(
        Key={
            "pk": pk,
            "sk": sk
        }
    )

    return response(
        200,
        {
            "resume_id": resume_id,
            "deleted": True
        }
    )