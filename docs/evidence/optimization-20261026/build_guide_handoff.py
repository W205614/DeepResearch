"""Refresh a complete source-grounded guide handoff without touching personal materials."""
import ast
import hashlib
import json
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[3]
JAVA = 'business-backend/src/main/java/com/deepresearch/business/'
FEATURES = []


def add(key, title, section, changes, references, question, followups, points, window, boundary, receipts=()):
    FEATURES.append(dict(id=key, title=title, section=section, change_ids=changes, references=references,
                         main_question=question, followup_questions=followups.split('|'), answer_points=points.split('|'),
                         failure_window=window, evidence_boundary=boundary, component_receipts=list(receipts)))


add('session-create', '会话创建与稳定标识', '会话与记忆', [11,12], [(JAVA+'api/ThreadController.java','create'),(JAVA+'service/ResearchCommandService.java','createThread')],
    '为什么会话标识不能直接用显示标题？', '并发创建同一空间的会话如何分配短编号？|创建后没有任务时会话归属如何判定？',
    '数据库id与thread_key、title分开|Java创建并写作者|空间锁保护短编号分配', '并发创建或事务回滚后，客户端不能凭标题猜测另一个会话。', '源码说明标识与权限设计，不证明无限并发容量。')
add('session-rename', '会话改名', '会话与记忆', [9,12], [(JAVA+'api/ThreadController.java','rename'),(JAVA+'service/ResearchCommandService.java','visibleThread')],
    '改名为什么也必须检查会话作者与空间？', '知道另一个会话id是否能改名？|管理员与作者权限如何区分？',
    '入口解析当前工作空间|会话可见性在服务端检查|改显示标题不改变报告证据', '角色被撤销或会话删除后继续提交旧页面的改名请求。', '不把前端隐藏按钮当权限证据。')
add('session-page', '会话和任务分页', '会话与记忆', [9,12], [(JAVA+'api/ThreadController.java','list'),(JAVA+'service/RunViewService.java','page')],
    '为什么历史列表采用游标而不是一次读取全部报告？', '相同创建时间如何避免漏页？|撤权后旧页和新页能否继续展示受限任务？',
    '时间与id组合排序|限制分页数量并验证游标|任务正文按作者和引用ACL过滤', '翻页期间新增、删除或撤权；游标不代表授权。', '固定分页用例不等同大数据量长期性能。', ['acl.json'])
add('session-download', '整个会话下载', '会话与记忆', [9,12], [(JAVA+'api/ThreadController.java','report'),('backend/api/app.py','thread_report')],
    '如何导出完整会话而不把无权查看的历史一起打包？', '导出是否只包含第一页？|撤销资料权限后批量下载会不会绕过单条限制？',
    '下载读取完整可见任务集合|逐任务应用引用权限|Markdown流式交付减少重复大对象', '列表已隐藏的任务被旧下载入口重新读取。', '下载文本校验与浏览器保存体验分别验证。', ['acl.json'])
add('session-delete', '会话删除与依赖保护', '会话与记忆', [5,9], [(JAVA+'api/ThreadController.java','delete'),('backend/services/runtime.py','delete_thread')],
    '删除会话为何不能只删threads一行？', '有正式报告引用时为什么拒绝删除？|数据库删完但向量和检查点清理失败怎么办？',
    '正式报告依赖先检查|SQL关联记录在事务内清理|外部对象通过可重入清理回收', '事务提交与向量、对象存储、检查点删除之间不是同一事务。', '可恢复清理工具不等于任意文件都可直接删除。')
add('personal-memory', '个人设定与偏好增删改', '会话与记忆', [5,9], [(JAVA+'api/MemoryController.java','add'),(JAVA+'api/MemoryController.java','edit'),('backend/services/runtime.py','_authorized_context')],
    '个人偏好与工作空间资料为什么要采用不同所有权？', '切换空间后能否编辑他人偏好？|清理研究数据是否应该顺带抹除个人设定？',
    'profile/preference归subject|新增和编辑校验作者及写角色|上下文只装入当前作者的个人设定', '空间管理员权限被误当作其他账号个人偏好所有权。', '个人职责未知；不能声称完成用户画像效果验证。', ['acl.json'])
add('semantic-memory', '同会话历史与语义记忆及撤权', '会话与记忆', [5,9], [('backend/services/runtime.py','context'),('backend/services/runtime.py','_authorized_context'),(JAVA+'api/MemoryController.java','list')],
    '为什么旧报告摘要不能绕过原文档权限进入下一次模型请求？', '向量库命中旧摘要时如何再次核对？|资料撤权与个人偏好保留如何同时成立？',
    '语义命中按SQL源任务重新取值|源任务作者/admin和当前资料ACL共同约束|等待向量返回后再次过滤历史|公开模式不装入私有上下文', '撤权发生在模型上下文组装或向量等待期间。', '摘要可用于理解上下文，不是新事实的证据。', ['acl.json'])
add('workspace-create', '空间创建与选择', '工作空间', [6,9], [(JAVA+'security/WorkspaceAccess.java','resolve'),(JAVA+'api/WorkspaceController.java','create')],
    '身份subject与工作空间id为什么不能混为一个字段？', '首次登录如何创建个人空间？|伪造X-Workspace-ID能否加入别人的空间？',
    'JWT身份和成员关系分别校验|请求头只选择已加入的空间|个人空间创建与默认额度持久化', '无成员时初始化与并发首次登录。', '共享数据库上的空间隔离不等于独立数据库隔离。')
add('workspace-member', '成员添加、角色调整与移除', '工作空间', [6,9], [(JAVA+'api/WorkspaceController.java','upsertMember'),(JAVA+'api/WorkspaceController.java','removeMember')],
    '成员管理如何防止移除最后管理员或用旧权限自提权？', '多个管理员同时降权如何处理？|已经打开的SSE在角色移除后多久停止？',
    '修改前锁空间并重查当前admin|保留管理员/创建者约束|流式连接逐批重查当前角色', '先鉴权后等待锁期间权限发生变化。', '已发给浏览器的字节无法因后续撤权召回。', ['acl.json'])
