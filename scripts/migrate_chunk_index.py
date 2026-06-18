"""
Milvus chunk_index 迁移脚本

当前走降级方案 B：
- 老文档没有 chunk_index，引用降级为 document_id + text hash 去重
- 新文档从 ingestion 自动赋值 chunk_index
- 老文档在用户重新上传时自然补齐

如需执行迁移（当 chunk 顺序可恢复时）：
  1. 备份 Milvus 数据
  2. 删除旧 collection
  3. 重新 init_collection（含 chunk_index 字段）
  4. 从备份导入，按 document_id 分组内稳定排序赋值 chunk_index
  5. 重新构建 BM25 索引

执行：此脚本当前为占位，直接运行不做任何操作。
"""

if __name__ == "__main__":
    print("当前走降级方案 B，无需迁移。")
    print("如需迁移老数据，请参考 Phase 3 Plan Step 3.2 手动执行。")
