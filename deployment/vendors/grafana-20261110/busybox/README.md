# BusyBox wget HTTP request-target fix

这是 `CVE-2025-60876` 的独立源码构建输入；不是 Alpine 新包版本，也不覆盖既有镜像或批准记录。源码为 BusyBox 1.37.0、Alpine aports `c3ef5d10e6ef6528852c51f0564963e2f8c1be19` 的 44 个原补丁和原配置，再叠加一个明确的本地补丁。`source.lock.json`、`files.sha256` 和 `archives.lock` 固定来源及摘要。

[BusyBox 邮件列表的已 review 补丁](https://lists.busybox.net/pipermail/busybox/2025-November/091840.html)提出拒绝 URL 中原始控制字符和空格；[Debian 跟踪器](https://security-tracker.debian.org/tracker/CVE-2025-60876)记录了下游固定版本。此处复用该校验，放在真正 HTTP 发送边界，并检查 `target.path` 与 `target.host`：BusyBox 的相对 `Location` 路径绕过 `parse_url`，代理绝对形式和 `Host` 头也包含 host。补丁同时拒绝 DEL，保持 `%20`、`%0d` 等编码字节，不解码后重写 URL。

## 构建和接口

仅面向 Linux/amd64。从仓库根目录构建：

```sh
docker build --platform linux/amd64 --target busybox-fix \
  -f deployment/vendors/grafana-20261110/busybox/Dockerfile \
  deployment/vendors/grafana-20261110/busybox
```

干净 clone 无需本机材料：构建按精确 URL 下载 47 个锁定的 Alpine 构建 APK、BusyBox 源码及两份静态库源码，逐个校验 SHA-256，使用固定 Alpine 基础镜像的官方 key 验证全部 APK 签名，然后离线安装。不使用 `allow-untrusted`。缺包、传输不完整、摘要或签名错误直接失败。官方源仍需联网可达，固定版本未来也可能从普通镜像退出；这里没有声称完全离线或永久可获取。

已有经过同样摘要校验的文件可放入被 Git 忽略的 `inputs/`，文件名见 `archives.lock`。本地命中仍逐个核对原摘要，并对 APK 正常验签。`inputs/` 中不允许放凭据或业务材料，不提交其中的 APK、源码包或输出二进制。

stage `busybox-fix` 输出：

- `/out/busybox`：自定义修复二进制，只有 musl 动态依赖。utmps 0.1.3.3 与 skalibs 2.15.0.0 的锁定、验签静态库显式静态链接；不向运行镜像添加新的共享库。
- `/out/metadata/build.json`：二进制、配置、实际修复源码和来源锁身份；另含 ELF、工具链、包签名及 50 例真实 loopback 回归结果。
- `/out/licenses/` 与 `/out/corresponding-source/`：上游 GPL/ISC 许可及版权、完整原始源码包、配置、44 个原补丁、新补丁、构建脚本和上游 APKBUILD。

顶层 Grafana Dockerfile 可嵌入同样的 stage 文本，将 `COPY . /recipe/` 改为 `COPY deployment/vendors/grafana-20261110/busybox/ /recipe/`，然后从 stage 复制 `/out/busybox` 到 `/bin/busybox`，并交付对应材料和身份 metadata。最终整合仍须核对实际运行根文件系统、ELF 依赖、权限、许可材料、扫描与 Grafana 行为。不要把原 APK 的 `1.37.0-r31` 元数据改写成不存在的官方固定版来消除告警。

`SOURCE_DATE_EPOCH=1768059508` 来自原 APK 的实际 build date；TZ 固定 UTC、LC_ALL 固定 C，源码和输出构建路径固定。它固定生成配置的时间，不代表任意工具链和架构都可复现相同二进制；实际输出以 `build.json` 摘要为准。

## 验证边界

构建时以 `RUN --network=none` 执行真实 BusyBox wget 和容器内 `127.0.0.1` 原始 TCP 服务，覆盖正常 GET/POST、编码 target、代理、相对/绝对重定向，以及 raw 0x01–0x20、DEL 和 CRLF 注入。raw NUL 无法进入 OS argv；`%00` 保持编码。响应中的 CRLF 是头字段分隔符，测试证明未转发响应注入头；raw TAB/SP 的重定向在第二次连接前被拒绝。未向外网发送攻击请求，未将此定向回归表述为上游全部测试通过。

`busybox-binsh` 原包只提供指向同一 `/bin/busybox` 的 `/bin/sh` 链接。`ssl_client` 是独立 TLS 字节转发 helper，不构造 HTTP request-target；不能将这三条 source-CPE 匹配都当作三个独立 wget 实现。原始扫描保留，最终需要按具体 payload 和固定代码证据判定，不能以本目录的存在宣称原始告警归零。