add('workspace-quota', '空间并发额度与平台硬上限', '工作空间', [1,2], [(JAVA+'api/WorkspaceController.java','limits'),(JAVA+'service/ResearchCommandService.java','create'),('backend/core/usage_governance.py','UsageGovernance.reserve')],
    '为什么工作空间并发额度不能替代平台费用上限？', '自建多个空间能否绕过个人周期预算？|管理员调高空间并发是否同时提高平台调用额度？',
    '并发额度控制任务准入|平台/subject/空间/供应商账号共同预留|周期硬上限来自运维配置而非空间API', '多个任务各自低于单任务预算但总供应商用量失控。', 'Token预留不是货币价格，也不是生产容量承诺。', ['governance.json'])
add('workspace-audit', '业务审计', '工作空间', [2,6,9], [(JAVA+'security/WorkspaceAccess.java','audit'),(JAVA+'api/WorkspaceController.java','audit')],
    '审计日志与调试日志分别解决什么问题？', '操作成功但审计写入失败该如何取舍？|为什么不把原始密钥和模型回复写入告警？',
    '记录actor、动作、目标和结果|业务写操作尽量与审计同事务|运行诊断保留分类和计数而非原始凭证', '外部调用成功但本地结果或审计未确认。', '数据库审计不等同不可篡改合规存证。')
add('task-admission', '任务准入与请求幂等', '任务可靠性', [1,2,8,11], [(JAVA+'service/ResearchCommandService.java','create'),(JAVA+'service/ResearchCommandService.java','verifyReplay')],
    '同一个请求重试为什么应返回同一任务而不是再收费执行？', '同一请求号换题目或附件如何处理？|队列满与空间并发满应返回怎样的可操作状态？',
    '业务事务内判定重放和额度|请求号绑定请求内容及作者|先确认研究范围再建立Outbox命令', '客户端超时后不知道服务端是否已接单。', '请求幂等不等于每次供应商调用Exactly Once。')
add('task-outbox', 'Outbox交付与租约', '任务可靠性', [1,11], [(JAVA+'service/OutboxService.java','claimNext'),(JAVA+'service/OutboxService.java','deliver'),('backend/services/runtime.py','accept_schedule')],
    '为什么任务与投递命令要在一个数据库事务中保存？', 'Python接收成功但Java没收到响应会怎样？|旧resume或abort延迟到达时如何拒绝旧代次？',
    '事务Outbox避免双写空窗|租约抢占不长持网络事务|命令版本和expected_attempt限制旧执行', '交付成功但确认丢失、租约过期或消息乱序。', 'SKIP LOCKED不自动保证跨消息顺序；语义是至少一次。', ['reliability.json'])
add('task-stream', '流式进度与事件续传', '任务可靠性', [1,9], [(JAVA+'api/ResearchController.java','events'),(JAVA+'api/ResearchController.java','streamEvents'),('backend/api/app.py','events')],
    'SSE断线续传为什么需要持久事件序号？', 'Last-Event-ID与after冲突时怎样选择？|任务完成、撤权和断线怎样结束连接？',
    '事件仅追加并按id读取|游标避免重复重放|终态与权限失效发送或触发关闭|心跳维持连接', '事件已写入但客户端没有收到，或旧连接跨过撤权。', '事件续传能力不等于端到端实时延迟SLA。', ['acl.json'])
add('task-orchestration', '研究路由与固定多角色编排', '研究过程', [7,11,12], [('backend/research/graph.py','ResearchGraph.build'),('backend/research/graph.py','ResearchGraph.router'),('backend/research/agents.py',None)],
    '哪些问题进入聊天、快速回答或深度研究，角色间如何交接证据？', '角色很多是否代表独立分布式Agent？|内部资料模式为什么不能随意补网页搜索？',
    'LangGraph固定有界流程|planner/scout/judge/analyst/reflector/writer/validator分工|公开与内部资料策略隔离', '路由误判或无证据仍进入长链模型调用。', '同进程固定角色流程不能包装成自治分布式Agent系统。')
add('task-cancel', '任务取消与晚响应围栏', '任务可靠性', [1,11], [(JAVA+'service/ResearchCommandService.java','cancel'),('backend/services/runtime.py','cancel'),('backend/core/reliability.py','Execution.check')],
    '取消成功后为什么还必须防止供应商晚响应写回？', '进程取消与持久取消状态谁是权威？|取消后立刻恢复会不会被旧abort杀掉？',
    'Java持久化取消状态|Worker每次调用和写入检查代次/状态|内部abort只是尽力停止执行', '外部HTTP已发出后不能保证撤销上游计费。', '取消不能承诺退还供应商已发生费用。', ['reliability.json'])
add('task-resume', '检查点恢复与累计预算', '任务可靠性', [1,11], [(JAVA+'service/ResearchCommandService.java','resume'),('backend/services/runtime.py','execute_run'),('backend/core/recovery_contract.py',None)],
    '恢复应继续哪个检查点，何时必须清除终态失败并重新检索？', '终态next为空但检索失败怎么处理？|恢复如何共享原任务累计时间和调用预算？',
    '持久执行代次拒绝旧任务|检索失败的终态图重新准备入口|预算不因恢复重置|权限/来源失效不得保存旧部分证据', '检查点已结束、Worker心跳异常或旧schema不兼容。', '一次恢复通过不能证明所有崩溃窗口都能无损继续。', ['reliability.json'])
