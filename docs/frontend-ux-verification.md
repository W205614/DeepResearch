# 前端视觉与体验验证（2026-10-16）

## 范围

仅调整 Vue 模板中的排版、文案和可访问性标记、CSS、字体及页面元信息。`App.vue` 的 `<script setup>` 与修改前逐字一致；API、业务规则、角色、资料范围策略、模型配置、数据库及后端依赖未改。

README 参考旅行规划项目的功能、界面、技术栈、启动与边界组织方式；旧交付记录保存至 [delivery-history.md](delivery-history.md)。截图为本轮本机 Docker 部署的真实浏览器画面，使用临时账号与确定性测试资料。它们证明页面展示和对应流程，不证明模型研究准确率。

## 设计与视觉检查

使用 `design-taste-frontend` 抽取 `type-only-statement`（Type and nothing else），seed `630787033`，没有重抽。石墨色导航与浅色文档区承载问题、报告和边栏证据，橙色只用于主要操作和少量交互强调；Bricolage Grotesque Latin 子集在本地托管，中文使用系统字体。保留字体 OFL 许可证。

该 skill 明确不面向仪表盘和应用工作台，因此采用其排版、配色、去模板化和实图复核方法，按本任务的“前端优化、不改功能”约束作适配：不添加四张装饰图片、视频或八个入场动画；保留真实表格和表单边界，只有实际来源抽屉使用投影。

- 初始源码检查：13 FAIL、5 WARN；登录页渲染检查：6 FAIL、4 WARN。
- 最终源码检查：0 FAIL、0 WARN。
- 最终部署登录页：1440 / 768 / 390px，显式放行 `few-pictures,still-page` 后 0 FAIL、1 WARN。剩余 `no-footage` 提示是上述工作台范围适配，不引入无助于任务的视频。此结果不代表无例外通过展示页全部规则。
- 逐张复核登录、工作台、资料库、研究报告和正式审核截图；三种宽度的报告、来源抽屉、输入和工具页无页面横向溢出。
- 设计自评（非第三方测评）：设计 7.5、可用性 8、创意 7、内容 8，加权 7.6/10。报告审核仍保留较长的身份与审计字段，长报告阅读密度还取决于实际内容；未声称画廊级评分或用户调研结果。

## 本地执行结果

| 检查 | 结果与边界 |
| --- | --- |
| Vitest | 12 项通过 |
| TypeScript / Vite | `npm run build` 通过 |
| 容器浏览器验收 | 11 项通过、3 项按计划跳过；无重试 |
| 真实 OIDC | 临时账号 PKCE 登录、工作台、设置与 ZIP 导出通过 |
| 任务与隔离 | Java 会话、偏好、幂等、取消与 SSE 生命周期；资料范围及失败/部分结果提示通过 |
| 文档真实适配器 | 开启 `E2E_DOCUMENTS=1`；上传、扫描/索引、检索、替换、删除及查看者授权通过，使用真实嵌入 |
| 报告审核 | 真实双账号提交、批准、冻结内容、原件版本变化、撤回与成员移除授权通过；另有独立前端替身流程 |
| 新增体验回归 | 三尺寸无横向溢出；示例只填入问题；Shift+Enter 不提交；空输入禁用发送；资料范围切换；来源抽屉；偏好保存、刷新读取及删除 |
| 键盘与动态效果 | 登录按钮键盘可达；收起的手机侧栏不进入可见性/焦点树；按钮触控高度至少 44px；支持减少动态效果 |
| 企业冒烟 | Web、Java、Python、OIDC、迁移、Redis、两 Worker、Prometheus、Grafana、Tempo、Loki 和 OTel 通过 |
| 部署核验 | 核心服务 ready、匿名权限拒绝、四个抓取目标 up、12 条告警规则、迁移 `0012_report_publication` 通过 |

3 条额外付费公开研究用例未在本地开启；文档测试会执行受限的真实模型/嵌入调用，不把本轮当作全量回答质量评测。GitHub 的 Answer Quality Gate 独立执行，状态以对应提交的工作流为准。

第一轮浏览器检查有两项新测试失败：隐藏侧栏按钮默认不进入角色查询，以及刷新后导航等待早于登录状态恢复。修正测试定位和等待条件后，在最终 Web 容器上完整重跑得到上表结果，没有放宽业务断言。

## 复现

```powershell
cd frontend
npm ci
npm test
npm run build
$env:E2E_BASE_URL='http://localhost:8380'
$env:E2E_KEYCLOAK_BASE_URL='http://localhost:8480'
$env:E2E_DOCUMENTS='1'
$env:E2E_SCREENSHOT_DIR='E:\project\DeepResearch\.cache\ui-review\screenshots'
npm run test:e2e:enterprise
```

端口按本机部署调整；默认 Compose 使用 Web 8080、Keycloak 8180。企业浏览器运行器读取本地 Keycloak 管理凭据，创建并清理临时账号与测试数据，不在报告中输出密码。`E2E_SCREENSHOT_DIR` 可省略，截图无内容修改，README 选用其中六张。

## 容器与清理

仅重建并替换 `web`：`docker compose -f compose.yaml -f .env.local-ports.compose.yaml up -d --no-deps --wait web`。其余 20 个项目容器的 ID、镜像及挂载与操作前一致；数据卷保留。

旧 Web 镜像先导出至忽略目录 `.cache/ui-review/web-before.tar` 并检查归档清单和 SHA-256；确认无容器引用后删除旧镜像标签及其不再引用的内容。成功退出的迁移和凭据初始化容器属于现行 Compose 拓扑，保留。未执行全局 prune，也未清理其他项目资源。构建过程中的第一次 npm 网络重置由原有重试机制恢复，最终构建通过。

本地详细日志、原始检查报告和回滚资料保存在 `.cache/ui-review/`；其中可能包含本机运行信息，不提交 GitHub。GitHub CI、Answer Quality Gate 与 Security 的最终状态须核对本次提交 SHA。
