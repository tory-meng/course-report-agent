# 训练、继续微调与对照评测

本代码包提供的是**已有基座上的 LoRA / QLoRA 监督微调（SFT）**，不包含从随机参数开始的大模型预训练。报告统计、Excel 解析、公式复算、画图及 Word 排版仍由 Python 代码完成，训练对象是解析后文本任务的答案；不包含图像识别或 OCR 训练。

本文命令均在代码包根目录执行。`python` 应指向按根 README 安装好的 ML 环境；脚本中的相对路径统一相对于项目根目录。运行时数据与权重由使用者在本机补齐，不在 GitHub 代码包中。

## 1. 两种初始化方式

| 配置 | 用途 | 本地必须准备 |
|---|---|---|
| `configs/training_new_lora.example.json` | `initialization=new_lora`，在基座上新建 LoRA | 完整基座权重、config、tokenizer |
| `configs/training_continue.example.json` | `initialization=continue_adapter`，从已有 LoRA 继续训练 | 同一基座及完整已有 Adapter |

训练固定使用 bitsandbytes **4-bit NF4 QLoRA**；报告服务默认使用 **8-bit 推理**。`expected_bits=8` 不是训练精度设置。当前训练入口是单卡 CUDA，默认要求 BF16；用 `CUDA_VISIBLE_DEVICES` 只暴露一张卡。`minimum_free_gib` 是启动前的最低门槛，并不保证某个模型一定能装入显存。基座规模、序列长度、LoRA 覆盖模块和优化器都会影响实际显存。训练和推理服务占用同一 GPU 时，先停止本实验代码包自己的服务。

仅支持 `model_service/loading.py` 中列出的模型架构；不能通过改写 `config.json` 的架构名强行适配。基座应为原始权重，脚本运行时量化，不能直接代入不兼容的预量化模型。所有模型读取均为本地模式，不会自动下载权重。

## 2. 无课程数据的最小训练链路：合成 smoke

这个示例只验证代码、Tokenizer、loss、梯度、checkpoint 和 Adapter 保存链路，不能证明课程报告质量。合成内容由确定性脚本生成，没有学生姓名、学号、真实课程材料或人工标注声明。

将兼容基座放到 `models/base_model/`，或修改配置中的 `model_path` 指向本机模型目录：

```bash
cp configs/training_new_lora.example.json configs/training_new_lora.json
python scripts/make_synthetic_sft.py --out data/synthetic_smoke
python -m training.length_check \
  --data data/synthetic_smoke/train.jsonl data/synthetic_smoke/validation.jsonl \
  --tokenizer models/base_model --max-length 4096 \
  --out outputs/synthetic_tokenization.json
python train_v2.py train \
  --config configs/training_new_lora.json --dataset data/synthetic_smoke \
  --out outputs/new_lora_preflight --stage smoke --prepare-only
CUDA_VISIBLE_DEVICES=0 python train_v2.py train \
  --config configs/training_new_lora.json --dataset data/synthetic_smoke \
  --out outputs/new_lora_smoke --stage smoke
```

输出路径必须是新目录。`--prepare-only` 校验数据、完整模型文件、真实 Tokenizer 编码与 Trainer 参数兼容性，不加载 GPU 模型，也不创建训练输出目录；它仍需要安装 ML 依赖。若只检查 Tokenizer，用前面的 `training.length_check` 即可。

生成器产生 12 条训练、4 条验证，使用两个独立合成课程组。它们标记为 `synthetic=true` 和 `review.kind=synthetic_fixture_review`，这是机器检查过的测试夹具，**不是人工或用户授权代理审批**。训练器会拒绝用这些夹具执行 `--stage formal`，即使把它们混入真实数据也会拒绝。不要通过改审核类型或 `synthetic` 标签绕过该门禁。

smoke 固定为 5 个优化器步，并输出：

```text
outputs/new_lora_smoke/
  run_metadata.json       # 起始配置、数据指纹、监督范围
  baseline_metrics.json   # 训练前 teacher-forced loss
  checkpoint-*/           # Adapter、优化器、调度器、随机状态
  trainer_state.json
  training_results.json   # 训练前后 loss、步数、显存、候选位置
  adapter/                # Adapter 与 Tokenizer；不含完整基座
```

smoke 的 Adapter 只能用于演示接线。五步合成训练不会让通用基座掌握真实课程评价，也不能替代正式实验。若有另行审核的真实 SFT 数据集，可以使用同一个 `new_lora` 配置执行 `--stage formal`；不要求先有旧 Adapter。

## 3. 在本机接入已有 Adapter

如果要复现真实报告与继续微调，需要自行提供与基座匹配的、合法取得的 Adapter。公开包不含原实验 Adapter、真实课程材料、已审核 SFT 数据或历史训练记录，因此不能仅凭公开包重现原实验的数值结果。

