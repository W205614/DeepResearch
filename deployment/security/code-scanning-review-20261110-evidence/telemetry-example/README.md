# Milvus telemetry 示例的维护修复

对应原告警 **35 / GO-2026-5932**。原依赖快照仍保留，不能将本文件理解为“原快照已经没有漏洞”或“忽略所有示例”。维护入口使用本目录的 `telemetry-maintained.patch`；没有改动生产配方、原 `source.lock.json`、日常镜像或原始取证材料。

实际发现并保留了三类失败：原作者绝对路径 replacement 不存在；旧 v2 依赖身份与当前源码中的 v3 SDK 不一致；尝试保留 v2 SDK 时又存在伪版本／Proto API 不兼容。最终补丁仅修正三个源码文件共九处 SDK import namespace、相应 Go 模块／replacement 和 Go 工具生成的依赖锁，不删除示例，不改变业务语句或函数签名。

维护配置使用同一固定 Milvus 源码中的 `client/v3`、`pkg/v3` 和它们声明的官方 Proto v3.0.3。完整 224 个 MVS 模块确认 `x/crypto` 实际选中官方 fixed `v0.58.1-0.20261009181718-2f1f834330b2`。显式约束有具体作用：不加约束的首次 v3 候选仍选中传递依赖 v0.58.0，不能仅凭 `go mod tidy` 删除直接声明就说模块消失。完整 430 个编译包没有导入 x/crypto/OpenPGP。

三个示例文件分别完成 Linux/amd64 `go build -mod=readonly` 和 test-compile，六个命令均退出 0；它们没有测试用例，没有运行 Milvus 业务、发送 telemetry 或调用模型。构建的 ELF 仅保留在本地，不作为本轮二进制制品发布。`review.json` 是公开派生回执，命令中的主机目录被改为文件名；原回执、原始图、完整失败日志和二进制仍在本地保留。

## 应用范围与复验

在隔离目录中获取并校验 `review.json` 所指固定官方源码归档，保持其中的完整源码布局，然后覆盖仓库 `deployment/vendors/milvus/modules/` 中对应的固定锁。进入该隔离源码树的 `examples/telemetry_demo/`，先以 `git -c core.autocrlf=false apply --check` 核对补丁，再以 `git -c core.autocrlf=false apply` 应用本目录补丁；显式选项保留 Windows 和 Linux 上相同的 LF 输出字节。五个输入及输出摘要在 `review.json`，回归测试会从原始五文件实际应用整份补丁并核对全部输出。

在 Go 1.27.3、Linux/amd64、CGO 禁用的隔离环境中复验：

```sh
go list -m -json -mod=readonly all
go list -deps -test -json -mod=readonly ./...
go build -mod=readonly -o main-demo main.go
go build -mod=readonly -o multi-database-demo multi_database_demo.go
go build -mod=readonly -o webui-demo webui_demo.go
```

三个文件各自声明 `main`，因此分别编译；没有把一次依赖列举等同编译成功。当前补丁不替换不可变的原快照；对原告警的拟处置是有此具体修复入口和当前构建边界依据的 **mitigated**，而不是声称原始扫描已经消失。SDK／源码或锁发生变化时应重新验证补丁与关闭依据。

`derive_review.py --root PATH_TO_CHECKOUT` 仅从留存的已验证本地 handoff 生成公开证明、补丁副本和原文测试夹具，不构建、访问网络或修改原快照。
