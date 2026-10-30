# M2-T02b1 目标参数与诊断上下文实施记录

日期：2026-10-30。上游：[06设计](../06-m2-tool-design.md)、[T02a数值验证](M2-T02a-verification.md)。本子步完成目标/context持久化，T02b整体仍在实施。

## 1. 本轮交付

- 0009_diagnostic_contexts迁移：新增asset_components、bearing_revisions、diagnostic_contexts。M1的17张业务表增至20张，不改变M1四产物完成约束。测试库、开发库均已升级；空schema从头重建及重复升级通过。
- 组件绑定设备，轴承参数为不可变版本，修订需提交expected_current_revision_id并锁定组件，同版本并发修改只有一个成功。组件身份不可覆盖/删除，可停用；历史上下文仍可授权读取。
- context绑定采集revision、设备、组件及轴承版本，复合外键约束同组织/同设备/同组件。新context使用当前输入和当前轴承版本，旧context保持原绑定并返回两个is_current标记。
- 工程服务要求有效会话、engineer角色、设备写范围和采集写范围。管理员没有隐含业务权限；读取同时检查当前采集范围和历史输入设备范围，撤权后不能继续解析历史context。
- rpm只从M1不可变revision加载。声明不接收rpm、rpm_checked、target或checked_by；核对人/时间由服务端记录。几何长度归一为mm，再校验有限范围；未知/不完整条件保留，数值工具返回unavailable而非伪造0。
- 抽取compute_capacity.capacity，M1使用这一总量为2的并发门。M2执行服务尚未接入，因此此时只验证门实例共享及容量，不宣称已经完成M1/M2同时执行的压力测试。

用户提交的出处、版本与核对记录属于可追溯工程声明，不等于独立来源鉴真、专业资质或现场标定。当前没有profile资格发布能力，不会把这些checked字段当成已合格的诊断配置。

## 2. 受控维护入口

从项目根执行，密码交互输入，不作为命令行参数；执行完毕撤销该CLI会话。JSON≤64KiB，未知字段/来源结构/数值非法拒绝。

```bat
.venv\Scripts\python.exe backend\scripts\manage_diagnostics.py --help
.venv\Scripts\python.exe backend\scripts\manage_diagnostics.py component --organization-id ORGANIZATION_UUID --username ENGINEER_NAME --input component.json
.venv\Scripts\python.exe backend\scripts\manage_diagnostics.py bearing-revision --organization-id ORGANIZATION_UUID --username ENGINEER_NAME --input bearing.json
.venv\Scripts\python.exe backend\scripts\manage_diagnostics.py context --organization-id ORGANIZATION_UUID --username ENGINEER_NAME --input context.json
.venv\Scripts\python.exe backend\scripts\manage_diagnostics.py read-context --organization-id ORGANIZATION_UUID --username ENGINEER_NAME --id CONTEXT_UUID
```

component.json格式：

```json
{"asset_id":"ASSET_UUID","component_code":"motor-drive-end","name":"电机驱动端轴承"}
```

bearing.json使用[请求schema](../../backend/src/vibradiag/schemas/diagnostic_contexts.py)的CreateBearingRevision：component_id、expected_current_revision_id（初次null）、parameterization、geometry或coefficients、reason。明确选择一条参数路径，附来源reference/version；不能传checked或核对人。

context.json使用CreateDiagnosticContext：measurement_revision_id、component_id、bearing_revision_id、conditions、可选acquisition、source_reference、reason。conditions补充rpm依据/不确定量来源/稳态/参考运动，不另填rpm；acquisition显式填写带宽和出处，不能由采样率反推。字段可不完整，但不代表相应计算条件已满足。目标和输入关联设备后才可建立context；创建context不代替操作者自己的输入确认，后续新工具执行仍须校验confirmation。

CLI只适用于受信任本机工程维护，未提供网络管理API或界面；本轮未向开发库加入模拟设备/context业务数据。

## 3. 验证证据

[完整JUnit结果](../../experiments/m2-t02b1-tests.xml)：**241 passed，0失败、0错误、0跳过**，最终pytest总耗时30.65s。原218项回归保留，新增23项覆盖：

- 从真实PostgreSQL解析M1转速、m→mm归一、来源与操作者审计；独立合成夹具BPFO=90Hz，不冒充实际轴承型号。
- 禁止客户端覆盖可信字段、不同角色/组织/采集与设备范围、撤权与失效会话。
- 轴承版本并发冲突、旧context保留原rpm、当前/历史版本标记、新context拒绝旧参数。
- 数据库历史不可更新/删除、组件身份不可移动/删除、跨组织/组件/设备关系不合法。
- 条件不足保持unavailable、Nyquist带宽越界、转速不确定区间错误、非合成输入的精确合成来源冲突、单位转换溢出。
- M1使用共享的总并发2实例；已有M1计算容量、幂等、损坏与恢复回归全部通过。

第一次新测试运行发现测试夹具ON CONFLICT列与原M1联合主键不同，已改为真实主键列后重跑；没有改变业务授权规则或放宽数据库约束。

[只读数据库及源码回执](../../experiments/m2-t02b1-contexts.json)记录Python3.11.15、PostgreSQL18.6、20张业务表、不可变触发器及12份源码/锁文件/测试结果SHA256。它不包含凭据或业务行；M1/T02a历史证据保持原样。

复现：

```bat
.venv\Scripts\python.exe backend\scripts\migrate.py
.venv\Scripts\python.exe -m pytest -c backend\pyproject.toml backend\tests -q --tb=short --junitxml=experiments\m2-t02b1-tests.xml
.venv\Scripts\python.exe backend\scripts\verify_m2_contexts.py
node scripts\check-docs.cjs
.venv\Scripts\python.exe backend\scripts\check_secret_hygiene.py
```

测试使用专用PostgreSQL测试库，增加随机ID记录；不清空开发数据。前端本轮未变，未重复浏览器验收。CLI帮助已实际执行，写入及读取服务由真实数据库集成测试验证，尚未做交互CLI端到端验收。

## 4. 下一步T02b2

实现tool_profiles、tool_runs、tool_artifacts、独立幂等账本，受理/计算/完成分事务，参数快照和原文件hash可追溯；接入同一API租约、总并发门和启动中断恢复。增加正式工具运行/历史/系列/下载接口，并覆盖响应丢失、撤权、失败、对账与并发。

配置资格需要真实执行并核对声明范围的验证清单，不能允许客户端上传一段qualification或只根据此次单元测试自动发布。匹配、facts/evidence、比较、公开数据验证及工厂试用准入仍属后续任务，当前不宣称M2整阶段完成。