add('task-deadletter', '任务及命令死信处理', '任务可靠性', [1,2], [(JAVA+'api/WorkspaceController.java','deadLetters'),(JAVA+'api/WorkspaceController.java','retryOutboxDeadLetter'),('backend/services/runtime.py','recover_dead_letter')],
    '哪些错误进入死信，为什么不能无限自动重试？', '凭证错误和暂时网络错误如何区分？|管理员重投是否绕过已耗尽的任务预算？',
    '有限重试与静态错误类别|人工重投受RBAC保护|恢复仍检查原预算和当前权限', '永久错误被当作瞬态错误反复收费。', '死信治理工具不是自动修复所有供应商故障。')
add('document-upload', '资料上传与内容去重', '资料处理', [3,7,9], [('backend/api/app.py','upload'),('backend/services/documents.py','Documents._add')],
    '上传接收、内容去重、容量准入和资料所有权如何一起判断？', '同hash重传能否接管别人的受限资料？|用户看到indexing是否就代表可检索？',
    '体积/类型与内容哈希检查|扫描前先隔离|成功后异步索引|原作者策略不因重复上传覆盖', '对象写入失败或重复hash命中旧受限元数据。', '上传HTTP成功与资料ready是两个阶段。')
add('document-scan', '隔离扫描与中断恢复', '资料处理', [3,6], [('backend/services/documents.py','Documents._finish_scan'),('backend/services/documents.py','Documents.recover_scans'),('backend/core/clamav.py','scan')],
    '扫描拒绝、扫描服务不可用和扫描进程中断为什么要分开记录？', '重启后卡在scanning的文件如何恢复？|扫描不可用时能否先送模型解析？',
    'quarantine与uploads分区|拒绝/暂不可用状态分开|持久扫描重试次数和有界恢复|未通过不进入解析和索引', 'ClamAV中断、对象移动失败或状态更新丢失。', '一次EICAR检查不代表能检出所有恶意文档。', ['parser.json'])
add('document-parser', '受限PDF、DOCX与图片解析', '资料处理', [4], [('backend/services/parser_process.py','parse_document'),('backend/services/parser_worker.py',None),('backend/services/document_parsing.py','extract_blocks')],
    '复杂文档为什么改为可终止子进程而不是只放线程？', 'PDF解压或原生解析卡死如何收回资源？|图片与解析输出分别受到什么约束？',
    '进程墙钟超时与终止回收|解析内存/CPU/输出大小约束|文档签名、像素、页数与解包限制|位置标识贯穿切块', '进程超时、过大解压流、损坏文档或解析后无正文。', '限制参数需Linux实际验收；离线正常样本不是解析器安全认证。', ['parser.json'])
add('document-attachments', '图片附件暂存、绑定与读取', '资料处理', [4,5,9], [('backend/services/attachments.py','Attachments.add'),('backend/services/attachments.py','Attachments.staged'),('backend/services/attachments.py','Attachments.visible')],
    '图片暂存与已绑定任务的访问规则为何不同？', '同一个暂存图片能否绑定多个任务？|未发送图片与已发送图片如何过期清理？',
    '图片属于上传subject和空间|任务绑定固定位置与归属|读取再次检查任务及作者/admin|未绑定图片有过期清理', '上传成功但任务创建失败，或图片已经被另一个任务绑定。', '视觉提取仅说明图中内容，不证明图中说法真实。')
add('document-version', '资料版本、差异与当前/待发布切换', '资料处理', [3,5,9], [('backend/services/evidence_lifecycle.py','EvidenceLifecycle.versions'),('backend/services/evidence_lifecycle.py','EvidenceLifecycle.diff'),('backend/services/documents.py','Documents._ingest')],
    '为什么新版本索引完成前仍保留旧ready版本？', '新解析或embedding失败是否会让旧文档失效？|历史版本缺失时能否推测一个差异？',
    'pending与current版本分开|成功后原子切换索引版本|历史版本和解析配置可追溯|没有历史材料时明确无法比较', '新向量写入成功但SQL版本切换失败。', '结构化差异不是模型对语义影响的完整证明。')
add('document-chunks', '原文片段和定位', '资料处理', [4,9], [('backend/api/app.py','document_chunks'),('backend/services/document_parsing.py','semantic_chunks')],
    '为什么引用必须带文档版本、块id和原文定位？', '用户如何验证否定条件没有被切块截断？|知道chunk id是否就能读取原文？',
    '切块保留章节/页/行定位|表格保留表头和单位|原文入口按当前文档ACL校验', '相邻条件、否定或表头被拆散，以及旧chunk命中。', '片段回查有助核实，不自动说明模型结论正确。', ['acl.json'])
add('document-replace', '资料替换与旧版保护', '资料处理', [3,9], [('backend/api/app.py','replace_document'),('backend/services/documents.py','Documents.replace')],
    '替换失败为什么应恢复旧ready状态而不是留下不可用资料？', '两个替换请求并发时谁拥有待发布对象？|read grant是否允许替换？',
    '文档锁跨越暂存与扫描|pending对象与当前对象分开|owner/admin或write grant控制修改|失败登记清理而保留旧版', '扫描、对象移动或数据库写入在中间阶段失败。', '替换正确性与模型解析质量分别验证。', ['acl.json'])
add('document-reindex', '资料重建索引', '资料处理', [3,4,7,9], [('backend/services/documents.py','Documents.reindex'),('backend/services/documents.py','Documents._reindex')],
    '内容不变时为什么还可能需要显式重建索引？', '恢复次数耗尽后如何避免自动重建循环？|新解析规则与embedding维度变化如何区分？',
    '显式重建生成新索引版本|原件哈希验证|索引恢复次数有上限|失败保持旧可用版本', '解析合同、向量维度或服务端配置不兼容。', '重建成功不代表历史报告自动成为最新结论。')
