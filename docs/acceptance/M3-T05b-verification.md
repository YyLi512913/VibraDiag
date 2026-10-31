# M3-T05b 反馈型评测与独立留出准入审计

> 2026-10-31；承接[T05a](M3-T05a-verification.md)。本轮实现反馈开发对照及准入检查，不把新仿真波形当成独立设备留出。

## 冻结协议与强基线

[反馈协议](../../backend/config/agent-feedback-evaluation-v1.json)冻结6个条件用例×3策略：缺/完整几何不确定量的两对新波形，以及实际频率完成后取消、实际频率完成后撤销确认。所有普通任务要求先基础、再理想频率，读取真实missing_requirements/uncertainty_complete；条件不足即停止，否则继续无绑定包络、同context包络与匹配。两个故障用例只验证生命周期围栏，不计作模型推理收益。

rules组是新增的离线反馈条件规则，不是T05a原无条件规则图；single_router可一次选择相同反馈规则，避免故意使用弱基线。Agent仍用未修改的正式model_numeric_v1，从真实工具缺项决定后续候选。三组使用相同描述、同字节输入、相同有效profile/context及实际工具。输入ID按组独立，避免一组撤销确认污染另一组；不通过事后复制结果制造相同输入。

只给模型任务要求、当前候选/工作流与实际回执，不给用例ID、数据划分、将注入的故障或金标。按用例轮换顺序，每组单次运行；不预热、不自动重试供应商或改提示筛选成功。单次路由仍只用于离线评测，模型预留和实际回执另计；没有注册新的生产执行器或修改数据库。

## 判断与故障注入

功能用例单独检查动作数量和频率优先顺序、真实来源链接、草稿状态及diagnostic_profile_not_qualified；动作集合正确但顺序不正确也不能通过。缺条件时只执行基础和频率，完整条件执行5个工具；终态仍是待复核降级草稿，不产生故障分类结论。

故障注入只在自有任务实际频率调用返回后发生，使用限定task_id的临时dispatch包装器；当前运行串行且关闭托管消费者。取消走已有HTTP命令，撤确认仅改变该组仿真夹具的确认行。退出上下文必须恢复原函数，生产无注入入口。判定要求确实注入、没有后续工具且没有草稿；撤确认可被规则识别为failed或被Agent挂起为waiting_information，均须保留原因，不能把预期围栏失败隐藏成正常完成。

## 独立留出审计

[准入检查](../../backend/src/vibradiag/evaluation/agent_admission.py)同时比较实际波形hash与上一轮开发hash、两分区hash及来源族，不以新文件名/UUID建立独立性。development与prospective_synthetic属于两个不同波形配置，但仍同一analytic_am_v1来源族，作者已知全部协议，无独立工程师标注。正向准入需要另行可验证实体来源及标注依据，本模块不会凭布尔声明授予资格。

因此即便功能用例全部通过，也不能标independent_holdout_admitted、diagnostic_release或factory_trial_approved为真。paired案例共享波形不等于独立采集，控制用例数量不用于故障准确率置信区间。审计报告与流程通过分别记录，不为追求绿灯改口径。

## 自动验证与复现

自动回归结果：全量774项、反馈/准入/旧基线专项11项全部通过，无失败、错误或跳过。真实供应商结果另行记录，不以模型替身代替。

[单元测试](../../backend/tests/unit/test_agent_feedback.py)检查hash泄漏、同来源族、缺分区、伪造独立标注声明、顺序、故障真实注入及错误引用。[集成测试](../../backend/tests/integration/test_agent_feedback_evaluation.py)通过真实数据库/文件/数值服务与模型替身跑18条反馈对照，验证故障隔离和包装器恢复；同时回归T05a旧基线。

~~~bat
.venv\Scripts\python.exe -m pytest -c backend\pyproject.toml backend\tests -q --junitxml=experiments\m3-t05b-tests.xml
.venv\Scripts\python.exe backend\scripts\benchmark_agent_feedback.py --allow-paid-model-calls
~~~

真实脚本须等全部pytest结束，使用专用测试库、独立目录、进程内ASGI路由和生产数值子进程。调用前冻结代码/协议/模型及测试指纹，已有公开回执拒绝覆盖；用量缺失为null，不生成货币成本结论。结果见[专项JUnit](../../experiments/m3-t05b-feedback-tests.xml)、[全量JUnit](../../experiments/m3-t05b-tests.xml)及[真实反馈回执](../../experiments/m3-t05b-feedback-comparison.json)。功能/生命周期、两个开发分区分别统计，不能把模拟取消成功计为模型推理准确率。

## 首轮真实结果与发现

本次真实DeepSeek完成18条记录，run_completed=true，但all_cases_met_expectations=false，不能把运行结束写成模型质量全通过。

| 策略 | 功能预期 | 生命周期预期 | 总模型调用 | 总工具次数 | 总token |
| --- | --- | --- | --- | --- | --- |
| 条件规则 | 4/4 | 2/2 | 0 | 18 | 0 |
| 单次路由 | 4/4 | 2/2 | 6 | 18 | 5346 |
| 逐步Agent | 3/4 | 2/2 | 20 | 16 | 16922 |

F02的Agent按要求先算基础、理想频率，并完成无绑定包络，但在仍有合法候选时主动结束，没有完成绑定包络和匹配。其3条实际来源引用有效，草稿保留model_finished_with_remaining_candidates和matching_not_completed，诊断资格仍未准入。结果是探索任务覆盖不足，不是错误诊断或引用伪造。F04同类完整条件任务通过，单次结果不足以确定原因或稳定性，需保留差异继续核查，不能宣称已证明某一提示因素导致失败。

取消后全部组保持cancelled且无后续工具/草稿。撤确认后，Agent挂起waiting_information，规则与单次路由明确failed；均保留原因并阻止后续执行/草稿，属于预期边界生效，不是隐藏掉的成功记录。每组4份带引用草稿链接均有效，但这不证明语义正确。

实际波形hash在两个分区及上一轮开发样例间无精确重复，然而仍共享analytic_am_v1来源族，无独立实体与标注证据，协议作者可见。因此独立留出、诊断及工厂准入保持false。这不是工程效果评估通过。

保持规则默认、模型图显式可选。下一步优先核查F02提前结束，区分探索计算条件和诊断发布资格，再以新版本协议验证；不能删除原始失败、屏蔽合法拒答或只重跑到成功。之后补方法资料与比较任务评测。

[收尾指纹清单](../../experiments/m3-t05b-closure.json)保留原始实测回执hash；仅验收、设计与阶段索引文档在测量后补充结果和下一步，代码、协议、旧回执和测试记录均与实测时一致，没有为更新文档重复模型调用。

## 后续

下一步T05c优先核查并修正F02提前结束，再补充方法资料的相关/不相关/无来源拒答，以及比较任务的固定评测矩阵，再汇总M3本机能力和未准入项。独立工程标注、实体/采集留出与现场试用仍需真实来源和验收；本轮审计阻断不妨碍继续完善系统，也不自动解除M2诊断门槛。
