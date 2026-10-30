# M2 总验收：确定性轴承工具与可追溯探索基线

日期：2026-10-30。软件阶段：M2 / v0.2。验收范围：本机离线工具、授权执行/产物、探索事实/证据、条件比较、可复现数值与公开数据适用性基线。操作者为项目开发代理，尚无独立现场工程师或工厂参与。

## 验收结论与范围

**M2软件开发交付完工，按本机探索工具范围有条件通过，可进入M3有界Agent开发。** 这不代表正式故障诊断、诊断证据发布或工厂试用准入通过。两个数据类验收项保留限制，四项诊断/发布条件继续阻断相关能力，不能由阶段结项绕过。

最终结论以[机器核验回执](../../experiments/m2-acceptance.json)为准；[退出计划](m2-exit-plan.json)逐项绑定实际执行测试及限制。要求的18个验收用例全部纳入追溯，16项在声明软件范围通过，D01/D02两项限范围通过。没有将缺失元数据、未知许可、零覆盖率或未见测试集不足改写成成功效果。

## 已交付能力

| 工具 | 结果与当前限制 |
| --- | --- |
| analyze_envelope | 离线SOS/Hilbert包络、有效原始索引、频响、谱标定、授权完整产物；缺rpm不阻断探索数值；诊断资格不足返回unavailable |
| calculate_bearing_frequencies | 几何/显式系数路径、FTF/BPFO/BPFI/BSF/2BSF及不确定量传播；不从型号或RAG猜参数 |
| match_frequency_evidence | 显著峰、谐波/边带、共享峰、转频/拍频替代解释及四种观察状态；持久探索facts/EV可回溯父hash和同一采集根 |
| compare_measurements | 受控双输入声明、双侧授权/确认/来源核验、可比性/同源拒绝、基础及可选完整包络差值；不自动判维修有效 |

执行具备受限请求、当前身份范围、不可变快照、幂等重放/冲突、共享M1/M2总并发2、原始/产物hash核验、失败与显式重试、启动中断恢复及旧执行隔离。当前是单API、本机有界同步执行，无持久队列或硬取消；M3再实现Worker/持久图执行。

数据库head为`0012_comparison_runs`、27张业务表。前端正式工作台已包含工具/来源/历史/比较的最小流程；完整知识管理、报告复核、工单及部署验收属于后续阶段。

## 18项验收追溯

精确测试选择器、参数化执行数与当前回执见退出计划及机器回执；下表是人工可读索引，不代替执行结果。

| 用例 | 结论 | 依据 |
| --- | --- | --- |
| M2-AT01-E01 | 通过 | 固定AM真值、幅值/包络误差及完整有效段测试 |
| M2-AT01-E02 | 通过 | 无调制指定目标无显著支持；本轮三个频带对照及v2回归 |
| M2-AT01-E03 | 通过 | 奇偶长度、g/SI等物理量与完整频率轴 |
| M2-AT01-E04 | 通过 | 非法频段/值、长度/周期/保护区及资源限制明确拒绝 |
| M2-AT01-E05 | 通过，限定夹具 | 边界冲击保留原hash/M1峰值与有效索引；邻带/保护区差异单独记录，不宣布任意profile已合格 |
| M2-AT01-E06 | 通过，限定夹具 | 拍频替代解释、带外/弱信号和邻带观察；不从单峰定类 |
| M2-AT02-F01 | 通过 | 独立几何真值、转速比例、单位等长比 |
| M2-AT02-F02 | 通过 | 参数/来源/运动学缺失或范围外时不造结果 |
| M2-AT02-F03 | 通过 | 几何/系数对应、冲突、BSF/2BSF及FTF语义 |
| M2-AT03-M01 | 通过 | 峰/无峰/窗口/显著性及多显著峰观察状态 |
| M2-AT03-M02 | 通过 | 不确定量交叉项、分辨能力与参考不足，不补零或裁窗 |
| M2-AT03-M03 | 通过 | 转频重合、共峰/重合组和2BSF共源 |
| M2-AT03-M04 | 通过，保留未检项 | 谐波/边带/拍频记录；独立邻带实验不回写历史EV为已合格 |
| M2-AT03-P01 | 通过 | 越权、父身份/hash、图登记原子性/撤权、不可变结果及跨算法版本历史读取 |
| M2-AT04-C01 | 通过 | 同条件差值/单位、零与近零基线百分比null |
| M2-AT04-C02 | 通过 | 条件不一致/缺失和同源复制明确不可比 |
| M2-AT05-D01 | 限范围通过 | CWRU/Paderborn来源与实体/文件清单可复现；许可及缺类边界见下文，封存数据未释放 |
| M2-AT05-D02 | 限范围通过 | 固定规则/逐类分母/拒绝与敏感性均有结果；严格公开匹配覆盖0/52，诊断准确率未知 |

