# Create A Link Artifact

Use this when the user gives a public webpage URL.

Call:

```json
{
  "tool": "memory_create_link_artifact",
  "arguments": {
    "url": "https://example.com/article",
    "source_text": "The user wants this article saved for later review.",
    "source_channel": "mcp",
    "extract": true,
    "sync": false
  }
}
```

Then inspect:

```json
{
  "tool": "memory_get_media_artifact",
  "arguments": {
    "artifact_id": "<artifact_id>"
  }
}
```

Do not paste the full webpage into a normal memory write. The media pipeline handles extraction and review status.
