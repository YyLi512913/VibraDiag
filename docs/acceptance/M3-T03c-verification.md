# M3-T03c Agent方法任务的受控知识引用

> 日期：2026-10-31；接续[T03b2](M3-T03b2-verification.md)。本轮为方法任务只读取证，不接DeepSeek决策/生成。

## 交付范围

新explain_method任务使用rules_knowledge_v1 / rules-knowledge-v1，图节点为prepare→knowledge。既有rules_graph_v1/rules-graph-v1数值任务和旧方法任务保留原版本；没有自动迁移旧检查点、补跑旧方法检索或改写旧任务终态。检查点的节点与光标按绑定版本分别校验，仍只保存任务ID/图版本，不保存正文、角色快照或模型输出。

POST /api/v1/agent-tasks新增可选knowledge_scope，包含equipment、component、可选asset_id，仅用于方法任务。新方法任务不接measurement_revision_id，知识引用不建立采集根。没有范围时给出有限组织范围选项，由原发起人明确选择；设备范围必须显式传入且具有当前读取权。不从自然语言猜设备、权限或部件。

方法查询使用description；超过2000字符或不符合查询文本约束时挂起knowledge_query_required，必须显式补充合规短查询，不静默截断。普通恢复文本不能改变既有设备/部件范围、采集参数或确认状态。模型、开发内容标志、任意task/call绑定等不作为公开覆盖字段。

search_published_knowledge登记为现有agent_calls中的一个逻辑工具调用，消耗工具预算而非LLM决策轮次。实际检索固定include_developer=false，继承T03b2发布/组织/设备/适用范围/索引身份及运行版本过滤；调用前、模型阶段之间和回执提交前检查任务身份、租约、当前范围与剩余预算，Embedding等待上限进一步取剩余任务时间与60秒的小值。

0017未新增业务表，仍为42张业务表与4张检查点表。它扩展动作/来源种类和图版本，并为knowledge_retrieval_runs追加可空agent_task_id/agent_call_id及唯一调用绑定。数据库插入触发器检查运行状态、租约、发起人、方法图、调用状态和固定查询/过滤载荷；绑定和回执不可改写，公开搜索不能传入这些内部字段。

检索回执已提交但调用结果/检查点未提交时，恢复按同一call_id找到原回执，不重新调用Embedding、不新增逻辑工具预留、不把响应未知当作免费重试。新查询失败/容量处理保留原工具预算；503明确挂起等待人工重试，取消和过期租约拒绝迟到结果。运行时间按现有保守规则累计，人工等待不重置预算。

## 引用与输出

来源必须是本任务、本调用的回执命中，不能借他人、其他任务或普通搜索的UUID建立引用。每次引用重读当前发布、活动索引、权限、配置身份和不可变来源，核对源hash、块hash和行/字符锚点。任务草稿提交时在同一事务中验证引用，并锁定文档/版本/活动头及相应设备授权，避免引用检查与发布切换交错。

方法资料只能作为method_explanation + knowledge_chunk；不允许冒充numeric_fact、tool_run或采集证据。草稿引用还经过现有纯策略校验，错误角色或未解析UUID明确失败。完成后每次读取任务重新验证；撤回、索引头替换或设备撤权会隐藏草稿/资料正文，或按任务范围直接拒绝访问。任务原终态/历史账本保留，原文历史仍通过独立授权接口追溯。

本轮返回“已定位待复核资料”的固定观察及knowledge_references，不自动把检索正文复制为建议或结论。每条资料保留真实块ID、索引代、来源元数据、原文位置/hash及untrusted_content=true；正文中的脚本、外链或工具指令不执行、不提升权限、不决定下一动作。

只要相关性尚未通过独立准入，有命中也返回completed_degraded、knowledge_relevance_not_qualified和未发布草稿；没有有效来源则refused，不编造解释。model_called表示LLM决策，本轮保持false；embedding_called单独表明该回执是否实际调用词嵌入。数值图的行为不因此扩展为知识增强诊断。

## 验证

