# M1-T04 基础分析、产物与幂等执行验证

日期：2026-10-29。本记录覆盖M1-T04后端子任务；完整M1与现场试用仍未验收。

## 实现范围

正式链路：登录 → 已校验CSV → 当前revision的本人输入确认 → POST /api/v1/analyses → running持久记录 → 全文件计算 → 产物校验 → completed事务 → 授权重读。基础计算不使用LLM，不输出轴承故障分类、健康判断或烈度等级。

算法basic-scaled-v1使用缩放矩计算，避免直接平方/四次幂导致溢出或下溢；输出原始RMS（含DC）、AC RMS、均值、绝对峰值、峰峰值、峰值因子与Pearson总体峭度。零RMS/零方差指标返回null及原因；不可表示的派生指标显式不可用，单位换算等无法支持时运行失败，不伪造数值。谱仅去均值，使用周期Hann及相干增益，单边峰值幅度按DC/Nyquist/奇偶长度归一化。单位g按9.80665换算为m/s²；时长N/fs，最后采样时刻(N−1)/fs。

分析绑定原文件SHA256、参数revision/来源、确认、算法版本及配置摘要；0007保护已接受快照不被改写。计算在数据库事务之外执行。完成事务登记result JSON、有限波形JSON、全谱数值NPZ、manifest JSON四种产物、指标与审计。manifest列前三种文件的hash/大小，其自身hash在数据库登记，避免循环自哈希。NPZ读取关闭pickle，JSON禁止NaN/Infinity。正式详情、series与下载核对原文件、四种产物及manifest，并检查当前资源与历史版本的设备权限；不直接暴露磁盘路径。

Idempotency-Key按组织/操作者/操作域隔离：首次接受返回201（须继续检查status，可能completed或failed）；同键同请求已终态返回200原资源，运行中202；同键不同请求409。并发相同键仅计算一次；容量总计2，满时429，无持久排队。显式重试用新键/新analysis_id，可通过retry_of关联同revision的failed/interrupted任务；同键不会自动重算失败任务。

M1仅支持一个API进程。进程生命周期持有数据库session advisory lock，第二进程拒绝启动；租约连接失效后拒绝新分析，须重启API，不静默重连并假定仍持锁。启动时将遗留running及其幂等记录改为interrupted并审计；不会自动续算。运行终态不可覆盖，旧进程不能把已中断任务改写为成功。此机制是M1受限同步运行，不是M3的Worker/队列/检查点恢复。

旧实验接口改为/api/v1/prototype/analyses，仍受VIBRADIAG_ENABLE_PROTOTYPE开关控制。前端原型客户端同步改用该地址；正式/analyses不再接收旧上传表单。正式数据尚未接入Agent，原型诊断仍读取原型结果；正式前端流程留M1-T05。

## 实际验证

环境：Windows、Python3.11.15、PostgreSQL18.6；依赖沿用锁文件。专用测试库与临时文件根隔离，无云端模型调用。

| 项目 | 实际结果 |
| --- | --- |
| 完整后端回归，生成experiments/m1-t04-tests.xml | 129 passed in 23.37s，0 skipped；本轮新增29项，原100项继续通过 |
| 前端npm run build（含vue-tsc） | 类型检查与Vite构建通过；本轮只调整原型API地址，未完成正式页面 |
| 固定数值验收 | 2sin(64Hz)、加DC=3、g换算、零/正负常量、偶数Nyquist、奇数长度、离格泄漏与重复性通过；1e−300—1e300尺度用独立Decimal公式核对RMS/峭度 |
| 输入/执行约束 | 未确认、跨revision/他人确认、非当前revision、错误重试及缺/坏幂等键拒绝；运行中改参数保持原快照；计算中禁用用户/撤销确认不会completed |
| 并发/持久化 | 同时相同键只创建/计算一次；运行中重复202、满容量429；失败同键返回原失败记录；新键显式重试生成新ID；单进程锁及启动中断恢复通过 |
| 访问/完整性 | 详情、series与下载实际路由检查同组织未授权用户、跨组织与管理员范围；原文件或产物损坏/缺失拒绝作为有效结果展示 |
| verify_analysis_flow.py真实HTTP | 1024点64Hz合成信号指标/NPZ通过；百万点合成常量输入提交后从DB观察到已提交running，强制终止脚本自己启动的API；重启后旧完成结果可读、遗留任务interrupted、同键不重算、新键显式重试完成 |

原始证据：[JUnit](../../experiments/m1-t04-tests.xml)、[HTTP与实际进程终止记录](../../experiments/m1-t04-http-smoke.json)。HTTP脚本新增两份明确标为synthetic的开发输入及关联运行，不覆盖旧记录；这些不是现场数据或故障识别评测。

## 复现与后续

从根目录运行 `.venv\Scripts\python.exe backend\scripts\migrate.py`，然后执行后端测试、前端build或 `.venv\Scripts\python.exe backend\scripts\verify_analysis_flow.py`。HTTP脚本读取私有.runtime/local-access.json中的local-engineer账号，使用临时回环端口；运行时需没有其他新版API持有同一数据库的M1单进程锁。运行常规开发服务前先停止该脚本自己的临时服务（脚本finally会处理）。对账CLI已扩展到未引用分析文件/分析暂存目录，仍只报告、不删除。

下一步M1-T05：前端登录、正式导入/确认/分析/历史版本流程，原型数据显式迁移工具、浏览器验证与性能基线，再执行完整M1五组验收。尚未验证PostgreSQL17参考环境、共享部署、完整Agent持久恢复、专业故障取证或现场适用性。
