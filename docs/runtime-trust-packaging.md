# 运行时与信任数据打包

当前Python镜像从签名的CPython 3.11.18源码构建，使用固定摘要的Debian 13 distroless运行层。构建阶段按锁定SHA安装SQLite与zlib包，并把真实库文件和包记录带入运行层；`uv 0.13.6` 用于安装依赖，导出镜像前移除。实现见[Dockerfile](../Dockerfile)与[运行层整理工具](../scripts/prepare_python_runtime.py)。

## CA与时区

[信任数据工具](../scripts/prepare_runtime_trust.py)按[runtime-packages.lock.json](../deployment/runtime-packages.lock.json)核对官方包，替换完整CA目录、bundle、OpenSSL别名、配置及真实包记录，再展开为最终运行层。完整替换使旧目录中的证书不会因层叠加而残留。

包来自Debian签名仓库并核对独立SHA256，版本记录直接来自实际安装包。`SSL_CERT_FILE` 指向有效bundle，Python还核对锁定的certifi数据。测试覆盖已移除根证书的DER指纹、所有可读PEM文件、默认HTTPX信任上下文和过期CAPATH。

etcd构建固定Go 1.27.3与etcd 3.5.34的三个静态目标，使用固定摘要的distroless static Debian 13运行层，并安装同样锁定的CA与时区数据。实际入口、用户、工作目录和端口在[配方](../deployment/docker/Dockerfile.etcd)中声明。

## 验证入口

单元测试覆盖整树替换、包身份、路径约束与包记录一致性；需要符号链接的用例使用Linux夹具。镜像验证检查真实信任路径与数据摘要、TLS握手、移除根证书的负例、时区文件、etcd读写和Milvus查询。

当前源码的[Security工作流](https://github.com/W205614/DeepResearch/actions/runs/42370035403)已通过完整镜像与原生运行时检查。2026-10-26的隔离验证保留在[历史窗口记录](evidence/optimization-20261026/runtime-trust-window-closure.json)中，使用其当时的镜像与包版本。
