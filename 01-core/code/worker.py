# =================================================================================
# worker.py
# SQS worker for V1 job ingestion, extraction, and scoring
#
# V1 flow
# 1) Read SQS message
# 2) Set job status = Scoring
# 3) If source_type == url, retrieve HTML from job URL
# 4) Strip irrelevant HTML and extract visible text
# 5) Call Bedrock to extract:
#    - job_title
#    - company_name
#    - job_text
# 6) Store normalized job_text in S3
# 7) Read resume_snapshot.txt and job_description.txt from S3
# 8) Call Bedrock again to score the resume against the job
# 9) Store job_analysis.txt in S3
# 10) Update DynamoDB with extracted fields and score
# 11) Set job status = Scored
#
# Expected SQS message body
# {
#   "user_id": "<user_id>",
#   "job_id": "<job_id>",
#   "resume_id": "<resume_id>",
#   "source_type": "url" | "raw_text",
#   "job_url": "<url or empty>"
# }
# =================================================================================

import json
import logging
import os
import re
import time
import urllib.request
from datetime import datetime, timezone

import boto3
from botocore.config import Config
from bs4 import BeautifulSoup, Comment

# =================================================================================
# Logging
# =================================================================================

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# =================================================================================
# AWS clients
# =================================================================================

dynamodb = boto3.resource("dynamodb")
table = dynamodb.Table(os.environ["TABLE_NAME"])

TOKEN_LIMIT_DEFAULT = 100_000

bedrock_runtime = boto3.client(
    "bedrock-runtime",
    config=Config(read_timeout=240, connect_timeout=10),
)
s3 = boto3.client("s3")

# =================================================================================
# Environment
# =================================================================================

BACKEND_BUCKET = os.environ["BACKEND_BUCKET_NAME"]
BEDROCK_MODEL_ID = os.environ["BEDROCK_MODEL_ID"]

# =================================================================================
# Constants
# =================================================================================

MAX_SOURCE_TEXT_CHARS = 120000
MIN_JOB_TEXT_CHARS = 100

# Bedrock read timeout in seconds. Set below the Lambda timeout (300s) so a
# slow or hung model call raises a catchable exception rather than letting
# Lambda terminate the process before the error status can be written.
BEDROCK_READ_TIMEOUT_SECONDS = 240

# Tags that generally do not contain useful job-description content.
REMOVE_TAGS = {
    "script",
    "style",
    "noscript",
    "svg",
    "img",
    "picture",
    "source",
    "video",
    "audio",
    "canvas",
    "iframe",
    "object",
    "embed",
    "form",
    "input",
    "button",
    "select",
    "option",
    "textarea",
    "label",
    "nav",
    "footer",
}

# Tags that should introduce visible spacing in extracted text.
BLOCK_TAGS = {
    "p",
    "div",
    "section",
    "article",
    "main",
    "aside",
    "header",
    "li",
    "ul",
    "ol",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "br",
    "tr",
    "table",
}

# =================================================================================
# Generic helpers
# =================================================================================


