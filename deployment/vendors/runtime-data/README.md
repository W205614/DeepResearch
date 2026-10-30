# 运行时 CA 与时区数据

固定的 Debian `.deb` 来源、SHA256 和官方校验页面见 `packages.lock.json`。Docker 的 `ADD --checksum` 验证包内容；包装阶段的 APT 使用固定 Debian 基础镜像内的 archive keyring 验证工具依赖。工具及包管理器不会进入最终镜像。

Loki 用 `prepare_runtime_trust.py` 替换完整 CA bundle、named/hashed 目录、证书来源树与配置，检查已撤销根证书不再处于活动信任集合。etcd 使用相同流程，并替换时区数据。

etcd 三个程序以 `CGO_ENABLED=0` 构建；上游同版本发布镜像也使用 distroless/static。运行时使用同发行版 `static-debian13`，移除程序没有链接的 glibc/GCC 等库。保留原服务 `USER 0` 和启动配置；`nonroot` 是基础镜像标签，不代表这里改变了运行用户。Debian 13 的时区闭包同时安装固定的 `tzdata`、`tzdata-legacy` 包，通过显式 `tz-with-legacy` 打包方式保留完整的常用、历史与 right/ 数据和两份真实包记录。

Tempo 保留原始 Debian 12 基础层，在最后一个 COPY 层中合并真实 `tzdata 2026f-0+deb12u1` 数据、原始 control/md5sums 与应用 buildinfo。构建先验证新时区树覆盖全部旧路径，再生成 overlay；若上游删除路径，构建明确失败，需要重新设计镜像与独立复核。此数据更新不改变 Tempo ELF，也不表示 Debian 12 的其他包或支持周期已经升级。

更改 Tempo 配方后，必须依据实际构建产物重新完成现有 exact1 独立复核；不能只替换配方哈希或扩大漏洞豁免。
