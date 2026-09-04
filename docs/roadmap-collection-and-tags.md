# Roadmap: Collection and Tags CLI

本文档定义 `passagen-cli` 对 collection 和 tag 的管理能力。该功能直接复用 Web
已经使用的 `CatalogService` 和同一份本地数据库，不调用 `passagen-web` HTTP API。

## 范围

Collection 是有序的 paper 集合；Tag 用于对 paper 分类。CLI 支持创建、列出、删除、
重命名以及添加或移除 paper，但不支持 collection 排序、成员 note、批量操作或交互式
编辑。这些扩展能力继续由 Web 提供。

## 标识规则

- Collection 和 tag 创建后由系统生成唯一 ID。
- `list` 同时显示 ID 和名称。
- 除 `create` 接收新名称外，所有修改命令均使用 ID 定位 collection 或 tag。
- Paper 继续使用现有 paper ID。
- 名称仅用于展示，不作为 collection 的唯一键；tag 继续保留现有的规范化名称唯一约束。

采用 ID 可以与 Web 和 `CatalogService` 的现有接口保持一致，也避免 collection 重名时
出现歧义。

## 命令

### Collection

```bash
passagen collection create <collection-name>
passagen collection list
passagen collection delete <collection-id>
passagen collection add <collection-id> <paper-id>
passagen collection remove <collection-id> <paper-id>
passagen collection rename <collection-id> <new-name>
```

行为约定：

- `create` 创建空 collection。
- `list` 显示 ID、名称、paper 数量和 description。
- `add` 将 paper 追加到 collection 末尾；paper 已存在时成功返回，不重复添加。
- `remove` 移除成员并由 Catalog 压紧后续 position，不删除 paper。
- `rename` 只修改名称并保留 description。
- `delete` 删除 collection 及其成员关系，不删除 paper。
- CLI 不提供 collection 重排命令。

### Tag

```bash
passagen tag create <tag-name>
passagen tag list
passagen tag delete <tag-id>
passagen tag add <tag-id> <paper-id>
passagen tag remove <tag-id> <paper-id>
passagen tag rename <tag-id> <new-name>
```

行为约定：

- `create` 创建不带颜色的 tag；颜色仍可在 Web 中编辑。
- `list` 显示 ID、名称和颜色。
- `add` 已存在时成功返回，不创建重复关系。
- `remove` 要求 paper 当前具有该 tag，否则返回 not found。
- `rename` 只修改名称并保留颜色。
- `delete` 删除 tag，并由数据库外键级联清理 paper-tag 关系，不删除 paper。

## 实现

这是一个小功能，直接作为一个增量完成，不拆分多个交付阶段：

- 在 `passagen_cli.commands` 中增加 collection 和 tag Typer 子命令。
- 在 CLI composition root 中注册 `collection` 和 `tag`。
- 命令从 root context 读取 settings，并构造共享 `CatalogService`。
- Collection 操作复用 Catalog 现有方法。
- Catalog 增加原子的单 tag add/remove 方法，避免 CLI 通过读取并替换完整 tag 列表实现。
- CLI 捕获公开的 Catalog 异常，输出稳定错误并以退出码 1 结束。
- 这些本地数据库命令不执行 provider health check。

## 验收条件

- 两组命令的 `--help` 可用，并出现在根命令帮助中。
- 可以通过 CLI 完成 collection 和 tag 的 create/list/rename/add/remove/delete 生命周期。
- list 输出的 ID 可以直接传给其他命令。
- add 是幂等操作，remove 不存在的关系会失败且不修改其他关系。
- rename 保留 collection description 或 tag color。
- 未初始化数据库、未知 collection/tag/paper ID 和名称冲突均返回非零退出码及明确错误。
- Catalog 单元测试和 CLI 集成测试覆盖以上行为。
- README 命令列表与实际命令保持一致。
