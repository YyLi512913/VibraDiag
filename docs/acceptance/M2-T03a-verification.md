# M2-T03a 频率匹配数值核心验证

日期：2026-10-30。上游：[06设计](../06-m2-tool-design.md)、[T02b2正式工具运行](M2-T02b2-verification.md)。本子步实现纯数值匹配；T03b再接匹配运行API、facts/evidence持久化和最小界面。

## 1. 已实现行为

[analysis/matching.py](../../backend/src/vibradiag/analysis/matching.py)接收包络、理论频率及内部来源引用，可补同输入的完整基础谱；[schemas/matching.py](../../backend/src/vibradiag/schemas/matching.py)约束引用及滑移声明。没有联网、模型调用、DB读写、EV注册或新的公开HTTP入口。客户端不能通过已有tool-runs请求直接调用这个新工具。

来源声明必须使用同一measurement_revision/context/原始hash，不同工具执行ID分开；理论目标revision也须一致。理论结果从其参数快照重算核对，包括单位和不确定量，不接受篡改后继续匹配。基础谱必须是原始完整点数，包络谱必须对应有效片段，两者不能混用；频率轴、长度、有限数值、非负幅度、单位、有效索引及观察频段均校验。

上述仅为数值输入一致性校验，声明的UUID/hash不是权限证明。T03b还必须由服务端查询真实父运行、授权、原文件及产物hash并构造引用；纯函数不能自证来源真实。

生成63个目标：BPFO/BPFI各1—5次谐波及每个±1/±2fr边带，BSF1—4次谐波且前两次含±1/±2FTF边带，FTF单项辅助。2BSF是BSF二次谐波的alias，不生成新的独立类别。非正频率、范围外及分辨能力不足分别记录。

窗口采用已传播的频率不确定边界，谐波乘倍数，边带将绝对不确定量贡献保守相加；再加2Δf和明示滑移比例。保留转速/系数交叉项，缺少不确定量返回unavailable，不假定0；总半宽超过中心5%标resolution_limited，不夹小后继续支持。

在完整谱上找局部峰，再按窗口筛选；输出稳定的执行/bin峰ID、实际Hz/幅度/频差/prominence。每项最多展示8峰，保留总候选数；排序按幅度、距目标偏差、bin。不将最近bin当命中，不将DC/Nyquist边界当普通局部峰。

局部参考为W=max(10Δf,4ε)范围内的幅度中位数，排除所有正频目标（含无法评估/部分越界目标）和1—10fr干扰窗，至少20个bin。参考为0时使用64eps×全谱最大幅度的数值地板并明确标识。计算经缩放避免溢出；地板不可表示等情况明确failed，JSON无NaN/Inf。

## 2. 开发配置与解释边界

| 配置 | 本版规则 |
| --- | --- |
| 匹配版本 | frequency-matching-v1 |
| 滑移比例 | 默认1%，附开发候选来源；可显式声明0—5%，不是工业通用标准 |
| 局部峰prominence | 在完整谱上计算，固定41-bin局部窗口；幅度比≥4且prominence比≥3才通过候选门槛 |
| 多峰/重合 | 保留multiple_peaks、连接区间overlap_group及共享peak_id；多个目标不能把同一峰算成独立采集 |
| 转频检查 | 包络支持峰与1—10fr窗口的重合保留歧义；有基础谱时额外记录基础谱候选 |
| 载波拍频筛查 | 原始载波频带内局部峰相对全基础谱最大幅度≥1%、局部prominence≥0.5%；最多64个原始峰、每目标展示8对，保留截断/总数 |
| 未检查项 | 滤波敏感性始终not_checked；原始峰筛查为有阈值/有上限的候选筛查，不能写“排除拍频” |

四种窗口状态为observed_support、no_observed_support、ambiguous、not_evaluable。多峰、有支持的重叠目标、转频/拍频替代解释保留歧义。FTF始终辅助，不提供保持架故障定类。

即使观察到支持，本子步始终evidence_eligible=false、confounder_checks_complete=false、diagnosis=null；没有故障概率、健康判定或已登记证据。结果标记一个共同采集根；63个派生目标不变成63份独立证据。

AM本身也有载波与边带，幅度谱上的频率差可以符合拍频筛查。本筛查只保留替代解释，不能根据幅度谱宣称区分了机械调制与独立载波；还需要其他上下文和观察。

## 3. 测试与发现

[完整JUnit](../../experiments/m2-t03a-tests.xml)：**311 passed，0失败/错误/跳过**，新增44项，原267项回归保留。覆盖：

- 解析构造的窗口内/外、窗口末bin、平谱/最近bin、弱峰、多峰、零信号与数值地板。
- 已声明转速/系数不确定量交叉项的独立算术期望，缺不确定量、过宽窗口、边界、参考不足、非法滑移。
- 目标/单位/数值/引用篡改、不同revision/context/hash、非法谱和轴、索引/版本错配、原始与有效段误用。
- 重合目标共用峰ID、转频冲突、BSF/2BSF去重、FTF辅助、谐波/边带及非正目标。
- 原始双载波差频、原始峰列表预算/截断、缺基础谱、未检查滤波敏感性、奇数有效长度、尺度变化、输入不变与确定性。
- 实际包络+频率+基础谱流水线：AM观察、无调制载波对照、两个载波形成包络峰的反例。

测试发现：若滑移为0、参数又为精确合成值，则ε=2Δf，W=10Δf，排除目标保护窗后最多约16个参考bin，达不到设计的20个。此时按既定规则返回noise_reference_insufficient；没有自动扩窗或放宽数量。最初窗口末bin测试不该同时采用这个不足配置，已改用有足够参考的默认窗口，并单列0滑移不足测试。真实工程适用性需后续数据验证，不能为得到命中而暗改规则。

## 4. 独立验证与复现

[纯数值回执](../../experiments/m2-t03a-matching.json)由[验证脚本](../../backend/scripts/verify_m2_matching.py)保存：48322点AM、同长双载波、百万点AM，实际运行包络/理论频率/基础谱/匹配并记录匹配阶段耗时、输入float64字节SHA256及状态/候选/限制。

所有UUID均是数学夹具，没有数据库记录；输入hash是合成float64信号的hash，不冒充现场CSV文件hash。结果含Python/NumPy/SciPy版本及9份源码/锁文件/测试结果SHA256。耗时仅指匹配函数，不含前置包络/基础谱、HTTP或持久化；未测本子步API内存/P95，不复用T02b2基线作匹配服务性能。

```bat
.venv\Scripts\python.exe -m pytest -c backend\pyproject.toml backend\tests -q --tb=short --junitxml=experiments\m2-t03a-tests.xml
.venv\Scripts\python.exe backend\scripts\verify_m2_matching.py
node scripts\check-docs.cjs
.venv\Scripts\python.exe backend\scripts\check_secret_hygiene.py
```

本轮没有新增依赖、迁移、公开工具请求类型或前端改动；数据库仍24张业务表，匹配结果没有持久化，未重新进行浏览器验收、公开数据留出评测或配置资格发布。

## 5. T03b承接

新增匹配运行契约，复用总并发2、幂等和恢复；校验真实父运行同输入/context及授权，验证基础谱/包络/理论结果hash和版本。再建立不可变facts/evidence及共同根/父关系，提供授权解析与篡改/撤权回归，并接最小包络谱、标线、状态和限制展示。正式匹配资格仍受profile验证/原始数据适用条件约束；不把纯数值观察直接包装成可发布诊断证据。
