# M2-T05a 公开数据清单与开发基线

日期：2026-10-30。本轮完成公开来源核验、内容锁定、按实体分组的泄漏检查及开发数值基线。不是M2整阶段验收，也不是诊断准确率或工厂试用效果验证。

## 交付与实际数据

[统一数据目录](../../datasets/public-data-v1/README.md)保存官方来源、使用条件、固定资产清单和28条完整通道manifest。原始MAT/RAR存放于忽略入库的`.runtime/public-data`，不随产品分发。

| 来源 | 原始下载 | 本轮通道记录 | 标签范围 | 分组 |
| --- | --- | --- | --- | --- |
| CWRU | 97、105、118、130四个MAT | 4 | 健康参考、内圈、滚动体、外圈；故障为人工0.007英寸/0 HP子集 | 缺少已核对实体ID，按DE故障家族保守合并全部尺寸/负载 |
| Paderborn | K001、KA01、KI01三个RAR | 24 | 健康参考、人工EDM外圈/内圈；不补造滚动体类别 | 三个轴承实体，每个四种工况、前两次重复归同组 |

[CWRU官方入口](https://engineering.case.edu/bearingdatacenter/download-data-file)、[故障表](https://engineering.case.edu/bearingdatacenter/12k-drive-end-bearing-fault-data)及[正常表](https://engineering.case.edu/bearingdatacenter/normal-baseline-data)支持文件和通道映射。已查看官方页面未发现明确的数据许可文本，登记为not_specified_on_reviewed_pages，不推断再分发授权。[Paderborn官方入口](https://mb.uni-paderborn.de/en/kat/research/bearing-datacenter)明确CC BY-NC 4.0及引用要求，商业使用须联系作者；本轮按本地非商业研究用途记录。

Paderborn采样标称与标签依据来自[官方论文](https://mb.uni-paderborn.de/fileadmin-mb/kat/PDF/Veroeffentlichungen/20160703_PHME16_CM_bearing.pdf)及[工况表](https://mb.uni-paderborn.de/en/kat/research/bearing-datacenter/operating-conditions)。完整引用保存在统一数据目录及catalog。

## 数据管线

`prepare_public_data.py`校验固定字节数与SHA，仅下载缺失官方资产；缓存变更即失败，不重新接受新hash。仅提取白名单MAT成员并核验成员SHA，从stdout读取，避免归档路径写到缓存外。Windows自带tar实测报Parsing filters is unsupported；改用官方7-Zip 26.05便携文件，安装范围仅项目忽略目录。

MAT适配器按显式DE字段或`vibration_1`名称选择，拒绝多通道矩阵静默展平、非有限值和越界路径；Paderborn按XIndex核验时间轴，检查DownSampling。原始文件hash与规范小端float64通道hash同时锁定，正负零统一，不修改原始记录。没有新增业务MAT上传接口、数据表或模型调用。

`audit_manifest`同时检查实体组、采集根、原文件hash和通道hash跨集冲突。已经用于开发的记录禁止变成validation/sealed_test。开发基线运行器拒绝处理这两类信号。检查只能识别已知来源关系与精确重复，不能证明所有物理实体独立，也不能识别任意未知变换后的同源信号。

本轮28条均为development、7个保守实体组，validation和sealed_test为0；四分类独立评测资格明确为false。没有为了填满三份拆分而把相邻片段当独立样本。

## 首轮基线与失败保留

固定完整通道、不切窗、不重采样。频带沿用既有合成开发的2000–4000 Hz，观察10–500 Hz，没有用公开标签选择频带或分类阈值。原始单位在本轮已查看依据中未确立；选取的Paderborn MAT实际单位字段为空。因此仅使用峰值归一化的形状代理，导出无量纲峰值因子、Pearson峭度和条件满足时的包络主峰频率/谱幅占比，不输出物理RMS、SI幅度或维修结论。

| 处理结果 | 记录数 | 原因 |
| --- | --- | --- |
| 包络形状特征完成 | 3 | CWRU105、118、130，有官方12k采样依据 |
| 频谱路径不可用 | 1 | CWRU97在已查看的官方来源中未确立采样率 |
| 频谱路径不可用 | 24 | Paderborn对应时间轴未通过相对标称间隔/整段时长0.001的保守一致性门槛 |

三份CWRU包络主峰分别约161.729、29.939、107.603 Hz。单个主峰不能直接转换成标签；尤其滚动体记录的最强峰不等于滚动体故障频率。此处仅记录观察，没有预测或准确率。

Paderborn选取记录的最大单步偏差范围约0.0145至180.436（相对标称采样间隔），整段时长相对偏差0至约0.01888。可能涉及时间轴导出或采集记录问题，本轮不推断原因、不擅自重采样，也不由该门槛判定传感器损坏。后续须调查原始时间轴的语义及采集日志，再另行设计可审计适配策略。

## 验证记录

- [数据完整性](../../experiments/m2-t05a-data-integrity.json)：7个原始资产、28个通道全部通过内容校验。
- [从原归档重新提取](../../experiments/m2-t05a-reproduction.json)：隔离缓存从零提取24个成员并全部复核，第二次运行提取0个、结果一致；记录7-Zip版本和可执行文件SHA。
- [开发基线和源码SHA](../../experiments/m2-t05a-development-baseline.json)：28份处理结果、全部拒绝原因、固定协议、软件版本与源码指纹。accuracy为null、合格诊断profile为0、factory_data_used为false。
- 新增19项单元测试覆盖跨组泄漏、精确重复、开发污染、路径逃逸、hash篡改、通道/单位误配、矩阵展平、已封存数据拒绝、归一化下溢及缺采样/时间轴不一致。完整后端 **436 passed，0失败、0跳过**，结果见[JUnit](../../experiments/m2-t05a-tests.xml)。

本轮未修改前端或业务API；保留T04b的417项历史结果。当前仍为0012迁移、27张业务表。

## 下一步：T05b

1. 核对Paderborn原始测量日志、单位和时间轴语义，为允许处理或拒绝给出来源依据；保留原始时间与数值，不静默替换。
2. 补充未查看的独立轴承实体，先冻结组级验证/测试清单和标签覆盖，再进行开发/验证；同轴承全工况/重复均固定归组。
3. 在明确适用类别与条件下执行频率匹配/歧义评测、拒答统计及配置选择记录；外部来源缺类单独说明。未完成这些条件前不发布四分类准确率或模型选型结论。

T05a完成不等于M2-AT05及M2整阶段通过；正式Agent/RAG准入仍在后续M3。
