# ================================================================================
# Folders API
#
# Purpose
# Handles CRUD operations for job folder objects.
#
# Design
# - Folder metadata stored in DynamoDB single-table alongside jobs/resumes
# - pk = USER#<user_id>, sk = FOLDER#<folder_id>
# - Deleting a folder clears folder_id from any jobs that referenced it
# ================================================================================

import json
import os
import uuid
from datetime import datetime, timezone

import boto3
from boto3.dynamodb.conditions import Key

# ------------------------------------------------------------------------------
# AWS clients
# ------------------------------------------------------------------------------

table = boto3.resource("dynamodb").Table(os.environ["TABLE_NAME"])


def utc_now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def response(code, body):
    return {
        "statusCode": code,
        "body": json.dumps(body)
    }


def get_user_id(event):
    """Extract authenticated user ID from the Cognito JWT claims."""
    claims = (
        event.get("requestContext", {})
        .get("authorizer", {})
        .get("jwt", {})
        .get("claims", {})
    )
    return claims.get("cognito:username") or claims.get("sub") or "demo"


def get_folder_id(event):
    """Extract folder_id path parameter from the API Gateway event."""
    path = event.get("pathParameters") or {}
    return path.get("folder_id")


# ------------------------------------------------------------------------------
# GET /folders
# ------------------------------------------------------------------------------

def list_folders(event):
    user_id = get_user_id(event)
    pk = f"USER#{user_id}"

    result = table.query(KeyConditionExpression=Key("pk").eq(pk))

    folders = [
        {
            "folder_id": item["sk"].replace("FOLDER#", "", 1),
            "name":       item.get("name", ""),
            "created_at": item.get("created_at", ""),
        }
        for item in result.get("Items", [])
        if item["sk"].startswith("FOLDER#")
    ]

    # Sort in Python — avoids requiring a composite index on this table
    folders.sort(key=lambda f: f["created_at"])
    return response(200, folders)


# ------------------------------------------------------------------------------
# POST /folders
# ------------------------------------------------------------------------------

def create_folder(event):
    user_id = get_user_id(event)
    body = json.loads(event["body"])

    name = (body.get("name") or "").strip()
    if not name:
        return response(400, {"error": "name is required"})

    folder_id = str(uuid.uuid4())
    now = utc_now()

    table.put_item(Item={
        "pk":         f"USER#{user_id}",
        "sk":         f"FOLDER#{folder_id}",
        "folder_id":  folder_id,
        "name":       name,
        "created_at": now,
    })

    return response(200, {"folder_id": folder_id, "name": name})


# ------------------------------------------------------------------------------
# DELETE /folders/{folder_id}
# ------------------------------------------------------------------------------

def delete_folder(event):
    user_id   = get_user_id(event)
    folder_id = get_folder_id(event)

    if not folder_id:
        return response(400, {"error": "folder_id is required"})

    pk = f"USER#{user_id}"

    # Clear folder_id from any jobs that referenced this folder
    result = table.query(KeyConditionExpression=Key("pk").eq(pk))
    for item in result.get("Items", []):
        if item["sk"].startswith("JOB#") and item.get("folder_id") == folder_id:
            table.update_item(
                Key={"pk": pk, "sk": item["sk"]},
                UpdateExpression="REMOVE folder_id"
            )

    table.delete_item(Key={"pk": pk, "sk": f"FOLDER#{folder_id}"})
    return response(200, {"deleted": True})
