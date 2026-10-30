# M3-T02b2 业务持久规则图、身份与租约围栏

> 日期：2026-10-30。范围：业务任务接入分步持久规则图；尚非模型Agent或生产硬取消。
> 上游：[07 v0.4](../07-m3-agent-design.md)、[T02a](M3-T02a-verification.md)、[T02b1](M3-T02b1-verification.md)。

## 交付与数据边界

新建任务默认rules_graph_v1，执行rules-graph-v1分步LangGraph图。旧rules_scaffold_v1任务按原执行器继续，旧快照/账本不改写。图按目标执行prepare/basic/profile/envelope/context/frequency/bound_envelope/matching/comparison节点，调用真实M1/M2服务；不调用DeepSeek或发布知识检索。

0014_agent_graph新增agent_graph_bindings及独立public_agent_checkpoints schema：32张业务表，另有4张官方检查点表。服务器在创建任务事务中生成不可变绑定，关联task、组织、发起人、thread_id、图版本和输入指纹。运行账号不执行setup/DDL，不能删除检查点历史、改迁移版本或调用prune。

检查点领域状态只有task_id和graph_version，其他为框架运行光标。身份、预算、参数选择、确认、调用、数值及引用均从业务表/真实来源加载。Cookie、会话、角色和聊天回答不保存进图状态。恢复校验字段/图版本/节点/通道，限制检查点和单个序列化载荷64KiB，禁止pickle及额外构造器。

TaskSaver读写前重查当前身份/数据范围及原输入；检查点事务对任务行FOR SHARE并核对running/租约，数据库触发器再次校验绑定thread_id及连接租约。取消或重启改变任务状态/租约后，已构建的旧Saver也不能继续读写。组织隔离由业务服务与绑定校验承担，未宣称所有业务表已实现数据库RLS。

公共任务详情返回executor、formal_graph_executed及graph.version/checkpoint_count，不暴露thread_id、namespace、检查点内容或供应商恢复命令。model_called仍为false。formal_graph_executed=true只代表已运行持久图，不代表自主模型决策或诊断结论。

## 恢复与两个提交窗口

业务挂起/恢复仍使用question_id、版本CAS、当前会话及幂等命令，预算沿用。图节点需要信息时保留失败/待执行光标，外层随后提交业务等待状态；受控恢复invoke(None)重试待执行节点，不接受浏览器直接传LangGraph Command。

| 窗口 | 实测规则 |
| --- | --- |
| 工具提交，节点检查点未提交 | 节点可重入，先查agent_calls及真实来源；不重复计算或新增逻辑预留 |
| 图已END，任务终态未提交 | 恢复END光标，不重跑节点，重读必需来源后完成业务草稿 |
| 等待状态提交前进程中断 | 启动将running标interrupted，再由显式恢复对账；预算不重置 |
| 取消/新租约与旧检查点竞态 | 任务行锁及令牌围栏阻止迟到写入，终态保持不可变 |

最终草稿提交在图保存结束后进行，并重新检查实际来源、确认、输入/轴承版本及必需运行。光标不能代替来源；无来源的终点不能生成可用草稿。状态类型检查不等于加密签名或故障语义正确性证明。

## 已执行验证

- 既有19项任务集成测试已切到新建任务的持久图路径，包含真实基础/包络/频率/匹配/比较、等待恢复、预算、取消和来源失效。
- 新增14项[业务图测试](../../backend/tests/integration/test_durable_rules_graph.py)：绑定不可变/私有、错身份/组织、会话及设备权限撤回、线程/namespace切换拒绝、原始setup/list/delete/prune禁用、缺/错数据库令牌写入拒绝、旧Saver围栏、两个提交窗口、坏光标拒绝、节点到检查点之间撤权、旧执行器兼容。
- 空schema重建同时覆盖33张主schema表（含Alembic版本）及4张独立检查点表，迁移事务回滚后两份schema均不存在。
- 完整回归与实际HTTP/进程恢复的最终统计见[JUnit](../../experiments/m3-t02b2-tests.xml)、[任务专项](../../experiments/m3-t02b2-task-tests.xml)和[HTTP回执](../../experiments/m3-t02b2-http.json)；本轮完整后端556项通过，0失败、0跳过；实际HTTP恢复由新脚本独立记录，不以既有阶段回执替代本轮结果。

实际HTTP脚本[verify_m3_durable_flow.py](../../backend/scripts/verify_m3_durable_flow.py)在专用测试库、隔离文件根启动新API及托管消费者。120000点任务缺确认挂起、补齐后完成；创建/恢复原键重放保持同一任务。1000000点任务观察到running调用后仅终止脚本自建API，重启后原任务中断，显式恢复/未知调用对账及关联retry_of后完成，并保留真实检查点与累计预算。没有工厂数据或模型外发。

复现（从项目根；测试与实际HTTP按顺序运行，避免同测试库API租约冲突）：

```bat
.venv\Scripts\python.exe -m pytest -c backend\pyproject.toml backend\tests -q --junitxml=experiments\m3-t02b2-tests.xml
.venv\Scripts\python.exe -m pytest -c backend\pyproject.toml backend\tests\integration\test_agent_tasks.py -q --junitxml=experiments\m3-t02b2-task-tests.xml
.venv\Scripts\python.exe backend\scripts\verify_m3_durable_flow.py
uv pip check --python .venv\Scripts\python.exe
.venv\Scripts\python.exe backend\scripts\check_secret_hygiene.py
node scripts\check-docs.cjs
```

## 限制与下一步

仍为单API租约、单托管线程消费者，M1/M2共享数值并发2；取消及120秒限制仍是步骤边界的协作控制。T02b1的子进程终止实验不等于本轮业务数值已被硬终止。T02b3负责数值隔离、跨进程容量、受控委托身份和退出对账。

本轮没有新增任务前端、发布报告或工单。正式RAG/DeepSeek决策/语义评测留后续阶段；探索匹配、来源确认和M2正式诊断资格限制保持。历史T01/T02a/T02b1回执属于当时源码版本，不因本轮接入而覆盖其状态。
