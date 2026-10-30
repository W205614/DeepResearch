# Milvus 示例证书的运行时打包边界

此前 Security job `122017588234`、artifact `12711121662` 的 Milvus 镜像报告没有 HIGH/CRITICAL 漏洞项，但在 `/milvus/configs/cert/{ca,client,server}.key` 检出3项 HIGH private-key secret。这是镜像包含公开示例密钥的问题，不通过 ignore、关闭 secret scanner 或路径豁免解决。

2026-10-30 的 Security job `123963781181`、artifact `12964077371` 新扫描检出 Ubuntu `libbrotli1 1.0.9-2build6` 的 HIGH 漏洞 `CVE-2026-111497`，无 secret 项。[Ubuntu USN-8956-1](https://ubuntu.com/security/notices/USN-8956-1) 指定 Jammy 修复版本为 `1.0.9-2ubuntu0.1`。当前配方先升级这一运行时包，再从升级后的文件树移除示例证书并导出完整运行时，保留更新后的库与 dpkg 元数据；Go 源码、编译器及 Milvus 自带 native 库不变。继续使用现有 Compose 镜像名，实际制品以本次构建摘要、扫描和运行证据为准；历史通过结果不替代新制品验收。

固定源码为 Milvus v3.0.3 commit `328df0cd2f323c2d3c50cef91bfc9f2c412bcb09`，源码归档 SHA256 `1dd3a0ff960af532f04172b2aebdb9716b1244cad2fb81781b27641f67216cf9`。[上游 configs/cert](https://github.com/milvus-io/milvus/tree/328df0cd2f323c2d3c50cef91bfc9f2c412bcb09/configs/cert) 包含9个公开文件（3个 key、3个 PEM、2个 CSR、1个 CA serial）；[上游 proxy 测试](https://github.com/milvus-io/milvus/blob/328df0cd2f323c2d3c50cef91bfc9f2c412bcb09/internal/distributed/proxy/service_test.go#L1093) 直接使用这些材料。只记录其路径与散列，不复制或打印密钥内容。

完整扫描还发现 `curl`/`libcurl4` 的低危更新，本次一并固定为 Ubuntu `7.81.0-1ubuntu1.30`，覆盖 [USN-8927-1](https://ubuntu.com/security/notices/USN-8927-1) 的相关修复。上游自带 Go 模块的低危及未定级告警仍保留在完整扫描中，不使用忽略规则掩盖，也不将本次 OS 包更新称为所有第三方依赖已无漏洞。

[默认配置](https://github.com/milvus-io/milvus/blob/328df0cd2f323c2d3c50cef91bfc9f2c412bcb09/configs/milvus.yaml#L1191) 的外部与内部证书路径指向该目录，但 `common.security.tlsMode=0`、`internaltlsEnabled=false`。这说明默认未启用这两种 Milvus TLS，不能据此宣称已经加密传输。移除示例材料的打包阶段不会替运维启用 TLS；后续单独授权的源码补丁只改变凭据初始化失败的处理，见下文。

原最终阶段直接继承固定 Milvus 镜像，覆盖一个不含 cert 的配置目录仍会留下旧文件。在继承层上 `rm` 也不足以消除归档中的密钥：[Trivy v0.75.0 的 layer applier](https://github.com/aquasecurity/trivy/blob/v0.75.0/pkg/fanal/applier/docker.go#L358) 明确保留后续层删除的 secret 发现。

`Dockerfile.milvus` 使用独立包装阶段：从已应用上述安全更新的运行时文件系统快照复制到 `/runtime-root`，移除其 `/milvus/configs/cert` 整个示例目录，最终从 scratch 复制过滤后的完整树及原有两个 Go 构建产物。最终镜像不继承含密钥的原层，也不复制编译器、开发包或源码目录。更新后的 OS 包数据库、Milvus 自带 native 库、系统 CA、OpenSSL/FIPS 模块及正常配置保留在文件树中；最终重声明原 Config 的6个 ENV、非 root User、tini Entrypoint、WorkingDir 和2个已有 OCI label，不虚构原本没有的 CMD、VOLUME、EXPOSE、HEALTHCHECK、STOPSIGNAL 或 SHELL。

这是新镜像打包方式，必须在云端正常 build 后重新进行完整漏洞和 secret 扫描及运行时验证。静态检查不能证明新镜像已经构建、没有其他 secret、保留所有文件元数据或实际 TLS 已通过。此前已验证镜像的 digest/运行证据不能自动绑定新制品；原报告及失败记录保留。

运维启用 TLS 时应将自己的 CA、服务端证书及私钥以只读挂载提供给运行用户，并显式配置 `tls.serverPemPath`、`tls.serverKeyPath`、`tls.caPemPath`（或对应 internaltls 字段）。仍可挂载 `/milvus/configs/cert:ro`，也可用其他明确路径；不要复用公开 CA、导入示例密钥或将真实材料 COPY 进镜像。文件权限应使实际 `milvus:milvus` 只能读取所需材料。

后续仅在云端隔离作业或另获准的窄窗口执行以下步骤，本次没有在本机执行 build/start：

1. 正常构建新镜像，保存精确 manifest/config/layers 身份和完整 Trivy 原始报告；扫描器及 HIGH/CRITICAL 门槛保持不变。逐层确认最终归档不含 `milvus/configs/cert`，并对比过滤前后的文件清单：除9个示例文件及原两个重编译产物外，没有其他预期外文件或元数据变化。确认6个 ENV、User、Entrypoint、WorkingDir、labels 与固定原 Config 相同，native 库与 OS 包覆盖完整。
2. 使用独立 project、独立合成卷和已健康的 etcd/S3 依赖；先以默认 TLS 关闭配置验证同一新制品可正常 ready。保留测试容器、数据卷和创建收据，不用 `--rm`、`down -v` 或 prune；不连接日常数据。
3. 外部 `tlsMode=1` 和 `2` 分别用缺失证书/密钥路径启动，明确记录原生 key-pair 加载错误及进程/监听/ready 结果。只有证书原因已确立且没有降级接受明文连接才可记 fail-closed；不得把依赖未就绪、启动超时或 generic unhealthy 当作 TLS 负例通过。
4. 在私密合成目录生成本轮专用 CA/服务端/客户端材料，只读挂载后分别验证单向/双向真实 TLS 连接；错误 CA、缺少客户端证书及明文连接均应拒绝。验证后记录材料指纹与挂载只读状态，真实私钥不进入报告、镜像或日志，不放宽服务器校验或客户端验证。
5. 对内部 TLS 单独做缺失证书与正常只读证书两组真实启动/握手检查，确认各内部端点不会退回明文。原固定上游 `internal/distributed/utils/util.go:getTLSCreds` 在错误时仅记录日志并返回 nil；DataNode、MixCoord、Proxy、QueryNode、StreamingNode 的5处调用点直接将返回的 ServerOption 交给 grpc.NewServer，再执行 Serve，没有在这些调用点补充证书错误检查。固定依赖 [gRPC v1.83.2 的 Creds](https://github.com/grpc/grpc-go/blob/v1.83.2/server.go#L476) 接受该 nil 值；其 [HTTP/2 server transport](https://github.com/grpc/grpc-go/blob/v1.83.2/internal/transport/http2_server.go#L154) 仅在 Credentials 非 nil 时握手。这条静态调用链显示原上游在缺失内部服务端证书时存在不做 TLS 握手的监听路径风险；不等于已经实测服务会整体 ready。其他客户端 CA 初始化可能另行失败，也不能据此代替所有内部监听的 fail-closed 检查。[外部 proxy TLS 初始化](https://github.com/milvus-io/milvus/blob/328df0cd2f323c2d3c50cef91bfc9f2c412bcb09/internal/distributed/proxy/service.go#L391) 则明确将加载错误传回并返回。现已获得最小内部凭据初始化源码补丁的独立授权，不修改其他 TLS 验证逻辑，也不新增 wrapper；实际启动/握手结果仍待后续执行。

当前 profile 未启用内部 TLS，以上是启用后的条件风险。后续窄窗口应把默认关闭模式的启动/扫描、外部 TLS1/2 的正负握手、内部 TLS 的正负握手分别登记；只对实际执行覆盖的模式给结论。尚未执行的内部 TLS 缺证书检查保持 `not_verified`，不得写成已 fail-closed 或零风险。

## 已授权的最小内部 TLS 源码补丁

补丁位于 `deployment/vendors/milvus/patches/internal-tls-fail-closed.patch`，只修改 `getTLSCreds` 一个函数：复用同文件既有的 CA 读取/PEM 解析函数；CA 失败时用固定文本 panic。服务端 cert/key 加载失败或返回 nil credentials 时也用固定文本 panic，在既有调用者构造 gRPC server 之前终止。panic 不带原始错误、证书或密钥正文。合法配置仍返回原 `credentials.NewServerTLSFromFile` 生成的凭据；未启用内部 TLS 时不会调用该函数。签名、调用者、默认开关、客户端验证、协议版本和鉴权策略均不改变。

`source.lock.json` 独立记录原文件 SHA256 `62ea686155db8df34ffa57c119de00968a3773decccfaab2c359bc8893094769`、补丁 SHA256 `a9c29644f45aee2afdaf475269c275f75eefdd17fd6e6e09221d43dc59d5644e` 和补丁后文件 SHA256 `3d26515e2d0a70e362922bcd5be3c913a4ac15480f4c486954a5c013b271bb6e`。原固定归档及全部模块锁保留，派生源码明确区别于未修改的上游。builder 对原文件、补丁和测试文件逐一验 hash，以 `patch --batch --forward --fuzz=0` 应用，再验结果 hash；任一步不匹配都停止构建。新增的 vendor 局部 `.gitattributes` 只固定补丁/Go 测试的 LF 行尾，避免 Windows checkout 破坏这些精确字节绑定。

Go 测试仅用标准库生成临时 CA/服务端材料及既有 gRPC/paramtable 依赖，覆盖 cert、key、CA 各自缺失或无效的6个反例、合法配置和默认关闭分支。反例断言初始化在抵达 `grpc.NewServer` 前以约定的固定文本 panic；合法/default 分支只构造并停止未监听的 server，不伪装成真实网络握手。Dockerfile 在正常 Linux builder 内要求执行这组定向测试，再编译生产二进制；本机仅做格式/静态补丁验证，尚未执行 Go 编译、Go 测试、镜像构建或 Linux 原生启动。新增镜像的完整扫描及第3—5步实际验证仍是后续门槛，不能用此补丁的存在替代结果。
