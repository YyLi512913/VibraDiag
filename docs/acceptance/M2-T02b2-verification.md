# M2-T02b2 正式工具执行与持久化验证

日期：2026-10-30。上游：[06详细设计](../06-m2-tool-design.md)、[T02b1上下文](M2-T02b1-verification.md)、[T02a数值](M2-T02a-verification.md)。本轮接通确定性工具服务与API；匹配/事实/证据、正式界面及M2整阶段验收仍待实施。

## 1. 交付行为

新迁移0010_tool_runs新增tool_profiles、tool_runs、tool_artifacts、tool_idempotency_records，当前**24张业务表**。开发/测试库升级及独立空schema重建通过，未修改已执行的0001—0009或M1固定四产物规则。

工具接收业务ID，由服务端校验当前输入、本人有效确认、设备/采集权限、上下文/参数版本及配置范围。包络可不带context执行探索计算；频率工具必须绑定context，rpm来自M1 revision。客户端不能覆盖转速、几何、频段、路径、checked或qualification。

受理事务保存running、完整输入/参数/配置快照、SHA256指纹、独立幂等账本与审计；计算及落盘在事务外进行。完成前再次核对会话/当前角色和数据范围、确认状态、原文件及产物hash，最终事务一次登记产物、结果、完成状态、幂等和审计。参数在计算期间修订时保留受理快照，结果标识历史版本；新运行仍须用当前版本。

幂等键按组织/操作者隔离，覆盖这两种工具；同键同请求重放原资源，运行中202、终态200；同键不同请求409。新资源201，包括已受理后失败的资源，调用方必须检查status/result_status。失败后的显式重试用新键/new ID，可传retry_of关联同输入同工具的failed/interrupted记录。

状态分开：运行状态running/completed/failed/interrupted；数值结果ok/unavailable/failed。completed + unavailable表示条件检查结束、未得到所请求的有效计算产物，不是诊断成功。failed/interrupted不登记有效产物或证据；未登记的已落盘文件由只读对账识别，不能猜测提交失败后直接删文件。

## 2. 配置与资格边界

新增受控CLI操作envelope-profile，沿用工程角色/设备范围、有界JSON、交互密码及审计。声明字段：asset_id、sampling_rate_hz、band_low_hz、band_high_hz、modulation_low_hz、modulation_high_hz、selection_reason。服务端验证数值约束，存入不可变配置、数值库/算法版本和hash；运行时检查同设备、同采样率、同数值版本。

```bat
.venv\Scripts\python.exe backend\scripts\manage_diagnostics.py envelope-profile --organization-id ORGANIZATION_UUID --username ENGINEER_NAME --input envelope-profile.json
```

本轮只能登记**unqualified探索配置**，数据库也拒绝qualified状态；没有开放资格自证/发布接口。diagnostic_evidence请求不会偷换为探索结果，而是completed/unavailable并列出qualified_numeric_profile等缺失项。已核对带宽、软件工程角色、合成测试通过均不自动代表现场标定或可发布诊断证据。资格发布需要后续执行并核查完整的适用范围/验证清单，再新增受控版本与相应迁移。

## 3. 正式接口

以下路径均位于/api/v1，沿用登录Cookie和写请求X-CSRF-Token；POST另需Idempotency-Key。

| 接口 | 行为 |
| --- | --- |
| POST /tool-runs | 受限判别schema，执行包络或理论频率；201/200/202及409/429语义同上 |
| GET /tool-runs?measurement_id=... | 授权分页运行历史，历史版本受原设备范围约束 |
| GET /tool-runs/{id} | 状态/结果、缺参/限制、输入快照、当前输入/轴承版本标记、确认有效性和产物引用 |
| GET /tool-runs/{id}/series?kind=envelope或spectrum | 展示数组≤2000点，完整数值不进入模型消息；无数组的结果409 |
| GET /tool-runs/{id}/artifacts/{kind} | 白名单result/arrays/manifest；hash验证、授权及下载审计 |

包络请求示例，UUID使用实际授权资源：