首次配置示例：

```bash
python scripts/configure_models.py \
  --base models/base_model --adapter models/report_adapter \
  --bits 8 --base-url http://127.0.0.1:18000
```

如果只演示合成链路，可将 `--adapter` 指向 `outputs/new_lora_smoke/adapter`，但不要把其输出当成正式报告。配置器生成 `configs/runtime.json`、`configs/training_continue.json` 和模型身份记录，绑定实际 Adapter 的 SHA256，不覆盖已有配置。若这些文件已经存在，应先核对现有文件，不能反复运行配置器来绕过身份检查。

在当前代码包根目录、单独终端启动服务：

```bash
CUDA_VISIBLE_DEVICES=0 python scripts/start_service.py \
  --runtime configs/runtime.json --device-map single \
  --max-input-tokens 8192 --max-output-tokens 2048
```

示例使用独立的 `18000` 端口，避免与其他项目混用。按照根 README 登记自己的课程资料并生成报告。至少准备两个独立课程家族，一个用于训练，另一个用于开发验证；同名课程不同学期、共享原始文件或共享同一任务答案不应分在两侧。

## 4. 方案 A：从实际运行记录逐条审核并导出

这个路径不需要手写复杂的候选整理配置，适合先跑通真实报告，再选取可靠记录训练。以下 `outputs/course_alpha`、`outputs/course_beta` 是你自己的实际生成结果，并非包内已经存在的课程数据。

```bash
python train_v2.py review --run outputs/course_alpha --out reviews/course_alpha
python train_v2.py review --run outputs/course_beta --out reviews/course_beta
```

查看 `review_queue.json` 及 `answers/` 中每条答案，逐条对照自己的原材料修正。审核还应确认完整答案未泄露在输入中、引用属于本课、缺失成绩没有被补零、教学安排没有伪造历史成效。之后对单条记录批准：

```bash
python train_v2.py approve \
  --record outputs/course_alpha/records/section_2_4.json \
  --answer-file reviews/course_alpha/answers/section_2_4.json \
  --reviewer your-reviewer-id --answer-isolation-checked
```

其他记录需要分别审核，不提供批量自动审批。不要照抄不存在的文件名；以实际 `review_queue.json` 为准。最少保证训练和验证两侧均有通过审核的记录：

```bash
python train_v2.py export \
  --runs outputs/course_alpha outputs/course_beta \
  --validation-group your-validation-group-id \
  --out data/approved_sft
```

`--validation-group` 填实际记录中的 `group_id`，可重复指定。也可省略以按固定 seed 分组，但需要检查生成的课程划分。导出器禁止同一课程组跨集合，并清理跨集合完全相同的答案；训练或验证为空会停止。人工确认课程家族关系仍然必要，不能把改名后的同一课程当独立验证。

## 5. 方案 B：整理来源绑定的任务候选，再逐条审核

`train_v2.py prepare` 会根据完整报告轨迹整理成绩来源选择、教学目标关联和章节写作任务。它需要实际报告输出、当前仍存在的源材料及人工整理的对应关系，不是只给一个空目录就能自动获得正确标签。

1. 复制 `configs/training_preparation.example.json` 为本地 `configs/training_preparation.json`。
2. 将每个 `run` 改为自己的报告输出目录；`paragraphs` 按本课教学方式顺序逐段填写，不写方式标题，且不能将完整答案预先放在任务输入中。
3. `goal_methods` 必须列全本课目标，使用实际的目标/方式 ID，例如 `G1` 和 `M1`；理由说明具体活动如何支撑目标及其边界，不能套用其他课程。
4. `validation_course_names` 填整课留出的课程名。共享材料、同名课程和原始分组会合并为课程家族，不能拆到训练/验证两侧。
5. `training_config` 指向已经与本地模型绑定的继续微调配置；可选 `tokenizer_path` 指定 Tokenizer，默认优先已有 Adapter 的 Tokenizer，再回退到基座。

```bash
python train_v2.py prepare \
  --config configs/training_preparation.json --out data/prepared \
  --tokenizer-python .venv-ml/bin/python
python scripts/manual_finetune.py \
  --stage review --preparation data/prepared --reviewer your-reviewer-id
python scripts/manual_finetune.py --stage check --preparation data/prepared
python train_v2.py export-prepared \
  --preparation data/prepared --decisions data/prepared/review_decisions.json \
  --out data/approved_sft
```

`.venv-ml/bin/python` 只是解释器路径示例，应替换为实际 ML 环境。候选准备不授予训练资格。交互审核使用 `y/n/r/q`，答案文件被编辑后必须重新显示、审核并绑定新哈希。来源文件、候选输入、答案或课程划分发生变化，原审批不能直接复用。

