# Passagen 设计方案

Passagen 是一个通过 CLI 整理 paper PDF，并调用 LLM 生成结构化英文摘要和中文 outline 的单机工具。

## 设计目标

- 增量扫描新加入的 PDF，已经处理过的论文不重复处理。
- 提取并补全论文元数据，统一重命名和归档原始 PDF。
- 生成经过 Schema 校验的英文结构化摘要。
- 仅以结构化摘要为输入生成中文 outline，避免两份结果相互矛盾。
- 记录各处理阶段的状态，支持失败重试和断点续跑。

首个版本只提供 CLI，不提供 Web UI、多用户管理、向量检索或自动下载论文。

## 处理流程

```text
扫描 PDF
  -> 计算 SHA-256
  -> 解析 PDF 和提取候选元数据
  -> 使用 DOI/arXiv ID 查询 Semantic Scholar
  -> 去重并建立论文记录
  -> 按章节切分正文
  -> LLM 生成结构化摘要
  -> Schema 校验和有限修复
  -> 基于结构化摘要生成中文 outline
  -> 归档 PDF 和生成结果
```

处理状态至少包括：

```text
discovered -> parsed -> metadata_resolved -> summarized -> outlined -> completed
                                                                  \-> failed
```

每一步成功后持久化状态。重新执行时从最后一个成功阶段继续，而不是重新调用全部外部服务。

## CLI 设计

首版提供以下命令：

```bash
passagen scan <directory>       # 扫描目录并登记新 PDF
passagen process [paper-id]     # 处理全部待处理论文或指定论文
passagen retry [paper-id]       # 重试失败任务
passagen list                   # 查看论文和处理状态
passagen show <paper-id>        # 查看元数据和产物路径
```

`scan` 和 `process` 分离，便于在调用 LLM 前检查识别出的论文，也可以提供 `passagen run <directory>` 依次执行二者。

## 论文标识和去重

每篇论文同时保存以下可用标识：

- DOI：标准化为小写，并移除 URL 前缀和 `doi:` 前缀。
- arXiv ID：移除 `arXiv:` 前缀，保留版本信息之外的规范 ID。
- PDF SHA-256：根据原始文件内容计算。

内部 `paper_id` 由数据库生成且创建后不可变。DOI、arXiv ID 和 SHA-256 分别建立唯一索引，满足以下任一条件即视为已有论文：

- DOI 相同；
- arXiv ID 相同；
- SHA-256 相同。

标题只用于辅助查找和人工检查，不单独作为自动去重依据。同一论文的不同 PDF 版本可能具有不同 SHA-256，此时依靠 DOI 或 arXiv ID 合并，同时保留导入文件和版本信息。这样后续补全 DOI 或 arXiv ID 时不会改变 `paper_id` 和归档路径。

## 元数据

### 字段

至少保存：

- `title`
- `authors`
- `year`
- `venue`
- `doi`
- `arxiv_id`
- `source_url`
- `original_filename`
- `pdf_sha256`

### Semantic Scholar

优先从 PDF 中识别 DOI 或 arXiv ID，再通过 Semantic Scholar Academic Graph API 查询权威元数据。查询使用 DOI 或 arXiv ID，不根据模糊标题结果自动合并论文。

Semantic Scholar 请求失败或未命中时，使用 PDF 解析器提取的元数据继续处理，并把字段来源记录为 `semantic_scholar`、`pdf` 或 `user`。API key 从环境变量读取，不写入配置文件或数据库。

## PDF 解析

### 工具比较

| 维度 | PyMuPDF | GROBID |
| --- | --- | --- |
| 部署 | Python 依赖，简单 | Java 服务，通常通过 Docker 运行 |
| 文本提取 | 快速，但偏底层 | 面向学术论文的结构化解析 |
| 章节识别 | 需要自行实现 | 可输出标题、章节、引用和参考文献结构 |
| 双栏和阅读顺序 | 需要额外处理 | 通常优于通用 PDF 提取器 |
| 页码和坐标 | 支持良好 | 可配置坐标输出，但处理更复杂 |
| 适用场景 | 轻量提取和降级处理 | 论文语义分段与元数据提取 |

### 选择

默认使用 GROBID 的 `processFulltextDocument` 接口，将 TEI XML 转换为内部统一的章节结构。结构化章节更适合后续按 Introduction、Method、Evaluation 等语义进行分块总结。

PyMuPDF 作为 `--parser pymupdf` 轻量模式，并在 GROBID 不可用或解析失败时作为降级后端。降级结果必须标记 `parser=pymupdf`，因为其章节边界和阅读顺序的可靠性较低。

内部解析结果统一为：

