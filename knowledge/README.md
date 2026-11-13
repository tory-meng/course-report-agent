# 本地参考检索库

此目录运行时保存 SQLite FTS5 索引、检索缓存及来源身份。原项目数据库和课程笔记不随仓库发布。

准备有权使用的 Word/PDF 后，可运行：

```bash
python scripts/reference_library.py build --source inputs/references --db knowledge/references.sqlite3 --no-ocr
python scripts/reference_library.py query --db knowledge/references.sqlite3 --query "课程目标 考核权重"
```

扫描件需要配置 OCR 并去掉 `--no-ocr`。把 `configs/reference_library.example.json` 的 `reference_library` 字段加入自己的 runtime 后才会在在线报告中启用；默认离线演示不依赖此库。
