# M3-T04b1 方法任务的模型驱动执行图

> 日期：2026-10-31；承接[T04a](M3-T04a-verification.md)，数值任务模型候选编排留T04b2。

## 范围与配置

VIBRADIAG_METHOD_EXECUTOR显式选择rules或model，分发包默认rules，避免无意外发。选择model后，新explain_method任务使用model_method_v1 / model-method-v1；数值任务和既有方法任务保留原图。用户不能从请求正文覆盖执行器/供应商。模型任务在受理事务中固定model_configuration（供应商、模型、可获知的digest及提示策略hash），默认配置变化不修改已接受任务。

云端仍要求私有key与外发开关；本地仍要求已安装的固定digest。缺权限或配置不满足不自动换供应商。0019扩展图版本和任务知识回执触发器，不新增业务表；当前44张业务表及4张检查点表。

## 执行路径

prepare确认范围与短查询，必要时在任何模型调用前挂起。decide通过T04a网关取得并校验已登记候选或结束动作；选择检索才进入knowledge，用服务器载荷调用既有授权检索。模型选择结束时不强行跑工具。

knowledge没有当前有效来源就直接拒答，不调用摘录模型；有来源才进入compose，模型只能选择真实source_id及逐字quote。两个模型阶段最多两轮，当前只有一个检索候选、最多一个逻辑工具调用；沿用任务8模型/20工具/120秒上限，不把单方法路径宣称为数值多工具自主诊断。

图只保存任务ID、图版本及执行光标，决定和摘录均从模型轮账本重读，工具来源从实际检索回执重读。候选在执行前重新生成/验证，模型不能指定任意工具参数。图结束后单事务重查身份、任务租约、模型输出hash/上下文、当前发布/索引及引用角色，才构造未发布草稿。

有效摘录用服务器固定前缀明确标注原文，不生成开放式故障判断；相关性未准入时状态为completed_degraded，保留knowledge_relevance_not_qualified。决策失败则拒答且不检索；摘录失败时只保留服务器固定标注的已授权资料上下文，并记录model_generation_unavailable。模型若认为资料不足，可以无摘录拒答。没有切换模型或静默规则代答。

## 版本、恢复与失败

原rules-graph-v1和rules-knowledge-v1不升级；新图的检查点允许节点与光标独立校验，取消和旧任务租约仍被数据库围栏拒绝。模型/工具结果已提交而检查点未知时，节点重入只读已完成账本，不重复请求。图END而任务终态未知时，恢复直接重新验证并完成草稿。

失败/执行未知的模型轮不自动重试或假装成功，当前图拒答/降级并保留原因；新任务可在显式配置变更后重新受理。知识查询的既有受控恢复不重置工具预算。取消阻断迟到结果但不硬终止供应商服务端推理；来源撤回、活动索引变化、权限撤销可隐藏已保存草稿或拒绝范围访问。

阶段提示升级为formal-method-json-v2，分别说明“检索前规划”和“检索后摘录”，避免把尚未检索误判为不能选择检索。T04a本地提前拒答的回执保持原样，不改写为成功；新提示/新图的少量开发结果不构成模型质量准入或提示改进的因果证明。

## 验证

自动回归结果：全量733项、模型图专项15项、兼容边界89项全部通过，无失败、错误或跳过。以下真实模型验证另行记录，不以模拟结果代替。

[模型图专项](../../backend/tests/integration/test_model_method_graph.py)使用真实PostgreSQL/LangGraph及模拟模型，覆盖选择/结束、错误决策不执行、摘录失败降级、合法拒答、范围澄清、创建时模型配置、规则模式保留、取消/撤回、已完成模型轮与检查点窗口恢复、END窗口恢复及未知结果不重试。

[实际HTTP/进程脚本](../../backend/scripts/verify_model_method_graph.py)使用真实DeepSeek/BGE-M3、已发布自编英文资料和公共任务接口。API关闭自动消费者以隔离任务ID，自建[探针](../../backend/scripts/m3_model_graph_probe.py)调用同一claim/execute/recover路径，API/探针交替持有单进程租约。它在真实摘录轮完成、节点检查点未提交时暂停并实际终止执行进程；重启并显式恢复后，关闭外部调用、使Embedding地址不可达，仍须从两条模型回执和一条工具回执完成同一逐字摘录草稿，预算预留仍为3。

另一任务在真实决策完成后模拟旧执行器释放全局租约，API取得租约并取消；释放旧调用后不得执行工具或发布草稿。再创建显式本地Qwen任务运行相同方法图，单独记录完成或拒答，断言无云端回退。最后撤回资料使原草稿失效。所有进程终止只针对自己持有句柄的进程；生产没有故障注入入口。

```bat
.venv\Scripts\python.exe backend\scripts\migrate.py
.venv\Scripts\python.exe backend\scripts\export_m3_agent_contract.py
.venv\Scripts\python.exe -m pytest -c backend\pyproject.toml backend\tests -q --junitxml=experiments\m3-t04b1-tests.xml
.venv\Scripts\python.exe -m pytest -c backend\pyproject.toml backend\tests\integration\test_model_method_graph.py -q --junitxml=experiments\m3-t04b1-model-graph-tests.xml
.venv\Scripts\python.exe backend\scripts\verify_model_method_graph.py
```

真实脚本须等全部pytest结束，会实际使用已授权的云端额度及本地模型。结果见[全量JUnit](../../experiments/m3-t04b1-tests.xml)、[模型图专项](../../experiments/m3-t04b1-model-graph-tests.xml)、[兼容回归](../../experiments/m3-t04b1-boundary-tests.xml)与[实际图/进程回执](../../experiments/m3-t04b1-model-graph.json)。普通单元/集成测试不隐式调用真实供应商，旧规则测试显式固定rules模式。

## 实测结论

真实DeepSeek通过本次自编英文样例：实际决策驱动检索、逐字摘录引用有效；两条模型回执、一条工具回执、三次预算预留在进程终止后被复用。恢复期间关闭外部调用并使Embedding地址不可达，仍完成相同草稿；取消阻断旧执行器，撤回资料隐藏草稿。

真实本地Qwen3.5:2b的决策轮协议合法，但提前选择结束，任务refused / MODEL_FINISHED_WITHOUT_RETRIEVAL，样例未通过。未执行检索，没有云端回退；提示v2仍未解决本次提前拒答，不能把协议通过写成任务质量通过。继续以DeepSeek推进流程，本地仅作为待评测可选配置。本次单样例不构成供应商整体排名或模型工业准入。

[收尾指纹清单](../../experiments/m3-t04b1-closure.json)保留原始实测回执的hash，只允许此验收文档补充实测结果；运行代码、契约及测试回执均须与实测时一致，不为文档更新重复模型调用。

## 限制与下一步

只有方法任务模型驱动，资料为自编internal_note，应用内自审不代表独立工程师或厂家认可。逐字摘录不证明相关性或工程适用性；没有开放式诊断结论、维修动作、正式留出评测、模型工业准入或知识/Agent工作台。

下一步T04b2将服务端候选与真实M1/M2数值工具接入独立模型图路径，保留条件、资格、单位和引用检查；之后T05实施规则/单次模型/Agent对照，M4/M6完成界面与试用准入。不能把本轮两个模型阶段和一个只读工具的闭环当作全部M3完成。
