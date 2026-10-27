# Keycloak audience 升级迁移

本项目的 API audience 是固定标识 `deepresearch-api`，seed 中只有交互客户端 `deepresearch-web`。因此 audience mapper 使用 `included.custom.audience`。API 对 issuer、签名、`aud=deepresearch-api` 的严格校验保持不变。

Keycloak 26.8 会检查 `included.client.audience` 指向的客户端是否存在且启用，不满足条件就不添加该 audience。26.4 允许本项目原有配置。仅替换镜像会使已有 realm 的 token 缺少 `aud`；启动时 `--import-realm` 会跳过已有 realm，修改 seed 不会修复已持久化的配置。[26.8 实现](https://github.com/keycloak/keycloak/blob/26.8.1/services/src/main/java/org/keycloak/protocol/oidc/mappers/AudienceProtocolMapper.java)

`scripts/reconcile_keycloak_audience.py` 只管理指定 realm 中固定客户端 `deepresearch-web` 的固定 mapper `deepresearch-api-audience`，只把该 audience 字段换成相同值的 custom audience。如果现场实际存在 `deepresearch-api` 客户端，脚本停止，保留其启用/禁用语义，交由维护窗口内单独核对。

## 维护窗口

先冻结配置写入者、保存现有数据库备份与镜像摘要。使用具备该 realm 客户端读取和 mapper 管理权限的短时管理 token，通过环境变量 `KEYCLOAK_ADMIN_TOKEN` 提供；不要把 token 放入命令行、Git 或交付记录。以下命令默认只读，不重启服务：

```powershell
.venv/Scripts/python.exe scripts/reconcile_keycloak_audience.py --base-url http://127.0.0.1:8180 --realm deepresearch
```

确认输出的固定 realm/client/mapper 和 `change_required`。建议在仍运行 26.4 时先迁移 mapper，因为 custom audience 已验证同时兼容 26.4 和 26.8。执行时必须给出私密备份文件，文件存在时不会覆盖：

```powershell
.venv/Scripts/python.exe scripts/reconcile_keycloak_audience.py --base-url http://127.0.0.1:8180 --realm deepresearch --apply --backup .cache/keycloak-audience/before.private.json
```

脚本先保存原配置及 before/after SHA，再重读检测冲突，修改一个 mapper 并读回确认。重复执行返回 `already_desired`。备份包含配置和内部 ID，留在受控私密目录，不作为公开证据上传。Keycloak Admin API 没有为此流程提供原子 compare-and-swap；维护窗口必须只有一个配置写入者。

升级仅针对恢复出的数据库副本先验证，确认 PKCE、JWKS 签名、固定 issuer/audience、sub/角色、refresh、logout 和错误 audience 拒绝，再按部署流程切换。已有 realm 必须实际执行迁移，不能以 seed 变更代替。

## 配置回滚

```powershell
.venv/Scripts/python.exe scripts/reconcile_keycloak_audience.py --base-url http://127.0.0.1:8180 --realm deepresearch --apply --rollback .cache/keycloak-audience/before.private.json
```

回滚绑定原 base URL、realm、client ID、mapper ID 和迁移后配置 SHA；其他人已修改配置时停止。重复回滚返回 `already_rolled_back`。将 mapper 退回不存在的 client audience 会再次破坏 26.8 token；通常回退服务器时可以保留跨版本兼容的 custom mapper。数据库跨 minor 的回滚仍需恢复升级前备份，不能让旧服务器直接读取迁移后的数据库。

## 本次证据边界

隔离合成 realm 在 26.4 实际执行 custom mapper、PKCE/JWKS/aud/sub/角色/refresh/logout 和错误 audience 拒绝；随后恢复 legacy 配置、导出 SQL、恢复到独立 PostgreSQL 17.11 副本并由 26.8 迁移 schema，真实执行 mapper 备份、幂等、回滚、重新应用及同样认证流程。上述隔离合成验证没有修改原日常与当时验收部署；后续统一候选切换另由部署记录证明。这不证明生产旧 token 连续有效、异机恢复、HA 或外部身份联邦；异机恢复为 `deferred_by_user_not_verified`。

Temurin 21.0.13 的 tar 和厂商 SBOM 已校验发布 SHA 与 GPG 签名。历史 overlay 尝试因仍含原 RPM/JDK 的 2 个 HIGH 被拒绝；最终 `dr-vendor-keycloak:26.8.1-temurin21.0.13-locked-20261026` 已合法卸载旧 RPM 及其实际文件，完整镜像与独立原生 JVM/默认全包扫描均为 0 HIGH/0 CRITICAL，中低危原样保留。最终来源、JRE 文件指纹、私有数据库副本认证及迁移证据见 [Keycloak vendor receipt](evidence/optimization-20261026/keycloak-vendor-remediation.json)。这些是最终候选证据；本迁移材料不代表现有候选或日常部署已经切换，既有 realm 的实际窗口迁移、数据库备份和统一验收仍由部署流程执行。
