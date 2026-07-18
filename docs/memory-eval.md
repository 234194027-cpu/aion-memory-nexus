# MemoryEval 基线

本评测只使用隔离 fixture 和确定性测试；不读取本地用户数据库、不发送真实 LLM/Embedding 请求，也不把测试内容当作用户记忆。

运行：

```powershell
python -X utf8 scripts/run_memory_eval.py
python -X utf8 scripts/run_memory_eval.py --list
python -X utf8 scripts/run_memory_eval.py --metrics-file docs/memory-eval-observations.example.json
```

`src/memory/services/memory_eval.py` 是唯一的用例目录。它把 16 类质量要求映射到可复用 pytest nodeid：事实召回、多会话组合、现行/历史、知识更新、时间有效期、认识状态、冲突、重复、拒答、引用、无来源、敏感度、跨用户、删除、纠正与提示词注入。

## 匿名指标计算

`--metrics-file` 只接收人工标注的 JSON 数组，字段仅允许 opaque observation、memory 和 source ID，以及人工判断的拒答/时间标签；不允许放入 query、原始记忆、回答文本或用户 ID。示例文件只包含虚构的 `example-*` 标识符。

在有对应标签时，脚本计算：Recall@1/3/5、MRR、引用准确率、来源覆盖率、拒答准确率和时间判断准确率。没有标签的维度输出 `null` 或空对象，绝不以 fixture、模型结果或缺失数据伪造百分比。

## 解释边界

- 当前基线验证的是确定性治理与检索不变量，不是对任一模型的主观“记忆能力”评分。
- Recall@K、MRR、引用准确率、时间判断准确率和拒答准确率可由匿名人工标注的试运行集计算；仓库示例不代表真实用户或模型成绩。
- P50/P95 延迟、Token/成本、失败率和降级率由现有运行指标在真实受控试运行中采集；任何真实供应商测试须单独授权并遵循 `docs/operational-runbook.md`。
- 本目录中的一个 nodeid 只代表它所对应的不变量，不代表产品端到端已经在真实数据规模上通过。
