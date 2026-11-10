# 固定上游示例原文

三个 `*.go.txt` 文件保留 Milvus 提交 `328df0cd2f323c2d3c50cef91bfc9f2c412bcb09` 的 `examples/telemetry_demo/` 原始字节与 Apache-2.0 文件头。对应官方源码归档 SHA-256：`1dd3a0ff960af532f04172b2aebdb9716b1244cad2fb81781b27641f67216cf9`。

它们只作为完整维护补丁的真实应用输入，测试时复制为原 `.go` 文件名；不加入应用运行或 Docker 构建。原 `go.mod`／`go.sum` 复用 `deployment/vendors/milvus/modules/examples/telemetry_demo/` 的固定快照，不复制另一份依赖清单。

许可：[Milvus Apache-2.0](../../../deployment/vendors/milvus/licenses/milvus-LICENSE)。维护补丁、五个输入／输出摘要及编译边界见 [示例修复证据](../../../deployment/security/code-scanning-review-20261110-evidence/telemetry-example/README.md)。