```json
{
  "tool_name": "analyze_envelope",
  "measurement_revision_id": "REVISION_UUID",
  "confirmation_id": "OWN_CONFIRMATION_UUID",
  "profile_id": "PROFILE_UUID",
  "context_id": "CONTEXT_UUID",
  "mode": "exploratory"
}
```

context_id可省略；缺转速不阻止合法探索包络。理论频率请求为：

```json
{
  "tool_name": "calculate_bearing_frequencies",
  "measurement_revision_id": "REVISION_UUID",
  "confirmation_id": "OWN_CONFIRMATION_UUID",
  "context_id": "CONTEXT_UUID"
}
```

成功包络保存result.json、arrays.npz、manifest.json；理论频率或unavailable保存result.json和manifest.json。NPZ含完整滤后波形/有效段包络/频率/幅度及滤波器响应；单文件≤64MiB，读取allow_pickle=False。数据库延迟约束按工具和结果状态验证产物集合，不沿用M1“四文件”假设；终态/已受理快照/配置/产物不可覆盖。

运行仍为单API租约、同步有界计算，无持久排队、硬超时或取消承诺。M1与M2使用同一总并发2门；API启动统一恢复两类遗留running为interrupted，旧执行不能覆盖终态。

## 4. 自动测试与故障验证

[完整JUnit](../../experiments/m2-t02b2-tests.xml)：**267 passed，0失败/错误/跳过**；新增26项，原241项全部保留。最终pytest耗时57.10s。

实际API/专用PostgreSQL/隔离文件根覆盖：包络与频率真值、可省context、无资格拒绝提升、白名单/2000点展示、幂等同键终态/运行中重放及冲突、身份/CSRF/角色/跨组织、客户端数值覆盖拒绝、撤权前后/计算中撤权、确认撤销、原输入历史快照、错误采样率/错context、产物损坏、终态/运行快照不可变、缺产物完成拒绝、共享容量429、磁盘失败、完成事务回滚、孤立文件对账、显式重试及恢复后的终态保护。

首次完整回归267项通过后，补齐工具接口统一422错误结构与轴承版本状态字段，并重跑完整267项再次通过。未新增模型或第三方依赖，未改前端；本轮未重复浏览器验收，也没有把API测试写成前端接入完成。

## 5. 真实HTTP、资源与进程终止

[HTTP/资源/恢复回执](../../experiments/m2-t02b2-http-resources.json)来自实际临时Uvicorn服务，不是内存TestClient。脚本使用独立测试库、新建随机组织与工程账号、隔离文件根、合成AM；只终止自己启动并核验PID的API进程，不操作已有开发服务，不调用云端模型。

回执保留120000/1000000点CSV字节数/hash、实际POST耗时、Windows API进程峰值工作集、混合并发和进程恢复检查、完整JUnit统计及源码/锁文件/测试结果SHA256。具体耗时与内存以该JSON为准；单次本机测量不是P95或现场性能准入，进程峰值覆盖终止前的独立/混合运行，不含PostgreSQL、浏览器或模型内存。

实际观察：M1基础分析与M2百万点包络同时为running，第三个请求429，两条已受理任务完成。另一次工具running持久化后强制终止API，重启恢复为interrupted；同键重放返回原ID/200且不重算，显式新键重试完成。

复现按顺序执行，HTTP脚本与完整测试不要同时占用专用测试库的单API租约：

```bat
.venv\Scripts\python.exe backend\scripts\migrate.py
.venv\Scripts\python.exe -m pytest -c backend\pyproject.toml backend\tests -q --tb=short --junitxml=experiments\m2-t02b2-tests.xml
.venv\Scripts\python.exe backend\scripts\verify_m2_tool_flow.py
node scripts\check-docs.cjs
.venv\Scripts\python.exe backend\scripts\check_secret_hygiene.py
```

历史T02a/T02b1和M1回执保持原样，对应当时schema/代码/环境；本轮不覆盖为新结果。

## 6. 下一步

进入T03：频率匹配、峰/容差/谐波/边带与转频/拍频混淆、facts/evidence来源关系和最小包络/标线/限制界面。配置资格发布仍需完整验证支撑。Agent正式决策与RAG在M3；当前工具结果不构成完整故障诊断或工厂试用准入。
