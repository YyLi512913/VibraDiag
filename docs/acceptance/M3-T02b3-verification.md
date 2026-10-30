# M3-T02b3 纯数值子进程、统一容量与终止验证

> 日期：2026-10-30。范围：可信纯数值核心的进程隔离；不是整个HTTP/数据库事务的硬中断。
> 上游：[07 v0.5](../07-m3-agent-design.md)、[T02b2](M3-T02b2-verification.md)。

## 本轮实现

五类纯数值核心（基础、包络、理论频率、匹配、比较）默认通过独立解释器执行。API保留受理、身份/范围、来源/hash/父结果核验、产物发布及数据库事务；子进程没有业务会话或数据库/模型凭据。算法、单位与既有来源校验保持，旧结果不重新计算。

单API数据库租约仍是唯一准入所有者，M1/M2共用两名额。子进程不自行增加名额，不需要第二套持久身份或独立Worker。Windows暂停创建进程、先归入Job Object再运行；关闭宿主句柄将终止整个子树。每个Job最多2进程（含venv启动器）、1GiB提交内存上限；数值库线程数固定1。该内存值是配置上限，不是实测内存峰值。

数值协议为固定动作/类型的JSON＋无pickle NPZ，校验nonce/action/hash及尺寸；JSON≤4MiB、数值总载荷≤128MiB、≤64个一维数组、每数组≤100万点，读取NPY头后才分配数组。环境只保留系统启动必要项和线程设置；私有执行回执记录backend/protocol/PID/耗时/有效期限，不保存密钥。

默认数值期限120000ms，可配置100—120000ms，并受任务剩余活跃预算进一步限制。独立看门狗不访问数据库；即使授权检查变慢，也能终止子进程。取消先提交任务终态，再通知对应数值Job并等待收尾，旧执行器/草稿仍受租约围栏约束。超时/崩溃/坏协议保存failed来源且无有效产物；终于退出后释放原名额。

## 分层测试

[协议测试](../../backend/tests/unit/test_numeric_codec.py)覆盖闭合类型往返、对象/非有限/多维数组拒绝、hash篡改、未知类型、目录逃逸、NPY伪造大shape在分配前拒绝，共8项。

[真实子进程专项](../../backend/tests/integration/test_numeric_process.py)共13项，所有API计算均使用真实数值子进程：

| 场景 | 判定 |
| --- | --- |
| 基础/包络/频率/匹配/比较 | 五类核心均在子进程完成；匹配仍有真实探索引用，比较保留已知−50%差值 |
| 凭据与委托 | 子进程PID不同，环境无VIBRADIAG/DEEPSEEK配置；只收到授权数值快照 |
| 崩溃/坏响应/挂起超时 | 来源failed、无产物、无活动Job；随后新运行可完成 |
| 混合容量 | M1/M2两个真实子进程占满后，第三请求429，不启动第三个计算 |
| 任务取消 | 返回cancelled时对应Job已收尾，迟到结果不生成草稿 |
| 确认撤销 | 运行中撤销后终止子进程并保存CONFIRMATION_REVOKED |
| 看门狗独立性 | 宿主guard被阻塞时，子进程仍先被期限终止，宿主随后收尾 |
| 剩余活跃预算 | 任务只余约1秒时限制子进程，耗尽后timed_out，预算不重置 |
| 测试模式隔离 | 非专用测试URL不能启用inline_test/故障场景；创建Job失败返回明确脱敏错误，不回退 |

历史权限/来源/事务故障测试保留可控inline_test，并限制当前数据库URL与专用测试URL一致；不能把这部分回归描述为全部使用子进程。最终全量及13项实际进程复验见[全量JUnit](../../experiments/m3-t02b3-tests.xml)、[进程专项JUnit](../../experiments/m3-t02b3-process-tests.xml)。全量回归577项通过，0失败、0跳过；最终13项真实进程复验另覆盖最新的Job创建失败脱敏分类。

## 实际HTTP与宿主死亡

脚本：[verify_m3_isolation_flow.py](../../backend/scripts/verify_m3_isolation_flow.py)。只操作专用测试库、私有文件根和自建回环API；保留实际进程句柄防止PID复用误杀。输入为明确标识的120000/1000000点解析AM合成CSV，不代表设备故障。

1. 正常任务完成并读取真实process执行回执，检查子进程环境未继承业务/模型配置。
2. 在测试库受控挂起数值子进程，确认其存活后终止自建API；验证Windows Job关闭后该数值进程退出。
3. 新API恢复原持久任务，显式恢复先对账旧中断调用，再以retry_of关联新调用完成；保留原记录与累计预算。
4. 另一个挂起任务通过真实cancel接口终止数值进程，返回cancelled且无草稿。
5. 独立M1挂起运行按配置期限失败，子进程退出、API继续可访问；切回正常配置后新运行可完成。

实际结果与源码指纹保存到[隔离回执](../../experiments/m3-t02b3-isolation.json)。未使用虚构工厂数据，也未调用模型。父进程死亡可能留下私有IPC副本；验证脚本只清理自己已确认死亡、在本次私有根下且nonce匹配的目录，不等于生产暂存清理策略。

```bat
.venv\Scripts\python.exe -m pytest -c backend\pyproject.toml backend\tests -q --junitxml=experiments\m3-t02b3-tests.xml
.venv\Scripts\python.exe -m pytest -c backend\pyproject.toml backend\tests\integration\test_numeric_process.py -q --junitxml=experiments\m3-t02b3-process-tests.xml
.venv\Scripts\python.exe backend\scripts\verify_m3_isolation_flow.py
node scripts\check-docs.cjs
.venv\Scripts\python.exe backend\scripts\check_secret_hygiene.py
```

## 边界与后续

本轮在Windows验证Job生命周期及限制，Linux父死亡信号分支尚未实测。参考[Windows Job Objects](https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects)及[提交内存限制](https://learn.microsoft.com/en-us/windows/win32/api/winnt/ns-winnt-jobobject_extended_limit_information)。数值子进程仍运行在同一OS用户下，不是用于不可信代码的文件/网络安全沙箱。

子进程期限包含其启动和执行，独立看门狗负责终止；宿主来源准备、授权SQL、文件IO、结果解码和入库可能晚于数值期限完成。取消只停止对应未完成数值Job，不能撤销已完成只读来源。不能据此声称整个诊断HTTP请求有精确硬实时保证。停止收尾超过5秒返回NUMERIC_STOP_PENDING，已提交取消终态保持。

业务表/图结构未新增迁移：仍0014、32张业务表加4张检查点表。生产暂存生命周期、独立Worker/多API、Linux部署、持续压力及整体运维准入留M6。下一步T03实现正式知识发布、来源/权限及pgvector检索，DeepSeek自主选择留T04；M2正式诊断资格和工厂试用准入仍保留原条件。
