# Upload A Table

Use this when the user gives a CSV or spreadsheet file.

Call:

```json
{
  "tool": "memory_upload_media_base64",
  "arguments": {
    "filename": "report.csv",
    "mime_type": "text/csv",
    "media_type": "spreadsheet",
    "source_channel": "mcp",
    "content_base64": "<base64-file-bytes>",
    "extract": true,
    "sync": false
  }
}
```

Expected behavior:

- The system stores the file as a `MediaArtifact`.
- The background extractor creates table source evidence with headers, row count, preview, and warnings when needed.
- Large or unsupported files should remain visible as failed or skipped artifacts, not disappear.
