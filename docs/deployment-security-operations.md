# 单机部署、最小权限与持续运行

本页描述可执行机制及操作边界。`docs/evidence/optimization-20261026/` 中的记录区分离线检查、隔离组件、日常部署和外部设施，不把配置模板视为已完成生产放行。

## 固定镜像与安全判定

运行镜像的 OS、应用依赖和解释器/JVM 必须分别确认扫描覆盖。包扫描未发现 HIGH/CRITICAL，不代表未被识别的运行时已经修复。本轮 Python 使用签名的3.11.18源码构建；Java 使用签名的Temurin21.0.13+9运行时，另有实际版本、安全回归及 native JVM/CPE 检查。最终放行仍需对应源码的完整 CI 和 Security 成功。

文件存储采用精确批准的 MinIO/Silo OCI 归档，Compose 不自动重建该镜像：

```powershell
python scripts/fetch_approved_image.py
# 离线传递时仍需校验同一个批准归档
python scripts/fetch_approved_image.py --archive <归档路径>
```

此工具只依赖本机 Python 标准库与 Docker。下载来源固定为本仓库 GitHub Release asset，归档内容按 [固定判定清单](../deployment/security/minio-fixed-disposition.json) 校验；私有仓库可从进程环境传入 `GITHUB_TOKEN`，不要写入配置或命令参数。下载失败、内容变化或判定到期均阻断，不回退其他标签。加载成功仅证明内容一致，不能代替完整漏洞扫描。

完整观测栈的 Grafana 使用独立的[精确请求](../deployment/security/grafana-candidate-request.json)、[明确授权](../deployment/security/grafana-candidate-authorization.json)、归档和标签；core 模式不需要加载它：

```powershell
python scripts/fetch_grafana_candidate.py
```

该镜像保留真实 VCS、实际13.2.4应用身份及全部原始告警。当前授权仅覆盖28条精确目标/告警：11条Grafana产品版本范围归因校正，2条Tempo后端仅编入 protobuf 数据结构的范围判定，5条OpenPGP与10条HTML完整生产闭包不适用判定。不能扩大为模块级忽略，也不能给本地源码模块编造版本号。原始Trivy为2CRITICAL/11HIGH/20MEDIUM/2LOW/15UNKNOWN，Grype为2HIGH/3MEDIUM/5UNKNOWN；3条BusyBox中危记录没有上游修复版本，仍保留风险。

8个重建插件后端由维护者的精确编译哈希认可，界面标记为维护者私有信任；原始PGP签名继续保护官方其余资产，并不声称改过的二进制由上游签名。签名、前端、未知文件及未签名插件的实际拒绝测试分别保存。原始报告不会被过滤或改写。

原始扫描保留1个HIGH、0个CRITICAL；仅 CVE-2026-39414 按这一个镜像的源码修复、二进制和兼容性证据判定为 `FIXED`。该授权最晚于2026-11-25上海时间结束，其他高危、未知结果、失败或缺失扫描均阻断。`Dockerfile.minio` 保留作为后续重建配方；重新构建得到的新镜像不能继承本次授权，必须重新审查。禁止用包级忽略、可变标签或改写原始扫描计数替代这个边界。

2026-10-28 新增的 MinIO HTML 告警由[独立技术审查](../deployment/security/minio-html4-technical-review.json)单独处理：两个实际 ELF 的完整生产依赖图、native 模块与函数清单均不包含 `golang.org/x/net/html`。新消费者 `scripts/check_minio_html_exact4.py` 保留原 FIXED 检查及原 OpenPGP exact2 检查的失败报告，只在原始告警恰好对应既有两条 OpenPGP 与新四条 HTML 精确目标记录时应用已审技术事实。当前原始 1 HIGH、6 UNKNOWN 和秘密扫描结果均原样保留；新增、重复、缺失、版本或级别变化及任何 secret 继续阻断。原128个冻结文件、授权、有效期和镜像身份不变。

