# Etcd 的固定 Debian 时区包

这里保存 Debian 官方 `tzdata`、`tzdata-legacy` 的原始 `2026e-0+deb13u1` 包，合计 452,260 字节。Etcd 原远程 `ADD` 在两次新 CI 作业中都收到不符合固定散列的内容，因此改为从 Git 构建上下文复制这两份已验证包，安装前仍以原 SHA256 强制校验。版本、包字节、时区数据与运行时包记录保持一致。

来源、大小、散列、验证时刻和失败作业见 `provenance.json`。原始 `InRelease.20261027` 的两份签名已通过固定 Debian 13 基础镜像中的 archive keyring 和 `sqv` 默认密码策略核验；它通过 `Packages.20261027.xz` 绑定这两份包的精确散列。公钥环与签名验证输出一并保留。

该历史 InRelease 已超过 `Valid-Until`，这里只用它认证固定包的历史来源，不把它用作当前 APT 新鲜度证明，不关闭任何过期或签名检查。其余构建工具继续使用当前经过认证的 APT 仓库。新增文件没有改写共享的冻结时区锁或打包脚本。

原许可见 `tzdata.copyright`、`tzdata-legacy.copyright` 和未修改包内的版权文件；IANA 时区数据库属于 public domain。对应 Debian 源码包地址也保留在来源记录中。

本目录的局部 `.gitattributes` 必须保留：父目录将 Etcd 源文件视为文本，而 `.deb`、压缩索引及签名材料需要原样进入 Git 和 Docker 上下文。
