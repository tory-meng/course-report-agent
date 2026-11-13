# 数据与接口格式

程序相对路径统一相对于 v2 根目录。JSON 使用 UTF-8，不加注释。完整、可运行的合成示例在 `examples/demo_course/`；示例的比例和阈值不能复制成实际课程规则。

已确认的模型使用说明继续作为内容/版式要求；其抽象模块协议在程序中落实为下列具体格式。写作响应是 `paragraphs`+`source_refs`，抽取响应是 `records`；任务 ID、图位和完成状态由宿主记录。

## case

| 字段 | 用途 |
|---|---|
| `id` | 本次材料案例标识 |
| `group_id` | 训练隔离的课程组；同一课程不同章节、不同改稿保持同组 |
| `report_type` | 本版本用 `course` |
| `course_name` | 必须与抽取事实中的课程名称一致 |
| `sources` | 原始资料列表，每项有唯一 `id`、`path`、`role`、`kind` |
| `facts_path` | 可选，人工修正后的事实 JSON；存在时跳过模型事实抽取 |
| `grade_tables` | 可选的明确成绩列映射；空数组时尝试保守识别 |
| `evaluation_policy` | 特殊成绩状态处理与来源；也可由已确认 facts 的 metadata 提供 |
| `ocr` / `ocr_lang` | 默认 false；启用时默认 `chi_sim+eng` |
| `extract_chunk_chars` | 抽取分块字符预算，默认 4500，不是 token 数 |
| `student_id_pattern` | 可选学号正则；默认字母、数字、下划线、连字符 |

来源 `role`：

- `current`：本轮课程事实和成绩。
- `rule`：本轮适用的评分制度、正式映射表。
- `history`：上一轮材料，仅用于 6.1；不得当作当前课程权重。
- `style_reference`：仅排版参考，不进入本轮事实计算。

来源 `kind`：成绩文件用 `grades`，其他资料用 `document`。如果工作簿只是一张教学映射表，应作为 document，避免误当成绩单。工作簿规则注释的自动提取主要读取前后各 8 行；若关键规则在中间单元格，请提取为单独的规则文件或人工补齐事实证据。

## 成绩映射

假设已确认某工作表第 5 行为表头、学号列 2、姓名列 3、总评列 8，可写成：

```json
{
  "grade_tables": [
    {
      "source_id": "grades",
      "sheet": "Sheet1",
      "header_row": 5,
      "student_id": 2,
      "name": 3,
      "total": 8,
      "items": {"I1": 4, "I2": 6}
    }
  ]
}
```

这里 I1/I2 的含义必须与 facts 中的考核项一致，列号只是格式示例。`status` 可选，指向明确的学生状态列；自由文本“备注”不一定能直接作为状态列。

多份分项表各写一个映射，用学号连接。没有总评的分项表可省略 `total`。CSV 的工作表名为文件名去掉扩展名。全部列号/表头行号从 1 开始，程序读表头后的数据行。自动识别不是对任何学校成绩表都通用。

程序保留空白和 0 的区别。同表重复学号、跨表同名同学号但评分冲突、空白与有效评分冲突均停止，不按行拼接或自动选择版本。学号应尽量以文本保存；若源表已经丢失前导零，程序无法凭空恢复。`.xlsx` 中明确的纯 `000…` 显示格式可保留对应前导零。

## 评价制度

下例只展示字段，并不代表学校制度：

```json
{
  "evaluation_policy": {
    "normal": "include",
    "missing": "exclude",
    "absent": "exclude",
    "deferred": "exclude",
    "exempt": "exclude",
    "source_refs": ["policy:L2"]
  }
}
```

每种状态必须明确：`include` 使用有效数值、`exclude` 排除该项有效样本、`zero` 按制度记零。正常状态必须 include。原始缺失评分即使配置 include 也不会变成虚构数值。课程规则可能规定缺考记零，必须按真实制度改，不能沿用示例。

若学生总体状态非 normal，先按该状态处理；否则按对应评分单元格的状态处理。目标有效人数按其所需非零系数考核项的完整记录计算，各目标有效人数可以不同。

## facts 的主要对象

每个实体有稳定 `id`（字母、数字、下划线、点、冒号或连字符）；metadata 和每个实体都必须有 `source_refs` 和 `quotes`。

```json
{
  "id": "G1",
  "text": "原始大纲中的正式课程目标表述",
  "threshold": 0.6,
  "source_refs": ["syllabus:P12"],
  "quotes": {"syllabus:P12": "能够在原文中定位的短引用"}
}
```

引用号取自 `source_manifest.json` 或 `inspect` 结果，不能虚构 `P12`。程序检查引用存在和引文能在原文找到；**这不是对全部语义推断正确性的证明**，权重/目标映射仍需人工核对。

