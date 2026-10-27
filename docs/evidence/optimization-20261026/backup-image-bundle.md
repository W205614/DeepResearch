# 恢复镜像包与缓存证据边界

原 `load_archive` 只核对 `images.tar` 整文件摘要，原 `restore` 在 Docker 已存在所有镜像时跳过 `docker load`。所以此前同机隔离恢复成功，不能单独证明镜像包在空缓存可导入。`docker save` 非零退出本来就会阻止完成 manifest；但导出命令成功和整包 SHA 自洽，仍不等于逐项校验内部引用与真实解压后 DiffID。

只读检查了受保护的 `deepresearch-pre-security-20261025` 备份，没有重导出、清缓存或停服务。其 `images.tar` 为 2,456,602,624 字节，SHA256 为 `3681a8e637c3af9f578300bb279ed7a15ebacfa64fb8808c2b97187e9f78b769`，属于 OCI 1.0 加 Docker `manifest.json` 兼容清单。22 个预期镜像 ID 全部对应根 descriptor，22 个 linux/amd64 runtime/config 链完整；232 个当前平台必需 blob 的 size/SHA 与 166 个实际解压层 DiffID 均通过，独立层解压总量为 6,557,843,456 字节，Docker 兼容清单引用同一 config/layer 链。最终 helper 实测 44.125 秒，仅是本机这份备份的观测。

包仍保留上游多平台索引元数据。当前平台遍历排除了 94 个非目标平台引用，其中 80 个 blob 未被导出，不能称为完整多架构归档。此前独立诊断还对包内全部 272 个现存 descriptor 做过 SHA 核对，这不填补那 80 个缺失的非目标平台 blob。Docker 官方说明 save 默认导出 daemon 中实际存在的平台，load 默认导入归档中实际存在的平台；两者另提供 API 1.48+ 的显式 `--platform`。本实现明确只证明 linux/amd64 内容链。

新增 `scripts/backup_images.py` 为独立 bundle 校验器：只用标准库，流式读和解压摘要，不向宿主提取层、不查 Docker。bundle 上限 32 GiB、唯一层累计解压上限 64 GiB、单项元数据 4 MiB、累计元数据 64 MiB，另有限定根数、节点数、图深和最多100000次descriptor访问。该范围独立于单 approved artifact 的 2 GiB 上限，原批准规则及 cap 未改。重复 Docker/OCI gzip MIME 别名按同一编码核对，编码冲突仍拒绝。

`backup()` 在写完成 manifest 前验证镜像包，并把标准库 sidecar 一起归档供独立 `restore.py` 使用。`load_archive()` 无条件验证当前平台包内容，发生在后续 cache lookup、建网络和建卷之前，因此全缓存命中也不能掩盖当前平台缺 blob 或假 DiffID。旧 v3 归档可通过当前仓库 CLI 校验；旧备份内冻结的旧 `restore.py` 不会被自动改写，新备份才会携带新版 standalone 脚本和 sidecar。不支持的非 OCI/Docker-compat 格式会明确失败，未默认为通过。

针对性测试共覆盖 35 个独立用例：最初相关两文件 33 项通过，后补真实包揭示的 Docker/OCI MIME 别名回归及 standalone sidecar 导入检查均通过。两个关键反例分别删掉选中平台层、把 config DiffID 改错并重算 config/manifest/index/外层 manifest SHA；两者都在任何 Docker 调用前被拒绝。Ruff 和 diff 检查通过，没有重复全恢复或全项目测试。

这关闭了“包内当前平台必需内容未验证，却可被本机缓存掩盖”的校验缺口。它不是空 daemon 的实际导入、异机恢复、整栈可用性或 RTO/SLA 证明。后续维护窗口仍需 fresh-project 冷恢复验收；异机恢复继续保留用户延期边界。

来源：[Docker save](https://docs.docker.com/reference/cli/docker/image/save/)、[Docker load](https://docs.docker.com/reference/cli/docker/image/load/)。原始诊断、正式 helper 正例和摘要收据保留在 `.cache/optimization-20261026/backup-cache-gap/`。