add('retrieval-hybrid', '关键词、向量与可选重排', '检索与证据', [2,7,9], [('backend/services/documents.py','Documents.search'),('backend/infrastructure/reranker.py','RerankerClient.rerank')],
    '为什么先融合关键词与向量，再把重排作为可选层？', '向量超时后如何说明降级结果？|重排增加延迟时如何判断是否值得保留？',
    '归一化向量与BM25加权融合|向量不可用时保留关键词候选|重排严格验证索引/分数并可回退|重排计入同一费用账本', '供应商超时、无效分数、重复索引或外发未授权。', '固定语料排序指标不是最终报告准确率。')
add('retrieval-capacity', '大语料候选召回与内存边界', '检索与证据', [7], [('backend/services/lexical_retrieval.py',None),('backend/services/documents.py','Documents.capacity'),('frontend/src/DocumentCapacity.vue',None)],
    '为什么超过小语料阈值后不再把全空间所有片段装入BM25？', '中文词项索引如何保持可比召回？|大候选量和解析容量分别在哪里限制？',
    'SQL词项/统计下推|候选数量与hydrate有界|小语料保留原排序路线|上传阶段明确容量限制', '全量装载造成内存突增，或派生词项落后于SQL当前版本。', '一次合成规模内存测量不是生产容量上限。', ['lexical-memory-final.json'])
add('retrieval-acl-cache', '缓存和向量命中的当前授权', '检索与证据', [7,9], [('backend/services/documents.py','Documents.check_corpus'),('backend/services/document_access.py','require_document'),('backend/services/lexical_retrieval.py',None)],
    '为什么向量metadata与缓存正文不能成为授权依据？', '集合撤权后缓存键没变怎么办？|向量库返回伪造正文与旧chunk如何处理？',
    'SQL当前版本和ACL重新hydrate|可读资料集合参与corpus指纹|返回前复核集合变化|旧命中只用于候选不直接信任正文', '权限撤销与检索等待并发发生。', '授权反例通过不等于完成全面渗透测试。', ['acl.json'])
add('evidence-judge', '证据筛选、补搜与范围核验', '研究过程', [7,11], [('backend/research/graph.py','ResearchGraph.judge'),('backend/research/graph.py','ResearchGraph.reflect'),('backend/research/evidence_policy.py',None)],
    '证据不足时如何补搜而不是把局限写成确定结论？', '已搜过同主题为什么还可能需要细化查询？|模型把未说明关系当事实时如何阻止？',
    '固定搜索/轮次上限|根据缺口细化查询|确定性范围/否定规则与模型判断协同|不足保留未答问题', '重复空查询、来源冲突或语义范围被模型扩大。', '补搜机制对照不能推广为通用准确率提升。', [])
add('report-draft', '报告草稿、引用支持与部分结果', '报告生命周期', [1,9,11], [('backend/research/graph.py','ResearchGraph.validator'),('backend/services/runtime.py','partial_report'),(JAVA+'service/RunViewService.java','visibleRun')],
    '完整、部分、证据不足与核验未完成应如何区分展示？', '格式修复耗尽后为什么保留草稿但阻止送审？|检查点中的旧主张什么时候不能恢复成部分报告？',
    '主张逐条引用核验|coverage与support分开|不完整核验保留明确状态|来源撤权及合同失效fail closed', '模型返回缺字段、截断或旧检查点证据失效。', '同模型核验结果是组件证据，不是独立真值。', ['reliability.json','acl.json'])
add('report-submit', '送审与冻结快照', '报告生命周期', [9,11], [(JAVA+'service/ReportPublicationService.java','submit')],
    '为什么正式报告必须复制正文和证据而不能实时指向可变runs？', '重复送审如何保持幂等？|送审时资料版本或权限刚变化怎么办？',
    '作者提交已完成有效核验结果|当前来源和语料修订号围栏|复制正文/来源/验证/产物并计算哈希|同一任务仅一份快照', '资料热更新与送审并发，或旧任务清理后再次送审。', '内容哈希证明一致性，不证明事实真实。', ['acl.json'])
add('report-review', '异人批准与驳回', '报告生命周期', [9,11], [(JAVA+'service/ReportPublicationService.java','approve'),(JAVA+'service/ReportPublicationService.java','reject')],
    '为什么作者不能审核自己，部分结果批准还要写范围说明？', '审批期间资料变化是否仍能发布？|驳回是删除报告还是保留记录？',
    '当前admin且非作者|验证快照指纹/来源/语料修订|部分结果强制说明发布边界|驳回保留原因与审计', '审核旧页面、篡改快照或资料刚被替换。', '工程上的双人审核机制不代表已经完成真人盲评。')
add('report-withdraw', '撤回与审核记录清理', '报告生命周期', [5,9], [(JAVA+'service/ReportPublicationService.java','withdraw'),(JAVA+'service/ReportPublicationService.java','delete')],
    '撤回当前版本为什么不自动把旧版重新设为当前？', '有修订依赖的报告为什么不能物理删除？|撤回后还能否追溯批准原因？',
    '撤回保留正文证据和原因|清空系列当前指针|存在下游修订依赖时拒绝删除|显式清理仅针对允许状态', '清理当前报告或被引用报告导致审计链断裂。', '保留策略需与实际组织要求匹配，不宣称法律合规认证。')
add('report-series', '报告系列与历史读取', '报告生命周期', [5,9], [(JAVA+'service/ReportPublicationService.java','series'),(JAVA+'service/ReportPublicationService.java','get')],
    '同一研究的多次正式发布如何展示当前版本与历史？', '历史资料已删时冻结引用怎样保留？|集合撤权后系列元数据是否也应过滤？',
    '系列id和revision_no固定演进关系|当前指针独立于是否发布|历史快照不可变|当前ACL控制每一历史版本及可见当前指针', '原件不可访问或系列当前版本被撤回。', '历史引用快照不意味着原始网页仍然存在。', ['acl.json'])
