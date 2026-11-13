# 私有训练数据目录

正式 SFT 数据、来源快照、审核决定和训练导出结果不随仓库发布。本目录默认被 Git 忽略。

`python scripts/make_synthetic_sft.py --help` 提供仅用于 smoke 的微型合成数据生成器。真实样本应从本机报告生成记录逐条核对后导出；课程家族必须在训练与验证之间隔离。完整步骤和 JSONL 字段见 `docs/TRAINING.md`、`docs/DATA_FORMATS.md`。

`examples/demo_course/` 中的小型课程与成绩数据是明确标记的合成演示，可公开保留；它不代表原项目训练集或报告质量评测集。
