# M2-T02a 包络与理论特征频率数值验证

> 日期：2026-10-29。状态：**本轮纯数值模块与对应测试通过**；不是整个M2验收，也没有接入正式API、数据库或页面。

## 1. 实现范围

| 文件 | 实际职责 |
| --- | --- |
| backend/src/vibradiag/analysis/envelope.py | SOS前后向带通、显式边界/Hilbert延拓、有效段、包络幅度谱、频响及展示预览 |
| backend/src/vibradiag/analysis/bearing_frequencies.py | 几何/系数两条明确路径、FTF/BPFO/BPFI/BSF/2BSF、条件检查及不确定区间 |
| backend/src/vibradiag/schemas/tools.py | 冻结/严格参数类型，数值与关系校验，来源/目标/资格的内部数值参数对象 |
| backend/src/vibradiag/domain/tool_results.py | 无HTTP/持久化依赖的ok/unavailable/failed结果；非法输入抛稳定ToolInputError |
| backend/tests/unit/test_envelope.py、test_bearing_frequencies.py | 本轮83项测试；解析真值、独立Decimal参考、非法/缺参/范围外/数值范围分支 |
| backend/scripts/verify_m2_numerics.py | 可复现的独立计算、输入/hash/版本记录及百万点冒烟，无数据库和模型调用 |

库函数接已验证的数组及数值参数，保留原数组，返回只读标志的大数组和可独立JSON序列化的概要。不会注册业务执行ID或EV-ID，不读取文件、不访问网络，不直接发布故障判断。M1的basic-v1和135项原有测试未改算法定义。

内部SourceReference/checked/qualification是未来服务构造的领域参数，不是可直接暴露的安全凭据。当前没有资格发布/台账校验服务；测试中的UUID/manifest hash是明确的合成夹具，不对应真实数据库记录或已批准配置。

## 2. 包络实现与数值结果

算法envelope-sos-hilbert-v1：g换算/缩放与有限性检查→全文件去均值→4个SOS的Butterworth带通（原型阶数4、单次总阶数8）→odd前后向滤波→reflect延拓G点→Hilbert→移除延拓并保留原索引[G,N−G)→包络去均值/周期Hann/相干增益/单边峰值谱。

实际保存P、极点半径/G、有效原始索引/时间、FFT长度、SOS、配置摘要及单次/前后向频响。临界频率的前后向增益由freqz_sos实际求值（测试约0.5），不拿理论常数冒充计算结果。不会把有效段时长替换成M1整文件时长。

数学非法配置/数组拒绝；有效但边界/周期/资源不足返回unavailable且无结果数组；数值不可表示返回failed，绝不输出NaN/Inf或把非零下溢改成0。零/常量输入仍可返回数值零包络，但明确无动态信息；不称健康。

合成固定夹具fs12000、带2000—4000Hz、载波3000Hz、峰值2、调制深度0.25、调制120Hz；G=161、P=27，有效48000点。目标幅值0.5的误差满足06事先规定的1e−5＋1%容差，包络逐点最大偏差满足0.02m/s²；奇数有效48001点及g/m/s²等价也通过。完整实测数值见[独立验证JSON](../../experiments/m2-t02a-numerics.json)。

固定无调制对照在10—500Hz内满足≤2e−4m/s²的谱幅值要求，但“无显著匹配支持”部分仍须等T03匹配器，不能单独据此标整个E02已验收。两载波拍频能产生包络峰的反例已测试，函数仍不返回故障类别；自动拍频/混淆识别尚未实现。

相邻探索频带具有不同配置指纹、频响和有效段；本轮证明身份/索引可追溯，不宣称任意频带边界误差已消除。配置qualified发布数仍为0。

概要的evidence_preconditions_satisfied只表示数值层核对了传入的作用域声明；实际来源、资格清单、资源关系及当前权限必须由T02b服务重新查证。exploratory永远不提升该标志。该标志不是故障确诊或正式证据发布许可。