[Agent知识专项](../../backend/tests/integration/test_agent_knowledge.py)使用真实PostgreSQL/LangGraph、模拟Embedding输出验证范围澄清、短查询补充、引用/工具账本/预算、开发排除、撤回和索引替换、查询中撤回、取消、任务剩余时间、两个进程恢复窗口、网络失败显式重试、恶意正文、引用角色/跨任务/数据库绑定、设备撤权以及旧方法图不自动升级。

[真实验证脚本](../../backend/scripts/verify_agent_knowledge.py)通过HTTP显式审核/发布开发自编MD并调用真实BGE-M3建索引；关闭自动消费者以隔离测试任务ID，由独立自建进程调用同一claim/execute/recover服务路径。API与正常执行探针交替持有既有单进程租约，不绕过runtime.ensure。首次任务先通过HTTP澄清范围；在真实检索回执提交、工具结果与检查点未提交时暂停自建执行进程，实际终止该进程并重启API；再显式恢复与HTTP resume。恢复执行进程的模型地址真实不可达，仍须复用唯一回执完成，证明没有第二次Embedding或逻辑调用。

另一任务在回执提交后由探针显式释放旧执行器的全局租约并保留延迟结果；API取得租约后通过HTTP取消，再释放旧进程的延迟返回，验证迟到结果不能形成草稿。这是固定的失租约故障注入，不是允许生产部署多执行器。随后撤回原文，检查原任务草稿/正文隐藏、原文历史可追及新任务无来源时不调用Embedding而拒答。只终止脚本自己持有句柄的进程；[探针](../../backend/scripts/m3_knowledge_probe.py)仅允许专用测试库及固定.runtime目录中的任务，不由API或消费者导入。

```bat
.venv\Scripts\python.exe backend\scripts\migrate.py
.venv\Scripts\python.exe backend\scripts\export_m3_agent_contract.py
.venv\Scripts\python.exe -m pytest -c backend\pyproject.toml backend\tests -q --junitxml=experiments\m3-t03c-tests.xml
.venv\Scripts\python.exe -m pytest -c backend\pyproject.toml backend\tests\integration\test_agent_knowledge.py -q --junitxml=experiments\m3-t03c-agent-tests.xml
.venv\Scripts\python.exe backend\scripts\verify_agent_knowledge.py
```

HTTP/进程验证应在所有pytest结束后运行；使用独立测试库，实际调用本地模型。证据见[全量JUnit](../../experiments/m3-t03c-tests.xml)、[知识专项JUnit](../../experiments/m3-t03c-agent-tests.xml)、[边界回归](../../experiments/m3-t03c-boundary-tests.xml)和[实际知识/进程回执](../../experiments/m3-t03c-knowledge.json)。当前机器契约更新为m3-t03c-runtime-contract-v1；早期T01回执中的旧契约hash仍是当时快照，不改写为新实现的证据。

完整后端回归682项通过，0失败、0错误、0跳过；最后的执行活动审计和输入边界调整后，知识专项17项、契约/原文/索引/数值任务与持久图/迁移权限等边界133项再次通过。各轮统计分别保留，不把模拟模型约束检查作为真实模型效果样本。

## 限制与下一步

测试资料为开发自编internal_note，应用内显式自审不代表独立工程师或厂家认可。本轮没有生成完整方法回答、验证检索相关性、开放维修动作、知识管理前端或通过工厂试用。取消检查保护持久副作用，但不硬终止Ollama服务端推理；模型客户端等待受任务剩余预算限制，数值子进程的硬终止能力不自动适用于模型服务。

下一步T04接DeepSeek受限动作选择、受控外发上下文及未发布草稿生成/校验，再由T05实施Agent/检索正式效果评测。数值任务的可选知识增强也随受限决策明确适用范围；在资格、参数或相关性不足时继续保留拒答，不用当前引用命中或规则图运行成功替代诊断准入。

Embedding活动按已提交回执确认：embedding_activity区分confirmed_called、not_called和unknown。取消或失败时没有成功回执不能据此断言模型从未执行；model_called仍单独表示LLM决策。