add('report-revise', '资料影响与定向修订', '报告生命周期', [8,9,10], [(JAVA+'service/ReportPublicationService.java','impacts'),(JAVA+'service/ReportPublicationService.java','revise'),('backend/services/evidence_lifecycle.py','EvidenceLifecycle.revision_context')],
    '哪些旧结论可复用，哪些变化必须重新完整研究？', '只新增资料时为什么不能安全只改旧命中结论？|撤权后是否还能以旧快照发起修订？',
    '来源版本变化记录影响|修订绑定当前系列和原快照哈希|仅有效且未受影响主张可复用|新增/删除/缺历史时回退完整研究', '原系列当前版本、资料修订或权限在发起时改变。', '定向修订是受限优化，不能保证自动发现全部业务影响。', ['acl.json'])
add('report-diff', '报告结论与证据差异', '报告生命周期', [9,10], [(JAVA+'service/ReportPublicationService.java','diff')],
    '如何区分结论变了和仅引用版本变了？', '检索诊断分数改变应算结论变化吗？|比较接口是否分别校验两个历史版本的权限？',
    '稳定主张映射|结论语义字段与证据身份分开|双方get应用当前ACL|旧产物缺失时标明legacy', '把诊断字段噪声当结论改变或跨系列比较。', '结构差异不能替代专家对语义影响的判断。', ['acl.json'])
add('report-download', '草稿与正式历史下载', '报告生命周期', [9,12], [(JAVA+'api/ResearchController.java','report'),(JAVA+'api/ReportPublicationController.java','download')],
    '草稿导出与正式版本下载应如何保持身份和版本边界？', '下载前能否只看一次列表权限？|下载的正文是否跟随当前runs自动变化？',
    '草稿按任务作者/admin读取|正式下载取冻结report_markdown|每次入口重查当前来源权限|Markdown编码与文件名受控', '旧页面保留链接但当前权限已撤销。', '下载成功不说明文件内容已被真人确认。', ['acl.json'])
add('data-export', '工作空间数据导出', '运维与数据', [5,6,9], [('backend/api/app.py','export_data'),('backend/services/runtime.py','export_user')],
    '业务数据导出与灾备备份为什么要分开？', '管理员导出是否应包含别人的个人偏好？|导出包能否直接代替整个服务恢复包？',
    '工作空间管理员入口|业务数据与当前subject个人设定分开|导出行为审计|备份另外包含配置/存储/身份等恢复材料', '把局部业务ZIP误当全部持久卷备份。', '导出能力不自动构成完整可用恢复证明。')
add('data-purge', '研究数据清理与外部资源回收', '运维与数据', [5,12], [('backend/services/runtime.py','purge_user'),('backend/services/run_cleanup.py','RunCleanup.stage'),('backend/services/run_cleanup.py','RunCleanup.reconcile'),('backend/services/retention.py','Retention.reconcile')],
    'SQL事务和外部删除如何避免半清理及孤儿数据？', '正式报告保护怎样阻止直接删除来源任务？|删除重试是否会误删仍被引用的文档版本？',
    '显式范围与确认头|正式依赖先校验|SQL事务与持久清理标记|对象/向量/检查点清理幂等且保护引用', 'SQL提交后外部存储不可用；缓存/版本长期增长。', '运行数据清理与本机任意文件删除不是同一授权。')
add('diagnostics', '静态诊断与真实运维探测', '运维与数据', [2], [('backend/api/app.py','check'),('backend/api/app.py','probe'),('backend/core/usage_governance.py','UsageGovernance.claim_probe')],
    '为什么成员查看服务配置不能顺带触发付费模型调用？', '自建空间admin为什么不等于平台运维？|多个运维重复点击时如何共享冷却缓存？',
    '静态检查不外发|真实探测平台subject名单默认关闭|持久冷却、单独额度和意图/完成审计|失败返回安全错误码', '重复诊断绕过研究任务额度或服务重启清空冷却。', '配置齐全不等于供应商当前可用；探测只证明该次请求。', ['governance.json'])
add('usage-budget', '所有付费路径统一账本', '运维与数据', [2], [('backend/core/usage_governance.py','UsageGovernance.reserve'),('backend/core/usage_governance.py','UsageGovernance.settle'),('backend/infrastructure/providers.py','Providers.post')],
    '如何在重试和多任务并发下保持先记账、后发请求？', 'usage缺失或进程崩溃后能否退回全部预留？|周期 rollover 是否抹除旧尝试记录？',
    '单事务预留所有维度|每次HTTP尝试累计|实耗/未知/待结算与历史预留分开|缺身份或账本不可用fail closed|评测另受更严传输账本限制', '上游已接受请求但本地不知道结果，或结算数据库故障。', '账本是费用暴露控制，不推算货币费用或保证上游计价。', ['governance.json'])
add('scope-intake', '研究范围澄清、保存、取消与过期', '新产品闭环', [8], [(JAVA+'api/ResearchIntakeController.java','preflight'),(JAVA+'api/ResearchIntakeController.java','confirm'),(JAVA+'service/ResearchIntakePolicy.java','requireConfirmed')],
    '为什么宽泛需求应在付费执行前确认时间、地区、对象与交付标准？', '关闭页面后如何继续未确认草稿？|确认内容被改写、过期或重放时如何处理？',
    '不调用模型的保守词组预检|持久owner-bound intake|确认绑定真实请求并转任务|明确问题可直接执行|未限定范围披露为假设', '同一client_request_id携带不同请求、重复确认及锁顺序冲突。', '确定性词组门不是完美语义消歧；需保持内容绑定。', ['scope-intake.json'])
add('collection-acl', '集合权限与文档归属', '新产品闭环', [9], [(JAVA+'api/CollectionController.java','grant'),(JAVA+'api/CollectionController.java','assign'),('backend/services/document_access.py','require_document')],
    '集合read/write、文档owner与空间admin之间如何取舍？', '撤回grant为什么不应剥夺owner的独立权限？|原文、检索、缓存、历史报告怎样保持一致？',
    '旧数据默认继承空间|restricted集合显式授权|owner/admin保留权限|源读取及发布链条统一当前SQL ACL', '旧缓存/旧向量或冻结报告成为撤权绕过入口。', '组件负例不等于全部攻击面已做渗透测试。', ['acl.json'])
