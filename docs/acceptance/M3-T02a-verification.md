# M3-T02a 持久任务与真实规则执行验证

> 日期：2026-10-30。范围：本机任务执行基础；不是M3整体验收或工厂试用准入。
> 设计：[07 v0.2](../07-m3-agent-design.md)；前序：[T01](M3-T01-verification.md)。

## 交付内容

新增`0013_agent_tasks`迁移及agent_tasks、agent_commands、agent_calls、agent_events四表，当前31张业务表。任务快照/命令/事件不可改写，终态冻结，运行状态与租约绑定，写操作采用行锁、版本CAS与事务审计。应用账号不自动建表。

提供创建、本人分页/详情、恢复和取消API；写请求校验Cookie/CSRF、角色、输入范围及幂等键。任务可等待本人输入确认或配置/目标选择，恢复重新核对问题、版本、身份及输入。description/answer不能覆盖测量参数或代替确认。任务历史只对发起人及其当前有效数据范围可见，管理员无隐含读取权。

一个API进程持有原数据库租约，一个托管规则消费者领取队列，M1/M2总数值并发仍为2。每人最多32个未结束任务、最多20次恢复；逻辑调用预留与累计活跃时间持久化，恢复不重置预算。容量429仅自动退避一次，之后中断等待显式处理。

执行器明确返回`rules_scaffold_v1`、`model_called=false`、`formal_graph_executed=false`。按目标调用既有真实授权服务，而非伪造工具响应：

| 目标 | 本轮行为 |
| --- | --- |
| inspect_signal | 基础分析与真实来源UUID引用 |
| bearing_observations | 基础、包络、理论频率；缺不确定量不强行匹配，条件满足时调用真实匹配并保留资格限制 |
| compare_measurements | 验证受控声明和双输入/本人确认，调用真实比较，不作维修有效判断 |
| explain_method | 正式发布知识尚未接入，refused并列缺失原因，不生成假KB引用 |

草稿始终unpublished_draft。重读时重新验证来源hash、输入版本、确认与轴承版本；历史状态保留，失效草稿隐藏。用户取消或旧租约的迟到结果不能覆盖任务终态；已进入M1/M2的只读计算可能完成，仍保留自己的来源记录。

## 测试与实际进程验证

新增集成测试见[test_agent_tasks.py](../../backend/tests/integration/test_agent_tasks.py)，使用专用PostgreSQL测试库、隔离文件根和解析AM合成信号，不调用云端模型。

| 检查 | 已执行覆盖 |
| --- | --- |
| 接受/重放/冲突 | 同键同请求同任务，同键异请求409；并发同键只创建一个 |
| 暂停/恢复 | 缺确认挂起、普通回答不能确认、补齐后继续、旧问题/版本409、重复恢复命令不重复消费 |
| 实际工具 | 基础、包络/频率降级、完整匹配、双输入比较，引用真实分析/工具ID |
| 来源有效性 | 确认撤销、输入修订、轴承版本更新、权限撤回均限制读取或执行 |
| 当前身份 | 跨组织/他人/管理员拒绝、CSRF校验、会话撤销后重新登录和受控恢复 |
| 并发/恢复 | 计算中取消与迟到结果围栏、未知完成响应原键查回、存储失败显式retry_of |
| 累计预算 | 工具预算耗尽不派发、活跃时间恢复保守计费、容量退避不新增逻辑调用 |
| 数据库 | 终态/快照/命令/事件保护；空schema全迁移重建及事务回滚 |

实际HTTP脚本：[verify_m3_task_flow.py](../../backend/scripts/verify_m3_task_flow.py)。脚本只使用独立测试库，启动回环API及真实托管消费者，凭随机测试账号执行；只终止自己启动并从日志识别的API进程，不输出凭据。

实际执行覆盖：

1. 120000点合成CSV上传，创建任务202，缺确认挂起；采集API确认后恢复完成。创建/恢复同键重放返回同一任务，只有一个逻辑基础分析调用。
2. 1000000点合成CSV，观察任务与调用账本均running后强制终止API；重新启动，原任务interrupted，原键查回同一任务。
3. 显式恢复先对账原调用，再关联新的retry_of调用完成。原中断调用保留，新增调用消耗第二次工具预算，累计时间不归零。

全量后端回归525项通过，0失败、0跳过；最终任务专项19项复验，覆盖恢复耗尽时换会话的终态处理。测试统计及最终进程回执见[全量JUnit](../../experiments/m3-t02a-tests.xml)、[专项JUnit](../../experiments/m3-t02a-task-tests.xml)与[HTTP回执](../../experiments/m3-t02a-http.json)。回执记录合成输入hash、状态、预算、实际重启、分层回归结果和源码字节指纹；不是任务成功率或P95基准。

复现（从项目根，先完成测试，不与专用测试库的另一API/测试并行）：

```bat
.venv\Scripts\python.exe -m pytest -c backend\pyproject.toml backend\tests -q --junitxml=experiments\m3-t02a-tests.xml
.venv\Scripts\python.exe -m pytest -c backend\pyproject.toml backend\tests\integration\test_agent_tasks.py -q --junitxml=experiments\m3-t02a-task-tests.xml
.venv\Scripts\python.exe backend\scripts\verify_m3_task_flow.py
.venv\Scripts\python.exe backend\scripts\check_secret_hygiene.py
node scripts\check-docs.cjs
```

开发库已升级0013；开发API已加载任务路由。未增加任务前端，调试入口为本机`/docs`；现有工作台不因此自动变为完整Agent界面。

## 发现与修复

- 容量429路径曾残留旧计时代码，集成测试发现NameError；已统一按持久tick累计，并在退避事务提交后再退出本次调度，避免异常退出回滚预算/队列更新。
- 数据确认撤销后不能直接重新确认同一版本。测试改为使用真实未确认的新采集模拟补充确认，沿用原有不可逆撤销语义。
- 轴承版本改变时，旧工具可授权读取历史，但任务旧草稿不应继续可用；已补上下文有效性检查与对应测试。
- 恢复次数耗尽且调用方换会话时，进入终态保留原执行会话，只在真正恢复queued时重绑定会话；避免违反数据库会话围栏。全量回归后增加该分支的专项复验。
- 原型与正式任务保持独立，T01及M2历史回执不重新生成；其源码指纹属于当时快照。

## 剩余边界与下一步

本轮不是完整模型Agent。尚无正式LangGraph持久检查点、服务端候选生成/模型选择、发布知识检索、DeepSeek正式外发及语义校验。规则执行通过不能替代M3-AT01—AT05。

取消和120秒限制属于协作/步骤边界控制，不能硬中断数值函数。启动恢复将最后tick到恢复的时间保守计入，包括停机；长停机可能直接耗尽活跃预算。多进程消费者、跨进程容量、未知调用完整对账、隔离执行和硬期限留T02b。终态不能原地重跑，需创建新任务；parent_task_id关联尚未实现。

未补齐来源的确认、诊断profile资格、公开严格匹配覆盖和现场核对仍按M2结项条件保留。下一步T02b先验证PostgreSQL检查点/迁移角色兼容性与执行隔离方案，再接正式持久图。
