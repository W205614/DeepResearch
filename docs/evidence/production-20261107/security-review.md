# 首轮安全阻断与修复边界

本记录保留首轮真实失败，不是发布批准。应用质量测试与安全放行分别判断；原始扫描、旧审查、旧授权和冻结128文件不得被改写。

首轮源码：`068c7d4a2cac2281a0399475d1e75fb9b632bf19`；[Security 42346438135](https://github.com/W205614/DeepResearch/actions/runs/42346438135)有18个成功Job、4个失败Job。

| 检查 | 首轮真实结果 | 处理边界 |
| --- | --- | --- |
| 旧Grafana源码快照 | 14条HIGH中新增9条x/net 0.60.0的HTML发现，原精确5条门禁拒绝 | 实际覆盖源码和已审native模块为0.62.0；须按9个目标逐项绑定当前合法选中制品，不得过滤原始扫描后喂给旧门禁 |
| MinIO | 两个原ELF的同一HTML公告由UNKNOWN变HIGH/MEDIUM，旧精确审查不匹配 | 完整生产构建图缺受影响的x/net/html；补充技术审查须精确绑定同CVE、版本、严重度、ELF、OCI及完整构建图；不是风险接受 |
| Agent原生库 | SQLite新增CVE-2026-119374、CVE-2026-119379两条HIGH未在旧固定代码证明列表 | 当前实际加载3.54.1-2官方库；只能在官方补丁、来源、包身份与已加载库SHA全匹配后作精确FIXED_CODE补充，其他发现仍阻断 |
| Grafana运行制品 | 新增3条CRITICAL Brotli及13条HIGH compress发现，旧批准未覆盖 | 实际插件含受影响符号，不能用缺包结论排除；必须构建修复依赖的新core和插件候选，再完整扫描、独立审查和精确授权 |

Grafana新增发现：`GO-2026-6888 / CVE-2026-111497`，Brotli实际1.2.2/1.2.6，官方修复1.2.7；`GO-2026-6865 / CVE-2026-116591`，compress实际1.19.1/1.19.2/1.20.0/1.20.1，官方修复1.20.2。公告发表于旧候选审查之后。受影响符号存在不能直接证明所有攻击路径可利用，但不足以判定NOT_AFFECTED。

一手来源：[Brotli公告](https://vuln.go.dev/ID/GO-2026-6888.json)、[compress公告](https://vuln.go.dev/ID/GO-2026-6865.json)、[HTML解析公告](https://vuln.go.dev/ID/GO-2026-6860.json)、[HTML渲染公告](https://vuln.go.dev/ID/GO-2026-6866.json)、[Debian SQLite修复记录](https://bugs.debian.org/cgi-bin/bugreport.cgi?bug=1152790)。

旧 `deployment/security/grafana-maintenance-authorization.json` 只授权archive SHA `408744f9d8d357a9b7dfd97ac802efbbdee662cb1177b80d8020bdc24c019e25`及33条精确发现；`scripts/check_grafana_maintenance.py::validate_authorization`要求request、subject及完整Counter匹配。它不能授权新制品或新增发现。

候选修复使用独立日期目录/recipe/tag，保留当前core13.2.5，更新受影响依赖并重建全部13个插件。只有同一新image的完整Trivy vuln/secret、Syft/Grype、实际ELF与完整生产构建图及独立审查完成后，才能准备新exact request。候选构建或收集成功不等于授权发布，更不等于日常部署；具体摘要、结果及后续状态以最终交付记录为准。

本机详细原始材料和精确SHA保存在忽略目录 `.cache/pr13-security/diagnosis.md`、`diagnosis.json` 及对应CI artifacts；不会用变更严重度阈值、泛化忽略或旧批准覆盖新增风险。

后续SQLite补充56项、MinIO/源码补充108项正反例通过，并对保留的真实CI工件离线复核、保留原失败verdict。两处HTML追加由另一代理只读复核，旧函数和旧证据未改；[脱敏补充回执](security-supplements.json)明确 `global_security_approved=false`。这不能替代最终提交的新鲜CI扫描，也不能覆盖Grafana新增16条运行时发现。
