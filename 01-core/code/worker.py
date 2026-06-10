# ================================================================================
# worker.py
#
# Purpose
# SQS-triggered RAG worker. For each query message:
#   1. Load corpus embeddings from S3 into memory
#   2. Embed the user question via Bedrock Titan
#   3. Cosine similarity search → top-k chunks
#   4. Fetch last N completed Q&A pairs from this conversation as history
#   5. Build stateful prompt: system + history + retrieved context + question
#   6. Call Bedrock Haiku for the answer
#   7. Write answer.txt and sources.json to S3
#   8. Update DynamoDB query record and accumulate tokens on user usage
#
# Expected SQS message body
#   {"user_id": "...", "conv_id": "...", "query_id": "..."}
# ================================================================================

import io
import json
import logging
import os
import time
from datetime import datetime, timezone

import boto3
import numpy as np
from boto3.dynamodb.conditions import Key
from botocore.config import Config

# ================================================================================
# Logging
# ================================================================================

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# ================================================================================
# AWS clients
# ================================================================================

dynamodb = boto3.resource("dynamodb")
table    = dynamodb.Table(os.environ["TABLE_NAME"])

bedrock = boto3.client(
    "bedrock-runtime",
    config=Config(read_timeout=240, connect_timeout=10),
)
s3 = boto3.client("s3")

# ================================================================================
# Environment
# ================================================================================

BACKEND_BUCKET   = os.environ["BACKEND_BUCKET_NAME"]
CHAT_MODEL_ID    = os.environ["BEDROCK_MODEL_ID"]
EMBED_MODEL_ID   = "amazon.titan-embed-text-v2:0"

# ================================================================================
# Constants
# ================================================================================

TOP_K          = 10    # chunks retrieved per query
HISTORY_WINDOW = 5     # prior Q&A pairs injected as conversation history
MAX_CHUNK_CHARS = 1500 # truncate individual chunks before injection


# ================================================================================
# Generic helpers
# ================================================================================

def utc_now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _read_s3_bytes(key):
    result = s3.get_object(Bucket=BACKEND_BUCKET, Key=key)
    return result["Body"].read()


def _read_s3_text(key):
    return _read_s3_bytes(key).decode("utf-8")


def _write_s3_text(key, text):
    s3.put_object(
        Bucket=BACKEND_BUCKET,
        Key=key,
        Body=text.encode("utf-8"),
        ContentType="text/plain; charset=utf-8",
    )


def _write_s3_json(key, obj):
    s3.put_object(
        Bucket=BACKEND_BUCKET,
        Key=key,
        Body=json.dumps(obj, ensure_ascii=False).encode("utf-8"),
        ContentType="application/json; charset=utf-8",
    )


def _s3_prefix(user_id, conv_id, query_id):
    return (
        f"users/USER#{user_id}/conversations/"
        f"CONV#{conv_id}/QUERY#{query_id}"
    )


# ================================================================================
# DynamoDB helpers
# ================================================================================

def _update_query_status(user_id, conv_id, query_id, status):
    table.update_item(
        Key={
            "pk": f"USER#{user_id}",
            "sk": f"QUERY#{conv_id}#{query_id}",
        },
        UpdateExpression="SET #s = :s, updated_at = :u",
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={":s": status, ":u": utc_now()},
    )


def _finalize_query(user_id, conv_id, query_id,
                    answer_key, sources_key, tokens_used):
    table.update_item(
        Key={
            "pk": f"USER#{user_id}",
            "sk": f"QUERY#{conv_id}#{query_id}",
        },
        UpdateExpression=(
            "SET #s = :s, answer_s3_key = :a, "
            "sources_s3_key = :src, tokens_used = :t, updated_at = :u"
        ),
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={
            ":s":   "complete",
            ":a":   answer_key,
            ":src": sources_key,
            ":t":   tokens_used,
            ":u":   utc_now(),
        },
    )


def _fail_query(user_id, conv_id, query_id, reason):
    table.update_item(
        Key={
            "pk": f"USER#{user_id}",
            "sk": f"QUERY#{conv_id}#{query_id}",
        },
        UpdateExpression="SET #s = :s, status_message = :m, updated_at = :u",
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={
            ":s": "failed",
            ":m": str(reason)[:500],
            ":u": utc_now(),
        },
    )


def accumulate_tokens(user_id, input_tokens, output_tokens):
    """Add consumed tokens to the user's lifetime usage record."""
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
        # Best-effort — never let token tracking block query completion
        logger.exception(
            "Failed to update token usage for user_id=%s", user_id
        )


# ================================================================================
# Corpus loading
# ================================================================================

def _load_corpus():
    """
    Load chunk metadata and embeddings from S3 into memory.
    Both objects are written by the ingest script before first use.
    """
    chunks_bytes     = _read_s3_bytes("corpus/chunks.json")
    embeddings_bytes = _read_s3_bytes("corpus/embeddings.npy")

    chunks     = json.loads(chunks_bytes.decode("utf-8"))
    embeddings = np.load(io.BytesIO(embeddings_bytes)).astype(np.float32)

    return chunks, embeddings