| 对象 | 主要字段与含义 |
|---|---|
| `metadata` | `course_name/course_code/college/major/teacher/class_name/grade/term/academic_year/semester/hours/credits/course_nature/course_category/report_date`；`total_max` 总评满分、`pass_score` 总评及格线、`standard_name` 制度名称、`total_rule_text` 正式总评构成说明 |
| `goals[]` | `id/text/threshold`；目标达成标准在 [0,1]，未知为 null |
| `points[]` | `id/requirement/text/course_weight/threshold/goal_weights/aggregation`；目标到观测点映射，及课程贡献权重 |
| `methods[]` | `id/name/description/goal_ids`；用于 2.4 分条文本及 2.5 映射表 |
| `assessments[]` | `id/name/total_weight/terms`；如期末、过程考核，terms 定义该方式自身成绩的归一化计算 |
| `items[]` | `id/name/assessment_id/max_score/score_basis/converted_max/total_weight/goal_weights`；成绩单上可对应的考核项 |
| `goal_rules[]` | `id` 与目标 ID 相同，`terms:[{item_id,coefficient}]`；定义式1的输入和系数 |
| `assessment_goal_weights[]` | `id/assessment_id/goal_id/weight`；表3-2、4-1、5-3中的方式到目标关系 |
| `assessment_point_rules[]` | `id/assessment_id/point_id/terms`；5.3.2该考核方式对观测点的真实个体序列规则 |
| `narratives[]` | `id/section/text`；section 用 `6.1`、`6.2`、`7` |
| `review` | 人工核对后填写 `approved:true` 和非空 `reviewer`；这与训练记录批准分开 |
| `conflicts` | 抽取冲突列表；有未解决冲突时停止，不能直接删列表掩盖问题 |

`score_basis` 用 raw（原始分）或 converted（折合分）；**`max_score` 必须与实际读入的分数尺度一致**。`converted_max` 用于表格展示，不在式1中再次偷偷折算。表内分层权重和 `goal_rules` 的实际计算系数分别保存，不自动认定是同一层。

比例权重以0–1填写，不把30写成30%。表格要求的方式×目标、考核项×目标矩阵必须区分0与未知：明确不支持的关系根据来源填写0，未知保留null并列为缺项。程序不会因为11种表都存在，就把缺少权重的表判定为完整。

## 计算口径

对学生 s、目标 i，程序计算：

`individual[s,i] = sum(score[s,m] * coefficient[i,m]) / sum(max_score[m] * coefficient[i,m])`

班级平均达成度 = 该目标有效个体达成度的算术平均。

达成率 = `count(individual >= threshold) / valid_count`，不是平均达成度。有效人数为零或阈值缺失时结果为 null，在报告显示“—”，不输出伪百分比。

观测点 `aggregation` 必须根据制度明确选择 `weighted_mean`（除以系数和）或 `weighted_sum`（不再除）；课程贡献值 = 观测点平均达成度 × `course_weight`。方式到观测点没有明确 rules 时，不把同一总评分复制到所有散点图。

总评统计以登记的总评为准，不用分项覆盖总评。分档按总评分/总评满分换算到百分制，区间互斥：90–100、80–<90、70–<80、60–<70、0–<60。中间计算不提前四舍五入；表中展示值做有限位数格式化。

## 人工修订事实后再生成

将生成目录中的 `facts.extracted.json` 或 `facts.json` 另存为 `cases/course_001_facts_reviewed.json`。核对引用、系数、阈值与成绩尺度；填写真实 reviewer。然后在 case 增加：

```json
"facts_path": "cases/course_001_facts_reviewed.json"
```

这是要加到现有 JSON 对象内的字段，不是完整配置。用新的输出目录执行 run。不要更改原始证据以迎合模型输出。

## 输出目录中的文件

| 文件/目录 | 内容 |
|---|---|
| `run_manifest.json` | 输入/代码/模板指纹、实际服务身份、状态、Word/PDF 哈希 |
| `source_manifest.json` | 原始提取内容、文件 SHA、工作表/段落来源 |
| `facts.extracted.json` | 模型合并后的原始事实及冲突（模型抽取时产生） |
| `facts.json` | 通过结构校验、供本轮计算使用的事实 |
| `normalized_grades.json` | 列映射、学生主键、匿名序号、分项、状态、单元格来源 |
| `calculations.json` | 未四舍五入的个体值、有效样本、排除原因、汇总与快照指纹 |
| `sections.json` | 实际填入的短文本 |
| `table_manifest.json` | 原生表格数据、列宽、合并关系 |
| `chart_manifest.json`、`figures/`、`chart_data/` | 图位状态、图片以及逐图输入数据与来源 |
| `records/*.json` | 原始提示、回答、重试、审核标志；后续训练从这里取已审核样本 |
| `validation.json` | 必选表型、图位缺项、数据缺项、待人工排版核验状态 |
| `pdf_validation.json` | PDF 转换产物、页数、SHA与转换信息 |
| `report.docx` / `report.pdf` | 同一次任务生成的可编辑 Word 和 PDF |
