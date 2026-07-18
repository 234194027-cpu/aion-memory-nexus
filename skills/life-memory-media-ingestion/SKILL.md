---
name: life-memory-media-ingestion
description: Teach an external agent how to send links, files, images, tables, audio, and video into the Life Memory System as reviewable media note artifacts through MCP.
---

# Life Memory Media Ingestion

Use this skill when an agent needs to submit a URL, file, image, spreadsheet, PDF, document, audio, or video into the Life Memory System.

The rule is simple: media is a source artifact first, not a permanent fact. Create a media artifact, let the system extract source evidence, then inspect the artifact and governance status.

## Required MCP Tools

Confirm these tools exist before submitting media:

- `memory_create_link_artifact`
- `memory_upload_media_base64`
- `memory_list_media_artifacts`
- `memory_get_media_artifact`
- `memory_extract_media_artifact`

If any tool is missing, call `memory_map` if available, refresh the MCP connection, and report that the client is connected to an older Life Memory MCP server.

## Link Workflow

Use `memory_create_link_artifact` for public `http` or `https` links.

Required fields:

- `url`: the public URL.
- `source_channel`: use `mcp` unless the caller has a more specific channel.

Optional fields:

- `source_text`: short context from the user or task.
- `extract`: default `true`.
- `sync`: default `false`.

Do not fetch the URL yourself and paste the webpage into `memory_after_end`. The system handles SSRF checks, extraction, failure status, and RawEvent/work-case evidence creation.

Example:

```json
{
  "url": "https://example.com/article",
  "source_text": "User asked me to preserve this article as a note source.",
  "source_channel": "mcp",
  "extract": true,
  "sync": false
}
```

## File Workflow

Use `memory_upload_media_base64` for files, images, spreadsheets, documents, audio, and video.

Required fields:

- `filename`: original filename or a safe descriptive name.
- `content_base64`: base64 file bytes.

Recommended fields:

- `mime_type`: explicit MIME type when known.
- `media_type`: one of `image`, `spreadsheet`, `pdf`, `document`, `audio`, `video`, or `file`.
- `source_channel`: use `mcp`.
- `extract`: default `true`.
- `sync`: default `false`.

Example:

```json
{
  "filename": "meeting-notes.csv",
  "mime_type": "text/csv",
  "media_type": "spreadsheet",
  "source_channel": "mcp",
  "content_base64": "<base64>",
  "extract": true,
  "sync": false
}
```

## Status Workflow

After creating or uploading an artifact:

- Use `memory_get_media_artifact` with the returned `artifact.id` for detail.
- Use `memory_list_media_artifacts` for recent status.
- Use `memory_extract_media_artifact` only when immediate synchronous extraction is necessary.

Important statuses:

- `received`: metadata exists, extraction/download not complete.
- `downloaded`: file is stored and ready for extraction.
- `processing`: worker is extracting.
- `extracted`: extracted source evidence is available for Working-Agent governance.
- `skipped`: low-confidence or no useful text, but artifact is preserved.
- `failed`: extraction failed; inspect `error_message` and `warnings`.

## Safety Rules

Never do these things:

- Do not write extracted media content directly as a permanent fact.
- Do not bypass `memory_create_link_artifact` for URLs.
- Do not upload secrets, credentials, private keys, payment data, or unrelated personal identifiers.
- Do not submit `localhost`, private-network, `file://`, or non-HTTP links.
- Do not retry large files repeatedly when the system reports size or MIME rejection.

When extraction is low confidence or failed, ask the user for a short clarification such as:

```text
I saved the source, but extraction was incomplete. What is the main point you want remembered from this file?
```

## User-Facing Summary Pattern

After a successful call, tell the user:

```text
I saved this as a media note artifact: <artifact_id>.
Extraction is queued. I will treat the extracted content as source evidence, not as confirmed fact.
```

If the status is `failed` or `skipped`, say:

```text
The source was preserved, but extraction did not produce a strong note. Add a one-sentence explanation and I can attach it as context.
```
