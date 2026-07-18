# 给其他 Agent 的极简接入提示词

```text
接入 Life Memory MCP V3。先阅读本 Skill 的 SKILL.md；用安装器执行只读 smoke，成功后依次调用 memory_map、memory_list_types、memory_policy_status。每个非简单任务前调用 memory_before_start，完成后只能通过 memory_after_end 或 memory_upload_event 追加 RawEvent。不得直接写正式记忆、不得操作 Graphiti/Neo4j 或管理接口，不得泄露 Token。链接和文件使用媒体工具。失败时报告脱敏错误，不要绕过权限或把 HTML 当 ZIP 安装。
```

发布地址、manifest 校验、安装、同步和排障细节由 `SKILL.md` 负责；先验证 HTTPS 下载确实是 ZIP，再执行安装。
