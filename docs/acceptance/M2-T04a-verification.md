# M2-T04a 前后比较数值核心验证

日期：2026-10-30。上游：[06设计](../06-m2-tool-design.md)、[T03b正式匹配与探索材料](M2-T03b-verification.md)。本步实现比较核心与内部声明契约；T04b再接真实记录/声明维护、运行持久化、权限和页面。当前没有比较API或新的数据库表。

## 1. 已实现行为

[analysis/comparison.py](../../backend/src/vibradiag/analysis/comparison.py)提供compare_measurements，接收两份内部采集快照、对应基础分析摘要及比较配置，可选两份完整包络结果。结果是NumericToolResult，数组不进入比较概要，不依赖LLM。

[schemas/comparison.py](../../backend/src/vibradiag/schemas/comparison.py)定义内部来源、时间核对、采集链、负载依据/容差、指标不确定量和输入版本。checked/source字段属于后续服务从授权工程声明重建的值，不是前端或模型可自行提交的资格。本轮没有将M1里未提供的负载/时间或来源说明自动升级为已核对条件。

| 层次 | 返回含义 |
| --- | --- |
| tool status=ok；comparison_status=comparable | 声明及处理条件满足本版规则，已计算数值差异 |
| tool status=unavailable；comparison_status=not_comparable | 条件缺失或不可比，保留原因/核对值，metrics为空 |
| tool status=unavailable；comparison_status=related_or_same_acquisition | 可能是重复/同源采集，不作为独立前后数据计算变化率；同时保留其他缺失条件 |
| tool status=failed | 必要单位转换/时间范围/容差等超出数值能力，不填NaN/Inf或假0 |
| ToolInputError | 参数语法/单位/版本/摘要自相矛盾等非法，不能当作已完成比较 |

“可比”是对所给工程声明和开发规则的条件性结论，不代表已验证现场采集质量、统计独立性或维修有效性。结果始终repair_effectiveness=null、health_assessment=null、diagnostic_evidence_eligible=false。

## 2. 可比性规则

- 同组织；明确且相同的设备、目标组件、测点和方向。other方向还须有相同说明。本版保守要求同一轴承参数版本；版本改变时需后续显式等价性/维修映射，不自动视为相同目标条件。
- 两份当前明确输入的原始采样率、点数及basic-v1全文件处理一致。基础摘要的输入/输出单位、指标集合/单位、可用性、时长和处理版本须自洽；不支持的旧/新算法摘要不混用。
- 两侧各有来源、核对过的采集链配置hash和有效带宽；带宽不能由fs/2推断。指纹应由后续服务根据传感器/安装/通道/DAQ/标定配置等受控资料构造，同hash不等于已证明仪器校准。
- 两侧各有明确采集ID、共同根引用和来源核对。相同measurement/revision、原文件对象、原内容hash、已提供的归一化样本hash、基本/包络执行ID、采集ID或共享根，会触发related判断。**相同内容并不证明物理上必为同一次采集**，本版保守排除这类重复/等价样本作为独立前后材料。
- 采集时间必须声明为start并有来源/核对；顺序另有显式核对。after须晚于before，且不早于before的完整采集窗口结束；不以上传时间代替，不接受时区不明时间。
- 使用本次可靠转速、稳态和参考运动声明，不能把估计/额定转速自动当实测。本版沿用固定外圈/内圈随轴转动的范围；转速不确定量需有来源，0仅用于明确精确合成值。
- 转速允许差值为max(两侧绝对不确定量之和，before转速的1%)。这是版本化开发规则，不是工业通用门槛；容差求和溢出不能变成Infinity后放行。
- 负载必须有数值、物理量、参考、稳态与核对来源，还需明确且已核对的绝对允许差异及出处。缺容差不默认为0；同一“%”必须对应相同额定量/分母，不能把额定电功率百分比与轴扭矩百分比混比。
- 负载只显式换算已支持单位：N/kN、Nm/N·m/N*m/kN*m、W/kW、%；必须与force/torque/power/relative_load含义及参考一致。未知单位不猜测换算，转换溢出明确失败。

采集根是来源目录中的实际采集关系，后续不得用每次上传随机生成的UUID伪造独立性。即使声明了两个不同根，输出也只称distinct_declared_acquisitions=2、statistical_independence=not_established。

## 3. 比较量与数值边界

基础比较覆盖mean、rms、ac_rms、absolute_peak、peak_to_peak、crest_factor、pearson_kurtosis。输入g/m/s²可不同，只要各基础父结果已经可靠归一到m/s²且摘要与输入一致。

