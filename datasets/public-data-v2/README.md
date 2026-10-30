# 公开数据实体分组与验证准入 v2

本版本扩展[v1开发清单](../public-data-v1/README.md)，不覆盖其28条开发记录与历史结果。用于非商业研究验证，不分发原始数据，也不代表诊断准确率评测完成。

## 固定分组

| 分区 | 轴承实体 | 本轮读取范围 |
| --- | --- | --- |
| development | v1既有CWRU及K001、KA01、KI01 | 原开发状态保持 |
| validation | K002、KA03、KI03 | 四工况×前两次重复×三个轴承，共24条vibration_1通道 |
| sealed_test | K003、KA04、KI04 | 三个完整RAR，仅校验归档字节数与SHA；不列出、提取或解析MAT成员 |

`split-plan.json`在新归档下载和信号检查前固定实体；`validation-protocol.json`在首次验证成员提取前固定准入规则。[冻结回执](../../experiments/m2-t05b-freeze.json)记录二者SHA。每个轴承的全部工况/重复始终归同一分区，其余未选择的重复不能转入别的分区。

标签映射依据[官方论文表4、表5、表7、表10](https://mb.uni-paderborn.de/fileadmin-mb/kat/PDF/Veroeffentlichungen/20160703_PHME16_CM_bearing.pdf)。验证故障为人工电刻，测试故障为加速寿命试验产生的损伤；这也包含损伤生成方式变化，不能当成同分布随机测试。每个保留分区每类仅一个轴承实体，三类子集不含滚动体类，不足以支持通用四分类或工厂泛化结论。正文部分KA04/KI18措辞与表格相反，记录此冲突，测试标签暂以表5/10为依据，后续测试释放前需核对原始轴承说明。

## 文件

- `catalog.json`锁定六个官方RAR和24个验证MAT成员；封存归档不允许带成员清单。
- `validation-manifest.json`锁定验证记录、原文件和通道hash、实体组、采集根与标签。没有生成封存测试信号manifest。
- `metadata-review.json`记录六份开发轴承原始PDF的归档路径/SHA及核验结论，不复制论文或PDF全文。

分组检查覆盖已知轴承身份、原归档hash、可读记录的采集根/文件hash/通道hash。封存归档未做信号级重复检测，未来正式释放前必须补做，不能由RAR的hash不同推出通道独立。

## 元数据结论

开发轴承的原始测量日志确认标称振动采样64 kHz，且给出传感器灵敏度。灵敏度不是存储通道的单位换算证明；MAT单位字段为空的问题仍保留。日志没有解释HostService时间坐标为何有非均匀步长，不能用标称采样率自动覆盖原时间轴。几何资料虽可读取，但不确定量与适用核验仍未完成，不补造为零。

本轮保持T05a时间一致性门槛0.001，不放宽至验证数据能通过的数值，不重采样、不裁去异常点、不填单位。验证器只做准入检查，不计算故障特征、阈值或预测。

## 复现

从项目根目录运行：

```text
.venv\Scripts\python.exe backend\scripts\prepare_holdout_data.py --fetch --extractor <7z.exe绝对路径>
.venv\Scripts\python.exe backend\scripts\validate_public_readiness.py
.venv\Scripts\python.exe backend\scripts\verify_holdout_reproduction.py
```

默认缓存`.runtime/public-data`；前两条命令支持`--data-root`。已有资产只核验不覆盖，缺失资产由`--fetch`从固定官方URL补齐。复现脚本在新缓存中复用原归档、重新提取全部验证成员，并拦截解压器调用以检查封存归档未被传入。

封存限制是该研究流程的代码门槛，不是对本机文件所有者的操作系统访问控制。本轮没有提供测试释放参数或绕过命令。原始数据受[Paderborn官方CC BY-NC 4.0及引用要求](https://mb.uni-paderborn.de/en/kat/research/bearing-datacenter)约束；商业用途需要另行联系作者。完整来源引用沿用v1目录。

结果与后续范围见[T05b实施记录](../../docs/acceptance/M2-T05b-verification.md)。

## 条件性时间轴研究（T05b2）

`research-adaptation-protocol.json`是额外研究协议，不替换原始validation-protocol或数据准入结论。假设X坐标为秒、显式线性插值并只输出归一化形状；不跨越大缺口、不外推、不建立SI幅度或诊断证据。标称索引时间轴只作为反事实敏感性对照。

```text
.venv\Scripts\python.exe backend\scripts\verify_time_adaptation.py
.venv\Scripts\python.exe backend\scripts\benchmark_time_adaptation.py --verify-replay
```

公开特征运行要求合成真值回执通过且协议/源码指纹一致，首次运行冻结候选；后续修改候选会阻断旧实验重放，需保留旧记录并建立明确的新实验版本。封存测试不读取；验证准入在T05b1已经查看，本轮不称完全盲验证。实测边界和全部拒绝见[T05b2记录](../../docs/acceptance/M2-T05b2-verification.md)。
