# 本地部署、最小权限与维护

本页说明本地Compose部署、升级、分权、备份与恢复。外部服务和多副本接入见[云平台手册](cloud-integration.md)，历次执行结果见[证据索引](evidence/production-20261107/README.md)。

## 固定镜像与安全判定

当前代码基线 `b8153f5` 的[CI](https://github.com/W205614/DeepResearch/actions/runs/42370035398)、[Security 22个作业](https://github.com/W205614/DeepResearch/actions/runs/42370035403)及[告警规则检查](https://github.com/W205614/DeepResearch/actions/runs/42370035381)均通过。运行镜像分别验证OS、应用依赖、解释器／JVM和原生组件。

Python采用签名CPython 3.11.18源码构建与锁定的SQLite、zlib运行库；Java采用经签名与实际文件核对的Temurin 21.0.13+9；前端构建使用固定摘要的Node.js 22.23.4。运行时信任数据、JVM和Milvus的维护说明分别见[运行层打包](runtime-trust-packaging.md)、[JVM门禁](native-jvm-security-gate.md)与[Milvus打包](milvus-image-packaging.md)。

新安装先加载精确批准的归档：

```powershell
python scripts/fetch_approved_image.py
python scripts/fetch_grafana_fixed_20261107.py
```

MinIO保留其独立请求与发布；Grafana当前标签为 `deepresearch-approved-grafana:20261107`，完整归档有14个分片，绑定14个ELF和13个重编插件。请求、授权、摘要、加载与更新步骤见[镜像归档手册](approved-image-artifacts.md)。core配置关闭可选观测栈，无需加载Grafana。

安全判定保留完整原始扫描、独立审查与逐项结论。现有精确授权按制品及绑定输入生效；镜像、源码、配方、扫描条目或严重级别变化后重新复核。历史日历修订和旧制品材料留在原路径，当前使用的标签由Compose及运行清单明确选择。

## 自动 CI 与付费质量评测

普通 `CI` 和 `Security` 继续在提交时自动运行。普通 CI 包含免费离线回归、固定资料验证、Java/前端检查及隔离组件和企业栈验证；真实模型评测使用独立的 `Answer Quality (manual, paid)` 工作流，仅在 GitHub Actions 中手动选择该工作流和 `main` 后运行。

手动入口保留原四项评测、评分门槛、运行时校验、同提交 CI/Security 前置条件和跨重跑累计账本。手动触发不会增加获批额度；余额不足时在构建镜像和调用模型前失败，未执行结果不会记为通过。自动检查与真实模型评测分别记录；历史付费结果保留原供应商、用例和调用账本。

截至 2026-10-29，提交 `ae65c828c7dd54bb57b3e3c37d7593dd6d518523` 的[付费尝试](https://github.com/W205614/DeepResearch/actions/runs/40725847694)已使用全部获批的 296 次新增调用，生命周期累计 1565 次，余额为 0。答案评测 10/10 通过；研究评测 29/30 通过，最后一题因 `evaluation_budget` 未完成；稳定性和扩展评测未运行。该次完整质量验收未完成。

`RAG Quality (manual)` 也是独立手动入口，默认 `prepare_only=true`：先验证固定制品的祖先源码绑定，加载批准的 MinIO 归档，构建评测镜像，在禁网容器中检查两个评测命令及输出写入权限，再构建并启动隔离的 etcd/MinIO/Milvus，结束后清理该临时项目。此模式不读取供应商密钥，不调用模型、嵌入或重排服务；通过只证明准备流程可用。容器使用 runner 的 UID/GID 写入挂载的评测目录，避免运行镜像用户无权写入结果。

只有已有明确的供应商调用与费用授权时才可取消 `prepare_only`，执行原有检索及问答门槛。该入口不会增加 Answer Quality 的获批额度，也不能以准备流程通过代替真实 RAG 质量结果。本次入口与文档修正仅验证准备模式，不执行付费评测。

2026-10-31，入口修正提交的首次 Security 扫描发现 `pypdf 6.21.2` 新增 7 条 HIGH（CVE-2026-119203 至 CVE-2026-119209）。依照上游 [6.21.3](https://github.com/py-pdf/pypdf/releases/tag/6.21.3) 和 [6.21.4](https://github.com/py-pdf/pypdf/releases/tag/6.21.4) 的修复记录，将最低版本与锁文件更新到 6.21.4；这些告警不继承任何旧制品豁免，最终源码及应用镜像仍须通过新提交的 Security。RAG 工作流的文件指纹同时按原有制品选择与安全门槛重新核对，不改变 Grafana/MinIO 授权、原始报告或高危阻断条件。依赖更新后的静态预算下界证明单独复核：研究及评分调用路径、用例、门槛和其他依赖均不变，只更新依赖指纹；不增加调用额度，不修改累计账本，也不产生新的付费质量结果。

## 现有数据升级

2026-10-29，用户明确停用本机自动备份并清理包括最新恢复集在内的完整备份、离线镜像包与可再生构建缓存。当时的完整恢复集已退役，恢复报告保留原验证时点。实际 Docker 镜像、容器、数据卷、业务文件、配置及必要来源记录保留。回收站容量不足的剩余文件只作待处理材料，不作为当前有效恢复集。

本机私密备份配置已禁用，`DeepResearch-Verified-Cold-Backup` 定时任务已关闭，当前有效备份时间清零。只采集容量的 `DeepResearch-Host-Probe` 保留；`DeepResearchBackupStale` 规则按这项停用策略设为返回空结果的 `vector(0) > 0`，其他监测规则不变。若以后重新启用备份，需显式启用私密配置和定时任务、先建立并验证新的恢复集，再恢复该规则注释中保留的原表达式。以下手动升级备份按维护窗口执行；自动调度继续遵循该停用策略。

先保存当前数据/配置/精确镜像及挂载的可恢复备份；日常容器替换必须在获分配的部署窗口内完成。不要删除卷或用全新数据库替换旧库。

源码已改而日常容器还在旧版本时，先用旧提交及实际本机覆盖冻结原运行拓扑，不能拿新 `bootstrap-db/storage-init` 编排配旧镜像做回滚包：

```powershell
$RunningCommit = 'REPLACE_WITH_RUNNING_COMMIT_SHA'
.venv/Scripts/python.exe scripts/freeze_deployment.py --baseline $RunningCommit --output .cache/pre-upgrade-deployment
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

`scripts/migrate_services.py` 是新表授权的共同入口；Java/Python 写入均用显式表白名单，新权限表不会自动变成执行数据。Python 对会话只可执行清理删除，对修订作业只可更新执行计划和时间，事件不可覆盖更新。运行账号均无 CREATE、CREATEROLE、CREATEDB、SUPERUSER 或 BYPASSRLS。用户、空间和集合边界由业务访问层执行，数据库角色限制各服务的数据写入范围。

生产 Python 配置 `CHECKPOINT_SCHEMA_MANAGED=true`；运行时不执行检查点 DDL。离线 SQLite/开发夹具保留显式开发初始化。

## 文件存储、令牌与网络

`storage-init` 用官方 MinIO SDK 的受限单次程序准备两个身份，不向应用交付 root 凭据：

- `dr-objects`：仅 `deepresearch-documents` 的 `uploads/`、`quarantine/` 对象读写及必要 bucket 查询。
- `dr-milvus`：仅现有默认 `a-bucket` 的对象/分段上传操作。现有 Milvus bucket/prefix 若曾自定义，先核实实际配置并更新策略，不能迁移到空 bucket 冒充数据保留。

两者都不能访问对方的 bucket。MinIO 可能将 ListBuckets 返回为按权限过滤的清单，而不是直接拒绝；验证应检查清单不包含无关 bucket，并实际请求无关对象。

告警令牌和内部业务调度令牌分别保存在两个卷；新版告警使用 `alert-relay-token-v2`，不会重新挂载含旧混合令牌的历史卷。旧混合卷仅为回滚保留，新业务令牌在独立卷生成。Alertmanager/告警转发组件不挂载业务令牌，两个 Worker 也不需要调度令牌。业务、数据库、索引存储、身份持久化、入口和遥测采用必要网络关系；没有挂 Docker socket。远程部署通过TLS入口、身份服务hostname／redirect URI和访问策略保护网络连接。

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

所有服务有 CPU/内存/PID 上限和 Docker 日志轮转。默认资源值用于本地运行，容量实测见[本地验收](evidence/production-20261107/README.md)。Prometheus 保留最长15天且最多2GB；日志保留7天、追踪保留3天；这些观测数据与业务正式证据的保留规则分开。历史匿名 Prometheus 卷仍需在重建前核对并按恢复清单保留，不因没有业务引用删除。

## 备份、异机目标与恢复目标

### 维护者本机的备份记录

自动备份按本机策略保持停用，`DeepResearch-Verified-Cold-Backup` 计划任务及私密配置禁用；容量探测继续保留。手动备份与隔离恢复按需运行，恢复点以实际生成并验证的清单为准。

2026-10-28 结构整理曾保留 `.cache/backup/scheduled/scheduled-20261027-042116/` 完整快照及数据库逻辑备份，并校验当时的清单和镜像内容。该记录已被 2026-10-29 的备份退役决定取代，不能继续将这些路径、旧恢复报告或回收站内容当作当前可用恢复点。历史整理记录见本机 `.cache/structure-cleanup-20261028/`，退役状态记录在 `.cache/backup/RECOVERY-DISABLED.json`。

私密配置、独有计费账本、运行数据及历史验收证据仍按用途保留，不能因备份停用就整目录清理 `.cache/`。

### 配置调度

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

默认RPO目标24小时、RTO目标15分钟，可按部署调整。每次演练记录备份时点、完整恢复耗时、业务校验和实际数据缺口；机外路径、通知收件人、异机恢复及SMTP／MFA分别验证。

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