def utc_now():
    """Return current UTC timestamp without microseconds."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def safe_status_message(message, max_len=500):
    """
    Keep status_message short enough for predictable DynamoDB storage and UI use.
    """
    message = str(message).strip()

    if len(message) <= max_len:
        return message

    return message[: max_len - 3] + "..."


def strip_code_fences(text):
    """
    Remove Markdown code fences if the model returns fenced JSON.
    """
    text = text.strip()

    if not text.startswith("```"):
        return text

    lines = text.splitlines()

    if lines:
        lines = lines[1:]

    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]

    return "\n".join(lines).strip()


def build_job_description_key(user_id, job_id):
    """
    Return the canonical S3 key for the job description artifact.
    """
    return f"users/USER#{user_id}/jobs/JOB#{job_id}/job_description.txt"


def build_resume_snapshot_key(user_id, job_id):
    """
    Return the canonical S3 key for the job-owned resume snapshot artifact.
    """
    return f"users/USER#{user_id}/jobs/JOB#{job_id}/resume_snapshot.txt"


def build_job_analysis_key(user_id, job_id):
    """
    Return the canonical S3 key for the job analysis artifact.
    """
    return f"users/USER#{user_id}/jobs/JOB#{job_id}/job_analysis.txt"


def read_s3_text(key):
    """
    Read a UTF-8 text object from S3.
    """
    result = s3.get_object(Bucket=BACKEND_BUCKET, Key=key)
    return result["Body"].read().decode("utf-8")


def write_s3_text(key, text):
    """
    Write a UTF-8 text object to S3.
    """
    s3.put_object(
        Bucket=BACKEND_BUCKET,
        Key=key,
        Body=text.encode("utf-8"),
        ContentType="text/plain; charset=utf-8",
    )


# =================================================================================
# DynamoDB helpers
# =================================================================================


def accumulate_tokens(user_id, input_tokens, output_tokens):
    """
    Add consumed Bedrock tokens to the user's lifetime usage record.

    Uses ADD so it's safe to call without a pre-existing item; DynamoDB
    will initialise missing numeric attributes to zero before adding.
    """
    total = int(input_tokens or 0) + int(output_tokens or 0)
    if total <= 0:
        return
    try:
        table.update_item(
            Key={"pk": f"USER#{user_id}", "sk": "USER#USAGE"},
            UpdateExpression="ADD tokens_used :n",
            ExpressionAttributeValues={":n": total},
        )
    except Exception:
        # Token tracking is best-effort — never let it block job completion
        logger.exception(
            "Failed to update token usage. user_id=%s tokens=%s",
            user_id, total
        )


def update_job_status(user_id, job_id, status, status_message):
    """
    Update the top-level processing status for a job.
    """
    table.update_item(
        Key={
            "pk": f"USER#{user_id}",
            "sk": f"JOB#{job_id}",
        },
        UpdateExpression=(
            "SET #status = :status, "
            "status_message = :status_message, "
            "updated_at = :updated_at"
        ),
        ExpressionAttributeNames={
            "#status": "status",
        },
        ExpressionAttributeValues={
            ":status": status,
            ":status_message": status_message,
            ":updated_at": utc_now(),
        },
    )


def update_job_title_and_company(user_id, job_id, job_title, company_name):
    """
    Write job_title and company as soon as extraction completes so they
    appear in the UI without waiting for scoring to finish.
    """
    table.update_item(
        Key={
            "pk": f"USER#{user_id}",
            "sk": f"JOB#{job_id}",
        },
        UpdateExpression=(
            "SET job_title = :job_title, "
            "company = :company, "
            "updated_at = :updated_at"
        ),
        ExpressionAttributeValues={
            ":job_title": job_title,
            ":company": company_name,
            ":updated_at": utc_now(),
        },
    )


def update_job_extracted_fields(
    user_id,
    job_id,
    job_title,
    company_name,
    job_description_s3_key,
    score,
):
    """
    Save extracted job metadata and numeric score back to the job record.
    """
    table.update_item(
        Key={
            "pk": f"USER#{user_id}",
            "sk": f"JOB#{job_id}",
        },
        UpdateExpression=(
            "SET job_title = :job_title, "
            "company = :company, "
            "job_description_s3_key = :job_description_s3_key, "
            "score = :score, "
            "updated_at = :updated_at"
        ),
        ExpressionAttributeValues={
            ":job_title": job_title,
            ":company": company_name,
            ":job_description_s3_key": job_description_s3_key,
            ":score": score,
            ":updated_at": utc_now(),
        },
    )


# =================================================================================
# URL retrieval helpers
# =================================================================================


def fetch_url_html(url):
    """
    Retrieve raw HTML from a job posting URL.

    A browser-like User-Agent improves compatibility with some job sites.
    """
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/122.0.0.0 Safari/537.36"
            )
        },
    )

    with urllib.request.urlopen(request, timeout=30) as response:
        charset = response.headers.get_content_charset() or "utf-8"
        return response.read().decode(charset, errors="replace")


# =================================================================================
# HTML cleanup helpers
# =================================================================================


def collapse_whitespace(text):
    """
    Normalize whitespace so the text is easier for Bedrock to process.
    """
    text = text.replace("\r", "\n")
    text = re.sub(r"[ \t\f\v]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def is_hidden(tag):
    """
    Detect common hidden-element patterns.
    """
    if not getattr(tag, "attrs", None):
        return False

    if "hidden" in tag.attrs:
        return True

    style = str(tag.attrs.get("style", "")).lower()
    if "display:none" in style or "display: none" in style:
        return True

    if "visibility:hidden" in style or "visibility: hidden" in style:
        return True

    aria_hidden = str(tag.attrs.get("aria-hidden", "")).lower()
    if aria_hidden == "true":
        return True

    return False


def remove_unwanted_nodes(soup):
    """
    Remove comments, junk tags, and hidden elements.
    """
    for comment in soup.find_all(string=lambda s: isinstance(s, Comment)):
        comment.extract()

    for tag_name in REMOVE_TAGS:
        for tag in soup.find_all(tag_name):
            tag.decompose()

    for tag in soup.find_all(True):
        if is_hidden(tag):
            tag.decompose()


def add_block_separators(soup):
    """
    Add newline spacing around block-like elements before text extraction.
    """
    for tag in soup.find_all(BLOCK_TAGS):
        if tag.name == "br":
            tag.replace_with("\n")
            continue

        if tag.string is not None:
            tag.insert_before("\n")
            tag.insert_after("\n")


def extract_visible_text(html):
    """
    Convert HTML into a cleaned text payload for model extraction.
    """
    soup = BeautifulSoup(html, "html.parser")

    remove_unwanted_nodes(soup)
    add_block_separators(soup)

    parts = []

    title_tag = soup.find("title")
    if title_tag:
        title_text = collapse_whitespace(title_tag.get_text(" ", strip=True))
        if title_text:
            parts.append(f"PAGE TITLE: {title_text}")

    meta_desc = soup.find("meta", attrs={"name": "description"})
    if meta_desc and meta_desc.get("content"):
        meta_text = collapse_whitespace(str(meta_desc["content"]))
        if meta_text:
            parts.append(f"META DESCRIPTION: {meta_text}")

    body = soup.body or soup
    body_text = body.get_text(separator="\n", strip=True)
    body_text = collapse_whitespace(body_text)

    if body_text:
        parts.append("VISIBLE TEXT:")
        parts.append(body_text)

    return "\n\n".join(parts).strip()


# =================================================================================
# Bedrock helpers
# =================================================================================


def extract_job_fields_with_bedrock(visible_text, user_id=None):
    """
    Ask Bedrock to extract structured job fields from visible page text.

    Expected output JSON:
    - job_title
    - company_name
    - job_text
    """
    prompt = f"""
