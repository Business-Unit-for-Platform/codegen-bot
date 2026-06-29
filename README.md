# future-codegen-bot

`future-codegen-bot` 是 RuoYi/Yudao 平台化流水线中的业务模块代码生成器。

核心流程：

```text
业务 SQL / 数据模型
  -> 调用 Yudao 内置 codegen
  -> 生成后端模块 + Vue3 管理端页面
  -> 同步到目标后端/前端仓库
  -> 通过 PR 或重建模式进入目标仓库
  -> Agent / 人审查后合并
```

它和 `Clone-ruoyi-vue-pro-Bot` 的关系：

```text
Clone-ruoyi-vue-pro-Bot：先造房子 / 平台底座
codegen-bot：再按业务数据模型生成房间 / 业务模块
```

平台策略事实源见：

```text
PeterKZhao/ai-project-operating-system/docs/architecture/ruoyi-yudao-platform-strategy.md
```

## 多模块生成

`tools/codegen/run.sh` 支持两种模式。

### 单模块模式

```bash
CODEGEN_MODULE_NAME=ticket \
CODEGEN_TABLE_PREFIX=ticket_ \
bash tools/codegen/run.sh
```

### 多模块模式

通过 `CODEGEN_MODULES` 一次生成多个模块：

```bash
CODEGEN_MODULES="ticket:ticket_,map:map_,ugc:ugc_,tourism:tourism_" \
bash tools/codegen/run.sh
```

格式说明：

- `module:prefix_`：推荐写法，例如 `ticket:ticket_`
- `module=prefix_`：兼容写法，例如 `ticket=ticket_`
- `module`：简写，自动使用 `module_` 作为表前缀

脚本会：

1. 重建数据库并导入 RuoYi/Yudao 基础 SQL。
2. 导入 `sql/schema/*.sql` 下的全部业务 SQL。
3. 按模块前缀逐个运行 Yudao 代码生成。
4. 将所有模块输出到同一个 `out/generated` 目录。
5. 最后统一生成 app controller，并由发布脚本同步到目标仓库。

如果没有设置 `CODEGEN_MODULES`、`CODEGEN_MODULE_NAME`、`CODEGEN_TABLE_PREFIX`，脚本会根据新增业务表的第一个下划线前缀自动推断模块。例如：

- `ticket_product` -> `ticket:ticket_`
- `map_poi` -> `map:map_`
- `ugc_post` -> `ugc:ugc_`

## 发布模式

`tools/codegen/publish.sh` 支持两个发布模式。

### update_existing_repo_with_pr

默认模式。

适用于长期业务仓库：

```bash
PUBLISH_MODE=update_existing_repo_with_pr \
GITHUB_OWNER=Business-Unit-for-Gaokao \
BACKEND_REPO=gaokao-admin-backend \
FRONTEND_REPO=gaokao-admin-frontend \
BACKEND_BRANCH=main \
FRONTEND_BRANCH=main \
bash tools/codegen/publish.sh
```

行为：

- 不删除目标仓库。
- 目标仓库不存在时直接失败。
- 从目标仓库 base branch 创建 `codegen/*` 分支。
- 同步生成代码。
- 写入 `generated/manifest.json` 和 `generated/codegen-report.md`。
- push 分支并尝试创建 PR。

### rebuild_from_upstream

适用于一次性生成仓库、演示仓库、或需要从最新上游重新构建的底座仓库：

```bash
PUBLISH_MODE=rebuild_from_upstream \
GITHUB_OWNER=FutureTechQuant \
BACKEND_REPO=ruoyi-vue-pro \
FRONTEND_REPO=yudao-ui-admin-vue3 \
bash tools/codegen/publish.sh
```

行为：

- 删除并重建目标仓库。
- 从 Gitee 上游拉取最新工作区快照。
- 同步生成代码。
- 直接推送到目标分支。

注意：该模式是显式重建模式，只用于可重建目标，不应用于长期业务仓库。

## GitHub Actions 输入

手动触发 `.github/workflows/codegen.yml` 时可设置：

- `target_owner`
- `backend_repo`
- `frontend_repo`
- `backend_branch`
- `frontend_branch`
- `publish_mode`
- `target_private`
- `codegen_engine_repo`
- `codegen_engine_ref`
- `codegen_modules`
- `codegen_module_name`
- `codegen_table_prefix`

## api/biz 拆分配置

默认配置文件：

```text
tools/codegen/split_api_biz.yml
```

默认启用拆分：

```yaml
split_api_biz:
  enabled: true
  default: true
  default_api_packages:
    - api
    - enums
  default_biz_packages:
    - controller
    - service
    - dal
    - convert
    - job
    - listener
    - framework
  modules:
    asset:
      enabled: true
      api_packages:
        - api
        - enums
      biz_packages:
        - controller
        - service
        - dal
        - convert
        - job
        - listener
```

拆分规则：

- 生成的 `yudao-module-<module>` 会被拆成：
  - `yudao-module-<module>-api`
  - `yudao-module-<module>-biz`
- Java 包路径中位于 `module/<module>/api`、`module/<module>/enums` 下的文件进入 `api` 模块。
- 其他文件默认进入 `biz` 模块。
- `biz` 模块自动依赖对应 `api` 模块。
- `yudao-server/pom.xml` 只依赖 `biz` 模块。
- 根 `pom.xml` 会加入 `api` 和 `biz` 两个模块。
- manifest/report 会记录拆分结果、规则和文件数量。

如果某个模块暂时不拆，可以配置：

```yaml
split_api_biz:
  modules:
    asset:
      enabled: false
```

## 生成审查要求

生成后必须人工或 Agent review：

- app/user controller 是否误暴露 admin 写接口。
- 租户、用户、数据权限边界是否明确。
- 后端模块 POM 和聚合 POM 是否正确。
- 前端页面、路由、菜单、权限是否需要人工配置。
- `generated/manifest.json` 和 `generated/codegen-report.md` 是否准确。
- 目标仓库 CI/build 是否通过。

## 当前限制

- 当前仍主要输出上游 Yudao 结构：`yudao-module-<module>`、`src/api/<module>`、`src/views/<module>`。
- 还未实现 `future-layout`：`modules/custom/<module>/future-module-<module>-api` + `future-module-<module>-biz`。
- app controller 自动生成只能作为起点，不能直接视为可上线接口。
