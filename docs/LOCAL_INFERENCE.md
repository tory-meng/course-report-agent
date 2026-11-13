# 本地推理与在线报告生成

本包提供本地模型服务和报告工作流。服务只监听 `127.0.0.1`，报告客户端核对基座路径、Adapter 路径、量化位数及已配置的 Adapter SHA256。服务不会自动下载模型，也不会自动选择目录中“最新”的 Adapter。

先在项目根目录执行主 README 的环境安装和公开模板生成步骤。本文命令中的 `python` 应指当前已激活的环境；文档环境与 GPU 环境可以分开安装。

## 1. 先验证无需模型的离线流程

```bash
python scripts/build_demo_template.py
python scripts/check_environment.py --runtime configs/runtime.example.json
python run_report.py run \
  --case examples/demo_course/case.json \
  --runtime configs/runtime.example.json \
  --out outputs/offline_demo \
  --offline
```

输出目录必须为空或不存在。再次运行请选择新目录，或仅在同代码、同输入、同配置且支持续跑的原任务上使用 `--resume`。

该演示使用合成学生、合成成绩和已给出的示例事实，不访问模型服务，不验证模型抽取或生成能力。文档、统计、图表、Word/PDF 和来源校验链路可以先在 CPU 环境运行。生成 PDF 需要可执行的 LibreOffice；中文图表和文档需要中文字体，例如 Noto CJK。

## 2. 准备自己获取的模型文件

建议采用以下目录，所有大文件均为本机准备，不包含在代码包中：

```text
models/
  base_model/
    config.json
    tokenizer_config.json
    tokenizer.json             # 或模型所需的vocab/merges文件
    model.safetensors.index.json
    model-00001-of-....safetensors
    ...
  report_adapter/
    adapter_config.json
    adapter_model.safetensors
    ...                        # 如有自己的tokenizer配置，可放同目录
```

`base_model` 是普通本地目录名，不是模型发布地址。按模型发布者的说明获取对应原始基座和分词器。`load_base()` 目前显式支持配置声明为 `Qwen3_5ForConditionalGeneration`、`Qwen3ForCausalLM` 或 `Qwen2ForCausalLM` 的架构；具体模型还须完成真实分词器和生成验证，不能把 `config.json` 中的架构名改为受支持名称来绕过检查。

当前入口面向原始浮点基座在加载时进行 bitsandbytes 4-bit/8-bit 量化。带有 `quantization_config` 的预量化模型不会直接通过此入口。生成只使用文本输入；不会因为基座含视觉组件就自动具备本项目中的图片/OCR抽取训练能力。

Adapter 可以由本包训练产生，步骤见 [训练与微调](TRAINING.md)。若使用外部 Adapter，须匹配同一基座、模块结构和分词器。公开包不含原项目私有基座、既有 Adapter 或候选训练权重。

## 3. 生成本地绑定配置

完成新建 LoRA 或继续微调后，显式选择要测试的 Adapter。例如：

```bash
python scripts/configure_models.py \
  --base models/base_model \
  --adapter models/report_adapter \
  --bits 8
```

也可以将 `--adapter` 指向本次训练输出中的 `adapter` 子目录。脚本检查必要模型文件，计算 Adapter 内容 SHA256，然后生成本机的 `configs/runtime.json`、`configs/model_identity.json` 和 `configs/training_continue.json`。相对路径均以项目根为基准；生成文件包含解析后的本机绝对路径，已在 `.gitignore` 中排除，不应提交到 GitHub。

脚本拒绝覆盖现有三份本机配置。若需要对比另一个 Adapter，保留旧配置并另存候选运行配置：

```bash
python scripts/configure_models.py \
  --base models/base_model \
  --adapter outputs/candidate_run/adapter \
  --bits 8 \
  --out configs/runtime.candidate.local.json \
  --runtime-only
```

`--runtime-only` 不写基线的训练/身份配置，仍拒绝覆盖 `--out` 指定的文件。之后以 `start_service.py --runtime configs/runtime.candidate.local.json` 启动所选候选。模型对比应分次进行并保留相同输入/schema/解码条件，不通过删除旧结果制造同身份续跑。`--copy-adapter-to` 是可选复制功能，会拒绝覆盖已存在的目标，并逐文件核对复制结果。

`8-bit` 是推理加载设置；训练可使用 `4-bit QLoRA`。配置值必须和服务实际加载值一致，不把这两个设置混写。

## 4. 检查环境并启动服务

在 GPU 环境中执行：

```bash
python scripts/check_environment.py \
  --runtime configs/runtime.json \
  --ml --check-cuda --check-model-files

python scripts/start_service.py \
  --runtime configs/runtime.json \
  --max-input-tokens 8192 \
  --max-output-tokens 2048
```