Grafana授权同样在2026-11-25到期，固定归档SHA为 `849d3d86c9d51c6bda4c78d3091f7d457ba93dbd37e66aefcc87ef57b25cda70`，并绑定其独立索引、平台清单、config、源码和二进制。当前 Compose/CI 标签为 `deepresearch-approved-grafana:20261028`，由加载器验证固定发布后取得。后续显式重建使用独立配方 `deployment/docker/Dockerfile.grafana-security`，新构建必须重新审查。归档加载、隔离端到端验证、日常部署与同SHA最终安全CI分别保留证据。

本次 [Grafana 依赖维护](../deployment/vendors/grafana-security/README.md)已完成新subject明确授权与固定发布，并更新当前配置选择；本地日常服务尚未由本次维护重启。原有配方及旧批准记录保留为历史证据。完整扫描、独立技术审查、授权与运行验证各自记录，材料生成本身不授予批准。

## 自动 CI 与付费质量评测

普通 `CI` 和 `Security` 继续在提交时自动运行。普通 CI 包含免费离线回归、固定资料验证、Java/前端检查及隔离组件和企业栈验证；真实模型评测使用独立的 `Answer Quality (manual, paid)` 工作流，仅在 GitHub Actions 中手动选择该工作流和 `main` 后运行。

手动入口保留原四项评测、评分门槛、运行时校验、同提交 CI/Security 前置条件和跨重跑累计账本。手动触发不会增加获批额度；余额不足时在构建镜像和调用模型前失败，未执行结果不会记为通过。自动检查全绿仅表示当前提交的自动 CI/Security 通过，不能代替真实模型质量验收；历史付费失败记录继续保留。