You are extracting structured data from a job posting.

Return valid JSON only.

Required JSON fields:
- job_title
- company_name
- job_text

Rules:
- job_title: best extracted job title, or empty string if unknown
- company_name: best extracted company name, or empty string if unknown
- job_text: plain-text job description, maximum 3000 characters, include
  only role responsibilities and candidate requirements
- Do not wrap the response in markdown
- Do not include any explanation

SOURCE TEXT:
{visible_text[:MAX_SOURCE_TEXT_CHARS]}
""".strip()

    body = {
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 1000,
        "temperature": 0,
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": prompt,
                    }
                ],
            }
        ],
    }

    logger.info(
        "Bedrock extraction call starting. model=%s input_chars=%s",
        BEDROCK_MODEL_ID,
        len(visible_text),
    )

    t0 = time.time()

    response = bedrock_runtime.invoke_model(
        modelId=BEDROCK_MODEL_ID,
        body=json.dumps(body),
        contentType="application/json",
        accept="application/json",
    )

    elapsed = time.time() - t0

    payload = json.loads(response["body"].read())
    usage = payload.get("usage", {})

    logger.info(
        "Bedrock extraction call completed. elapsed_sec=%.1f "
        "input_tokens=%s output_tokens=%s",
        elapsed,
        usage.get("input_tokens", "n/a"),
        usage.get("output_tokens", "n/a"),
    )

    if user_id:
        accumulate_tokens(
            user_id,
            usage.get("input_tokens", 0),
            usage.get("output_tokens", 0),
        )

    text = payload["content"][0]["text"].strip()
    text = strip_code_fences(text)

    return json.loads(text)


def score_resume_with_bedrock(resume_text, job_text, user_id=None):
    """
    Ask Bedrock to score a resume against a job description.

    Expected output JSON:
    - score
    - summary
    """
    prompt = f"""
You are scoring a resume against a job description.

Return valid JSON only.

Required JSON fields:
- score
- summary

Rules:
- score: integer from 0 to 100
- summary: plain-text analysis with exactly three labeled paragraphs in
  this order: "Overview:" (2-3 sentences explaining why the score is
  what it is), "Strengths:" (2-3 sentences on resume positives relative
  to the job), "Weaknesses:" (2-3 sentences on gaps or missing
  qualifications)
