# M3-T04a 受控模型网关与模型轮预检

> 日期：2026-10-31；设计：[09 v0.1](../09-model-runtime-design.md)。本阶段不将规则图替换为模型自主执行。

## 交付

- 新[模型网关](../../backend/src/vibradiag/infrastructure/llm/formal_gateway.py)显式选择DeepSeek或本地Ollama，不复用M0无任务账本的聊天入口；私有密钥只在官方HTTPS认证头中使用，不发给本地模型，不允许任意URL/代理/重定向/供应商工具。
- 配置固定请求模型、可获知的本地digest、输出上限、JSON/非思考方式；每任务第一次调用写入不可变绑定，后续沿用，不自动切换或下载。配置、外发开关和参数边界见[模型设计](../09-model-runtime-design.md)。
- 0018新增配置绑定与模型轮两表，当前44张业务表加4检查点表；发起前占用8轮/120秒任务预算，记录受控上下文与指纹、状态、用量、响应hash和校验结果。取消、恢复、截止和数据库租约阻止迟到输出。
- 模型轮内只给出建议，不执行candidate、不发布草稿。决策须已登记ID；摘录须来自当前授权检索的真实source_id，且为给定片段逐字子串。改写、伪造、越权、错任务、额外工具参数、截断/空白/坏JSON均失败，失败预算不退。
- 单进程名额1，调用时限取60秒与任务剩余时间较小值；响应2MiB/正文8192字节/云端提示24000字节，本地提示6000字节保守边界。失败不自动修复或重试，未知模型轮不能免费重放；完成轮可在当前上下文一致时复用。

## 验证

[网关单元测试](../../backend/tests/unit/test_formal_model_gateway.py)使用模拟传输验证云端/本地封装、认证隔离、外发开关、HTTP错误脱敏、无重定向/回退、输入/输出/上下文限制、截断/工具响应拒绝、本地指纹前后变化、截止、取消及名额释放。[模型服务集成](../../backend/tests/integration/test_agent_models.py)使用真实PostgreSQL及模拟模型，验证绑定、模型预算、幂等、最小上下文、原文摘录、撤回/取消、错误候选、不可变历史及未知响应恢复。

[真实模型脚本](../../backend/scripts/verify_formal_models.py)使用独立测试库及同一单进程租约，显式审核/发布开发自编英文方法资料并真实BGE-M3建索引。两个新任务分别固定DeepSeek和Qwen配置；实际请求决策，验证同轮重放不新增请求，再独立准备授权检索，验证模型原文摘录。独立准备检索是为了测试摘录网关，不能据此宣称模型决策已驱动工具。

每供应商分别记录协议/策略检查与清晰开发样例完成；合法拒答并不算完成要求检索/摘录的样例。云端失败保留回执且阻止预检通过；本地失败原样记录，不暗中换云端。成功也只有两个英文兼容阶段，不是留出评测、中文能力或工业效果结论。任务以预检结束取消，不形成用户报告。

```bat
.venv\Scripts\python.exe backend\scripts\migrate.py
.venv\Scripts\python.exe -m pytest -c backend\pyproject.toml backend\tests -q --junitxml=experiments\m3-t04a-tests.xml
.venv\Scripts\python.exe -m pytest -c backend\pyproject.toml backend\tests\unit\test_formal_model_gateway.py backend\tests\integration\test_agent_models.py -q --junitxml=experiments\m3-t04a-model-tests.xml
.venv\Scripts\python.exe backend\scripts\verify_formal_models.py
```

真实脚本会实际使用已授权的云端API额度；应等所有pytest结束后运行，专用库和开发库不能相同。普通测试不调用真实供应商。结果以[全量JUnit](../../experiments/m3-t04a-tests.xml)、[模型专项](../../experiments/m3-t04a-model-tests.xml)、[边界回归](../../experiments/m3-t04a-boundary-tests.xml)及[真实模型回执](../../experiments/m3-t04a-models.json)为准；旧阶段回执保持历史字节。

完整后端回归714项通过，0失败、0错误、0跳过；本地上下文字节上限、提示策略绑定和运行租约调整后，模型专项及既有任务/知识/迁移边界另行复验，统计分别保留在上述JUnit中。

## 实际模型结果

两种模式均通过两个阶段的接口/结构/政策校验，但清晰样例的完成结果不同：

| 配置 | 候选阶段 | 摘录阶段 | 样例是否完成 |
| --- | --- | --- | --- |
| DeepSeek Flash | 选择已提供的检索候选，约1.36秒 | 逐字摘录通过，约0.78秒 | 是 |
| 本地Qwen3.5 2B | 以缺少原文为由提前结束，约15.72秒 | 在探针独立准备来源后摘录通过，约12.16秒 | 否 |

本地的合法拒答没有计为样例成功，也没有调用云端替代。该现象发生在当前分阶段提示下，可能涉及提示理解与模型能力，不能从单例归因为模型固有缺陷；T04b应改进阶段提示，T05扩大固定案例。耗时包含各自网络/加载条件，不是公平硬件排名或P95。当前继续以DeepSeek作为默认开发方案，本地仅保持显式适配、待验证。

运行回执保持测量时字节；实测后补写的文档及最终源码另见[闭环清单](../../experiments/m3-t04a-closure.json)，不为更新文字而反复重测或覆盖本地失败。

## 失败与限制

JSON模式不能代替schema、引用、数值或语义验证；逐字摘录只证明文本来自片段，不证明相关性、专家认可或工程结论。云端权重无法自行attest，本地manifest与服务版本也不构成硬件/权重执行证明。提示与回执可能含问题和授权片段，保存在受控业务库，不公开到前端日志。

T04a没有生产图模型接管、数值任务LLM候选、开放式草稿生成、格式修复、网络自动重试、本地工业准入或任务前端。下一步T04b增加独立模型图版本，消费受限决策并执行现有授权动作，再完善草稿与语义/数值检查；T05做正式对照评测，M4/M6继续处理操作界面和试用准入。