## 本轮修复与配置选择

新增6类合成场景×3个频带的统一流水线基线：AM、无调制、双载波拍频、带外干扰、噪声中弱调制、首尾冲击。过程中发现matching-v1将多个未达显著性阈值的小峰也标成multiple_peaks歧义，已保留[修复前回执](../../experiments/m2-matching-v1-regression.json)。

新执行采用 **frequency-matching-v2**：只有多个通过既定幅度比/显著度阈值的峰才产生多峰歧义；候选仍保留以便追查。加入“全不显著”“一个显著且邻峰弱”和历史不重新计算回归。旧运行、配置、facts/EV和版本不变；阈值配置仍叫matching-v1，算法版本与阈值配置版本分别记录。

[当前基线](../../experiments/m2-release-baseline.json)：三个频带的无调制/带外/弱噪声指定目标均为no_observed_support，拍频为ambiguous；AM本身保留拍频替代解释，没有宣称确诊。2–4kHz保留为已有合成开发候选，对照1.8–4.2kHz及2.2–3.8kHz；保护区与有效长度不同使目标离格程度变化，最大bin幅值差不能直接解释成物理故障变化。没有按公开标签优化频带或发布合格诊断profile。

## 公开数据基线与资格缺口

- 52条可读manifest记录：CWRU开发4条、Paderborn开发24条、Paderborn验证24条。严格匹配适用性检查为52条unavailable，有效匹配覆盖率 **0/52**；没有实际匹配执行、分类准确率或误报率，不能把拒绝率当成正确率。
- 条件性时间轴研究是另一条离线实验：48条Paderborn中40条完成形状观察、8条拒绝，两次结果一致；18组合成插值真值通过。它没有解决存储单位/时间语义，也没有进入业务工具允许清单。
- 三个测试轴承归档保持不透明，没有提取/读取信号；未完成正式测试效果或信号级去重。外部子集缺滚动体类别、每类独立实体少，不能报告四分类泛化。
- Paderborn按已核对的CC BY-NC 4.0及引用要求用于本地研究。CWRU已查看页面未给出明确许可，不推断商业再分发授权。原始研究数据不随工厂产品提供。

## 资源、恢复及交付核验

本轮使用独立测试数据库和脚本拥有的回环API重新验证120000及1000000点输入；实际请求、hash/父链复核、受理/持久化与读取均在测量范围内，父结果创建耗时另行排除。原始回执：

- [包络/共享并发/恢复](../../experiments/m2-acceptance-tool-http.json)：实际M1+M2两任务并行，第三个请求429；终止本脚本拥有的API，重启后原键读同一interrupted任务，显式新任务重试成功。
- [匹配/fact/EV](../../experiments/m2-acceptance-matching-http.json)：大小两类输入、持久来源图及授权解析，探索证据资格false。
- [双输入比较/恢复](../../experiments/m2-acceptance-comparison-http.json)：两侧各120000/1000000点、基础/包络差值、缺条件/同源拒绝，以及实际进程重启恢复。
- [完整后端JUnit](../../experiments/m2-tests.xml)、[前端类型/构建](../../experiments/m2-frontend-build.json)、[总验收及源码清单](../../experiments/m2-acceptance.json)。完整后端485项通过（0失败、0跳过），前端类型检查/构建通过；全量包含M1回归、迁移重复执行与空schema重建。


