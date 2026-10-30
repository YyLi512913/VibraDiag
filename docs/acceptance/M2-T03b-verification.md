# M2-T03b 匹配运行、探索证据与最小页面验证

日期：2026-10-30。上游：[06设计](../06-m2-tool-design.md)、[T03a数值核心](M2-T03a-verification.md)、[T02b2运行基础](M2-T02b2-verification.md)。本步接通匹配持久化与最小页面，不代表完整故障诊断、配置资格发布或工厂试用准入。

## 1. 本轮交付

0011_matching_evidence新增analysis_facts、evidence_items，扩展tool_runs的三个父引用及匹配类型；当前**26张业务表**。开发/测试库升级、重复升级、空schema重建及事务回滚通过；不修改已执行的0001—0010。

匹配复用单API租约、总并发2、同组织/操作者幂等空间、运行快照、受理/计算/完成事务、失败及启动中断恢复。只有当前输入/轴承版本、本人有效确认和当前采集/设备范围满足时才受理；匹配配置固定matching-v1，客户端不能另填滑移、阈值、几何、转速、路径或资格。

父结果须为同组织授权资源：同revision的成功基础分析、同revision/context的成功包络和理论频率。受理快照保存各父运行算法/配置指纹和产物hash/大小清单。计算通过原有授权/完整性读取服务加载完整谱，完成前再次核对来源与确认；重读匹配、下载或解析事实/证据时还会校验所有父产物和原文件。父文件损坏、错输入/context或撤权不能靠一个有效UUID绕过。

事实类型为calculation_fact、observation_fact、matching_relation。观察峰按执行/bin身份去重；关系引用目标及候选峰事实。根关联同一file_object、原文件SHA256、revision和context，轴承版本可由该context及运行快照解析。63个目标材料不会变成63份独立采集。

fact/evidence使用运行UUID命名空间生成稳定ID；同键重放返回原运行和原ID，不再登记。内容/父关系不可更新或删除；数据库校验同组织/同运行/共同根、父事实先存在、材料归属及最终数量。图和产物与完成状态同事务，失败/中断/unavailable不留下有效图，发布文件但事务失败只作为对账孤立文件保留。

## 2. API与有效性

沿用Cookie登录、写请求X-CSRF-Token和POST的Idempotency-Key。新增匹配请求：

```json
{
  "tool_name": "match_frequency_evidence",
  "measurement_revision_id": "CURRENT_REVISION_UUID",
  "confirmation_id": "OWN_CONFIRMATION_UUID",
  "context_id": "CONTEXT_UUID",
  "envelope_run_id": "ENVELOPE_UUID",
  "frequency_run_id": "FREQUENCY_UUID",
  "basic_analysis_id": "BASIC_UUID",
  "config_profile": "matching-v1"
}
```

`POST /api/v1/tool-runs`返回运行状态、fact_refs/evidence_refs、父引用及结果/清单产物。匹配没有数组产物，谱图读取授权包络父结果；运行完成与观察状态分开，不将unavailable写成成功诊断。

| 新增/扩展入口 | 行为 |
| --- | --- |
| GET /api/v1/facts/{id} | 解析类型、单位/数值、父事实、共同根、来源运行、输入/context及当前版本状态 |
| GET /api/v1/evidence/{id} | 解析关联关系事实、观察极性、来源完整性和确认有效性 |
| GET /api/v1/tool-runs/options/{measurement_id} | 该授权记录当前revision的上下文和同设备/采样率探索配置，最多各100条；不是维护或审核接口 |
| GET /api/v1/tool-runs?measurement_id=... | 历史列表增加context/profile引用，供同输入父结果选择 |

不存在/不可披露的ID返回404；来源产物损坏等返回409。解析时从已验证运行结果重建预期图，比较存储的内容和单位，不以“数据库里存在这个ID”作为真实性结论。

本轮所有evidence用途限定exploratory_observation，**evidence_eligible=false**，数据库也禁止改为true。它们是有来源的探索观察材料，不是已审核诊断证据。确认撤销时保留授权历史，但validity.input_confirmations_active=false；新运行被拒绝。不同操作者共享输入时，父结果确认撤销也会使匹配历史的input_confirmation_active=false，即使匹配操作者本人的确认仍有效。当前配置仍未合格，混淆检查/滤波敏感性仍不完整，不能发布故障结论。

## 3. 最小工作台

正式采集详情新增“轴承工具 · 探索观察”：

- 选择真实可访问上下文/探索配置，执行包络和理论频率；当前页同输入/上下文的成功工具结果可作为匹配父结果。
- 基础分析父结果从当前基础分析历史页选择；工具历史每页20条，可翻页。尚无工程参数/配置时明确提示补齐，不提供默认猜测值。
- 显示原始有效索引、包络谱、理论名义标线、63项窗口/状态/峰/歧义和共享窗口组；图最多2000个展示点，计算仍用完整谱，标线不等于命中。
- 从“查看观察来源”进入关系事实，再查看上游目标/峰事实，显示共同采集根、hash、单位和有效性；匹配表限制高度滚动，不让63行挤满整个页面。
- 提供工具历史、产物下载和失败/中断显式重试。未确认响应的请求只保存按组织/用户/采集版本隔离的请求ID/参数及幂等键，不缓存业务结果。
- 请求失败保留待确认请求；刷新后可同键重放，停止跟踪不取消服务端任务。401通知工作台重新登录，403/404清理工具展示和选择。用户/输入版本切换销毁旧组件，异步结果按生命周期隔离。

