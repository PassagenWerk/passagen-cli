# Passagen 设计方案

Passagen 是一个通过 CLI 整理 paper PDF，并调用 LLM 生成结构化英文摘要和中文 outline 的单机工具。

## 设计目标

- 增量扫描新加入的 PDF，已经处理过的论文不重复处理。
- 导入 PDF 后立即建立受 Passagen 管理的副本，后续处理不依赖源文件。
- 提取并补全论文元数据，统一管理 PDF 和生成产物。
- 生成经过 Schema 校验的英文结构化摘要。
- 仅以结构化摘要为输入生成中文 outline，避免两份结果相互矛盾。
- 记录各处理阶段的状态，支持失败重试和断点续跑。

首个版本只提供 CLI，不提供 Web UI、多用户管理、向量检索或自动下载论文。

## 处理流程

```text
扫描 PDF
  -> 计算 SHA-256
  -> 去重并将 PDF 导入受管理存储
  -> 解析 PDF 和提取候选元数据
  -> 使用 DOI 查询 Crossref，使用 arXiv ID 查询 arXiv API
  -> 补全或合并论文记录
  -> 按章节切分正文
  -> LLM 生成结构化摘要
  -> Schema 校验和有限修复
  -> 基于结构化摘要生成中文 outline
  -> 归档生成结果
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

标题只用于辅助查找和人工检查，不单独作为自动去重依据。同一论文的不同 PDF 版本可能具有不同 SHA-256，此时依靠 DOI 或 arXiv ID 合并，同时保留各版本的受管理 PDF artifact。这样后续补全 DOI 或 arXiv ID 时不会改变 `paper_id`，PDF 的内容寻址路径也不依赖论文元数据。

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

`original_filename` 只用于展示和审计，不参与后续文件读取。数据库不保存扫描目录中的源路径。

### Crossref 与 arXiv

优先从 PDF 中识别 DOI 或 arXiv ID，再按标识类型精确查询元数据：

- DOI 使用 Crossref REST API。
- arXiv ID 使用 arXiv API。
- 同时具有 DOI 和 arXiv ID 时可以查询两者，Crossref 用于已发表版本的 venue、year 和 DOI 元数据，arXiv 用于预印本标识和版本信息。
- 没有可靠标识时只使用 PDF parser 的结果，不根据模糊标题自动查询或合并论文。

字段合并优先级为 `user > crossref > arxiv > pdf`。每个字段记录实际来源 `user`、`crossref`、`arxiv` 或 `pdf`，不能只记录整条论文的单一来源。

Crossref 或 arXiv 请求失败、限流或未命中时，保留 PDF parser 已提取的元数据并继续处理。外部补全是 best-effort 能力，不是摘要流水线成功的前置条件。

`metadata_resolved` 表示本地元数据已经标准化并完成可用的外部补全尝试，不表示 Crossref 或 arXiv 请求必须成功。

## PDF 导入与托管

`scan` 接受用户目录中的 PDF 作为一次性导入源。计算 SHA-256 并完成去重后，程序将文件复制到相对 `data_dir` 的内容寻址路径：

```text
pdfs/<sha256 前两位>/<sha256>.pdf
```

导入完成后的规则：

- 数据库通过 `artifacts(kind="original_pdf")` 保存相对 `data_dir` 的路径，不保存源文件绝对路径。
- 解析、重试、重新生成和状态查询只使用受管理副本。
- 用户可以移动或删除扫描目录中的源文件，不影响已导入论文。
- 相同 SHA-256 复用同一个受管理文件，不重复复制。
- `original_filename` 可以保留为审计元数据，但不是文件定位依据。

复制先写入 `data_dir` 内的临时文件，校验实际写入内容的 SHA-256 后再原子重命名到最终路径。数据库记录失败时清理本次创建且尚未被引用的文件；最终路径已存在时校验后复用。

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

## 本地运行目录

Passagen 默认把配置和所有受管理数据限制在启动命令时的当前工作目录：

```text
./passagen.yaml
./data/
```

- 仓库提供可直接运行的最小 `passagen.yaml`；文件不存在或内容为空时仍可使用内置默认值和环境变量。
- `data/` 保存数据库、受管理 PDF 和生成产物。
- 默认运行不会读取或创建 `~/.config/passagen`、`~/.local/share/passagen` 等用户级目录。
- `--config`、`--data-dir`、`PASSAGEN_DATA_DIR` 等显式覆盖仍然有效。
- 相对覆盖路径以执行命令时的当前工作目录为基准。
- API key 继续只从指定环境变量读取，不写入 `passagen.yaml`。

当前最小配置使用 `passagen` 顶层分区：

```yaml
passagen:
  data_dir: data
  database_path: null
  debug: false
```

配置文件使用 `yaml.safe_load` 解析。根节点和各配置分区必须是 mapping，不允许使用可执行 Python tag。后续 LLM、parser 和 pipeline 配置应增加独立顶层分区，避免把所有字段堆入 `passagen`：

```yaml
passagen: {}
parser: {}
llm: {}
pipeline: {}
```

当前配置优先级为：CLI 参数 > 环境变量 > YAML > 内置默认值。

## 数据存储

SQLite 保存论文索引、处理状态和外部调用记录，本地文件系统保存 PDF 及生成产物。默认根目录是当前工作目录下的 `data/`。程序直接使用 Python 标准库 `sqlite3` 和显式 SQL，通过 `PRAGMA user_version` 管理 Schema 版本：

```text
data/
  pdfs/
    <sha256-prefix>/
      <sha256>.pdf
  papers/
    <paper-id>/
      extracted.json
      summary.json
      summary.yaml
      outline.zh.md
  passagen.db
```

数据库至少包含：

- `papers`：论文标识、元数据、元数据来源和当前状态；
- `artifacts`：受管理 PDF、解析结果、摘要和 outline 相对 `data_dir` 的路径及版本；
- `processing_runs`：每个阶段的开始时间、结束时间、状态和错误；
- `llm_calls`：provider、模型、prompt/Schema 版本和 token 用量。

导入 PDF 时，文件原子落盘与数据库 artifact 登记必须作为一个可恢复操作处理，不能留下引用源目录的记录。后续 artifact 只有在文件完整落盘且数据库事务成功后才推进对应处理状态。失败产生的响应和中间结果应保留，便于诊断和重试。

## 首版范围

首版实现：

- 单机 CLI；
- DOI/arXiv ID 与 SHA-256 去重；
- Crossref DOI 与 arXiv API 元数据补全；
- GROBID 默认解析和 PyMuPDF 降级解析；
- 一个 OpenAI-compatible LLM provider；
- Pydantic/JSON Schema 校验及有限修复；
- 英文结构化摘要和基于该摘要生成的中文 outline；
- SQLite 状态管理、断点续跑和失败重试。

首版不实现 Web UI、OCR、向量数据库、多用户管理和论文自动下载。
