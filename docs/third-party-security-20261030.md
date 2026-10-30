# 第三方告警修复与日常使用边界（2026-10-30）

本轮覆盖现有 18 类运行镜像，以及源码中的第三方锁文件和开发依赖。修前基线为 `e88ec0eace686541df2f7be33071c455608441c6`。完整包表和原始严重度保留，没有增加通配忽略或降低 HIGH/CRITICAL 门槛。

## 修复内容

| 范围 | 实际变更 |
|---|---|
| 前端开发工具 | Vitest / `@vitest/mocker` 升至 4.1.12，修复代码注入和 Windows 路径穿越两项中危；生产入口不新增兼容逻辑。 |
| Milvus | 保留 3.0.3 源码、Go 1.26.10 和既有 C++ ABI，更新 x/net、x/crypto、cilium/ebpf、OpenTelemetry 等依赖；生产及已保存的测试/示例锁分别更新，不宣称遗留示例均能编译。 |
| Alertmanager / etcd | 更新 OpenTelemetry、x/net 及其必要依赖，按真实上游标签构建。etcd 三个真实静态 ELF 均无动态解释器或依赖库，采用同 Debian 13 的官方 static 基础镜像，移除不使用的 native 运行库。 |
| Loki / Prometheus | 增加同发行版本的源码安全构建，更新 x/net；源码与 UI 资产校验、真实 Git 身份和只读 Go 锁进入配方，Compose/CI 均选择这些构建。 |
| 存储初始化 / PostgreSQL | 重建更新后的 x/net、gosu 的 moby/sys/user 和 x/sys，解决源码锁已更新但旧容器仍用旧 ELF 的差异。 |
| 证书 / 时区 | Loki 完整替换 CA 来源、bundle 和哈希目录；Tempo 安装 Debian 12 的真实 tzdata 2026f 数据及包记录；etcd 安装固定校验和的 Debian 13 CA/tzdata/tzdata-legacy，完整保留旧时区别名及真实包记录。 |
| Java 运行镜像 | 移除直接 Java 入口不使用的 GPG、coreutils 及其依赖；构建阶段仍验证 JRE 签名，运行时保留 CA 和固定地址健康检查。 |

主要修复依据：[Vitest 路径穿越](https://github.com/vitest-dev/vitest/security/advisories/GHSA-xp26-3454-mjgp)、[Vitest mocker 注入](https://github.com/vitest-dev/vitest/security/advisories/GHSA-qxg6-89mg-rqv8)、[OpenTelemetry](https://github.com/open-telemetry/opentelemetry-go/security/advisories/GHSA-8wmf-6v46-5gfg)。具体版本、源码摘要和包校验和以各 `deployment/vendors/*/source.lock.json`、`runtime-data/packages.lock.json` 和实际 Go 锁为准。

## 为什么还有告警

逐个 CVE、包版本、触发条件、日常影响和一手来源见[逐项基线表](evidence/third-party-20261030/baseline-advisories.json)及[机制简表](evidence/third-party-20261030/baseline-mechanisms.md)。这两份文件明确记录**修复前**的 198 个包版本/ID 组合、104 个唯一 ID，不能当作本次修复后的残留数量。

- Debian、Ubuntu、RHEL 的部分系统库尚无所在稳定发行版的修复包，厂商标为 no-dsa、deferred、Will not fix 等。保留原始告警；不把其他发行版的 ABI 混装进运行镜像，也不把自行维护一批上游 C 补丁等同于可直接升级的稳定修复。
- GCC 文档元包、libgcc、OpenMP 和 libstdc++ 对应不同实际文件。模板实例化、aligned operator new、libiberty 等漏洞的触发点不同，不能用同源包版本一概认定全部路径存在或全部已修复。Power8、32 位专用路径与当前 amd64 的不适用结论仅限相应 CVE。
- glibc、PCRE2、SQLite、压缩库等记录涉及特定输入、扩展、JIT、认证或本地命令路径。项目没有向用户开放任意正则、上传数据库执行或 shell 命令代理，但没有穷尽所有 native 依赖调用图，条件性内存错误、拒绝服务和 TLS 语义风险仍保留。
- 后端 BusyBox wget 仍用于固定的 `http://127.0.0.1:8080/readyz` 健康检查。其请求注入告警需要把不可信、带控制字符的 URL 交给 wget；当前固定地址不满足此前置条件。不能据此声称工具不存在或漏洞已经修复。
- Go 弃用 OpenPGP 的未定级告警没有可升级到的修复模块版本。是否适用必须核对实际 ELF 的完整生产包闭包，不能只凭 x/crypto 模块存在下结论。当前 Milvus 两个新 ELF 的[独立闭包核对](evidence/third-party-20261030/milvus-openpgp-current2-review.json)仅适用于其中记录的精确源码、镜像和二进制。
- Grafana、Tempo 的一部分产品告警把 `v0.0.0-…+dirty` 当成旧发行版本。已有技术判定只用于绑定的具体源码、ELF 和告警范围；原始扫描记录仍保留。镜像、源码、配方、证据或告警变化时重新复核。

## 验证与使用条件

本次版本须通过对应提交的 CI、Security，以及实际重建后的健康检查与数据核对。提交前的候选扫描、历史通过的工作流和源码锁更新都不能代替最终部署验收。CI 继续扫描完整源码和实际 Compose 选择的镜像；新的源码构建不会继承 Grafana/MinIO 的固定制品授权。

后端完整 Syft 清单经[独立对照](evidence/third-party-20261030/backend-native-apk-baseline-review.json)确认：仅移除 37 个 OS 包，APK 从 73 减至 36，79 个 Java 归档和 1 个 JRE 二进制的名称、版本多重集不变。因此只同步 APK 数量基线，JRE 文件指纹、镜像来源链、其余清单基线和漏洞门禁均保留。

项目仍定位于本机、受控内部日常使用。剩余条件性风险不等于日常操作必然失败，也不构成公网多租户或任意不可信数据处理的安全保证。当前本机自动备份已停用、旧离线恢复集已退役，没有有效完整恢复集；本轮保留业务卷及已有账号、文档和报告，维护验收不调用付费研究模型。
