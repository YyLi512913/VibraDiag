# M2-T04b 双输入比较服务与页面验证

日期：2026-10-30。范围：将[T04a数值核心](M2-T04a-verification.md)接入受控声明、双输入工具运行、持久产物与工作台。M2整阶段及工厂试用准入尚未验收。

## 实现

- 数据库迁移至`0012_comparison_runs`，共27张业务表。新增不可变`comparison_declarations`，数据库约束绑定两侧输入、context和父运行；工具身份字段与快照不可修改。
- 工程师通过`backend/scripts/manage_diagnostics.py comparison-declaration --organization-id <组织UUID> --username <工程师账号> --input <声明JSON>`维护比较依据，使用交互密码及审计。声明包含两侧context/basic/可选envelope ID、采集来源根、开始时间来源、负载依据、测量不确定量、顺序依据及负载容差。缺条件可登记，运行时明确返回不可比。
- 采集链提供六项配置：sensor_id、channel_id、daq_id、mounting_reference、calibration_reference、acquisition_configuration，以及source；服务端计算配置SHA。公开声明不接受checked和configuration_sha256。原始转速、负载、时间及采样信息来自输入版本和context，不由运行请求覆盖。人工声明属于有审计的来源陈述，不代表独立现场校准。
- `GET /api/v1/tool-runs/comparison-options/{measurement_id}`返回当前用户能访问的双侧声明。运行仍使用`POST /api/v1/tool-runs`，带Idempotency-Key。

```json
{
  "tool_name": "compare_measurements",
  "measurement_revision_id": "比较后输入版本UUID",
  "confirmation_id": "当前用户的比较后确认UUID",
  "before_confirmation_id": "当前用户的比较前确认UUID",
  "declaration_id": "受控声明UUID"
}
```

显式重试另带retry_of并使用新幂等键。父结果可由其他授权人员生成，两份输入均须当前执行人确认。准备阶段按固定顺序锁定双侧记录；计算在事务外，共享M1/M2总并发2。

## 来源与权限

接受、发布、查询和下载均校验双输入权限与历史资产范围。只有比较后权限不能列出、读取或下载包含比较前信息的结果。读取两侧原始文件、基础产物和可选完整包络时核验hash，禁止父文件篡改或越权继续发布。

两份原始序列转换为规范SI浮点值并统一正负零后计算内容hash，补充原文件hash、执行ID及采集根排重。不可比和同源状态保留原因、空差值；不得将同一信号换文件名或重复上传当作独立前后数据。

产物为结果JSON与manifest，记录双侧来源及父hash。历史读取分别显示两侧版本是否仍为当前，并核验本次与父运行确认有效性。比较不注册单根匹配用的fact/EV对象，diagnostic_evidence_eligible为false，不自动输出维修有效或故障确诊结论。数值及百分比边界沿用T04a。

## 实测结果

- 新增23项比较服务集成测试：双侧确认、幂等、禁止参数覆盖、工程师维护、缺参/同源、不可变约束、父产物篡改、版本/确认变化、单侧权限泄露、计算中撤权、容量、失败重试、完整包络与规范内容排重等。
- 完整后端 **417 passed，0失败、0跳过**，含迁移重复执行和空schema重建；[JUnit回执](../../experiments/m2-t04b-tests.xml)。前端vue-tsc与Vite构建通过。
- 实际Tabbit页面完成两侧用户确认、正常比较7行指标（合成RMS变化−50%）、缺负载容差时无差值、共享采集根时无差值。服务端完成后注入响应丢失，刷新并重新选择记录后恢复原请求；重放HTTP200、同一运行ID。页面无横向溢出。[浏览器回执](../../experiments/m2-t04b-browser.json)。本次验证使用实际DOM与HTTP响应，未保存页面截图，不作为完整视觉验收。
- 独立测试数据库、真实HTTP及API进程验证：[资源回执与源码SHA](../../experiments/m2-t04b-http-resources.json)，复现脚本`backend/scripts/verify_m2_comparison_flow.py`。两组均有双输入基础/完整包络父结果，普通和包络RMS差均为−50%，原键重放返回同一资源。

| 每份输入点数 | 比较POST耗时（秒） | 结果 |
| --- | --- | --- |
| 120000 | 2.606 | completed / comparable |
| 1000000 | 15.410 | completed / comparable |

POST耗时包含双侧来源校验、持久化及最终重读，不含此前父结果创建。单机每规模一次，不能称P95。API进程峰值工作集570019840字节，包含首个进程的父分析，不含数据库、浏览器和模型。

百万点运行过程中终止本脚本拥有的实际API进程，重启后原任务为interrupted；原键读取同一任务，显式新任务重试completed。浏览器响应丢失与服务端实际进程中断分别验证。

## 边界与下一步

所有本轮信号和条件均明确为合成数据，没有使用工厂设备数据；幅度下降仅证明数值流程，不证明维修效果、独立采集或现场诊断准确率。声明工具为受控CLI，完整维护页面后续再做。历史T04a的394项记录保留，不用本轮417项覆盖其历史结果。

下一步 **M2-T05：公开数据来源/许可清单、按设备或采集实体分组拆分、固定开发基线与评测记录**。之后依据完整M2退出条件做阶段验收，再接M3正式Agent。