环境检查的默认模式不导入 PyTorch、不分配模型显存、不请求服务。`--check-cuda` 才查询 CUDA；`--check-model-files` 会读取 Adapter 计算 SHA。检查通过只说明依赖和必要文件可用，不替代实际模型运行。

服务启动时会先建立 JSON 约束词表并回放 Unicode/JSON 自检，随后才加载模型。大词表构建可能耗时较长；保留启动日志，不要因为暂未出现监听端口就重复启动多个模型进程。27B 级模型的显存需求受量化、上下文长度、架构及生成长度影响，代码包不承诺特定显存容量能运行所有配置。

`--device-map auto` 可让 Transformers 自动分配设备；实际是否适用取决于本机设备和模型。出现显存不足时，先保存日志，停止当前任务后缩短输入/输出预算或选择已验证的加载配置。训练和推理避免同时占用同一块已接近满载的 GPU。

## 5. 在另一个终端检查接口

```bash
curl http://127.0.0.1:18000/health
curl http://127.0.0.1:18000/progress
python run_report.py doctor --runtime configs/runtime.json --check-service
```

接口包括：

| 路径 | 方法 | 用途 |
| --- | --- | --- |
| `/health` | GET | 模型/Adapter绝对路径、Adapter SHA、量化位数、上下文预算、结构化输出能力 |
| `/progress` | GET | 当前是否生成中、已输出token数和开始时间 |
| `/count_tokens` | POST | 按真实分词器统计messages输入token数 |
| `/generate` | POST | 受输入/输出预算约束的文本或JSON生成 |

例如检查 token 数：

```bash
curl -X POST http://127.0.0.1:18000/count_tokens \
  -H 'Content-Type: application/json' \
  -d '{"messages":[{"role":"user","content":"请用一句话说明课程目标评价。"}],"max_new_tokens":128}'
```

结构化生成的请求可带 `output_schema`，服务返回 `schema_enforced`、`schema_sha256` 等信息。报告客户端不会在约束器缺依赖或身份不符时默默退回无约束调用。JSON合法只证明结构成立；课程事实和评价逻辑还需独立校验。

## 6. 生成自己的课程报告

按 [数据格式](DATA_FORMATS.md) 将本课资料放在本地忽略目录，创建来源清单和成绩列映射。先运行读取检查，再运行在线任务：

```bash
python run_report.py inspect \
  --case configs/case.example.json \
  --out outputs/inspection.json

python run_report.py run \
  --case configs/case.example.json \
  --runtime configs/runtime.json \
  --out outputs/my_course_run
```

这里的 `case.example.json` 是待填写配置，不是保证可直接运行的真实课程资料。缺少关键成绩、课程身份冲突或映射不明确时，应查看停止记录或补充映射，不将考勤或班级汇总替代正式学生成绩。

材料目录清点可使用：

```bash
python run_report.py catalog \
  --root inputs/current \
  --out outputs/material_catalog
```

清点只生成资料安排与候选清单，不代表所有包已经通过成绩语义确认或报告验收。`material-run` 的自动准备仅支持其已实现、可核对的材料结构；不支持的课程包会停止提示，仍可按通用事实/成绩映射流程接入。毕业设计模板默认未接入，不会自动套用课程模板。

参考报告检索是可选功能，须自己准备有权使用的本地参考资料并建立 SQLite 索引；公开包不含参考报告原文和数据库。参考报告只用于字段解释和写作组织，不能自动成为当前课程学生成绩或已批准评价制度。

## 7. 复核和常见问题

- 先看 `validation.json`、`run_manifest.json`、来源和计算审计，再打开 Word/PDF 检查所有页。程序核算通过不等于学校审批。
- “Adapter身份不符”：核对 `/health` 和本地runtime，显式启动所选权重；不要删除哈希检查。
- “不支持Qwen3_5”：检查当前 Transformers 是否提供真实架构类，参考已记录的实验环境版本；不要改架构名伪装兼容。
- “JSON约束回放失败”：保留分词器与日志，运行 `scripts/probe_tokenizer.py --help` 查看CPU分词器诊断，不以重新生成掩盖非法结构。
- 输入超限会拆批或停止；输出超限保留失败记录，按当前校验重试。超时后检查 `/progress`，旧请求可能仍在执行，不应立即重复提交。
- PDF失败通常需要检查 LibreOffice路径、可写临时目录和中文字体。已生成Word与源计算记录应保留，按 `run_report.py pdf --help` 重试转换。

本次代码包验证不重新训练27B模型或替换原本地服务。公开包上的合成演示、单元测试、历史真实课程验证和小样本微调结果是不同证据，应分别记录。
