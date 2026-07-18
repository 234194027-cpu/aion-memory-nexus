# MCP Config Examples

Use the portable proxy when the target machine does not have the Life Memory repo.

## Claude Desktop

```json
{
  "mcpServers": {
    "life-memory": {
      "command": "python",
      "args": ["C:/path/to/life-memory-mcp-connect/scripts/life_memory_mcp_server.py"],
      "env": {
        "LIFE_MEMORY_API_URL": "https://memory.example.invalid",
        "LIFE_MEMORY_AGENT_ID": "<agent_id>",
        "LIFE_MEMORY_AGENT_TOKEN": "<token from secret store>"
      }
    }
  }
}
```

## Cursor / Windsurf `.mcp.json`

```json
{
  "mcpServers": {
    "life-memory": {
      "command": "python",
      "args": ["./skills/life-memory-mcp-connect/scripts/life_memory_mcp_server.py"],
      "env": {
        "LIFE_MEMORY_API_URL": "https://memory.example.invalid",
        "LIFE_MEMORY_AGENT_ID": "<agent_id>",
        "LIFE_MEMORY_AGENT_TOKEN": "<token from secret store>"
      }
    }
  }
}
```

## Existing Repo Mode

If the target environment already runs inside the Life Memory repo, the MCP server can also be:

```json
{
  "mcpServers": {
    "life-memory": {
      "command": "python",
      "args": ["-m", "src.mcp_server"],
      "env": {
        "LIFE_MEMORY_API_URL": "https://memory.example.invalid",
        "LIFE_MEMORY_AGENT_ID": "<agent_id>",
        "LIFE_MEMORY_AGENT_TOKEN": "<token from secret store>"
      }
    }
  }
}
```

Prefer the portable proxy for external agents because it has fewer installation assumptions.