# ================================================================================
# Embedding + retrieval
# ================================================================================

def _embed_query(question):
    """Embed a question string via Bedrock Titan Embeddings v2."""
    body = json.dumps({
        "inputText": question,
        "dimensions": 1024,
        "normalize": True,
    })

    response = bedrock.invoke_model(
        modelId=EMBED_MODEL_ID,
        body=body,
        contentType="application/json",
        accept="application/json",
    )

    payload = json.loads(response["body"].read())
    return np.array(payload["embedding"], dtype=np.float32)


CAREER_BOOST = 1.5  # multiplier for local career-fact chunks vs GitHub/YouTube

def _cosine_search(query_vec, embeddings, chunks, top_k):
    """Return indices of top_k most similar rows by cosine similarity.

    Career fact chunks (repo='resume') are boosted so they outrank
    GitHub README chunks when both match the query.
    """
    scores = embeddings @ query_vec
    for i, chunk in enumerate(chunks):
        if chunk.get("repo") == "resume":
            scores[i] *= CAREER_BOOST
    indices = np.argsort(scores)[::-1][:top_k]
    return indices.tolist(), scores[indices].tolist()


def _retrieve_chunks(question, chunks, embeddings):
    """Embed question and return top-k chunk dicts with scores."""
    query_vec = _embed_query(question)
    indices, scores = _cosine_search(query_vec, embeddings, chunks, TOP_K)

    results = []
    for idx, score in zip(indices, scores):
        chunk = dict(chunks[idx])
        chunk["score"] = round(float(score), 4)
        results.append(chunk)

    return results


# ================================================================================
# Conversation history
# ================================================================================

def _fetch_history(user_id, conv_id, exclude_query_id):
    """
    Return the last HISTORY_WINDOW completed Q&A pairs for this
    conversation, oldest first, excluding the current query.
    """
    result = table.query(
        KeyConditionExpression=(
            Key("pk").eq(f"USER#{user_id}") &
            Key("sk").begins_with(f"QUERY#{conv_id}#")
        )
    )

    items = [
        item for item in result.get("Items", [])
        if item.get("status") == "complete"
        and item.get("query_id") != exclude_query_id
    ]

    # Oldest first, then take the last HISTORY_WINDOW
    items.sort(key=lambda x: x.get("created_at") or "")
    items = items[-HISTORY_WINDOW:]

    history = []
    for item in items:
        question = answer = None

        if item.get("question_s3_key"):
            try:
                question = _read_s3_text(item["question_s3_key"])
            except Exception:
                pass

        if item.get("answer_s3_key"):
            try:
                answer = _read_s3_text(item["answer_s3_key"])
            except Exception:
                pass

        if question and answer:
            history.append({"question": question, "answer": answer})

    return history


# ================================================================================
# Bedrock Haiku call
# ================================================================================

SYSTEM_PROMPT = """You are an AI interview assistant for Mike Monaco. \
Mike is a principal-level cloud architect with 10+ years of experience in \
production AWS environments, pharmaceutical cloud consulting, SAS/Posit \
analytics platforms, and multi-cloud architecture across AWS, GCP, Azure, \
and OCI. He has built 100+ public reference architectures and runs a YouTube \
channel with 120,000 subscribers.

Your job is to answer questions about Mike's background, career, skills, \
accomplishments, and what he is looking for in his next role — as if you \
were Mike speaking in a job interview. Use the provided context to ground \
your answers in specific facts, metrics, and examples from Mike's actual \
experience.

When the context contains a specific number or metric (clients served, \
users supported, cost savings, RTO/RPO targets, data volumes, etc.), \
always state that number directly and prominently — do not bury it or \
omit it. Career facts and metrics from Mike's background take priority \
over general portfolio or repository descriptions.

If the context does not contain enough information to answer confidently, \
say so clearly rather than guessing."""


def _call_haiku(question, retrieved_chunks, history):
    """
    Build the messages array with history + context and call Bedrock Haiku.
    Returns (answer_text, input_tokens, output_tokens).
    """
    # Build context block from retrieved chunks
    context_parts = []
    for i, chunk in enumerate(retrieved_chunks, 1):
        source = chunk.get("source_url") or chunk.get("repo") or "unknown"
        text   = (chunk.get("text") or "")[:MAX_CHUNK_CHARS]
        context_parts.append(f"[{i}] Source: {source}\n{text}")

    context_block = "\n\n---\n\n".join(context_parts)

    # Inject prior turns as alternating user/assistant messages
    messages = []
    for turn in history:
        messages.append({"role": "user",      "content": turn["question"]})
        messages.append({"role": "assistant", "content": turn["answer"]})

    # Current question with retrieved context appended
    user_content = f"""Context excerpts from Mike's portfolio:

{context_block}

---

Question: {question}"""

    messages.append({"role": "user", "content": user_content})

    body = {
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 1024,
        "temperature": 0.3,
        "system": SYSTEM_PROMPT,
        "messages": messages,
    }

    logger.info(
        "Haiku call starting. turns=%d chunks=%d",
        len(history),
        len(retrieved_chunks),
    )

    t0 = time.time()

    response = bedrock.invoke_model(
        modelId=CHAT_MODEL_ID,
        body=json.dumps(body),
        contentType="application/json",
        accept="application/json",
    )

    elapsed = time.time() - t0
    payload = json.loads(response["body"].read())
    usage   = payload.get("usage", {})

    logger.info(
        "Haiku call complete. elapsed=%.1fs input=%s output=%s",
        elapsed,
        usage.get("input_tokens"),
        usage.get("output_tokens"),
    )

    answer = payload["content"][0]["text"].strip()
    return answer, usage.get("input_tokens", 0), usage.get("output_tokens", 0)