目标/参数/profile维护仍是受控工程CLI，未扩成管理系统。没有开放模型写台账、审核配置或生成工单。

## 4. 自动测试与构建

[完整JUnit](../../experiments/m2-t03b-tests.xml)：**332 passed，0失败/错误/跳过**，新增21项，原311项回归保留；最终耗时见JUnit记录。前端vue-tsc及Vite生产构建通过。

新增实际PostgreSQL/API测试覆盖：完整匹配图及三类事实解析、同键无重复、伪造/错类型父ID、角色/跨组织/采集权限、配置引用隔离、父产物损坏、确认撤销、运行中撤销/中断、事实/证据不可改及不可提升资格、撤权后的旧ID、图写入失败与缺图完成原子拒绝、共享容量429、未知数值覆盖拒绝、不确定量不足unavailable且无假图。

首次迁移试验遇到psycopg对DDL中百分号的占位符解析，已在尚未执行成功的0011中正确转义后重新升级/重建；没有改旧迁移或放宽约束。前端初次类型检查发现模板内下载回调类型问题，移至脚本处理函数后构建通过。

## 5. 实际浏览器验证

[浏览器回执](../../experiments/m2-t03b-browser.json)来自Tabbit实际页面，开发库独立新增明确synthetic的资产、目标和48322点AM记录，使用本机已有工程账号；账号密码不进入源码、文档或回执。

实际操作：登录→选择合成记录→输入确认→基础分析→包络→理论频率→选择三个父结果→匹配→查看观察来源→关系事实→上游理论事实（BPFO=90Hz）及单位。结果214条事实、63条探索材料，一个共同采集根；页面有63行匹配和一个谱图，DOM检查无水平溢出。

另一次包络请求在服务端完成后人为丢弃响应，刷新页面仍保留待确认状态；重放HTTP200、原ID、历史只新增一次。工具视图重新打开可读持久化结果，未重复登记图。

[实际绘制的谱图](../../experiments/m2-t03b-envelope-chart.png)通过页面canvas导出并用图像查看器检查了波形与理论标线。浏览器整页截图超时、定位截图发生错位，因此**没有整页截图视觉验收**；此PNG不是整页截图。未做移动端专项验收，不能由DOM无溢出推断所有视口排版合格。验证页面已保留供查看。

## 6. 真实HTTP与资源记录

[HTTP/资源回执](../../experiments/m2-t03b-http-resources.json)由[verify_m2_matching_flow.py](../../backend/scripts/verify_m2_matching_flow.py)启动自有临时Uvicorn，使用专用测试库、新建随机组织/账号及隔离文件根，全部为合成AM和精确数学参数，不调用云端模型、不终止开发服务。

| 原始点数 | 匹配POST耗时 | 事实 / 探索材料 | 结果 |
| --- | --- | --- | --- |
| 120000 | 约1.08s | 133 / 63 | 持久化并授权解析通过 |
| 1000000 | 约3.09s | 284 / 63 | 持久化并授权解析通过 |

差异来自实际谱峰候选数，不代表独立证据数量增加。API进程峰值工作集约542MiB，包含两种长度及其父计算，不含PostgreSQL/浏览器/模型内存。POST耗时含父来源/hash核验、匹配、产物/图持久化及最终验证读取，不含前置上传/父计算；单次本机值不是P95或工厂性能准入。

回执保存输入CSV哈希、环境、JUnit统计和后端/迁移/测试/脚本/前端/依赖锁/测试结果SHA256。历史M1及T02/T03a证据不覆盖。

复现先完成后端测试，再启动专用测试库HTTP脚本，二者不要同时占用单API租约：

```bat
.venv\Scripts\python.exe backend\scripts\migrate.py
.venv\Scripts\python.exe -m pytest -c backend\pyproject.toml backend\tests -q --tb=short --junitxml=experiments\m2-t03b-tests.xml
npm --prefix frontend run build
.venv\Scripts\python.exe backend\scripts\verify_m2_matching_flow.py
node scripts\check-docs.cjs
.venv\Scripts\python.exe backend\scripts\check_secret_hygiene.py
```

## 7. 下一步T04

实现前后比较工具，先校验设备/目标/测点/方向/采集顺序、转速/负载、带宽和处理版本可比性；缺条件输出not_comparable，不直接算改善率。before为0时百分比null，同次采集复制不当作维修前后独立采集。随后T05公开数据manifest、分组/留出验证及配置适用性评测；M2整阶段、正式Agent决策和工厂试用准入仍未验收。
