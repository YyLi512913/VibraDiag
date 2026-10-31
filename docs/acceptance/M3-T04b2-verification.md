# M3-T04b2 数值任务模型候选编排

> 日期：2026-10-31；承接[T04b1](M3-T04b1-verification.md)。本轮接数值工具编排，效果评测及工业准入留T05/M6。

## 范围与执行方式

VIBRADIAG_NUMERIC_EXECUTOR=rules/model独立控制新数值任务，分发默认rules；方法模式继续由VIBRADIAG_METHOD_EXECUTOR控制。客户端不能指定执行器、供应商或工具参数。新模型数值任务使用model_numeric_v1 / model-numeric-v1，受理时固定供应商、模型、可获知的digest及数值提示策略hash，旧规则及方法任务保持原图。数值策略formal-numeric-json-v1与方法v2隔离，不改变方法策略hash。

inspect_signal提供基础分析候选；bearing_observations先基础分析，再提供包络与理想频率候选，模型可以决定这两类工具的顺序。服务器先验证确认与用户选择的profile/context，再提供受控候选。缺少选择时显式挂起，回答文字不能代替输入确认或创造参数。

只有实际包络、理想频率均可用且频率不确定量完整，才提供绑定相同context的包络候选；实际绑定包络完成后才提供匹配候选，父ID只从本任务实际回执解析。compare_measurements只提供绑定既有比较声明、双侧输入及确认的比较候选。探索模式固定，不允许模型升级诊断资格、选择阈值、改单位或编造参数。

模型每轮只能选择当前候选ID或结束，不接受模型自造问题/工具参数。最多5个逻辑工具、6轮决策，沿用全局8模型/20工具/120秒活动预算。允许提前结束；已有部分数值结果时保留草稿并明确model_finished_with_remaining_candidates，无结果时拒答。工具失败/执行未知仍使用既有受控恢复；失败或未知模型轮不自动重试、退款或换供应商。

## 持久化与最终草稿

图按decide_0至decide_5、act_0至act_4展开，节点名承担步骤光标，检查点仍只保存任务ID和图版本。显式工具重试按operation_key保留最新执行回执作为逻辑步骤，失败尝试和预算仍全部留存。第i轮上下文只取前i个逻辑工具操作的真实回执，即使第i条工具已提交、检查点未保存，仍可从同一模型轮及工具逻辑键恢复，不重新请求模型或计算。

执行前重新生成服务器候选并验证已完成模型输出hash/配置/上下文；实际工具仍执行当前权限、输入、确认、参数与父绑定检查。图END后重新读取终止决策与全部实际来源，锁定可变输入记录、确认及涉及的轴承组件头，校验引用角色和输入范围，再提交草稿。取消、撤权、输入变更及确认撤销阻断迟到结果或隐藏已保存草稿。

草稿由服务器以确定性文案组装，仅引用实际basic_analysis/tool_run。模型没有开放式数值结论生成功能；轴承始终保留diagnostic_profile_not_qualified，缺少匹配时明确matching_not_completed。比较结果不代表维修有效。0020扩展图和模型阶段/绑定约束，不新增表，仍为44张业务表、4张检查点表。

## 验证及复现

自动回归：全量755项、数值/方法/网关专项82项（其中数值图20项）全部通过，无失败、错误或跳过。真实供应商调用另行记录，不以模型替身代替。

[数值图专项](../../backend/tests/integration/test_model_numeric_graph.py)使用真实数据库、文件、M1/M2数值服务和模型替身，覆盖基础分析、两种工具顺序、不确定量阻断、实际父匹配、双输入比较、伪造ID/参数/问题拒绝、提前结束、配置固定、规则保留、确认澄清、撤权/撤确认、取消、模型/工具回执提交窗口、END窗口、存储失败后的显式工具重试及未知模型不重试。网关另验方法/数值提示策略不能混用。

[真实HTTP/进程脚本](../../backend/scripts/verify_model_numeric_graph.py)使用自编AM信号、真实DeepSeek及实际数值服务，故意不提供几何不确定量，要求真实调用基础/包络/理想频率而不提供匹配候选。固定样例BPFO为90Hz。API关闭托管消费者以隔离任务ID；[自建探针](../../backend/scripts/m3_numeric_graph_probe.py)运行同一生产claim/execute/recover路径，交替持有单进程租约。

脚本在最终模型决策回执提交、节点检查点尚未保存时实际终止自有执行进程；显式恢复后关闭外部模型调用，要求复用4轮模型/3个工具/7次预算预留完成草稿。另验证决策后旧执行器丢失租约、HTTP取消仍阻断工具，及确认撤销隐藏草稿。进程终止仅使用自有句柄，生产没有故障注入入口。本轮不重测本地模型，T04b1的本地提前拒答保留。

运行顺序：先迁移、导出契约、运行专项与全量pytest，全部结束后再运行真实脚本，避免争用同一数据库执行租约。

~~~bat
.venv\Scripts\python.exe backend\scripts\migrate.py
.venv\Scripts\python.exe backend\scripts\export_m3_agent_contract.py
.venv\Scripts\python.exe -m pytest -c backend\pyproject.toml backend\tests -q --junitxml=experiments\m3-t04b2-tests.xml
.venv\Scripts\python.exe backend\scripts\verify_model_numeric_graph.py
~~~

结果见[全量JUnit](../../experiments/m3-t04b2-tests.xml)、[数值/方法/网关专项JUnit](../../experiments/m3-t04b2-numeric-tests.xml)及[真实图回执](../../experiments/m3-t04b2-model-graph.json)。回执记录运行源码指纹，文档收尾另列清单，不为改文档重复模型调用。

## 实测结论

真实DeepSeek在本次自编AM样例中完成4轮决策并驱动基础、包络、理想频率3个实际工具，BPFO为90Hz；几何不确定量缺失时未提供匹配候选，草稿保留诊断资格与匹配缺项。进程终止后关闭外部模型调用，仍复用原回执完成同一草稿，预算预留保持7次；取消围栏和确认撤销隐藏草稿均通过。本次未重测本地模型，T04b1提前拒答记录保持原样。

[收尾指纹清单](../../experiments/m3-t04b2-closure.json)保存原始实测回执hash，只有本验收文档在实测后补充结论；运行代码、契约及测试回执须匹配实测时指纹，不为更新文档重复真实调用。

## 限制与下一步

开发者自编仿真信号不等于真实工厂数据，单样例通过不证明泛化质量或工业资格。这里只实现有限的模型工具编排与有来源草稿，尚无正式规则/单次模型/Agent对照、故障分类模型准入、开放式诊断生成或完整Agent工作台。工厂试用仍须M4/M6收尾。

下一步M3-T05制定冻结任务集、运行条件及规则/单次模型/Agent对照，记录完成、拒答、降级、错误、耗时、模型调用/成本与引用有效性；严格区分开发样例与独立留出测试，不先宣布Agent优于规则。