## 3. 理论频率与不确定性

几何路径要求明确单列球轴承、合法Z/d/D/接触角；纯数值层支持mm/m相同长度单位的比值计算。未来持久化统一mm时仍需单独检查转换范围，不能悄悄生成Inf或0。

只有明确目标、可靠本次转速/来源及核对、稳态与支持旋转假设时才计算。未知/额定/估计转速、未知目标、未核对参数、源冲突、变速/双圈转动对应unavailable。系数路径必须明确shaft_order、BSF自转定义和适用条件，不把2BSF或Hz系数当作BSF阶次。

合成Z8、d10mm、D40mm、角0°、1800rpm：FTF11.25、BPFO90、BPFI150、BSF56.25、2BSF112.5Hz；900rpm按比例减半。几何/明确系数、mm/m结果一致，2BSF保留derived_from=BSF，FTF不用于保持架分类。

名义频率可计算而公差/转速不确定量缺失时，仍保留名义值，区间为null、uncertainty_complete=false；不会填伪0公差。零不确定量须为精确合成来源；低于数值分辨率的转速不确定量显式标限制。几何/系数区间与转速区间相乘，保留交叉影响。

独立Decimal参数盒81个组合检查了几何/角度/转速端点与中间值均在区间内；另有系数与rpm交叉项的闭式期望。区间是声明边界的保守传播，不是统计置信区间，也不含未知现场误差。uncertainty_complete只说明当前期望频率的不确定量信息完整，不等于频谱已经可分辨或匹配成立。

一次初始溢出测试夹具实际上仍在float64可表示范围内，预期失败因此未通过；已修正为真正使BSF×fr超范围的夹具，重新验证数值失败分支。未将无关条件伪造成计算失败。

## 4. 实际执行

| 检查 | 实际结果 |
| --- | --- |
| 新增包络测试 | 40项通过 |
| 新增特征频率测试 | 43项通过 |
| 完整后端回归 | **218 passed in 28.09s，0 skipped**，含M1原135项；[JUnit](../../experiments/m2-t02a-tests.xml) |
| 独立验证 | 4种AM/无调制奇偶夹具及5个理论频率通过，输入hash不变 |
| 1000000点包络冒烟 | ok，有效999678点，输出全有限；本次约0.733s，只计纯函数，不是HTTP/P95或峰值内存测试 |
| 依赖 | 沿用Python3.11.15、NumPy2.4.6、SciPy1.17.1，未增装/升级模型或科学计算依赖 |

独立验证保存环境、8个源码/锁文件hash、输入float64字节hash和配置指纹。脚本在完整回归同时执行，耗时只作本次冒烟记录，不能与M1含持久化/HTTP的基线直接比较。

复现：

```bat
.venv\Scripts\python.exe -m pytest -c backend\pyproject.toml backend\tests -q --tb=short --junitxml=experiments\m2-t02a-tests.xml
.venv\Scripts\python.exe backend\scripts\verify_m2_numerics.py
node scripts\check-docs.cjs
```

## 5. 当前边界和下一步

本轮只覆盖E01/E03数值、E04/E05部分基础分支、F01—F03的纯参数/计算部分。E02匹配、E06混淆识别、M/P/C/D整体用例仍待实现；新模块没有权限、真实源资料、配置资格或数据拆分的实际验收证据。

正式数据库保持M1的17张业务表、迁移head=0008_legacy_imports；未创建M2候选表、tool-runs接口或包络页面。M1服务无需为本轮模块重启；没有调用云端模型、训练分类器或宣称现场试用。

下一步M2-T02b：最小目标轴承/参数版本、诊断context、profile资格与来源校验、工具运行/产物持久化、与M1共享总容量2。对外API只接业务ID，必须从服务端记录重建本轮参数对象，不能允许客户端自填checked或qualification即通过。再进入T03频率匹配与facts/evidence。
