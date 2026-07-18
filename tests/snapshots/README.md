# Snapshots directory for characterization tests.

# WP-0A-T05: 该目录保存 characterization tests 期望的响应 schema 快照。
# 目前测试使用内联断言（assert set(response.keys()) == expected_keys），
# 该目录保留作为未来 snapshot-on-disk 扩展的锚点（如 syrupy/pytest-incremental）。
