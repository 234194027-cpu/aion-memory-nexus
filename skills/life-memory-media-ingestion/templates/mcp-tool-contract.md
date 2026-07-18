# Media MCP Tool Contract

Agents should depend on these tool names, not on database tables.

## `memory_create_link_artifact`

Creates a link artifact and optionally queues extraction.

Required:

- `url`

Optional:

- `source_text`
- `source_channel`
- `extract`
- `sync`

## `memory_upload_media_base64`

Uploads file bytes as a media artifact and optionally queues extraction.

Required:

- `filename`
- `content_base64`

Optional:

- `mime_type`
- `media_type`
- `source_channel`
- `extract`
- `sync`

## `memory_get_media_artifact`

Returns safe artifact details. It must not expose local storage paths, WeCom media IDs, or temporary download URLs.

Required:

- `artifact_id`

## `memory_list_media_artifacts`

Lists recent media artifacts and statuses.

Optional:

- `limit`

## `memory_extract_media_artifact`

Triggers synchronous extraction for one artifact. Use sparingly.

Required:

- `artifact_id`
