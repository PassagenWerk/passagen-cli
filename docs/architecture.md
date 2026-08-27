# Passagen Python 包架构

本文档定义 Passagen 的模块职责、依赖方向、核心 contract 和扩展规则，用于回答“新逻辑应该放在哪里”。产品行为见 [`design.md`](design.md)，实施顺序见 [`roadmap.md`](roadmap.md)。

本文借鉴 Denkbild 的架构设计方式：入口与业务语义分离、pipeline 只负责编排、跨阶段数据显式建模、子包文档描述职责和扩展点。Passagen 不复制编译器特有层级，而是把这些原则映射到论文处理流程。

## 当前状态

当前 M0-M2 实现采用职责明确的顶层模块：

| 路径 | 当前职责 |
|------|----------|
| `src/passagen/cli.py` | Typer 入口、配置错误呈现和数据库命令 |
| `src/passagen/config.py` | 配置读取、优先级合并和运行时校验 |
| `src/passagen/models.py` | Paper 状态与基础领域模型 |
| `src/passagen/db.py` | SQLite 连接、事务和 Schema migration |
| `src/passagen/repository.py` | Paper/PDF artifact 持久化、查询和 row mapping |
| `src/passagen/scanning.py` | PDF 发现、基础完整性校验、内容寻址导入和失败隔离 |

这些模块在职责仍然紧凑时可以保持不拆分。后续里程碑引入解析、metadata、LLM 和 pipeline 后，再按本文定义的边界演进；不要为了匹配目标目录预先创建空包。

默认运行边界是命令启动时的当前工作目录：配置从 `./passagen.yaml` 读取，受管理状态写入 `./data/`。用户 Home 目录不属于隐式配置或持久化来源；只有调用方显式传入的绝对路径可以把数据放到当前目录之外。

## 系统职责

Passagen Python 包负责：

- 读取配置并提供 CLI 工作流。
- 扫描、识别和持久化论文及处理状态。
- 调用 PDF parser、metadata service 和 LLM provider。
- 校验并保存 parsed paper、summary 和 outline artifacts。
- 编排可恢复、幂等的论文处理 pipeline。

Passagen Python 包不负责：

- 实现 GROBID 服务或 LLM 服务本身。
- 自动下载论文、OCR、Web UI、多用户和分布式任务。
- 在 CLI 层重新实现领域规则或持久化规则。
- 把外部供应商的响应格式直接暴露为内部稳定模型。

## 数据流

一次完整处理沿稳定阶段推进：

```text
Settings + command input
  -> content-addressed managed PDF artifact
  -> discovered Paper
  -> ParsedPaper artifact
  -> resolved PaperMetadata
  -> section fact artifacts
  -> validated StructuredSummary artifact
  -> Chinese outline artifact
  -> completed Paper
```

数据库保存身份、阶段状态、版本和 artifact 索引；文件系统保存 PDF 与较大的结构化产物。扫描目录只作为一次性导入源，后续阶段读取 `data_dir` 下已提交的受管理 artifact，而不是源路径或上一个函数仍在内存中的临时对象，因此源文件移走和进程中断都不影响恢复。

## 目标包边界

目录只在对应能力进入 Roadmap 实现时创建。

| 包 | 职责 | 不应包含 |
|----|------|----------|
| `config/` | 分区 YAML Schema、文件与环境变量合并 | 业务阶段选择、HTTP 调用 |
| `cli/` | 命令注册、参数转换、用户输出 | SQL、PDF/LLM 语义 |
| `domain/` | Paper、状态转换、稳定值对象和领域错误 | Typer、HTTP、sqlite3 连接 |
| `storage/` | migration、repository、事务和 artifact 路径管理 | CLI 展示、LLM prompt |
| `parsing/` | `PaperParser` contract、GROBID/PyMuPDF adapter、统一 ParsedPaper | metadata 查询、summary 生成 |
| `metadata/` | 标识标准化、Crossref/arXiv adapter、字段来源合并 | PDF 页面解析、pipeline 编排 |
| `summarization/` | summary Schema、分块、prompt、校验和有限修复 | CLI、数据库 migration |
| `outlining/` | 从合法 summary 生成中文 outline | 直接读取和总结 PDF |
| `providers/` | LLM provider contract 和供应商 adapter | summary 领域决策 |
| `pipeline/` | stage 顺序、输入输出检查、恢复与运行记录 | parser 算法、SQL 细节、prompt 内容 |
| `utils/` | 无业务语义的文件或日志小工具 | Paper、summary、metadata 规则 |

