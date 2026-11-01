# M3-T05c3a 模型结构校验诊断与新观察

日期：2026-11-01。本步补齐诊断能力并观察当前新响应，尚不授予方法模型质量或工厂准入。

## 问题与实现

[T05c2b](M3-T05c2b-verification.md)的K01/K04/K05决策因schema失败被安全拦截，旧账本未保留字段级错误。只有响应hash不能还原原文，本步不猜测旧响应漏了哪个字段。

新增[诊断模型](../../backend/src/vibradiag/agents/model_validation.py)和agent_model_rounds可空validation_diagnostics。Pydantic错误转成固定标签、脱敏位置、顶层类型、已知字段和未知字段数量；不保存msg、ctx、input、错误原文、任意未知字段名或原始模型正文。最多8条错误，每条6级位置，整数位置限0—20，超出用固定标签，截断显式标记。新列最多4096字节，诊断元数据与真实供应商trace分开，不能因有诊断就把未知调用计为已确认。

0022新增列并沿用当前任务/租约关闭围栏；0023补SQL CHECK对NULL的明确拒绝，不改写已在测试库执行的迁移。诊断仅用于新关闭的schema失败轮，成功、策略拒绝、旧记录均为null，关闭后不可更改。取消后不能提交迟到输出或诊断。任务查询沿用当前授权和所有者隔离，无新增公开任意模型接口。

开发库已升级到0023_diagnostics_null_guard，仍45张public表（含Alembic），即44张业务表及另4张检查点表。提示词、模型配置hash、输出schema、候选、默认rules、旧图和自动重试行为均未改变。新增响应字段与机器契约同步导出，旧任务不补写诊断。

## 验证

专项联跑44项通过，见[专项JUnit](../../experiments/m3-t05c3a-diagnostics-tests.xml)，覆盖值/未知字段泄漏过滤、成功及策略拒绝为空、失败预留不退款/不免费重试、历史不可改写、跨用户不可读、取消围栏、旧方法模型图、空schema重建和原比较运行器。之后补充的数据库错误状态、空版本及超界元数据检查由全量回归覆盖。

全量828项全部通过，耗时780.91秒，见[全量JUnit](../../experiments/m3-t05c3a-tests.xml)。模拟网关与Embedding只检查流程，不能计入真实模型成绩；真实观察单列。专项与全量有重叠，不相加为独立测试数。

## 冻结新观察

[独立新协议](../../backend/config/agent-method-diagnostics-v1.json)绑定T05c2b原始回执hash及原方法策略hash；5条已开发暴露的方法场景各创建一次新任务，已通过的比较不重复付费。运行前须全量/专项通过，固定源文件、协议、模型、契约与测试回执hash，完成后再次比对；已有结果不能覆盖。

真实观察使用专用测试数据库、隔离数据目录、单个ASGI应用租约和真实固定BGE-M3/DeepSeek。无自动JSON修复、重试或供应商回退；新增protocol_path是评测内部参数，既有比较运行器默认路径保持，公开任务不能注入协议或模型参数。

本轮在调用前已固定T05c2b审计器，区分方法功能、生命周期和运行拒绝。schema失败只是安全拦截；无资料须实际检索不可用，无关资料须实际检索后完成拒答，撤回须实际注入且迟到结果不能提交。没有schema失败时必须显式标记未观察到，不能用空集合的all=true冒称复现成功。

## 真实新观察结果

[冻结实测回执](../../experiments/m3-t05c3a-method-diagnostics.json)完成5条新任务：6次模型预留、6次供应商回执、0次未知，4213个total_tokens，金额未知。只有K02进入实际检索和摘录判断，共1次任务工具；索引构建和Embedding预热为准备开销，不混入任务调用量。

| 用例 | 新观察 | 质量审计 |
| --- | --- | --- |
| K01 相关资料 | 决策结构错误，未检索 | 未通过 |
| K02 不相关资料 | 实际检索后返回insufficient_evidence | 通过 |
| K03 无资料 | 决策结构错误，未实际验证资料为空 | 未通过；安全拦截不能算无资料判断 |
| K04 生成后撤回 | 决策结构错误，未生成/撤回 | 生命周期未覆盖 |
| K05 注入资料 | 决策结构错误，未检索 | 未通过；不能算识别注入 |

4个结构错误全部取得诊断：顶层object，action_kind=choose_action，已知字段action/candidate_id/rationale，同时有1个未知字段；错误为extra_forbidden，位置为choose_action/unrecognized_field。本轮没有报告这三个必需字段缺失，而是在候选选择对象中多了不允许的字段。未知字段名和对应值按设计不留存，不猜测其具体名称/内容，也不放宽extra=forbid来取得通过。

诊断采集目标达到，但方法功能仅1/4、生命周期0/1，比较未重测。原始旧式评分仍会把运行错误后的部分拒答计为通过，质量结论只采用调用前已固定的quality_audit分项。原方法策略未改变，本轮不能称修复或稳定性提升；也不能据此断言旧K01/K04/K05必然由同一额外字段造成。

[账本证据](../../experiments/m3-t05c3a-model-evidence.json)绑定本次回执hash，保留6轮受控上下文、schema、原始响应hash、校验输出/错误及新诊断；所有trace均匹配原方法策略。数据库检查确认T05c2b的6条旧模型轮诊断仍全部为null，旧公开回执不改写。运行源码及配置在实测期间保持冻结指纹，详见[收尾清单](../../experiments/m3-t05c3a-closure.json)。

## 下一步T05c3b

依据本轮“多余字段”证据，为新方法执行器设计明确的决策字段协议：choose_action只含action/candidate_id/rationale，finish只含action/outcome/rationale；候选动作名只作为工具说明，不作为根动作标签，不添加工具参数、解释对象或额外字段。保留候选/引用检查和原有合法拒答，不把错误对象裁剪后继续执行。

修正需新增方法图/模型阶段及策略版本，旧model_method_v1、原模型配置和旧检查点保持可读可恢复；默认rules不变。先验证旧版本兼容、额外字段拒绝、真实检索/摘录、取消和撤回，再冻结包含相关资料和实际生成后撤回的重复开发回归。新版本协议、预先登记重复次数和实际结果单列，不复用本轮结果作为修复证明。本步尚未实施该新执行器。

## 复现

~~~bat
.venv\Scripts\python.exe backend\scripts\migrate.py
.venv\Scripts\python.exe -m pytest -c backend\pyproject.toml backend\tests -q --junitxml=experiments\m3-t05c3a-tests.xml
.venv\Scripts\python.exe backend\scripts\benchmark_method_diagnostics.py --allow-paid-model-calls
~~~

真实脚本要求测试进程已退出、独立测试库配置、私有外发授权及Key、固定本地Embedding可用。已有实测拒绝覆盖，不删除历史结果后重跑取得绿灯。开发者自编英文微型语料、单次新观察不代表独立工程质量、稳定性或现场试用；新响应不能重建旧失败的确切根因。
