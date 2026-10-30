# 公开数据开发清单 v1

本目录只保存来源、许可核验记录、SHA和元数据，不分发原始数据。用途是非商业研究开发，未进入工厂试用准入或诊断效果验收。

- `catalog.json`：7个官方原始下载、精确字节数/SHA、24个允许提取的MAT成员、来源与使用条件。
- `manifest.json`：28条完整通道记录、原文件及规范float64通道SHA、标签依据、实体组、采集根、开发使用记录、采样与单位缺口。

28条全部属于development。Paderborn同一轴承的四种工况和两次重复始终放在同一组；CWRU缺少已核对的实体ID，保守按故障家族合并不同损伤尺寸和负载。不得随机切窗后跨开发/验证/测试集分配。审计还检查采集根、原文件SHA和通道SHA跨集冲突；这些检查不能证明统计独立性。

本版本没有validation或sealed_test，也不具备四分类独立评测条件。后续以新版本补充独立轴承实体，不把已经查看的记录重新标成未见测试数据。

## 来源

[CWRU官方文件入口](https://engineering.case.edu/bearingdatacenter/download-data-file)、[故障表](https://engineering.case.edu/bearingdatacenter/12k-drive-end-bearing-fault-data)、[正常表](https://engineering.case.edu/bearingdatacenter/normal-baseline-data)：选97/105/118/130的DE通道。105/118/130对应0.007英寸、0 HP的内圈/滚动体/6点位置外圈人工故障。已查看页面未注明明确的数据许可文本，因此不推断商业使用或再分发授权。

[Paderborn官方入口](https://mb.uni-paderborn.de/en/kat/research/bearing-datacenter)明确CC BY-NC 4.0和引用要求，商业使用须联系作者。原始数据仅在忽略入库的本地研究缓存保存，不随产品提供。[官方论文](https://mb.uni-paderborn.de/fileadmin-mb/kat/PDF/Veroeffentlichungen/20160703_PHME16_CM_bearing.pdf)的表4、表7支持本轮KA01外圈EDM、KI01内圈EDM、K001健康参考映射；本子集没有滚动体类，也没有自然退化故障。

引用：Lessmeier, C.; Kimotho, J. K.; Zimmer, D.; Sextro, W. (2016). *Condition Monitoring of Bearing Damage in Electromechanical Drive Systems by Using Motor Current Signals of Electric Motors: A Benchmark Data Set for Data-Driven Classification*. European Conference of the PHM Society. Paderborn University, Chair of Design and Drive Technology (KAt), [KAt DataCenter](https://mb.uni-paderborn.de/en/kat/research/bearing-datacenter).

## 复现

项目虚拟环境已安装后，从仓库根执行：

```text
.venv\Scripts\python.exe backend\scripts\prepare_public_data.py --fetch --extractor <7z.exe绝对路径>
.venv\Scripts\python.exe backend\scripts\benchmark_public_data.py
.venv\Scripts\python.exe backend\scripts\verify_public_data_reproduction.py
```

首次下载合计约528.4 MB，另需提取空间。`--fetch`只补缺失文件；缓存hash不符立即失败，不能重新生成SHA接受变更。默认路径`.runtime/public-data`；`--data-root`可指定独立缓存。Windows自带tar实测不能解析这些RAR的部分过滤器；使用支持RAR的7-Zip。本机已在`.runtime/tools/7zip/portable`准备便携版26.05，没有修改系统安装。复现提取脚本使用本机7z/该便携路径，原始文件以只读方式复用，不修改内容。

选取成员采用stdout提取并核对精确长度/SHA，避免归档成员路径写入任意目录。MAT加载仅用于本地受控研究脚本，未新增公开上传MAT/RAR的接口。

## 本轮固定基线

完整原始通道、不裁窗、不重采样；包络频带沿用既有合成开发的2000–4000 Hz，观察10–500 Hz。没有按标签搜索频带或调整分类阈值。单位无法由本轮已查看依据确立时，只对峰值归一化的形状代理运行数值核；仅导出无量纲峰值因子/峭度、条件满足时的包络主峰频率与谱幅占比，不导出物理RMS或SI幅度。

Paderborn从名称`vibration_1`定位通道，并按XIndex检查对应时间轴；不按数组位置误取电流。时间轴相对标称间隔或整段时长偏差超过0.001时停止频谱路径，保留数值；这一保守门槛尚待导出格式调查，不能据此认定传感器损坏。单位字段为空不自动填g或m/s²。97记录在已核对的官方来源中未确立采样率，因此暂不运行频谱。

实际结果与限制见[阶段记录](../../docs/acceptance/M2-T05a-verification.md)。本目录schema和脚本属于离线评测工具，业务系统仍使用受控CSV入口及原有诊断资格门槛。
