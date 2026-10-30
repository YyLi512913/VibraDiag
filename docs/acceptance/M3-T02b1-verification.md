# M3-T02b1 官方检查点与隔离进程兼容性验证

> 日期：2026-10-30。范围：独立验证图；业务任务执行器仍为T02a规则线程。
> 上游：[07 v0.3](../07-m3-agent-design.md)、[T02a](M3-T02a-verification.md)。不是M3整体或工厂试用验收。

## 本轮交付

锁定langgraph-checkpoint-postgres 3.2.0、psycopg-pool 3.3.3；原有LangGraph/checkpoint/psycopg版本保持。[官方包说明](https://pypi.org/project/langgraph-checkpoint-postgres/)要求持久后端，并明确连接参数与反序列化限制；[中断文档](https://docs.langchain.com/oss/python/langgraph/interrupts)说明恢复时节点会重入。这里通过实际数据库和进程验证这些接入条件。

验证模块为[m3_checkpoint_probe.py](../../backend/scripts/m3_checkpoint_probe.py)，不被生产API/任务执行器导入。只从专用测试库URL连接，迁移/应用账号必须不同；schema限定随机UUID前缀并写归属标记。4张官方检查点表及1张验证账本只存在于专属schema，完成后校验标记再清理。业务迁移仍0013_agent_tasks，31张业务表。

官方包共10条迁移，三条使用事务外并发建索引。验证模块仅针对全新空表转换为事务内普通索引，由迁移账号一次提交；测试确认中途失败能整体回滚。未提供已有生产表的在线升级方案，应用启动不调用setup。

应用账号只有schema USAGE，运行表SELECT/INSERT/UPDATE，迁移版本表SELECT。实际尝试CREATE、ALTER、更新迁移版本及DELETE历史均被数据库拒绝。没有将原始Saver或检查点列表接口开放给浏览器。

## 已执行专项

新增17项测试，见[test_checkpoint_probe.py](../../backend/tests/integration/test_checkpoint_probe.py)：

| 检查 | 实际覆盖 |
| --- | --- |
| schema边界 | 拒绝public、非法前缀、拼接SQL；无归属标记不能清理 |
| 迁移及角色 | 官方表/版本完整，运行角色非超级用户且不能DDL；全新schema迁移回滚 |
| 持久图 | 关闭连接后重建Saver/图，interrupt使用Command恢复，保留状态和真实基础指标 |
| 节点重入 | 新连接恢复时节点进入两次，验证账本计算一次；并发测试计数实际数值函数也只执行一次 |
| 未知结果恢复 | 接受resume后、节点结果提交前注入失败，再invoke(None)继续；三次进入、计算一次 |
| 序列化 | 禁用pickle回退；pickle/篡改中断记录拒绝读取；未批准msgpack类型不构造为Python类 |

这里的“计算”调用真实compute_basic，对4096点、4096Hz、幅度2的128Hz解析正弦计算RMS；预期√2 m/s²。它不是设备故障信号，也不登记正式分析、fact或evidence。

官方严格序列化模式阻止未批准构造器，但可以返回普通字典。因此“已限制类型”不等于检查点状态已做完整性/业务校验；正式接入仍须独立校验字段、大小、版本及服务器绑定。原始PostgresSaver也不提供项目所需的组织/操作者/任务租约隔离。

## 真实子进程验证

脚本：[verify_m3_checkpoints.py](../../backend/scripts/verify_m3_checkpoints.py)。不调用模型，不使用业务API，不终止现有开发服务。

1. 子进程A运行验证图，计算后动态interrupt，持久化光标并退出。
2. 子进程B以同一服务器生成thread_id恢复，在interrupt已消费、节点结果尚未提交时阻塞。
3. 父进程确认就绪后等待0.5秒，触发超时并仅终止脚本自己创建的子进程；Windows同时处理Python启动器与实际解释器的PID关系。
4. 新子进程C使用同一检查点invoke(None)，继续已持久化的resume；验证三次节点进入、一次计算、指标正确。
5. 检查官方表/版本、检查点数量及序列化类型；清理带标记的测试schema并确认不存在，业务表和迁移保持不变。

0.5秒从子进程进入受控阻塞后开始，不含解释器启动、模型调用或业务工具总时长。该结果只能证明验证子进程可被超时终止，不能称业务任务已具备硬取消。

最终全量回归及实际进程结果见[JUnit](../../experiments/m3-t02b1-tests.xml)和[检查点回执](../../experiments/m3-t02b1-checkpoints.json)。回执保存依赖版本、官方迁移指纹、进程恢复、数值结果、清理状态和本轮源码字节指纹。完整回归542项通过，0失败、0跳过；上述17项均纳入全量。真实子进程结果由脚本独立记录，不用单进程异常注入代替进程终止证明。

复现（从项目根，使用专用测试库和私有测试配置）：

```bat
.venv\Scripts\python.exe -m pytest -c backend\pyproject.toml backend\tests -q --junitxml=experiments\m3-t02b1-tests.xml
.venv\Scripts\python.exe backend\scripts\verify_m3_checkpoints.py
uv pip check --python .venv\Scripts\python.exe
.venv\Scripts\python.exe backend\scripts\check_secret_hygiene.py
node scripts\check-docs.cjs
```

## 进入业务接入前的约束

T02b2接分步持久规则图前，需要完成任务/thread_id服务器绑定、图版本、恢复状态schema与大小限制，以及读取/写入时的当前身份、输入和租约围栏。API不能接受任意检查点ID/namespace作为授权；预算、动作及来源从业务表加载，检查点只作可重建光标。

还需明确检查点提交与任务挂起/恢复/取消两个事务之间的对账窗口。节点重入先查agent_calls/真实工具幂等键，不能清空预算或把未知响应当失败重算。图的最终输出仍要经现有来源、确认、版本和资格检查才能写入未发布草稿。

生产数值隔离另需跨进程共享总容量、受控委托身份和子进程资源/退出对账；不能每个进程各建两个计算槽，也不能绕过当前单API租约。DeepSeek选择、正式RAG、诊断资格与工厂试用准入保持后续阶段。