每项输出before、after、delta=after−before、单位、相对变化率和不可用原因/限制。所有百分比均称“相对变化”，不称“改善率”。均值可能正负跨零，本版只显示其有单位差值，不提供均值百分比。

幅值/形状指标在before非0且能可靠表示时计算100×delta/before；before为0、基线接近最小浮点分辨量、落入所给基线不确定界或百分比溢出/下溢时，百分比null并有原因。不加epsilon制造巨大变化率。

父指标不可用时仍显示两侧值及原原因，差值/百分比null；个别差值超出float64范围不伪造数值，也不抹去其他可计算项。变化接近2个ULP时标change_at_float64_resolution。

指标测量不确定量不是自动的0：缺失或未核对会标记；提供时核对其单位/来源，0只接受精确合成来源。变化落入两侧绝对界之和时标限制，不把这个界当作统计显著性/置信区间。基线已落入其不确定界时，不显示相对百分比。

可选包络比较要求两侧都有成功父结果、同数值配置指纹/处理、同有效原始索引/长度，且载波频段位于各自已声明有效采集带宽。只提供一侧不会静默舍弃后继续比较。通过时用完整有效包络计算envelope_mean、envelope_rms、envelope_peak，不用2000点预览计算。

## 4. 测试与独立验证

[完整JUnit](../../experiments/m2-t04a-tests.xml)：**394 passed，0失败/错误/跳过**，新增62项，原332项回归保留；最终pytest耗时106.74s。

新增覆盖：同工况正确差值/单位、均值百分比限制、目标/测点/方向/来源链/时序/带宽/采集链/稳态缺失或不同、采样率与点数差异、转速容差边界/不确定量、负载缺失/未知单位/同物理量换算/%分母、同源/复制/归一化样本、g与SI、零基线和退化指标、浮点分辨率/差值与比例溢出、未知或未核对不确定量、非法摘要/单位/参数版本、model_copy绕过重校验、合成来源冲突、完整包络/不同配置/缺父结果/索引差异、输入不变及确定性。

初次测试文件存在装饰器语法遗漏，修正后运行；一个“设备缺失”夹具仍带测点，触发M1既有“测点必须关联设备”的正确校验，已将该夹具改成同时缺设备/测点后验证不可比。没有放宽已有校验来迁就测试。

[独立数值回执](../../experiments/m2-t04a-comparison.json)由[verify_m2_comparison.py](../../backend/scripts/verify_m2_comparison.py)运行五个夹具：48322点幅值减半（含包络）、零基线、完全相同内容、缺负载、百万点幅值减半（含包络）。幅值减半的基础/包络RMS相对变化均为−50%；使用Decimal独立高精度算术核对变化率，并核对原数组hash不变。

百万点比较函数本次约0.015s；仅为纯比较阶段单次计时，**不含父结果计算、HTTP、数据库或内存基线**，不能和T03b的3秒级服务测量直接相比。回执保存环境、11份源码/依赖锁/测试结果SHA256；UUID均为数学夹具，信号hash为float64字节hash，不冒充现场CSV或数据库证据。

这些用例覆盖M2-AT04-C01/C02的数值和声明条件子场景，尚未完成其双输入授权、持久化/恢复、界面和真实资料端到端验收。

```bat
.venv\Scripts\python.exe -m pytest -c backend\pyproject.toml backend\tests -q --tb=short --junitxml=experiments\m2-t04a-tests.xml
.venv\Scripts\python.exe backend\scripts\verify_m2_comparison.py
node scripts\check-docs.cjs
.venv\Scripts\python.exe backend\scripts\check_secret_hygiene.py
```

## 5. 边界与T04b承接

本轮未改数据库、公开请求契约、现有运行服务或前端；仍26张业务表，公开tool-runs仍只有包络/理论频率/匹配三种类型。没有新增依赖或调用模型，未重复浏览器验收或历史HTTP性能测试。

当前M1/M2表没有完整保存上述采集根、采集链指纹、负载参考/容差、时间核对和指标不确定量；不能为了让比较通过而直接从已有文件名、上传时间、source_note或模型输出补齐checked。

下一步T04b先提供不可变的受控比较声明维护及来源/作用域校验，再由服务端从两份授权输入、当前明确revision、本人确认、真实父结果及声明记录构造本内部输入。复用总并发2、幂等、父hash复核和恢复机制，补双输入撤权/篡改/来源失效/失败事务测试，并在页面显示可比条件、拒绝原因和数值差异。报告/工单、维修是否有效及独立复测验证仍留M4/M5；本次不提前宣布T04或M2整阶段完成。