add('source-rechecks', '公开来源显式重核验', '新产品闭环', [10], [('backend/api/source_rechecks.py','report_for'),('backend/api/source_rechecks.py','recheck')],
    '检测网页文本变化为什么不能叫事实重新验证？', '网页不可访问与事实不存在有什么区别？|如何保留旧正式报告而提示需要修订？',
    '报告当前可见性和资料ACL先检查|安全抓取、逐来源与整体时限|保存变化状态/时间/新文本摘要指纹|不改冻结正文与旧证据|变更提示needs_review', '网页已变、源站超时、只有摘要或比较合同不充分。', '哈希变化只证明提取文本变化，不代表事实真假或权威性。', ['source-revalidation.json'])
add('identity-deploy', '登录、OIDC与本机部署边界', '部署与恢复', [6,12], [(JAVA+'config/SecurityConfig.java','apiSecurity'),('backend/core/auth.py','TokenVerifier.verify'),('keycloak/deepresearch-realm.json',None),('compose.yaml',None)],
    'OIDC、浏览器PKCE与工作空间授权分别承担什么责任？', '身份服务管理台admin是否就是空间admin？|默认本机HTTP部署为什么不能直接视作公网配置？',
    'issuer/audience/有效期与签名校验|PKCE/state保护授权码流程|JWT只证明身份，成员关系决定空间权限|默认入口绑定回环地址', '登录回调、hostname、CSP或JWKS配置不一致。', '单机试用配置不等同公开互联网部署安全基线。')
add('service-privileges', '服务账号、对象存储身份与网络隔离', '部署与恢复', [6], [('scripts/verify_service_privileges.py',None),('scripts/verify_storage_identity.py',None),('compose.yaml',None)],
    '为什么迁移、Java、Python和Milvus不能共用最高权限凭证？', '告警组件为什么只挂载自己的token？|已有数据库如何无损切换受限运行账号？',
    '迁移角色与运行角色分开|存储按业务/Milvus划分身份|secret按文件/用途分发|必要服务网络与默认拒绝权限验证', '旧角色权限残留或新角色缺少业务必需写权限。', '角色脚本源码与隔离组件实测需分开陈述，不能推断日常部署已切换。', ['privileges-isolated.json','storage-isolated.json'])
add('backup', '自动备份、保留与异机复制接口', '部署与恢复', [5,12], [('scripts/recovery.py','backup'),('scripts/backup_images.py','verify_image_bundle'),('scripts/scheduled_backup.py','execute'),('scripts/host_task.py','run_logged_target'),('scripts/copy_backup.py',None)],
    '为什么备份成功标记必须在完整校验后才更新？', '业务数据、Keycloak与ClamAV材料是否都能恢复？|复制到其他路径是否等于已经做异机灾备？',
    '清单与哈希绑定备份项|完成备份及查询镜像缓存前强制验证当前平台OCI链和实际DiffIDs|归档独立恢复脚本及标准库校验sidecar|Windows任务与子进程隐藏且保留日志和真实退出码|保留策略保护当前可恢复包|定时任务防重入|复制接口保留明确目标和校验', '调度器重叠、部分存储未复制、缓存掩盖镜像包缺失或目标盘不可用。', '历史包的linux/amd64内容验证不是空缓存导入或整栈恢复；静默合成入口检查不是日常调度实测；同机复制接口不是异机灾备证明，RPO/RTO必须有测量前提。', ['backup-image-bundle.json','windows-host-task-silence.json'])
add('restore', '隔离恢复与可用性演练', '部署与恢复', [12], [('scripts/recovery.py','restore'),('scripts/drill_logical_recovery.py',None)],
    '如何在不覆盖日常业务卷的情况下证明备份可用？', '为什么只检查哈希或healthz不够？|恢复后需要验证哪些业务关系与证据哈希？',
    '单独Compose项目与卷|校验双数据库、账号关系、业务报告与引用|验证最小接单/取消和扫描|记录原容器状态与恢复耗时', '归档可解压但配置/身份/派生索引无法恢复。', '同机隔离恢复不是跨主机高可用或生产RTO承诺。')
add('observability', '健康、指标、日志、追踪与告警', '部署与恢复', [1,12], [(JAVA+'api/HealthController.java','ready'),('backend/core/observability.py','configure_telemetry'),('observability/alerts.yml',None),('scripts/verify_alert_delivery.py',None)],
    '进程活着、能够接单和业务质量良好为什么是不同信号？', 'Worker PID健康检查能否发现队列卡住？|如何演练告警通道又避免泄露prompt或密钥？',
    '存活/依赖就绪分层|队列年龄、重试、费用和任务终态可观测|安全字段日志与trace上下文|告警转发鉴权/去重/退避', '进程未退出但依赖、循环或告警投递已经失效。', '可观测工具齐全不等于已经证明24小时稳定性。', ['alert-delivery-isolated.json','observability-acceptance.json'])
add('contract-schema', '跨语言契约和兼容迁移', '验证与交接', [11], [(JAVA+'service/ResearchCommandContract.java',None),('backend/core/recovery_contract.py',None),('migrations/versions/0017_product_governance.py','upgrade')],
    'Java业务控制面与Python执行面怎样避免同一状态被双写覆盖？', '旧检查点合同不兼容时能否直接继续？|为什么数据库迁移必须保留历史顺序？',
    '明确表字段写入所有者|版本化命令和恢复合同|旧代次及不兼容产物拒绝|固定跨语言用例与向前兼容迁移', '一侧升级而另一侧仍按旧字段/状态理解。', '共享runs/events是渐进拆分，不是独立数据库微服务。', ['reliability.json'])