# ================================================================================
# Core pipeline
# ================================================================================

def process_query(user_id, conv_id, query_id):
    """Run the full RAG pipeline for one query."""

    _update_query_status(user_id, conv_id, query_id, "processing")

    prefix = _s3_prefix(user_id, conv_id, query_id)

    # -------------------------------------------------------------------------
    # Read question from S3
    # -------------------------------------------------------------------------

    try:
        question = _read_s3_text(f"{prefix}/question.txt").strip()
    except Exception as exc:
        logger.exception("Failed to read question from S3")
        _fail_query(user_id, conv_id, query_id, f"Could not read question: {exc}")
        return

    if not question:
        _fail_query(user_id, conv_id, query_id, "Question is empty")
        return

    # -------------------------------------------------------------------------
    # Load corpus
    # -------------------------------------------------------------------------

    try:
        chunks, embeddings = _load_corpus()
        logger.info("Corpus loaded. chunks=%d", len(chunks))
    except Exception as exc:
        logger.exception("Failed to load corpus from S3")
        _fail_query(user_id, conv_id, query_id, f"Corpus unavailable: {exc}")
        return

    # -------------------------------------------------------------------------
    # Retrieve relevant chunks
    # -------------------------------------------------------------------------

    try:
        retrieved = _retrieve_chunks(question, chunks, embeddings)
        logger.info("Retrieved %d chunks", len(retrieved))
    except Exception as exc:
        logger.exception("Retrieval failed")
        _fail_query(user_id, conv_id, query_id, f"Retrieval failed: {exc}")
        return

    # -------------------------------------------------------------------------
    # Fetch conversation history
    # -------------------------------------------------------------------------

    try:
        history = _fetch_history(user_id, conv_id, exclude_query_id=query_id)
        logger.info("History fetched. turns=%d", len(history))
    except Exception as exc:
        # Non-fatal — proceed without history rather than failing the query
        logger.exception("Failed to fetch history; continuing without it")
        history = []

    # -------------------------------------------------------------------------
    # Call Haiku
    # -------------------------------------------------------------------------

    try:
        answer, input_tokens, output_tokens = _call_haiku(
            question, retrieved, history
        )
    except Exception as exc:
        logger.exception("Haiku call failed")
        _fail_query(user_id, conv_id, query_id, f"Model call failed: {exc}")
        return

    # -------------------------------------------------------------------------
    # Persist answer and sources to S3
    # -------------------------------------------------------------------------

    answer_key  = f"{prefix}/answer.txt"
    sources_key = f"{prefix}/sources.json"

    sources_payload = [
        {
            "repo":       c.get("repo"),
            "file":       c.get("file"),
            "title":      c.get("title"),
            "source_url": c.get("source_url"),
            "score":      c.get("score"),
        }
        for c in retrieved
    ]

    try:
        _write_s3_text(answer_key, answer)
        _write_s3_json(sources_key, sources_payload)
    except Exception as exc:
        logger.exception("Failed to write answer/sources to S3")
        _fail_query(user_id, conv_id, query_id, f"Failed to store result: {exc}")
        return

    # -------------------------------------------------------------------------
    # Finalise DynamoDB record and accumulate tokens
    # -------------------------------------------------------------------------

    total_tokens = int(input_tokens or 0) + int(output_tokens or 0)

    _finalize_query(
        user_id, conv_id, query_id,
        answer_key, sources_key, total_tokens,
    )

    accumulate_tokens(user_id, input_tokens, output_tokens)

    logger.info(
        "Query complete. user=%s conv=%s query=%s tokens=%d",
        user_id, conv_id, query_id, total_tokens,
    )


# ================================================================================
# Lambda entry point
# ================================================================================

def lambda_handler(event, context):
    """
    SQS-triggered entry point. Each record is processed independently so
    one bad message does not block the rest of the batch.
    """
    for record in event.get("Records", []):
        try:
            message  = json.loads(record["body"])
            user_id  = str(message.get("user_id",  "")).strip()
            conv_id  = str(message.get("conv_id",  "")).strip()
            query_id = str(message.get("query_id", "")).strip()

            if not user_id or not conv_id or not query_id:
                logger.error("Message missing required fields: %s", message)
                continue

            logger.info(
                "Processing query. user=%s conv=%s query=%s",
                user_id, conv_id, query_id,
            )
            process_query(user_id, conv_id, query_id)

        except Exception:
            logger.exception("Unhandled error processing SQS record")

    return {"statusCode": 200}
