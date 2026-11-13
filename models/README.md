# 本地模型目录（权重不随仓库发布）

建议结构：`models/base_model/` 放完整基座；`models/report_adapter/` 放选定的 LoRA Adapter。

基座至少包含 `config.json`、完整 safetensors 权重（或索引及全部分片）、tokenizer 及聊天模板文件；视觉架构还应保留其处理器等原发布文件。请从你有权使用的模型发行源取得完整快照，不要仅复制单个配置文件。服务默认 `local_files_only=True`，不会自动下载。

Adapter 至少包含 `adapter_config.json` 和 `adapter_model.safetensors`（或完整分片），并保留其 tokenizer 文件（若训练时保存）。Adapter 必须与基座兼容；不能用训练 checkpoint 的单个 `.pt` 文件代替完整 Adapter。

如果尚无 Adapter，可按 `docs/TRAINING.md` 从基座新建 LoRA；纯合成示例仅用于 smoke 链路检查。已有的真实训练权重应在本机另行配置，仓库不提供历史 Adapter 下载地址。

初始化后运行 `scripts/configure_models.py` 生成当前机器的 runtime 和 Adapter SHA 绑定。`.gitignore` 默认忽略本目录所有资源，仅保留此说明。