当一个包内部出现多个独立职责时，再拆成模块。例如 `storage/` 可以逐步包含 `connection.py`、`migrations.py`、`papers.py` 和 `artifacts.py`。在此之前，一个清晰的小模块优于只有转发作用的目录层级。

## 依赖方向

依赖从运行时外壳指向稳定业务 contract：

```text
cli
  -> pipeline/application services
       -> domain models + service protocols
       -> storage/parsing/metadata/providers adapters

config -> cli and composition root
adapters -> domain models or their own boundary models
domain -> Python standard library only
```

具体规则：

- `domain` 不导入 Typer、Rich、HTTP client、Pydantic Settings 或 sqlite3 connection。
- YAML 只存在于配置输入边界；其他业务包依赖经过 Pydantic 校验的配置模型，不读取原始 mapping。
- `pipeline` 依赖 parser/provider/repository 的 Protocol，不依赖某个供应商才能表达业务流程。
- adapter 可以依赖第三方库，但必须把响应转换为 Passagen 模型后再返回。
- `cli` 是 composition root：读取 Settings，构造具体 adapter，调用应用入口并呈现结果。
- 子包之间不能通过导入对方的私有 helper 建立隐式 contract。

## 模型边界

不同数据模型服务于不同边界，不要求一个 `Paper` 类型承担所有职责。

| 模型 | 推荐实现 | 用途 |
|------|----------|------|
| 领域实体和值对象 | dataclass、StrEnum | 状态转换、标识和领域 invariant |
| 外部配置 | Pydantic Settings | 文件、环境变量和 CLI override 校验 |
| 外部响应 DTO | Pydantic model 或局部解析函数 | 隔离供应商字段和缺失值 |
| ParsedPaper / StructuredSummary | Pydantic model | artifact Schema、版本化和 JSON 校验 |
| 数据库记录 | 显式 row mapper | SQL row 与领域对象转换 |

禁止把未经校验的 `dict[str, Any]` 跨模块传递。外部字典应在 adapter 边界转换，artifact 应带 `schema` 与 `version`，数据库结构由 `PRAGMA user_version` 管理。

## Pipeline 编排

Pipeline 与 Denkbild 的 compiler pipeline 保持相同职责边界：只决定阶段顺序、校验前置条件、记录运行状态并调用具体服务。

建议阶段：

| Stage | 输入 | 输出 | 业务实现所有者 |
|-------|------|------|----------------|
| `scan` | 源目录、Settings | managed PDF artifact、discovered Paper | scanning/storage |
| `parse` | managed PDF artifact | ParsedPaper artifact | parsing |
| `resolve_metadata` | identifiers、ParsedPaper | resolved metadata | metadata |
| `summarize` | ParsedPaper | validated summary | summarization/providers |
| `outline` | validated summary | Markdown outline | outlining/providers |
| `complete` | 必需 artifacts | completed status | pipeline/storage |

每个 stage 应具备：

- 稳定 stage id 和明确输入、输出。
- 执行前的前置条件检查。
- 成功后在同一事务边界提交运行结果和状态。
- 可区分的业务失败与执行失败。
- 根据 artifact、prompt、Schema 和 provider 版本判断能否复用结果。

Pipeline 不应把所有中间对象堆成一个不断扩张的 context 字段集合。跨阶段结果通过数据库记录和有类型的 artifact reference 传递；仅一次运行需要的依赖可以放在轻量 context 中。

## 持久化与事务

- repository 负责 SQL 与 row mapping，领域层不拼接 SQL。
- 默认配置路径和 `data_dir` 以当前工作目录为根；不得在 adapter 内回退到用户 Home 目录。
- migration 按整数版本只向前执行，不在运行时猜测旧表结构。
- 数据库版本高于程序支持版本时立即拒绝运行。
- SQLite connection 统一启用 foreign keys、WAL 和 busy timeout。
- 数据库事务只覆盖数据库操作，不在持有写事务时调用 GROBID、Crossref、arXiv 或 LLM。
- artifact 路径相对 `data_dir` 保存，不能持久化扫描源目录的绝对路径。
- 原始 PDF 使用 SHA-256 内容寻址并视为不可变对象；解析阶段只读取受管理副本。
- PDF 导入先在 `data_dir` 内写临时文件并校验 hash，再原子重命名并用短事务登记 artifact；失败时清理未引用的临时或新建文件。
- 外部调用结果先安全写入临时 artifact，随后用短事务登记 artifact 并推进状态。
- 重试依赖持久化的 stage run 和版本信息，不依赖捕获所有异常后从头执行。

