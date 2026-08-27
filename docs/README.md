# Passagen 文档索引

本文档是 Passagen 的开发文档入口。产品行为和数据约定记录在设计方案中，实施顺序记录在 Roadmap 中，代码组织和开发约定分别由架构与代码风格文档负责。

## 阅读入口

| 目标 | 文档 |
|------|------|
| 理解产品目标、处理流程和数据格式 | [`design.md`](design.md) |
| 查看版本范围、里程碑和验收条件 | [`roadmap.md`](roadmap.md) |
| 判断新代码应该放在哪里 | [`architecture.md`](architecture.md) |
| 编写、测试和检查 Python 代码 | [`code-style.md`](code-style.md) |

## 文档类型

| 类型 | 职责 |
|------|------|
| `README.md` | 文档入口，不重复展开具体设计 |
| `design.md` | 面向产品和系统行为的稳定设计决策 |
| `architecture.md` | 包职责、依赖方向、跨模块 contract 和扩展规则 |
| `code-style.md` | 可执行的编码、类型、错误处理和测试约定 |
| `roadmap.md` | 尚未实现或正在推进的工作及验收条件 |

## 维护规则

- 文档必须区分当前实现与未来计划，不能把 Roadmap 当作已交付行为。
- 外部配置、数据库 Schema、处理状态或 artifact 格式变化时，同步更新 `design.md` 和对应测试。
- 包职责、依赖方向或 pipeline stage contract 变化时，同步更新 `architecture.md`。
- 开发命令、静态检查或测试分层变化时，同步更新 `code-style.md`。
- 新增重要开发文档后，在本索引中登记。