```yaml
metadata: {}
sections:
  - title: string | null
    text: string
    pages: [integer]
references: []
parser: grobid | pymupdf
```

首版只保证处理包含文本层的 PDF。扫描版 PDF 的 OCR 不在首版范围内，检测到无有效文本时应明确失败并给出原因。

## 结构化摘要

结构化摘要使用英文。以下 YAML 用于展示字段结构；程序内部以 Pydantic 模型和 JSON Schema 为准，规范结果先保存为 JSON，再按需导出 YAML。

所有非必填字段均允许 `null`。当论文没有报告相关内容时必须输出 `null`，不得根据常识补写。列表字段可为空列表。

```yaml
identity:
  title: string
  authors: [string]
  tags: [string]
  year: integer | null
  venue: string | null
  doi: string | null
  arxiv_id: string | null

problem:
  problem_statement: string | null
  motivation: string | null
  limitations_of_prior_work: [string]

approach:
  core_idea: string | null
  architecture: string | null
  algorithm: string | null
  novelty: [string]

system:
  hardware: [string]
  software: [string]

implementation:
  tools_and_dependencies: [string]

evaluation:
  key_results:
    - claim: string
      value: string | null
      baseline: string | null
      evidence_pages: [integer]
  datasets: [string]
  workloads: [string]

conclusion:
  limitations: [string]
  useful_conclusions: [string]
  reusable_evaluation_methods: [string]

research_connections:
  related_works:
    - title: string
      relationship: string | null
```

正文超过模型上下文限制时，先按章节切分并生成中间事实摘要，再合并为最终结构化摘要。中间结果应保留，失败重试时不重复处理成功分块。

## Schema 校验和修复

LLM 必须使用支持结构化输出的调用方式；无论供应商是否声称保证 JSON 格式，返回值都要经过本地 Pydantic 校验。

处理顺序如下：

1. 解析模型返回的 JSON。
2. 使用 Schema 校验字段、类型和必填项。
3. 对可确定的格式问题执行本地修复，例如移除 Markdown code fence、将缺失的可空字段补为 `null`。
4. 仍不合法时，将校验错误和原始结果交给模型修复，最多重试两次。
5. 仍然失败则保存原始响应和错误信息，并将任务标记为 `failed`。

本地修复不得改写摘要语义，也不得自动生成论文中不存在的内容。

Schema 具有独立版本号。Schema 或 prompt 更新后，可以显式重新生成旧论文，而不将旧产物误认为当前版本结果。

## 中文 Outline

中文 outline 只能在结构化摘要通过校验后生成。输入为规范化后的结构化摘要，不再次直接读取 PDF，也不独立总结全文。

outline 使用论文式组织方式，包含：

- Introduction
- Background
- Design
- Implementation
- Evaluation
- Related Work

当结构化摘要中的对应内容为 `null` 或空列表时，outline 应明确省略该内容，不允许模型自行补充事实。结果保存为 Markdown。

## LLM Provider

通过统一 provider 接口支持 DeepSeek、OpenAI 等服务。首版优先实现 OpenAI-compatible API，运行时配置 provider、base URL 和模型：

```yaml
llm:
  provider: openai_compatible
  base_url: https://api.example.com/v1
  model: model-name
  api_key_env: PASSAGEN_API_KEY
  temperature: 0
```

每次调用记录模型、prompt 版本、Schema 版本、token 用量、调用时间和错误信息。API key 只从指定环境变量读取。

## 数据存储

SQLite 保存论文索引、处理状态和外部调用记录，本地文件系统保存 PDF 及生成产物：

```text
data/
  inbox/
  papers/
    <paper-id>/
      original.pdf
      extracted.json
      summary.json
      summary.yaml
      outline.zh.md
  passagen.db
```

数据库至少包含：

- `papers`：论文标识、元数据、元数据来源和当前状态；
- `artifacts`：PDF、解析结果、摘要和 outline 的路径及版本；
- `processing_runs`：每个阶段的开始时间、结束时间、状态和错误；
- `llm_calls`：provider、模型、prompt/Schema 版本和 token 用量。

文件归档只有在数据库事务成功后才更新为完成状态。失败产生的响应和中间结果应保留，便于诊断和重试。

## 首版范围

首版实现：

- 单机 CLI；
- DOI/arXiv ID 与 SHA-256 去重；
- Semantic Scholar 元数据补全；
- GROBID 默认解析和 PyMuPDF 降级解析；
- 一个 OpenAI-compatible LLM provider；
- Pydantic/JSON Schema 校验及有限修复；
- 英文结构化摘要和基于该摘要生成的中文 outline；
- SQLite 状态管理、断点续跑和失败重试。

首版不实现 Web UI、OCR、向量数据库、多用户管理和论文自动下载。