## 外部服务

每种外部能力由小型 Protocol 与 adapter 组成：

```text
PaperParser.parse(path) -> ParsedPaper
MetadataClient.lookup(identifier) -> MetadataResult | None
LLMProvider.generate(request) -> LLMResponse
```

- Protocol 使用 Passagen 的输入输出模型，不泄漏 HTTP response object 或 SDK 类型。
- DOI 由 Crossref adapter 处理，arXiv ID 由 arXiv adapter 处理；路由依据标识类型，不执行标题模糊搜索。
- 元数据补全失败是可降级结果，pipeline 保留 PDF 元数据并继续；只有本地数据自身无效时才是业务失败。
- timeout、认证、重试和供应商错误转换属于 adapter。
- fallback 选择和是否继续处理属于应用或 pipeline 策略。
- 所有 client 支持注入，以便测试使用固定 fake，而不是访问真实服务。

## CLI 边界

CLI command 的标准流程：

1. 解析 Typer 参数。
2. 从 root context 获取 Settings 和应用服务。
3. 调用一个应用入口。
4. 用 Rich 呈现结果。
5. 将预期业务异常转换为稳定退出码。

CLI 不打开数据库事务、不解析第三方响应、不决定状态是否合法。库入口必须可以不经过 Typer 直接调用，测试优先覆盖库行为，再用少量 CLI 测试覆盖参数和输出 contract。

## 扩展规则

### 新增 PDF parser

1. 实现 `PaperParser` Protocol。
2. 输出统一 `ParsedPaper`，不把 TEI 或 PyMuPDF object 传出 adapter。
3. 注册到 composition root 或明确的 parser registry。
4. 添加 adapter 单元测试、统一 contract test 和 fallback 集成测试。
5. 更新配置与 `design.md` 中的 parser 行为。

### 新增 metadata provider

1. 实现最小 MetadataClient contract。
2. 明确支持的精确 identifier，不默认加入标题模糊合并。
3. 转换 rate limit、timeout、not found 和 invalid response。
4. 添加固定 HTTP 响应测试，并更新字段来源规则。

### 新增 LLM provider

1. 实现 provider Protocol，不在 summarization 中分支判断供应商名称。
2. 保留模型、token、prompt 和 Schema 版本信息。
3. 将 SDK 异常转换为受控 provider 错误。
4. 使用相同的结构化输出 contract tests 验证所有 provider。

### 新增 pipeline stage

1. 定义稳定 stage id、前置状态、输入 artifact 和输出 artifact。
2. 将业务算法放入所属包，stage 只调用它。
3. 定义成功、可重试失败和终止失败对状态的影响。
4. 添加 stage 单元测试和与相邻 stage 的集成测试。
5. 更新本文件的数据流和 `roadmap.md` 的验收条件。

## 测试分层

| 层级 | 覆盖范围 |
|------|----------|
| unit | 单个领域规则、parser 转换、Schema 校验和 row mapper |
| component | 单个业务子包内多个模块协作，不访问真实网络 |
| integration | SQLite、adapter fake 与多个业务包之间的 contract |
| e2e | 从 CLI 或应用入口完成用户工作流，外部服务默认固定响应 |

测试目录规模增长后应镜像业务包边界。跨边界 fixture 由 `tests/fixtures/` 管理，测试构造器由 `tests/support/` 管理；不要把只服务测试的 helper 放入生产包。

## 架构检查清单

新增功能时检查：

- 业务语义是否落在所属子包，而不是 CLI 或 pipeline。
- 外部动态数据是否已在边界转换为明确模型。
- 数据库、文件和外部调用是否具有清晰的提交顺序。
- 重试是否能复用已成功、版本匹配的 artifact。
- 新依赖方向是否符合本文规则，是否绕过公开 contract。
- 测试是否覆盖成功路径、边界条件和可恢复失败。
- 实现、测试、设计文档和 Roadmap 对当前状态的描述是否一致。