add('testing-delivery', '聚焦测试、预算评测和最终交付', '验证与交接', [0,2,11,12], [('.github/workflows/ci.yml',None),('.github/workflows/security.yml',None),('scripts/bounded_evaluation.py','Budget.reserve'),('scripts/evaluation_scope.py','scoped_evaluation')],
    '如何证明修改有效，同时避免重跑无关功能或无界付费评测？', '本地组件通过与最终SHA CI通过有什么区别？|自动模型评分与真人盲评应怎样分账？',
    '只测修改及直接影响路径|所有评测重试共用更严传输账本|固定开发/留出题与阈值|最终SHA分别核对CI/Security/质量证据|人工职责和独立真值不推断', '复用旧评分被误标新模型通过，或未绑定最终提交。', '当前交接包不宣称真人评分、外部用户价值、HA或通用准确率。')


add('usage-visibility', '个人与空间费用可见性', '运维与数据', [2,9], [('backend/api/usage.py',None),('frontend/src/UsageBudget.vue',None),(JAVA+'api/AgentProxyController.java','proxy')],
    '费用面板如何避免泄露其他成员或其他空间的调用记录？', '空间管理员能否修改或查看平台总额度？|已知usage、尚未返回和未知结果为什么不能合计成已结算费用？',
    '服务端按当前subject及选中空间汇总|平台总额仅向明确运维subject提供|预留与已知输入输出分开|持久原子预留对所有供应商路径生效', '工作空间切换、无usage的网络失败、权限变化与并发扣额。', '供应商usage与保守预留不等同人民币账单或供应商最终退款。', ['governance.json','usage-postgres.json','real-provider.json'])
add('document-ui-scope', '资料界面与切换空间后的异步响应', '资料处理', [9,12], [('frontend/src/useDocumentLibrary.ts',None),('frontend/src/api.ts',None),('frontend/src/CollectionAccess.vue',None)],
    '切换工作空间时，旧请求的资料和错误为什么不能覆盖新空间页面？', '只丢弃旧响应是否就足以防止请求发往错误空间？|异步上传结束后应刷新哪个空间？',
    '资料状态抽到独立组合函数|epoch丢弃旧响应与错误|请求显式捕获发起空间|api保留调用方明确的工作空间头|服务端仍执行当前权限', '慢请求跨越空间切换；迟到刷新导致误导或错误上下文。', '前端隔离改善展示与请求正确性，不能代替服务端鉴权。', ['roles-and-scope.json','acl.json'])
add('runtime-image-security', '运行镜像漏洞与升级兼容证据', '部署与恢复', [6,12], [('scripts/runtime_ci_inventory.py',None),('scripts/scan_runtime_images.py',None),('frontend/Dockerfile',None)],
    '如何把包扫描、源码修复、兼容性和实际交付分开证明？', '重编后的主模块变成devel或扫描失败能否算零漏洞？|源码已修但漏洞库仍报同一CVE时应如何处理？',
    '编排导出所有预制和自建运行镜像|固定工具和漏洞库分别保存原始报告|保留真实模块版本及包识别范围|新旧数据副本与回滚验证|未知与未处置HIGH保持门禁失败', '升级改变OIDC受众、存储格式或插件签名；扫描器归因与真实修复状态可能不一致。', '本地普通镜像和独立native门禁、获批MinIO/Grafana精确判定分别有证据；最终SHA冷CI仍待执行，不能把局部通过写成零风险。', ['application-image-security.json','vendor-fresh-scan.json','minio-ci-integration.json','grafana-artifact-governance.json','native-jvm-ci.json','unified-candidate.json'])
add('ci-cumulative-budget', '跨CI重跑的累计付费预算', '验证与交接', [0,2,11], [('scripts/ci_budget_ledger.py','contract'),('scripts/reconcile_ci_budget.py','reconcile'),('.github/workflows/answer-quality.yml',None)],
    '为什么每个CI运行新建账本会绕过用户允许的总调用次数？', '失败或重跑的原始artifact缺失时如何处理？|PR合并提交与分支提交不同，能否直接复用前置安全结果？',
    '保留历史逐次请求和来源run/attempt/SHA|所有新增尝试按同一授权累计|artifact摘要与前缀校验防重置/漏算|未知usage保守保留预留|同源码普通CI和Security成功后才开始付费', '工作流并发、artifact过期、取消或只重跑失败job；这些情况不能把未知消耗视为零。', '离线必要预算下界不保证通过；最终余额和质量结论以真实GitHub artifact为准。', ['ci-budget-assessment.json'])


add('approved-artifact', '固定镜像判定与归档加载', '部署与恢复', [0,6,12], [('scripts/image_disposition.py','verify_archive'),('scripts/grafana_image_disposition.py','load_binding'),('scripts/oci_artifact.py',None),('scripts/fetch_approved_image.py','load_image'),('scripts/runtime_ci_inventory.py','matrices')],
    '为什么获批修复判定必须绑定实际运行的同一个镜像？', '重新构建相同源码能否直接继承旧镜像批准？|归档不完整、新漏洞或审批到期时能否回退可变标签？',
    '固定OCI索引、平台清单、config与实际解压DiffIDs|逐层摘要及whiteout后有效二进制绑定|分段远程校验后再验证完整归档|正常远程加载及缓存先核实际Release已发布|原始全级别告警与精确判定分账|各制品独立授权来源与到期校验|新发现或缺失证据失败关闭', '流水线重建新镜像却误用旧授权、分段不齐全、草稿或缓存绕过发布生命周期，或扫描失败被包装成零告警。', 'MinIO仅一个精确FIXED；Grafana新subject独立授权FIXED2/NOT_AFFECTED16，仍保留原始中低危及Unknown；私密draft远程审计不是正式发布或最终SHA安全验收。', ['minio-ci-integration.json','minio-remediation.json','grafana-artifact-governance.json','grafana-remote-transport.json','oci-reuse-review.json'])
