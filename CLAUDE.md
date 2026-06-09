# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working
with code in this repository.

## What This App Does

AWS RAG demo — a ChatGPT-style chat interface that lets users ask natural
language questions about Mike Monaco's multi-cloud reference architecture
portfolio. Answers are grounded in GitHub READMEs, Terraform files, and
YouTube video content indexed into a vector corpus. Conversations are
stateful (last 5 Q&A pairs injected as history per query). Token usage is
tracked per user in DynamoDB with a 500K lifetime cap.

## Architecture

    01-core/           # Backend: Terraform + Python Lambda source
      code/            # Lambda source files
    02-webapp/         # Frontend: vanilla JS SPA deployed to S3
    03-ingest/         # Corpus ingestion — crawl GitHub + YouTube, build corpus

### Request flow

1. User types a question → POST /conversations/{conv_id}/queries → API Lambda
2. API Lambda writes question.txt to S3, creates QUERY# record (status=pending),
   enqueues SQS message
3. Worker Lambda (SQS trigger):
   - Loads corpus/embeddings.npy + corpus/chunks.json from S3
   - Embeds query via Bedrock Titan Embeddings v2
   - Cosine similarity → top-5 chunks
   - Fetches last 5 completed Q&A pairs from DynamoDB/S3 as history
   - Calls Bedrock Haiku with system prompt + history + context + question
   - Writes answer.txt + sources.json to S3
   - Updates QUERY# record (status=complete) and USER#USAGE tokens
4. Frontend polls GET /conversations/{conv_id}/queries/{query_id} every 2s
5. On completion, renders answer with collapsible sources section

### Lambda files

- `handler.py`       — API router
- `conversations.py` — conversation + query CRUD; token budget enforcement
- `users.py`         — registration (USER_CAP=100) + GET /usage
- `worker.py`        — RAG pipeline (embed → retrieve → history → Haiku → store)

### Data model (DynamoDB single-table)

- `pk=USER#<id>`, `sk=USER#USAGE`          — tokens_used, token_limit (500K)
- `pk=USER#<id>`, `sk=CONV#<id>`           — title, created_at, updated_at
- `pk=USER#<id>`, `sk=QUERY#<conv>#<id>`   — status, S3 key pointers, tokens_used

### S3 layout

    corpus/chunks.json                                — chunk metadata array
    corpus/embeddings.npy                             — float32 (n_chunks, 1536)
    users/USER#<id>/conversations/CONV#<c>/QUERY#<q>/question.txt
    users/USER#<id>/conversations/CONV#<c>/QUERY#<q>/answer.txt
    users/USER#<id>/conversations/CONV#<c>/QUERY#<q>/sources.json

### Key Terraform variables (01-core/variables.tf)

- `region`           — default us-east-1
- `bedrock_model_id` — default us.anthropic.claude-haiku-4-5-20251001-v1:0

### Authentication

Cognito User Pool with Hosted UI, OAuth2 authorization code flow. All API
routes require JWT Bearer token.

## Deployment

```bash
./apply.sh      # full deploy
./destroy.sh    # tear down
./check_env.sh  # validate tools and credentials
```

Python deps install into the Lambda source dir so Terraform can zip them:

```bash
cd 01-core/code && pip install -r requirements.txt -t .
```

## Corpus ingestion

Runs automatically as stage 3 of `apply.sh`. To run manually:

```bash
cd 03-ingest
pip install -r requirements.txt
python ingest.py --bucket <backend-bucket-name>
```

The ingest script crawls all public `mamonaco1973/*` GitHub repos (README.md
and CLAUDE.md only) and optionally YouTube video descriptions, embeds via
Bedrock Titan, and writes `corpus/chunks.json` and `corpus/embeddings.npy`
to the backend S3 bucket.

## Code Commenting Standards

See the project-level CLAUDE.md in the workspace root for full standards.
Short version: comment the *why*, not the *what*. Section headers for
logical blocks, inline comments only for non-obvious intent.