- Do not wrap the response in markdown
- Do not include any explanation outside the JSON

RESUME:
{resume_text[:MAX_SOURCE_TEXT_CHARS]}

JOB DESCRIPTION:
{job_text[:MAX_SOURCE_TEXT_CHARS]}
""".strip()

    body = {
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 4000,
        "temperature": 0,
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": prompt,
                    }
                ],
            }
        ],
    }

    logger.info(
        "Bedrock scoring call starting. model=%s resume_chars=%s "
        "job_chars=%s",
        BEDROCK_MODEL_ID,
        len(resume_text),
        len(job_text),
    )

    t0 = time.time()

    response = bedrock_runtime.invoke_model(
        modelId=BEDROCK_MODEL_ID,
        body=json.dumps(body),
        contentType="application/json",
        accept="application/json",
    )

    elapsed = time.time() - t0

    payload = json.loads(response["body"].read())
    usage = payload.get("usage", {})

    logger.info(
        "Bedrock scoring call completed. elapsed_sec=%.1f "
        "input_tokens=%s output_tokens=%s",
        elapsed,
        usage.get("input_tokens", "n/a"),
        usage.get("output_tokens", "n/a"),
    )

    if user_id:
        accumulate_tokens(
            user_id,
            usage.get("input_tokens", 0),
            usage.get("output_tokens", 0),
        )

    text = payload["content"][0]["text"].strip()
    text = strip_code_fences(text)

    return json.loads(text)


# =================================================================================
# S3 helpers
# =================================================================================


def put_job_description_to_s3(user_id, job_id, job_text):
    """
    Store the normalized job description in the backend bucket.

    Object layout:
      users/USER#<user_id>/jobs/JOB#<job_id>/job_description.txt
    """
    key = build_job_description_key(user_id, job_id)
    write_s3_text(key, job_text)
    return key


def put_job_analysis_to_s3(user_id, job_id, analysis_text):
    """
    Store the job analysis in the backend bucket.

    Object layout:
      users/USER#<user_id>/jobs/JOB#<job_id>/job_analysis.txt
    """
    key = build_job_analysis_key(user_id, job_id)
    write_s3_text(key, analysis_text)
    return key


# =================================================================================
# Scoring helper
# =================================================================================


def score_job_against_resume(user_id, job_id, track_user_id=None):
    """
    Read the stored resume snapshot and job description, score the resume
    against the job, store the analysis in S3, and return the numeric score.
    """
    resume_snapshot_key = build_resume_snapshot_key(user_id, job_id)
    job_description_key = build_job_description_key(user_id, job_id)

    # -----------------------------------------------------------------------------
    # Read stored scoring inputs from S3
    # -----------------------------------------------------------------------------

    try:
        resume_text = read_s3_text(resume_snapshot_key).strip()
        job_text = read_s3_text(job_description_key).strip()
    except Exception as exc:
        raise RuntimeError(
            f"Failed to read scoring inputs from S3: {exc}"
        ) from exc

    if not resume_text:
        raise RuntimeError("Stored resume snapshot is empty")

    if not job_text:
        raise RuntimeError("Stored job description is empty")

    # -----------------------------------------------------------------------------
    # Ask Bedrock to score the resume against the job
    # -----------------------------------------------------------------------------

    try:
        scored = score_resume_with_bedrock(resume_text, job_text, user_id=track_user_id)
    except Exception as exc:
        raise RuntimeError(
            f"Failed to score resume against job: {exc}"
        ) from exc

    if not isinstance(scored, dict):
        raise RuntimeError("Bedrock scoring returned an invalid response")

    score = scored.get("score")
    summary = str(scored.get("summary", "")).strip()

    # Accept a numeric string if the model returns "82" instead of 82.
    if isinstance(score, str) and score.strip().isdigit():
        score = int(score.strip())

    if not isinstance(score, int):
        raise RuntimeError("Bedrock scoring did not return an integer score")

    if score < 0 or score > 100:
        raise RuntimeError("Bedrock scoring returned a score outside 0-100")

    if not summary:
        raise RuntimeError("Bedrock scoring did not return analysis text")

    # -----------------------------------------------------------------------------
    # Persist human-readable analysis text to S3
    # -----------------------------------------------------------------------------

    try:
        put_job_analysis_to_s3(
            user_id=user_id,
            job_id=job_id,
            analysis_text=summary,
        )
    except Exception as exc:
        raise RuntimeError(f"Failed to store job analysis: {exc}") from exc

    return score


# =================================================================================
# Core worker logic
# =================================================================================


def process_url_job(user_id, job_id, job_url):
    """
    Process a URL-based job by retrieving the page, extracting job fields,
    scoring the resume against the normalized job text, and saving results.
    """
    if not job_url:
        logger.error(
            "Missing job URL. user_id=%s job_id=%s",
            user_id,
            job_id,
        )
        update_job_status(
            user_id=user_id,
            job_id=job_id,
            status="Error",
            status_message="Job URL is missing",
        )
        return

    # -----------------------------------------------------------------------------
    # Retrieve raw HTML from the job URL
    # -----------------------------------------------------------------------------

    try:
        html = fetch_url_html(job_url)
        logger.info(
            "Retrieved job URL successfully. user_id=%s job_id=%s bytes=%s",
            user_id,
            job_id,
            len(html),
        )
    except Exception as exc:
        logger.exception(
            "Failed to retrieve job URL. user_id=%s job_id=%s url=%s",
            user_id,
            job_id,
            job_url,
        )
        update_job_status(
            user_id=user_id,
            job_id=job_id,
            status="Error",
            status_message=safe_status_message(
                f"Failed to retrieve job URL: {exc}"
            ),
        )
        return

    # -----------------------------------------------------------------------------
    # Convert raw HTML into visible text suitable for extraction
    # -----------------------------------------------------------------------------

    try:
        visible_text = extract_visible_text(html)

        if not visible_text:
            update_job_status(
                user_id=user_id,
                job_id=job_id,
                status="Error",
                status_message="No visible job text extracted from URL",
            )
            return

        logger.info(
            "Extracted visible text successfully. user_id=%s job_id=%s "
            "chars=%s",
            user_id,
            job_id,
            len(visible_text),
        )
    except Exception as exc:
        logger.exception(
            "Failed to extract visible text. user_id=%s job_id=%s url=%s",
            user_id,
            job_id,
            job_url,
        )
        update_job_status(
            user_id=user_id,
            job_id=job_id,
            status="Error",
            status_message=safe_status_message(
                f"Failed to extract visible text: {exc}"
            ),
        )
        return

    # -----------------------------------------------------------------------------
    # Extract structured job fields from the cleaned text
    # -----------------------------------------------------------------------------

    try:
        extracted = extract_job_fields_with_bedrock(visible_text, user_id=user_id)

        logger.info(
            "Bedrock extraction completed. user_id=%s job_id=%s",
            user_id,
            job_id,
        )

        if not isinstance(extracted, dict):
            update_job_status(
                user_id=user_id,
                job_id=job_id,
                status="Error",
                status_message="Bedrock returned an invalid response",
            )
            return

        job_title = str(extracted.get("job_title", "")).strip()
        company_name = str(extracted.get("company_name", "")).strip()
        job_text = str(extracted.get("job_text", "")).strip()

        logger.info(
            "Extracted fields. user_id=%s job_id=%s title_len=%s "
            "company_len=%s job_text_len=%s",
            user_id,
            job_id,
            len(job_title),
            len(company_name),
            len(job_text),
        )

        if not job_text:
            update_job_status(
                user_id=user_id,
                job_id=job_id,
                status="Error",
                status_message="Bedrock did not return job text",
            )
            return

        if len(job_text) < MIN_JOB_TEXT_CHARS:
            update_job_status(
                user_id=user_id,
                job_id=job_id,
                status="Error",
                status_message="Extracted job description is too short",
            )
            return

    except Exception as exc:
        logger.exception(
            "Failed to extract job fields. user_id=%s job_id=%s",
            user_id,
            job_id,
        )
        update_job_status(
            user_id=user_id,
            job_id=job_id,
            status="Error",
            status_message=safe_status_message(
                f"Failed to extract job fields: {exc}"
            ),
        )
        return

    # -----------------------------------------------------------------------------
    # Write title and company to DynamoDB immediately so they show in the UI
    # while scoring is still in progress
    # -----------------------------------------------------------------------------

    update_job_title_and_company(
        user_id=user_id,
        job_id=job_id,
        job_title=job_title,
        company_name=company_name,
    )

    # -----------------------------------------------------------------------------
    # Persist extracted job description to S3
    # -----------------------------------------------------------------------------

    try:
        job_description_s3_key = put_job_description_to_s3(
            user_id=user_id,
            job_id=job_id,
            job_text=job_text,
        )

        logger.info(
            "Stored job description in S3. user_id=%s job_id=%s key=%s",
            user_id,
            job_id,
            job_description_s3_key,
        )
    except Exception as exc:
        logger.exception(
            "Failed to store job description. user_id=%s job_id=%s",
            user_id,
            job_id,
        )
        update_job_status(
            user_id=user_id,
            job_id=job_id,
            status="Error",
            status_message=safe_status_message(
                f"Failed to store job description: {exc}"
            ),
        )
        return

    # -----------------------------------------------------------------------------
    # Score the stored resume snapshot against the stored job description and
    # update DynamoDB with the extracted metadata plus score
    # -----------------------------------------------------------------------------

    try:
        score = score_job_against_resume(
            user_id=user_id,
            job_id=job_id,
            track_user_id=user_id,
        )

        update_job_extracted_fields(
            user_id=user_id,
            job_id=job_id,
            job_title=job_title,
            company_name=company_name,
            job_description_s3_key=job_description_s3_key,
            score=score,
        )
    except Exception as exc:
        logger.exception(
            "Failed to score job or update metadata. user_id=%s job_id=%s",
            user_id,
            job_id,
        )
        update_job_status(
            user_id=user_id,
            job_id=job_id,
            status="Error",
            status_message=safe_status_message(
                f"Failed to score job: {exc}"
            ),
        )
        return

    # -----------------------------------------------------------------------------
    # Mark the job complete
    # -----------------------------------------------------------------------------

    update_job_status(
        user_id=user_id,
        job_id=job_id,
        status="Scored",
        status_message="",
    )

    logger.info(
        "URL job processed successfully. user_id=%s job_id=%s",
        user_id,
        job_id,
    )


def process_raw_text_job(user_id, job_id):
    """
    Process a raw-text job by reading the previously stored S3 artifact,
    extracting metadata from it, scoring the resume against it, and saving the
    results.
    """
    job_description_key = build_job_description_key(user_id, job_id)

    # -----------------------------------------------------------------------------
    # Read the stored raw-text job description from S3
    # -----------------------------------------------------------------------------

    try:
        job_text = read_s3_text(job_description_key).strip()
        logger.info(
            "Read raw-text job description from S3. user_id=%s job_id=%s "
            "chars=%s",
            user_id,
            job_id,
            len(job_text),
        )
    except Exception as exc:
        logger.exception(
            "Failed to read raw-text job description. user_id=%s job_id=%s",
            user_id,
            job_id,
        )
        update_job_status(
            user_id=user_id,
            job_id=job_id,
            status="Error",
            status_message=safe_status_message(
                f"Failed to read stored job description: {exc}"
            ),
        )
        return

    if not job_text:
        update_job_status(
            user_id=user_id,
            job_id=job_id,
            status="Error",
            status_message="Stored job description is empty",
        )
        return

    if len(job_text) < MIN_JOB_TEXT_CHARS:
        update_job_status(
            user_id=user_id,
            job_id=job_id,
            status="Error",
            status_message="Stored job description is too short",
        )
        return

    # -----------------------------------------------------------------------------
    # Extract title and company from the stored job text
    # -----------------------------------------------------------------------------

    try:
        extracted = extract_job_fields_with_bedrock(job_text, user_id=user_id)

        logger.info(
            "Bedrock extraction completed for raw-text job. user_id=%s "
            "job_id=%s",
            user_id,
            job_id,
        )

        if not isinstance(extracted, dict):
            update_job_status(
                user_id=user_id,
                job_id=job_id,
                status="Error",
                status_message="Bedrock returned an invalid response",
            )
            return

        job_title = str(extracted.get("job_title", "")).strip()
        company_name = str(extracted.get("company_name", "")).strip()
        extracted_job_text = str(extracted.get("job_text", "")).strip()

        # Prefer the Bedrock-cleaned text if it meets the minimum length.
        # Fall back to the original stored text if the model returns blank
        # or something shorter than what was supplied.
        if extracted_job_text and len(extracted_job_text) >= MIN_JOB_TEXT_CHARS:
            job_text = extracted_job_text

    except Exception as exc:
        logger.exception(
            "Failed to extract raw-text job fields. user_id=%s job_id=%s",
            user_id,
            job_id,
        )
        update_job_status(
            user_id=user_id,
            job_id=job_id,
            status="Error",
            status_message=safe_status_message(
                f"Failed to extract job fields: {exc}"
            ),
        )
        return

    # -----------------------------------------------------------------------------
    # Write title and company to DynamoDB immediately so they show in the UI
    # while scoring is still in progress
    # -----------------------------------------------------------------------------

    update_job_title_and_company(
        user_id=user_id,
        job_id=job_id,
        job_title=job_title,
        company_name=company_name,
    )

    # -----------------------------------------------------------------------------
    # Re-write the canonical job description artifact with the cleaned text
    # -----------------------------------------------------------------------------

    try:
        job_description_s3_key = put_job_description_to_s3(
            user_id=user_id,
            job_id=job_id,
            job_text=job_text,
        )

        logger.info(
            "Stored normalized raw-text job description. user_id=%s job_id=%s "
            "key=%s",
            user_id,
            job_id,
            job_description_s3_key,
        )
    except Exception as exc:
        logger.exception(
            "Failed to store normalized raw-text job description. "
            "user_id=%s job_id=%s",
            user_id,
            job_id,
        )
        update_job_status(
            user_id=user_id,
            job_id=job_id,
            status="Error",
            status_message=safe_status_message(
                f"Failed to store job description: {exc}"
            ),
        )
        return

    # -----------------------------------------------------------------------------
    # Score the stored resume snapshot against the normalized job description and
    # update DynamoDB with the extracted metadata plus score
    # -----------------------------------------------------------------------------

    try:
        score = score_job_against_resume(
            user_id=user_id,
            job_id=job_id,
            track_user_id=user_id,
        )

        update_job_extracted_fields(
            user_id=user_id,
            job_id=job_id,
            job_title=job_title,
            company_name=company_name,
            job_description_s3_key=job_description_s3_key,
            score=score,
        )
    except Exception as exc:
        logger.exception(
            "Failed to score raw-text job or update metadata. "
            "user_id=%s job_id=%s",
            user_id,
            job_id,
        )
        update_job_status(
            user_id=user_id,
            job_id=job_id,
            status="Error",
            status_message=safe_status_message(
                f"Failed to score job: {exc}"
            ),
        )
        return

    update_job_status(
        user_id=user_id,
        job_id=job_id,
        status="Scored",
        status_message="",
    )

    logger.info(
        "Raw-text job processed successfully. user_id=%s job_id=%s",
        user_id,
        job_id,
    )


def process_job_message(message):
    """
    Process one SQS message for V1 job ingestion.
    """
    user_id = str(message.get("user_id", "")).strip()
    job_id = str(message.get("job_id", "")).strip()
    resume_id = str(message.get("resume_id", "")).strip()
    source_type = str(message.get("source_type", "")).strip()
    job_url = str(message.get("job_url", "")).strip()

    if not user_id or not job_id:
        logger.error(
            "Message missing required fields. user_id=%s job_id=%s message=%s",
            user_id,
            job_id,
            message,
        )
        return

    update_job_status(
        user_id=user_id,
        job_id=job_id,
        status="Scoring",
        status_message="Started job scoring",
    )

    logger.info(
        "Processing job. user_id=%s job_id=%s resume_id=%s source_type=%s",
        user_id,
        job_id,
        resume_id,
        source_type,
    )

    if source_type == "url":
        process_url_job(user_id=user_id, job_id=job_id, job_url=job_url)
        return

    if source_type == "raw_text":
        process_raw_text_job(user_id=user_id, job_id=job_id)
        return

    logger.error(
        "Unsupported source_type. user_id=%s job_id=%s source_type=%s",
        user_id,
        job_id,
        source_type,
    )

    update_job_status(
        user_id=user_id,
        job_id=job_id,
        status="Error",
        status_message=f"Unsupported source_type: {source_type}",
    )


# =================================================================================
# Lambda entry point
# =================================================================================


def lambda_handler(event, context):
    """
    SQS-triggered Lambda entry point.

    Each record is processed independently so one bad message does not stop the
    rest of the batch from being attempted.
    """
    for record in event.get("Records", []):
        try:
            message = json.loads(record["body"])
            logger.info("Received message: %s", message)
            process_job_message(message)
        except Exception:
            logger.exception("Unhandled error while processing SQS record")

    return {
        "statusCode": 200,
    }