add('native-runtime-coverage', '解释器与JVM的独立覆盖', '部署与恢复', [6,12], [('scripts/verify_python_runtime.py',None),('scripts/check_native_jvm.py',None),('business-backend/Dockerfile',None),('Dockerfile',None)],
    'OS和依赖包零高危为什么仍可能漏掉Python或Java运行时？', '如何防止只修改版本文本伪装修复？|签名来源、真实安全回归和漏洞数据库各能证明什么？',
    '确认扫描实际识别的组件|官方签名和固定源码归档|实际进程版本与JRE文件树对比|原生CPE与完整包扫描分账|受损CA拒绝和合法TLS正例同时通过', '旧CPython扫描为零但仍接受受损证书；原JVM不在Trivy的OS/JAR识别范围内。', '版本和回归验证限于已列范围，不是所有漏洞不存在或一般可用性证明。', ['application-image-security.json','full-python-build.json','ci-python-runtime.json'])
add('monitoring-state-migration', '监控状态卷迁移', '部署与恢复', [5,12], [('scripts/migrate_monitoring_storage.py',None),('compose.yaml',None)],
    '把匿名监控卷改成命名卷为什么可能丢失数据？', '如何区分原数据在匿名卷还是容器可写层？|为什么迁移必须先停写且在新编排创建目标卷之前执行？',
    '核对实际容器归属与挂载|冷快照保留文件摘要及uid/gid/mode|只创建尚不存在的精确目标卷|校验后由新编排使用|保留原材料供回滚', '直接启动新命名卷产生空silence或丢指标；运行中复制并不提供一致快照。', '隔离夹具迁移通过不代表日常卷已经迁移；日常操作仍须部署窗口。', ['monitoring-volume-migration-isolated.json'])


def source_entry(path, symbol):
    file = ROOT / path
    content = file.read_text(encoding='utf-8')
    line = 1
    if symbol and file.suffix == '.py':
        matches = []
        def walk(node, prefix=''):
            for child in ast.iter_child_nodes(node):
                if isinstance(child, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                    qualified = prefix + child.name
                    if qualified == symbol or ('.' not in symbol and child.name == symbol):
                        matches.append(child.lineno)
                    walk(child, qualified + '.')
                else:
                    walk(child, prefix)
        walk(ast.parse(content))
        if not matches:
            raise ValueError(f'Missing current Python symbol: {path}:{symbol}')
        line = min(matches)
    elif symbol:
        pattern = re.compile(r'^\s*(?:(?:public|private|protected|static|final|synchronized)\s+)*[A-Za-z_][\w<>,?\[\]. ]*\s+' + re.escape(symbol) + r'\s*\(')
        matches = [number for number, value in enumerate(content.splitlines(), 1) if pattern.search(value)]
        if not matches:
            raise ValueError(f'Missing current Java symbol: {path}:{symbol}')
        line = matches[0]
    raw = file.read_bytes()
    return {'path': path, 'symbol': symbol or 'module/configuration', 'line': line,
            'source_sha256': hashlib.sha256(raw).hexdigest(),
            'source_text_lf_sha256': hashlib.sha256(raw.replace(b'\r\n', b'\n')).hexdigest(),
            'hash_boundary': 'source_sha256 binds raw workspace bytes; source_text_lf_sha256 additionally permits explicit CRLF/LF comparison across checkouts, without claiming raw-byte identity'}


def build():
    for feature in FEATURES:
        feature['source_entries'] = [source_entry(*reference) for reference in feature.pop('references')]
        feature['evidence_classification'] = {
            'source': 'current source entries resolved, not inferred from old interview text',
            'current_measurement': 'only the named component receipts and their explicit cases; other feature behavior not re-executed by this handoff builder',
            'historical': 'README and dated delivery documents are historical context, not silently upgraded to current evidence',
            'personal_responsibility': 'unknown; coordinator must obtain human confirmation before first-person ownership claims',
            'future_design': 'improvements or scaling plans require new implementation/evidence and cannot be narrated as delivered'}
    changes = {str(number): [item['id'] for item in FEATURES if number in item['change_ids']] for number in range(13)}
    result = {'artifact': 'complete-functional-guide-handoff', 'date': '2026-10-26',
              'baseline_git_head': '8768aa6c1e4f0d24a8c355ce769112e98b3b8c10',
              'source_git_head': subprocess.check_output(['git','rev-parse','HEAD'], cwd=ROOT, text=True).strip(),
              'final_delivery_sha': None, 'source_state': 'working tree; coordinator must refresh source lines after final code changes',
              'personal_material_mapping': '由协调方映射到既有导学/面经；本工具未查找、读取或修改共享简历与个人面经',
              'human_review': 'not_performed', 'external_user_feedback': 'not_established',
              'feature_count': len(FEATURES), 'change_0_12_mapping': changes,
              'learning_order': ['身份与空间','数据写入所有权','任务状态/命令与恢复','资料扫描/解析/版本','检索与证据','报告审核/修订/权限','备份恢复与评测证据'],
              'features': FEATURES,
              'handoff_rules': ['每个功能保留一个主问与至少两个追问；答案要点继续展开为问题、机制、失败兜底和证据边界。',
                                '不按简历篇幅删掉非简历功能；会话/记忆/运维/数据清理同样属于项目能力。',
                                '源码事实、组件实测、历史记录、个人职责未知、未来设计分开标记。',
                                '最终SHA及真实报告尚待协调方绑定；同模型评分不是独立真值。'],
              'blind_review_package': 'blind-review/README.md; prepare_blind_review.py; reviews.csv; manifest.example.json'}
    target = Path(__file__).with_name('guide-handoff.json')
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(json.dumps({'features': len(FEATURES), 'target': str(target), 'all_followups_at_least_two': all(len(row['followup_questions']) >= 2 for row in FEATURES)}, ensure_ascii=False))


if __name__ == '__main__':
    build()
