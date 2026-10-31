# M3-T05c1 条件作用域修正与已知开发回归

> 2026-10-31；优先处理[T05b](M3-T05b-verification.md)发现的F02提前结束，方法/比较评测后续承接。

## 原始证据

从持久模型账本读取F02的4轮实际context与validated_output，保存在[原始决策回归夹具](../../backend/tests/fixtures/t05b-f02-decision.json)，绑定原始实测回执hash。最后一轮仍有context_bound包络候选；频率实际missing_requirements为空，包络实际缺verified_acquisition_bandwidth与qualified_numeric_profile。模型结束理由明确把后两项套用了任务的频率不确定量停止条件，并判断剩余候选不能补齐。这提供该次错误解释的直接证据，不证明所有提前结束都由同一原因导致。

旧T05b回执、结果与模型策略不改写，不将新回归称独立验证，也不删除模型原有拒答能力。

## 修正及版本边界

新增model_numeric_v2 / model-numeric-v2，显式VIBRADIAG_NUMERIC_EXECUTOR=model_v2仅影响新任务；rules仍为分发默认，model继续表示原v1，方法图不变。0021只扩展图绑定、模型阶段和租约触发器允许值，仍44张业务表与4张检查点表。

新策略formal-numeric-json-v2与numeric_decision_v2独立固定到受理快照和模型账本；原数值v1与方法策略hash保持不变。检查点光标、候选参数、输入/权限/租约、预算和引用仍由服务器控制，旧任务不升级、不重写旧检查点。

模型上下文保留原始missing_requirements，额外标记执行阻断、频率匹配前提、诊断发布限制和未知项。只有真实completed/ok、工具为analyze_envelope且明确mode=exploratory时，两个已知诊断限制才标入diagnostic_release_limitations；unavailable、diagnostic_evidence模式及未知项不会被同样解释。频率不确定量直接取实际工具summary的布尔字段，缺失/非布尔为null，不由“缺项列表为空”推断。

候选标为探索机会并禁止诊断发布，不保证下一工具必成功。策略说明频率条件不能套用到任意包络缺项，绑定context的后续包络可能补充先前无绑定结果缺少的信息。未知/实际执行阻断仍须保留；合法finish/refuse继续接受，不通过强制跑完工具获得评测绿灯。

## 回归设计与复现

自动回归：全量795项、作用域/版本/恢复/契约/迁移专项43项全部通过，无失败、错误或跳过。真实模型测量单独记录。

[单元测试](../../backend/tests/unit/test_numeric_requirement_scope.py)以原始F02为回归证据，检查缺项无损分类、不可用/诊断模式不被“放行”、未知项保留、实际不确定量字段及v1策略hash不变。[集成测试](../../backend/tests/integration/test_numeric_scope_v2.py)运行真实数据库和工具，检查条件完整继续、不完整停止、合法拒答、旧图/配置保留、新阶段隔离、取消及模型/工具/END提交窗口恢复。

[新冻结协议](../../backend/config/agent-scoped-regression-v1.json)只复测v2 Agent：原6个用例各一次，并额外各复测一次F02/F04完整条件任务，共8条（6功能、2生命周期）。这是已知失败的开发回归，所有波形此前都已使用，不能称独立测试、复现稳定性证明或工厂效果。两次重复是预先登记的任务，不在失败后自动重试。旧规则/单次路由不重复付费，其历史成绩只作背景，不作同时段对照或因果证据。

复用的评测运行器新增可选参数，原T05a/T05b默认路径保持。新CLI调用前冻结代码、协议、模型、契约与测试记录，已有公开回执拒绝覆盖；所有模型输出均由本地schema/候选校验，不回退供应商。

~~~bat
.venv\Scripts\python.exe backend\scripts\migrate.py
.venv\Scripts\python.exe backend\scripts\export_m3_agent_contract.py
.venv\Scripts\python.exe -m pytest -c backend\pyproject.toml backend\tests -q --junitxml=experiments\m3-t05c1-tests.xml
.venv\Scripts\python.exe backend\scripts\benchmark_agent_scoped.py --allow-paid-model-calls
~~~

全部pytest退出后才能运行真实脚本。结果分别见[专项JUnit](../../experiments/m3-t05c1-scope-tests.xml)、[全量JUnit](../../experiments/m3-t05c1-tests.xml)、[新实测回执](../../experiments/m3-t05c1-scoped-regression.json)。本轮仅真实DeepSeek，v2更长上下文可能触及本地模型字节限制，本地准入未验证，不宣称两端效果等价。

## 首轮新版本实测

真实DeepSeek按冻结协议完成8条，全部满足开发预期：6条功能任务（包括F02/F04各两次）完成规定链路，2条生命周期边界通过。完整条件下每条均执行5个实际工具，缺频率不确定量时仍只执行基础/频率并停止；6份带引用草稿的来源链接均有效，取消无草稿、撤确认后挂起且无后续执行。

全部任务图为model-numeric-v2，34次模型调用均有匹配新策略hash的DeepSeek回执，实际28次工具执行、39906个供应商total_tokens；金额成本未知。原T05b的失败与v1策略不变，旧基线没有在本轮重新运行，因此不做同时段耗时、费用或总体准确率比较。

这证明本批已知开发任务与预先登记重复通过，不证明少量重复的稳定性、独立泛化或所有提前结束均修复。波形已开发暴露，独立留出审计仍被阻断，诊断和工厂准入仍为false。继续保留rules默认，model_v2仅显式启用。

[收尾指纹清单](../../experiments/m3-t05c1-closure.json)保留新原始回执hash，只在实测后补充验收、设计与阶段索引文档；运行代码、协议、原F02证据和测试回执保持冻结指纹，不为文档更新重复模型调用。

## 下一步

先按新实测结果判断修正是否达到本批开发预期；仍需更大冻结集合、重复性与独立标注，不能从少量重复认定已解决全部提前结束问题。之后T05c2补方法资料相关/不相关/无来源拒答与比较任务矩阵，再汇总M3本机条件验收。规则默认及诊断/工厂准入阻断保持不变。