| 流程 | 120000点POST（秒） | 1000000点POST（秒） | API进程峰值（MiB） |
| --- | --- | --- | --- |
| 包络 | 0.685 | 3.937 | 575.08 |
| 匹配 | 1.197 | 3.509 | 540.34 |
| 双输入比较（每侧点数） | 3.536 | 18.331 | 543.96 |

峰值为该脚本首个API进程跨规模/父计算的峰值，不是每个规模各测一次的内存峰值。

这些是单机指定规模的资源/恢复验证，不是P95、并发压力目标、24小时稳定性或现场诊断效果。API内存峰值不包含数据库、浏览器和模型。既往实际浏览器确认/谱图/来源/响应丢失重放记录仍见[T03b](M2-T03b-verification.md)、[T04b](M2-T04b-verification.md)；本轮未声称新增页面视觉验收。

## 交给M3的契约与禁止升级项

[机器可读工具契约](../contracts/m2-tool-contract.json)直接导出当前公共请求schema、四类工具版本、必要业务引用、状态/错误与重试方式。它描述现有服务，不授予权限；M3必须继续调用授权服务，不直接读取磁盘/表或填写checked、参数来源和证据ID。

M3可开发探索取证、缺参澄清、受控RAG和拒答路径；不得把observed_support改成故障确诊、no_observed_support改成健康，或把同源派生图虚增为独立证据。模型不得替用户确认输入、维护台账或自行批准配置。

下列条件不阻断上述受限M3开发，但继续阻断对应诊断/交付能力：

| 条件 | 影响与后续归属 |
| --- | --- |
| M2-R01：可核实的存储尺度/时间语义、转速与轴承参数不确定量 | M3保持资格门槛与拒答；M6现场/数据范围核对，不补造来源 |
| M2-R02：受控数值配置资格/发布 | M3证据接入时实现完整发布规则，M6复核；当前发布数0 |
| M2-R03：独立实体与足够类别的效果评测 | M3固定诊断评测及M6候选版本锁定；未建立前不得报告工业效果 |
| M2-R04：拟定用途的数据授权/许可 | M6交付复核；不随产品打包未知授权/研究限制数据 |

以上条件有完整责任阶段和执行阻断，不删除需求或将工厂试用P0降为可选。M3如要开放正式确定性诊断输出，必须先闭合对应条件；M6试用准入仍需全项目验收。

## 复现顺序

从项目根执行（测试需要已有独立测试数据库，数据库连接保存在忽略入库的backend/.env）：

```text
.venv\Scripts\python.exe backend\scripts\export_m2_tool_contract.py
.venv\Scripts\python.exe backend\scripts\verify_m2_release_baseline.py
.venv\Scripts\python.exe -m pytest backend/tests -q --junitxml=experiments/m2-tests.xml
.venv\Scripts\python.exe backend\scripts\verify_frontend_build.py
.venv\Scripts\python.exe backend\scripts\verify_m2_tool_flow.py --receipt experiments/m2-acceptance-tool-http.json --tests-report experiments/m2-tests.xml
.venv\Scripts\python.exe backend\scripts\verify_m2_matching_flow.py --receipt experiments/m2-acceptance-matching-http.json --tests-report experiments/m2-tests.xml
.venv\Scripts\python.exe backend\scripts\verify_m2_comparison_flow.py --receipt experiments/m2-acceptance-comparison-http.json --tests-report experiments/m2-tests.xml
.venv\Scripts\python.exe backend\scripts\verify_m2_acceptance.py
node scripts/check-docs.cjs
.venv\Scripts\python.exe backend\scripts\check_secret_hygiene.py
```

三个HTTP脚本和完整pytest按顺序运行，避免争用同一测试数据库的API租约。更改候选/源码后重跑受影响证据，总验收器会核对测试追溯、当前源码SHA、前端构建和资源回执，不只检查文件是否存在。

下一步：M3-T01，设计有界Agent图、工具选择/预算、持久执行与澄清/拒答输出契约。