## 6. 训练前基线、smoke 与正式继续训练

先保留原模型在固定验证集上的生成结果，随后停止当前实验的推理服务以释放显存：

```bash
python train_v2.py evaluate \
  --runtime configs/runtime.json --data data/approved_sft/validation.jsonl \
  --out outputs/baseline_generation.json
```

停止服务应在其终端按 `Ctrl+C`，确认 GPU 已释放；不要停止其他项目的模型进程。再执行：

```bash
python train_v2.py train \
  --config configs/training_continue.json --dataset data/approved_sft \
  --out outputs/continue_preflight --stage formal --prepare-only
CUDA_VISIBLE_DEVICES=0 python train_v2.py train \
  --config configs/training_continue.json --dataset data/approved_sft \
  --out outputs/continue_smoke --stage smoke
CUDA_VISIBLE_DEVICES=0 python train_v2.py train \
  --config configs/training_continue.json --dataset data/approved_sft \
  --out outputs/continue_formal --stage formal
```

smoke 和 formal 都读取配置里指定的**同一个起始 Adapter**；formal 不会自动接着 smoke 的五步权重继续，避免把链路检查混入正式实验。示例配置是 1 epoch、batch 1、梯度累积 8、max length 4096、learning rate 5e-6，都是可修改的实验参数，不保证适合所有模型或数据。训练不截断过长答案，只监督最后一条 assistant 答案与 Qwen 结束标记；prompt 和 padding 的 label 均为 `-100`。

如使用方案 B，另有可选的一键编排入口：

```bash
python scripts/manual_finetune.py \
  --stage train --preparation data/prepared --out outputs/managed_sft \
  --runtime configs/runtime.json --gpu 0 \
  --doc-python .venv-doc/bin/python \
  --train-python .venv-ml/bin/python \
  --service-python .venv-ml/bin/python
```

三种解释器默认都为当前 Python，分环境时显式指定。辅助脚本仅管理**当前代码包根目录启动、当前用户拥有、指定解释器运行的本地服务**；服务忙碌或 PID/目录不符时停止，不会跨项目终止原项目。它执行基线、导出、smoke 和 formal，并尝试恢复原 Adapter 服务；不自动加载候选。若恢复失败，查看 `service_restore.log`，使用 `--stage restore --out outputs/managed_sft` 重试，不要重复启动占用端口的服务。

## 7. 候选服务与同输入对照

将新候选作为独立配置另存，不能覆盖基线 runtime。以下代码只创建候选配置，不启动或停止服务：

```bash
python scripts/configure_models.py \
  --base models/base_model --adapter outputs/continue_formal/adapter \
  --bits 8 --out configs/runtime.candidate.local.json --runtime-only \
  --base-url http://127.0.0.1:18000
```

停止仍在使用同一 GPU 的基线服务，再启动候选：

```bash
CUDA_VISIBLE_DEVICES=0 python scripts/start_service.py \
  --runtime configs/runtime.candidate.local.json --device-map single \
  --max-input-tokens 8192 --max-output-tokens 2048
```

另一个终端评测完全相同的验证文件：

```bash
python train_v2.py evaluate \
  --runtime configs/runtime.candidate.local.json --data data/approved_sft/validation.jsonl \
  --out outputs/candidate_generation.json
```

保持基座、输入 JSONL、量化位数、Token 上限和解码规则一致。`baseline_generation.json` 与 `candidate_generation.json` 保留每题原始回答、模型身份、输入指纹及分项结果。应分别比较 JSON/schema、任务约束、语义与来源正确性，并用真实课程再次生成完整报告检查数字、图表、漏项及页面外观。`exact_match` 不等于语义准确率，loss 下降也不等于报告质量提升。确认收益前保留基线 Adapter，不自动推广候选。

## 8. 中断后恢复与“继续微调”的区别

```bash
CUDA_VISIBLE_DEVICES=0 python train_v2.py train \
  --config configs/training_continue.json --dataset data/approved_sft \
  --out outputs/continue_formal --stage formal \
  --resume outputs/continue_formal/checkpoint-N
```

把 `checkpoint-N` 改成实际目录。完整恢复需要同一训练输出目录中的 Adapter、`trainer_state.json`、optimizer、scheduler 和 RNG 状态，且配置、数据与起始 Adapter 指纹不变。只拿最终 `adapter/` 只能作为**新一轮继续微调起点**，不能恢复原优化器步数；使用新的输出目录与重新绑定的配置。

常见停止原因：源材料或 Adapter 哈希变化、训练/验证课程重叠、答案泄露、过长样本、未审核样本、空集、无 BF16/CUDA、显存不足、LoRA target_modules 不匹配、Trainer 参数版本不兼容。按报错检查数据和配置，不要删除校验或伪造审核/合成标记来强行运行。