截至 2026-10-29，提交 `ae65c828c7dd54bb57b3e3c37d7593dd6d518523` 的[付费尝试](https://github.com/W205614/DeepResearch/actions/runs/40725847694)已使用全部获批的 296 次新增调用，生命周期累计 1565 次，余额为 0。答案评测 10/10 通过；研究评测 29/30 通过，最后一题因 `evaluation_budget` 未完成；稳定性和扩展评测未运行。这些结果不构成完整质量验收通过。

## 现有数据升级

先保存当前数据/配置/精确镜像及挂载的可恢复备份；日常容器替换必须在获分配的部署窗口内完成。不要删除卷或用全新数据库替换旧库。

源码已改而日常容器还在旧版本时，先用旧提交及实际本机覆盖冻结原运行拓扑，不能拿新 `bootstrap-db/storage-init` 编排配旧镜像做回滚包：

```powershell
.venv/Scripts/python.exe scripts/freeze_deployment.py --baseline 8768aa6c1e4f0d24a8c355ce769112e98b3b8c10 --output .cache/optimization-20261026/pre-upgrade-deployment
```

该只读工具核对既有服务集合，用实际容器环境、命令、镜像ID替代猜测值，生成展开的私密 `rollback-frozen.private.compose.json`。在维护窗口执行完整冷备时，将这个文件显式传给 `recovery.py backup --path <新备份目录> --compose-file <冻结文件> --source-project deepresearch`，而不是使用已改写的新源码编排。备份工具继续捕获实际匿名卷及 ClamAV 数据，并复制只读 bind 内容。升级成功后再用新拓扑备份，旧、新两份恢复材料分别保留。此处维护工具需要本机 Python 及仓库依赖，或使用已验证的维护运行环境；全新 Docker 部署不应被文档误写成无需任何维护运行环境即可执行这些本机命令。

```powershell
.venv/Scripts/python.exe scripts/prepare_service_credentials.py --env .env
docker compose config --quiet
```

凭据准备工具只补充缺失的独立随机密码，保留已有值，首次变更前创建私密的 `.env.before-service-roles`。两者都不得提交或公开。全新部署可在复制 `.env.example` 后执行同一工具，另行填写原有供应商和基础服务密码。

全新部署不安装本机 Python 时，可以手动填写所有独立密码，或用只运行标准库的容器生成它们：

```powershell
docker run --rm --network none -v "${PWD}:/workspace" -w /workspace python:3.13-alpine python scripts/prepare_service_credentials.py --env .env
```

此命令只准备私密配置，不启动业务或访问供应商；已有部署仍应先冻结旧拓扑再生成新凭据。

`bootstrap-db` 是唯一持有旧 `deepresearch` 管理密码的应用数据库部署作业。它在当前数据库原地创建角色，转移 public 下应用关系的所有权，不碰系统目录、不清空表、不替换卷。`migrate` 随后用非超级用户 `dr_migrator` 执行 Alembic、LangGraph 检查点准备和权限收敛。迁移失败时应用不启动。不要用 `REASSIGN OWNED` 对初始化超级用户做全局所有权重分配。

| 角色 | 数据权限与限制 |
| --- | --- |
| 保留的 `deepresearch` | 管理引导和恢复；不传入 Java/Python/监控容器 |
| `dr_migrator` | 应用 schema 所有者，负责版本化 DDL；非超级用户 |
| `dr_java` | 业务控制表写入；runs 仅更新控制状态、更新时间、送审标记，不能写研究正文；审计只追加 |
| `dr_python` | 执行、资料、检索、预算和检查点写入；不能写成员、额度控制、Outbox 或报告发布状态；只能更新快照时效标记；文档策略只可插入继承默认值 |
| `dr_monitor` | 只读容量查询及指定状态列，不能读取研究主题、正文或成员表 |

`scripts/migrate_services.py` 是新表授权的共同入口；Java/Python 写入均用显式表白名单，新权限表不会自动变成执行数据。Python 对会话只可执行清理删除，对修订作业只可更新执行计划和时间，事件不可覆盖更新。运行账号均无 CREATE、CREATEROLE、CREATEDB、SUPERUSER 或 BYPASSRLS。本项目未宣称用 RLS 实现数据库级租户隔离，用户/空间/集合边界仍由业务访问层执行。

生产 Python 配置 `CHECKPOINT_SCHEMA_MANAGED=true`；运行时不执行检查点 DDL。离线 SQLite/开发夹具保留显式开发初始化。

## 文件存储、令牌与网络

`storage-init` 用官方 MinIO SDK 的受限单次程序准备两个身份，不向应用交付 root 凭据：

- `dr-objects`：仅 `deepresearch-documents` 的 `uploads/`、`quarantine/` 对象读写及必要 bucket 查询。
- `dr-milvus`：仅现有默认 `a-bucket` 的对象/分段上传操作。现有 Milvus bucket/prefix 若曾自定义，先核实实际配置并更新策略，不能迁移到空 bucket 冒充数据保留。

两者都不能访问对方的 bucket。MinIO 可能将 ListBuckets 返回为按权限过滤的清单，而不是直接拒绝；验证应检查清单不包含无关 bucket，并实际请求无关对象。

告警令牌和内部业务调度令牌分别保存在两个卷；新版告警使用 `alert-relay-token-v2`，不会重新挂载含旧混合令牌的历史卷。旧混合卷仅为回滚保留，新业务令牌在独立卷生成。Alertmanager/告警转发组件不挂载业务令牌，两个 Worker 也不需要调度令牌。业务、数据库、索引存储、身份持久化、入口和遥测采用必要网络关系；没有挂 Docker socket。网络区分不等同流量加密，远程部署仍须由受控 TLS 入口、身份服务 hostname/redirect URI 和访问策略承接。

## 配置档与资源边界

```powershell
# 完整单机环境，含可观测组件
docker compose -f compose.yaml -f compose.production.yaml config --quiet
# 本地开发源代码挂载，保留 OIDC、扫描、额度和服务角色
docker compose -f compose.yaml -f deployment/compose/compose.development.yaml config --quiet
# 显式不运行可选的完整观测栈
docker compose -f compose.yaml -f deployment/compose/compose.core.yaml config --quiet
```

发布时选择相应配置并在授权维护窗口启动。`deployment/compose/compose.core.yaml` 是有意关闭外部遥测的资源受限模式，不应将该模式写成完整生产监控已启用。完整部署直接使用基本编排；生产覆盖为业务进程增加只读根文件系统、必要 tmpfs 和能力收敛。

所有服务有 CPU/内存/PID 上限和 Docker 日志轮转。默认值是单机试用起点，不能当作容量测量结果。Prometheus 保留最长15天且最多2GB；日志保留7天、追踪保留3天；这些观测数据与业务正式证据的保留规则分开。历史匿名 Prometheus 卷仍需在重建前核对并按恢复清单保留，不因没有业务引用删除。

## 备份、异机目标与恢复目标

现有 `scripts/recovery.py` 继续负责完整冷备和独立项目恢复，备份包含私密数据库、配置、认证材料及镜像。新增调度入口复用该机制，不用只导出一个表的“备份”替代它。

1. 将 `examples/backup-schedule.example.json` 复制到被忽略的私密维护位置，核对备份目录、实际 Compose 覆盖文件、磁盘路径和维护时段。
2. 只有明确启用 `enabled` 和 `maintenance_window_acknowledged` 才允许调度执行。冷备会短暂停止当前服务，检测到活跃研究/索引时拒绝执行；不会为赶上计划强制清空任务。
3. 指定 `remote_target` 时，管理员必须确认它确实位于另一主机，且已落实访问控制和静态加密。复制对整个版本3嵌套清单逐文件核对，不覆盖已有目标。工具无法仅凭盘符证明异机或验证底层加密，这些保留为外部证据。
4. 在已选定维护时段安装任务：

```powershell
./scripts/install_backup_task.ps1 -Configuration <私密配置路径> -DailyAt 03:30
```

安装不会立即执行备份，也不保存 Windows 密码；Docker Desktop 必须在该用户会话中可用。默认维护窗口是上海时间03:15–04:30；机器未开机时没有成功记录，补运行如果已超出维护窗口会拒绝冷备并记录原因，不会在白天突然停服务。安装器要求本机UTC+08，其他时区主机必须先明确换算并核对原生触发器。任务使用互斥锁防止重叠，结果写 `scheduler-status.json`；活跃任务、异常、跳过和过期都不会推进完整备份校验时点。备份不会自动删除。历史恢复包需要按明确清单和回滚需求人工审核保留。

Windows 任务动作强制使用本工作区的 `pythonw.exe`，缺少时拒绝安装，不能回退为可弹出控制台的任务入口。两个任务显式使用当前用户的 `Interactive` / `Limited` principal，不保存密码、不提权；只在该用户登录且 Docker Desktop 可用时执行。无控制台入口通过 `host_task.py` 给实际操作提供隐藏子进程及有效日志句柄；恢复工具全部 Windows 子进程使用 `CREATE_NO_WINDOW` 和隐藏启动标记，不额外启动 PowerShell/cmd。原始任务退出码继续传给计划程序，失败恢复的 `finally` 不变。

备份和探针的 stdout/stderr 以 UTF-8 追加保存到被 Git 忽略的 `.cache/ops/logs/backup.log` 与 `probe.log`，带开始/完成和退出码。可能含凭据的 Docker 原始结果仍按原规则隐藏，失败日志只增加执行程序名称和子进程退出码。日志不自动删除；按当前用户权限保护并人工审核保留。没有可写日志路径时不能产生伪成功。入口静态检查和小型子进程验证不代表已安装任务、已验证桌面调度或已完成日常备份。

核对和撤销计划（撤销只移除计划，不删除备份或私密配置）：

```powershell
Get-ScheduledTaskInfo -TaskName DeepResearch-Verified-Cold-Backup
Get-ScheduledTaskInfo -TaskName DeepResearch-Host-Probe
Unregister-ScheduledTask -TaskName DeepResearch-Verified-Cold-Backup -Confirm:$false
Unregister-ScheduledTask -TaskName DeepResearch-Host-Probe -Confirm:$false
```

证据必须分别记录安装前后任务定义、下次运行时间、`LastTaskResult`、最近成功备份清单和主机探针时点。工具存在不表示已安装、已成功执行或已送达异机；本轮安装和首次完整备份须由日常部署窗口阶段单独完成。

默认 RPO 目标24小时、RTO目标15分钟，均可配置；目标不是承诺。备份耗时不能写成恢复耗时，同机隔离演练也不能写成异机恢复。应记录备份时点、完整恢复耗时、业务校验和实际数据缺口。自动化、机外路径、实际接收人、异机恢复以及 SMTP/MFA 配置须各自验收，缺失项不能用脚本存在代替。

## 容量、备份新鲜度与受控告警

`ops-exporter` 使用列级只读监控角色读取数据库体积、连接占用和资料积压；不读 Docker socket、用户正文或私密备份目录。宿主机通过下列命令生成脱敏容量与完整备份校验时点：

```powershell
.venv/Scripts/pythonw.exe scripts/ops_probe.py --backup-root .cache/backup --disk workspace=E:/project/DeepResearch --disk docker_storage=E:/Docker
```

上述备份安装器同时准备 `DeepResearch-Host-Probe`，默认每5分钟运行一次只读容量采样。快速采样保留最近一次完整备份校验时点，不重新散列所有镜像归档，也不把当前采样时间当成新的备份成功；从未完整校验时保留0并触发新鲜度告警。每日冷备完成或上面的完整探针命令才逐文件校验恢复包。两种写入使用同一状态锁，避免快速采样覆盖刚完成的备份结果。安装器会拒绝覆盖执行入口不属于本仓库的同名任务。

单次只读快速采样：

```powershell
.venv/Scripts/pythonw.exe scripts/ops_probe.py --config <私密配置路径> --capacity-only
```

手工启动 `pythonw` 时不向终端打印结果；应核对对应日志的 `host_task_finished.returncode` 和状态文件时点。终端重新出现提示符不能代替操作完成或成功证明。

路径必须按当前主机核对；容器内 `/data` 的剩余空间不能替代宿主机、Docker VHDX 和数据库容量。探针缺失/过期、备份超出RPO、数据库连接占用、容量和文档积压均有告警规则。监控阈值需要随实际容量和业务时段校准。

告警转发对重复状态串行去重，持久化安全指纹/状态，重启后不清空去重历史；不持久化 webhook 地址或原始告警载荷。先用 `deployment/compose/compose.test.yaml` 的受控接收端验证触发、重复抑制与恢复通知，再由管理员配置真实接收端。网络不确定性仍可能产生重复外部通知，不宣称 Exactly Once。未配置 webhook 时返回未启用，不伪装送达成功。

账号恢复需要真实 SMTP 的可达性、发信授权和投递验收；MFA 需要在目标 Keycloak realm 配置并用独立测试账号验证新注册、已有账号、恢复码与会话失效。未进行这些真实外部设施配置时保留 `blocked_external`。

只读核查入口（管理密码从进程环境读取，不作为命令参数）：

```powershell
.venv/Scripts/python.exe scripts/check_identity_readiness.py --base-url <实际身份入口> --realm deepresearch --output <私密或脱敏核查文件>
```

该工具仅请求管理员令牌并读取 realm/required-action 配置，不发邮件、不注册 OTP、不更新用户。`resetPasswordAllowed=true` 只表示开放重置入口，不能代替 SMTP 投递；支持 `CONFIGURE_TOTP` 不表示所有已有用户已强制启用 MFA。

防爆破升级先在隔离 realm 验证，再在维护窗口进入 Keycloak 的 Realm settings → Security defenses → Brute force detection：启用保护、使用临时锁定，建议从5次失败、60秒递增等待、最大900秒开始，结合实际用户行为调整。升级前记录这些设置的旧值；不要删除用户、重导整个 realm 或清除全部会话来达到配置效果。已有部署的 realm import 不会自动更新既存 realm，必须通过受控管理配置并重新读取核对；本轮未静默修改日常 realm，也未声称已实测公网身份防护。

## 观测数据卷的维护窗口迁移

新版显式挂载 `prometheus-data:/prometheus` 与 `alertmanager-data:/alertmanager`，均以 UID/GID65534 使用数据。Alertmanager 明确设置 `--storage.path=/alertmanager`，关闭不需要的集群监听。2026-10-26 的只读核查发现，旧日常这两个目录均来自匿名卷，不能把新命名卷默认为空，也不能删除旧匿名卷。

先冻结旧部署并用冻结配置完成冷备。下列检查可以在运行中执行，不写源数据：

```powershell
python scripts/migrate_monitoring_storage.py inspect --container deepresearch-prometheus-1 --project deepresearch --service prometheus
python scripts/migrate_monitoring_storage.py inspect --container deepresearch-alertmanager-1 --project deepresearch --service alertmanager
```

以下命令只在已经授权的窗口、相关旧容器已停止且完整冷备验证完成之后执行，必须早于新 Compose 创建目标卷。工具本身不会停止、启动、删除或替换源容器。目录示例须改成此次窗口的新目录；已经存在的目录或目标卷会拒绝覆盖。

```powershell
python scripts/migrate_monitoring_storage.py snapshot --container deepresearch-prometheus-1 --project deepresearch --service prometheus --directory .cache/upgrade-monitoring/prometheus
python scripts/migrate_monitoring_storage.py apply --container deepresearch-prometheus-1 --project deepresearch --service prometheus --directory .cache/upgrade-monitoring/prometheus --target-volume deepresearch_prometheus-data --retain-temporary-resources
python scripts/migrate_monitoring_storage.py snapshot --container deepresearch-alertmanager-1 --project deepresearch --service alertmanager --directory .cache/upgrade-monitoring/alertmanager
python scripts/migrate_monitoring_storage.py apply --container deepresearch-alertmanager-1 --project deepresearch --service alertmanager --directory .cache/upgrade-monitoring/alertmanager --target-volume deepresearch_alertmanager-data --retain-temporary-resources
```

工具要求精确 Compose project/service/config-hash 身份、固定源路径和停止状态；有其他运行写入者的源卷会被拒绝。它通过只读 `docker cp` 流保存原数据，核对安全归档路径和逐文件 SHA256/大小/UID/GID/模式，apply 前重新读取源防止旧快照覆盖后来变化。目标只能是该 project 的对应新命名卷。所有拷贝与核验均不连接外网，归档保留在忽略的私密 `.cache`。根目录设为65534:65534/0750，文件保留原权限与所有者；核验失败保留双方数据并停止，不自动重试覆盖。

本次不含清理许可的窗口，apply 必须带 `--retain-temporary-resources`：仅复制和核验两个新 helper 取消 `--rm`，分配唯一名称以及 project/operation/source/invocation 标签；CID、创建命令、实际镜像/创建时间/状态/挂载身份写入私密目录的 `migration-receipt.json`，失败时也保留已创建资源的记录。标签只辅助发现，不能单独作为日后删除依据；helper 与源容器/卷均等待后续独立清理审查。省略该参数仍沿用原 helper 自动移除行为，inspect/snapshot 不受影响。

迁移凭据、旧匿名卷名、原容器和冻结旧配置必须保留到窗口验收与回滚责任人明确放行；后续重建旧容器的替换安排由窗口统一执行。Prometheus 查询历史、Alertmanager silence状态、Grafana dashboard/datasource和只读角色分别核对，不能只用“容器健康”代替数据完整性。两个新命名卷也必须纳入新版冷备。已完成的隔离 synthetic fixture覆盖匿名卷和容器可写层、运行源拒绝、错误 project拒绝、文件一致性和65534读写；这不代表日常迁移已经执行。

## 回滚

先恢复与旧镜像相容的运行配置和已记录挂载，保留新增数据库字段与权限元数据；不要自动执行破坏性 schema downgrade。旧版本如仍依赖旧超级用户，应只在受控回滚窗口使用原私密配置，不将它作为新版常态。完整恢复只进入新建的 `dr-restore-*` 项目和新卷，不覆盖当前数据。
