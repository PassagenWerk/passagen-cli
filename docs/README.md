# Passagen CLI Documentation

本目录只记录 `passagen-cli` 终端适配器的行为。共享业务、数据库、pipeline、provider 和
artifact 设计由 [`passagen-core`](../../passagen-core/docs/README.md) 维护。

## Documents

| Goal | Document |
|---|---|
| 理解 CLI 包边界和命令调用流程 | [`architecture.md`](architecture.md) |
| 安装、配置和运行 CLI | [`operations.md`](operations.md) |
| 编写和检查 CLI Python 代码 | [`code-style.md`](code-style.md) |
| Collection 和 Tag CLI contract | [`roadmap-collection-and-tags.md`](roadmap-collection-and-tags.md) |
| 理解共享产品和 pipeline 设计 | [`passagen-core/docs/design.md`](../../passagen-core/docs/design.md) |
| 查看共享功能 roadmap | [`passagen-core/docs/roadmap.md`](../../passagen-core/docs/roadmap.md) |

## Ownership Rule

- Typer 参数、Rich 输出、CLI execution log 和退出码写在本目录。
- 领域规则、Schema、migration、provider、LLM diagnostics 和恢复语义写在 Core 文档。
- HTTP、后台 Web runner 和浏览器行为写在 `passagen-web/docs`。
- 不在多个项目复制同一稳定规则；适配器文档通过相对链接引用 Core。
