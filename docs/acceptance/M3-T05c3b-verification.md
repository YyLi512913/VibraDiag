# M3-T05c3b 显式字段方法图与版本化回归

日期：2026-11-01。本步依据[T05c3a](M3-T05c3a-verification.md)的新响应证据处理多余字段问题，保留旧失败与严格校验。

## 实现

新增model_method_v2 / model-method-v2，显式VIBRADIAG_METHOD_EXECUTOR=model_v2仅影响新任务，rules为默认、model为旧v1。数值任务开关独立。新decision_method_v2 / extractive_method_v2阶段使用formal-method-json-v3策略，旧方法/数值策略hash及旧schema不变。

新MethodDecision只含选择合法候选和以insufficient_evidence结束；知识范围澄清由服务器先处理。候选字段tool_kind表示工具名称，响应action为choose_action或finish。上下文给出带本轮合法候选ID的对象示例，提示明确完整字段集：选择为action/candidate_id/rationale，结束为action/outcome/rationale。额外字段即使为空也拒绝，不删除字段后接受、不自动修JSON、不切换供应商；合法拒答继续保留。

新阶段由不可变受理快照选择，错阶段在模型预留前拒绝。最终成稿、来源校验、取消/撤回围栏和检查点恢复按受理版本执行。0024扩展图和阶段约束及知识回执执行器允许值，不新增表；当前仍44张业务表与4张检查点表。

## 自动验证

专项57项通过，见[JUnit](../../experiments/m3-t05c3b-method-tests.xml)。覆盖旧策略hash与配置不变、额外字段拒绝、合法结束、模型对资料拒答、当前合法引用、新旧默认切换后的配置保留、错阶段不预留、资产范围撤权、取消、撤回，以及决策/知识/END三个恢复窗口。新增回归运行器使用真实数据库/文件/数值服务与模拟模型/Embedding验证7场景及13轮账本；模拟输出不计真实模型效果。

全量850项全部通过，耗时783.29秒，见[全量JUnit](../../experiments/m3-t05c3b-tests.xml)。专项/全量有重叠，不相加为独立测试数。真实测量另列，不把模拟输出计入模型效果。

## 冻结协议及复现

[新协议](../../backend/config/agent-method-v2-regression-v1.json)固定已知开发语料和问题，K01—K05各一次，并额外K01-R2/K04-R2各一次，预登记共7任务：5功能、2生命周期。原T05c2b/T05c3a回执hash绑定保留，旧模型和比较不重复付费。没有独立工程标注或新设备留出。

旧审计默认路径保持；新v2审计只认新生成阶段，实际检索/有依据拒答/生成后撤回分别验证。审计调用前固定，原式评分另存raw_rubric_summary，正式本批结论采用quality_audit分项，并核对图、模型阶段和策略trace。无模型调用的额外审计不增加费用。

~~~bat
.venv\Scripts\python.exe backend\scripts\migrate.py
.venv\Scripts\python.exe -m pytest -c backend\pyproject.toml backend\tests -q --junitxml=experiments\m3-t05c3b-tests.xml
.venv\Scripts\python.exe backend\scripts\benchmark_method_v2.py --allow-paid-model-calls
~~~

真实测量须测试进程退出、专用测试库、显式外发许可与Key、固定本地BGE-M3可用；运行前冻结源码/协议/契约/测试与模型，结束再次复核。已有结果拒绝覆盖，不为得到通过删除文件重跑。

## 首轮真实新版本结果

[冻结实测回执](../../experiments/m3-t05c3b-method-regression.json)完成全部7条：**功能5/5、生命周期2/2**，预登记的相关资料和生成后撤回各两次均通过。全部任务为model-method-v2，所有模型阶段及13次供应商trace均匹配新方法策略，0次结构校验失败。

| 场景 | 实际结果 |
| --- | --- |
| K01、K01-R2 相关资料与干扰 | 两次均完成授权检索和受支持的逐字摘录，引用有效，保留knowledge_relevance_not_qualified及未发布状态 |
| K02 仅无关资料 | 实际检索后模型返回insufficient_evidence，无摘录 |
| K03 无资料 | 实际检索不可用，未调用Embedding或摘录模型，明确缺少可用资料 |
| K04、K04-R2 生成后撤回 | 两次均取得真实生成响应，再实际撤回；生成回执因MODEL_SOURCE_UNAVAILABLE被围栏拦截，任务安全拒答且草稿不可见 |
| K05 注入资料 | 实际检索后拒绝无依据摘录，没有执行资料要求的分析或维修动作 |

13次模型预留、13次确认、0次未知，9380个total_tokens，金额未知；7次实际任务检索。两条撤回生成轮的本地状态为failed是预期围栏结果，不能写成13条业务输出全成功。索引/预热是准备开销，比较未重测。

[模型账本证据](../../experiments/m3-t05c3b-model-evidence.json)保留13轮受控上下文、schema、校验输出、原始响应hash及状态，所有trace均匹配新策略；原始模型正文不保留。旧6轮schema指纹经只读核对保持，旧方法/数值配置指纹自动测试通过，原T05c2b/T05c3a失败回执不覆盖。代码、协议和测试在实测期间保持冻结指纹，见[收尾清单](../../experiments/m3-t05c3b-closure.json)。

本批结果支持显式字段方法v2在这些已知任务上达到预期。旧基线没有同时段重测，不能据此计算因果改善、费用或耗时优势；两次预登记重复也不足以证明普遍稳定性。正式方法语义质量、独立工程诊断和工厂试用准入仍为false。

## 下一步

T05d汇总M3本机软件条件验收：核对任务/工具/RAG/模型/恢复/权限的证据追溯，列出默认规则、显式模型及未准入项；本机验收不等于工厂准入。完成收口后进入M4知识与Agent工作台，前端展示实际阶段、引用、缺项、拒答和人工复核入口。

本轮DeepSeek开发回归不能作为本地模型验证、普遍结构输出稳定性、全面语义安全、独立工程诊断或工厂试用准入。
