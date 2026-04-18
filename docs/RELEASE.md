# ruisheng-shared 发布流程

## 两个独立版本字段（不要混淆）

| 字段 | 类型 | 例 | 用途 | Bump 时机 |
|---|---|---|---|---|
| `__version__` / pyproject `version` | str | `"0.1.0"` | 包 API semver，CHANGELOG 追踪，tag `shared-v<ver>` | 任何 release（major/minor/patch） |
| `SHARED_SCHEMA_VERSION` | int | `20260415` | DB schema 兼容，api/gw 启动校验 | **仅 breaking schema change**（字段删/改类型/必填新增） |

## 发布新版本

1. 在 `ruisheng-shared/pyproject.toml` 更新 `version`（遵 semver）
2. 同步更新 `src/ruisheng_shared/__init__.py` 的 `__version__`（与 pyproject 一致）
3. **若** 本次含 breaking schema 变更，同步 bump `SHARED_SCHEMA_VERSION`（整数日期）
4. 在 `ruisheng-shared/CHANGELOG.md` 把 `[Unreleased]` 下的条目搬到新版本下
5. Commit：`chore(release): ruisheng-shared vX.Y.Z`
6. 打 tag：`git tag -a shared-vX.Y.Z -m "..."`
7. Push：`git push && git push --tags`
8. GitHub Actions 自动创建 Release（CHANGELOG 对应段作为 body）

## SemVer 规则（`__version__`）

- **major**：shared 的 schema 接口（enum 值、常量、错误码、pydantic model 字段）有 **breaking** 改动
- **minor**：新增枚举值 / 新增 schema 字段（向后兼容）
- **patch**：文档、测试、内部实现改动
