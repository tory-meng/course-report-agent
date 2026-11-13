# 环境与资源要求

## CPU 文档环境

建议 Python 3.11。安装 `requirements-test.txt` 可同时满足报告处理、单元测试和约束解码测试；不需要 PyTorch、CUDA 或真实模型。

Word → PDF 需要 LibreOffice Writer 与中文字体。Ubuntu 示例：

```bash
sudo apt-get update
sudo apt-get install -y libreoffice-writer fonts-noto-cjk
```

可提取文字的 PDF/Word 不要求 OCR。处理扫描 PDF 时再安装 `tesseract-ocr tesseract-ocr-chi-sim`，并在 case 中启用 OCR。旧 `.doc` 转换同样需要 LibreOffice。

## GPU 环境

训练和推理可共用一个独立 GPU Python 环境，文档环境仍可独立运行并通过本地 HTTP 服务调用模型。先按实际驱动与硬件安装 PyTorch，再按主 README 同时安装 `requirements-ml.txt`、`requirements-structured.txt` 和 `requirements-docagent.txt`，满足全链路环境检查及前处理依赖。程序读取本地模型，不自动拉取权重。

原阶段使用的环境元数据如下，它是实际运行记录，不是所有机器均适用的安装锁：

| 组件 | 已使用版本 |
|---|---|
| Python | 3.11 系列 |
| PyTorch | 2.10.0+cu128 |
| Transformers | 5.15.0 |
| PEFT | 0.21.2 |
| Accelerate | 1.15.0 |
| bitsandbytes | 0.50.2 |
| safetensors | 0.8.0 |
| FastAPI / uvicorn | 0.143.0 / 0.54.0 |
| lm-format-enforcer | 0.11.3 |
| python-docx / openpyxl / xlrd | 1.2.0 / 3.1.5 / 2.0.2 |
| PyMuPDF / matplotlib / jsonschema | 1.28.2 / 3.11.2 / 4.26.0 |

原训练基座约 27B 级，本地架构配置为 `Qwen3_5ForConditionalGeneration`，本仓库不指定与本地目录同名的公开模型下载标识。应使用与代码和 Adapter 匹配的完整模型发行版；不同 Qwen 架构由 `model_service/loading.py` 选择相应加载类，不承诺任意模型均兼容。

原设备实际可见约 48 GiB 显存；继续微调时记录的峰值 allocated 约 38.46 GiB、reserved 约 44.83 GiB。不能据此认为普通 24 GiB 显卡能复现同配置。4-bit 只降低部分权重存储，并不消除激活、优化器和长上下文显存开销。显存不足可先选更小且兼容的基座、降低 max_length 和批量；这些属于新实验条件，应重新评测。

推理默认 8-bit；训练脚本使用 4-bit QLoRA。训练时先关闭占用同一 GPU 的推理服务，训练完成再显式启动所需 Adapter，脚本不会替你杀进程。

## 检查

```bash
python scripts/check_environment.py --help
python scripts/check_environment.py
python scripts/check_environment.py --ml
```

模型文件和 CUDA 检查分别使用 `--check-model-files`、`--check-cuda`，详见 `--help`。默认检查不加载模型、不启动训练，也不请求模型服务。完成本地模型配置并启动服务后，连通性及身份检查另用：

```bash
python run_report.py doctor --runtime configs/runtime.json --check-service
```

GPU 依赖升级后，先检查架构支持与 tokenizer，再运行 `--prepare-only` 和 smoke；正式训练使用独立输出目录。不要把较新 Transformers 的参数变动误当成模型或数据问题。
