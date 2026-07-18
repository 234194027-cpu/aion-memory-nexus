# Upload An Image

Use this when the user gives an image or screenshot.

Call:

```json
{
  "tool": "memory_upload_media_base64",
  "arguments": {
    "filename": "screenshot.png",
    "mime_type": "image/png",
    "media_type": "image",
    "source_channel": "mcp",
    "content_base64": "<base64-file-bytes>",
    "extract": true,
    "sync": false
  }
}
```

If OCR is not available or no useful text is found, the system should preserve the artifact and mark its source evidence as low confidence or needing more evidence.
