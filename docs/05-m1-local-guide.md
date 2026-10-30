# 05 M1本机操作与验证指南

> 日期：2026-10-29。适用范围：本机M1数据工作台。阶段证据见[M1验收记录](acceptance/M1-acceptance.md)。

## 1. 启动与登录

已有环境从仓库根运行：

```bat
.venv\Scripts\python.exe backend\scripts\migrate.py
.venv\Scripts\python.exe scripts\dev.py
```

打开 http://127.0.0.1:5173/ 。M1只支持同库单API进程，已有开发服务运行时直接使用，不能重复启动。数据库初始化、依赖安装及CLI账号维护见[后端说明](../backend/README.md)。

本机已生成的组织ID、local-engineer与local-admin账号在私有 `.runtime/local-access.json`；账号密码不是PostgreSQL密码，文件不要分享或提交。新环境通过受控CLI初始化，无公共注册或默认账号密码。local-engineer用于本机开发数据流程，local-admin只分配管理员角色，不默认读取工程师数据。

登录组织ID、用户名和密码后会显示授权采集记录。密码不保存在浏览器存储中；会话Cookie为HttpOnly。当前只在回环HTTP运行，正式HTTPS/Secure Cookie和共享部署仍待M6。

同一标签页刷新可恢复登录、上次记录及分析。新标签页缺少本页操作凭据时会要求重新登录；出现会话过期或CSRF错误可用“重新登录”清空当前显示并重新验证身份。“重新登录”不是注销服务端所有会话；“退出登录”才撤销当前会话。

## 2. 导入数据

点击“导入 CSV”，选择UTF-8单列value文件，填写真实采样率和g/m/s²单位。文件上限32MiB，1024—1000000点；错误行、NaN/Inf、多列、空行等会被拒绝，不删点后继续计算。

没有采集文件时可用“使用合成样例”：生成1024点64Hz正弦，采样率1024Hz、单位m/s²，并明确记录为合成数据。样例不是工厂试用或故障识别数据。选择另一个文件会清除样例的采样率、单位及来源声明，需重新填写。

设备/测点可留空，只能选择已授权对象。本次转速、方向、负载和含时区采集时间可选；未知不填0、不用台账额定转速或上传时间替代。来源是用户声明，系统未验证现场真实性。MD知识文档不走这个入口，正式知识导入在M3实现。

## 3. 核对并确认

上传完成后检查样本数、采样率、单位、来源、文件hash及原始波形。展开“本版本采集信息”核对其他字段。预览最多2000点，按桶保留极值；分析使用全量原始数据。

勾选核对声明后点击“确认当前输入”。确认绑定本人和当前参数版本，不表示传感器校准、专业诊断复核或故障结论确认。

## 4. 分析与下载

点击“运行基础分析”，查看含DC的RMS、AC RMS、绝对峰值、峰峰值、峰值因子、Pearson峭度与幅度谱。输出加速度统一m/s²，峰值因子/峭度无量纲；零RMS或零方差指标显示不可用及原因，不用0代替。常量/零信号会说明动态信息不足，不据此判断健康。

频谱使用周期Hann、谱去均值和相干增益归一化；时长N/fs，bin间隔fs/N不等于实际可分辨能力。图只展示有限预览；可下载原CSV、结果JSON、波形JSON、全谱NPZ与产物清单。NPZ供程序读取时使用 `numpy.load(..., allow_pickle=False)`。

每次读取会检查权限与原文件/产物完整性。损坏、缺失或撤权时停止展示有效结果并返回错误，不用缓存绕过。

## 5. 参数与分析历史

“修订参数”填写完整新参数及原因，保存为新revision；旧数据/分析不覆盖。新版本需重新确认，分析历史保留各自输入。查看旧版本结果会显示历史参数提示，不把旧结果当作新参数计算。

采集、参数历史及分析列表分页。授权范围由后端检查，知道UUID不等于有访问权。管理员没有隐含工程师业务权限。

## 6. 失败、中断与请求重试

网络请求响应丢失时，使用“重试原请求”。页面保留原幂等键，刷新后仍可查询同一任务，不重新创建。运行中会查询状态。停止本页跟踪只移除本页跟踪，不取消服务端任务。

分析failed/interrupted是明确终态，不能当作计算成功。先核对error_code和当前输入，再点击“显式重试（新任务）”，生成新ID并保留关联。服务器启动会把遗留running标interrupted，不自动续算；完整Worker/Agent恢复留M3。容量满返回429，请稍后重试。

## 7. 原型数据迁移与本机验证

原JSON/CSV原型目录保留。迁移命令默认只检查：

```bat
.venv\Scripts\python.exe backend\scripts\import_legacy.py --source .runtime\analyses --actor ENGINEER_USER_UUID --batch migration-01
```

核对清单后显式加 `--apply`。采样率/原始单位缺失时拒绝，不猜测；可用 `--manifest` 指定JSON对象，将legacy ID映射到明确的元数据修正。原文件hash不符不能绕过。按组织/来源目录/legacy ID记录映射，重复导入返回同一记录，输入变化冲突，不覆盖历史。

导入只复制原文件和未确认参数，保留legacy来源；原诊断/指标不作为正式结果导入，须重新确认和计算。dry-run不复制文件或建立业务记录，但会留下受控CLI会话/审计。该CLI仅供可信本机运维使用。

验证命令：

```bat
.venv\Scripts\python.exe -m pytest -c backend\pyproject.toml backend\tests -q
npm --prefix frontend run build
.venv\Scripts\python.exe backend\scripts\reconcile_files.py
.venv\Scripts\python.exe backend\scripts\benchmark_m1.py
node scripts\check-docs.cjs
```

对账仅报告，不删除文件。benchmark在独立测试库和独立文件根创建合成数据，执行100次典型分析及百万点/并发基线，不能作为现场故障识别指标。真实HTTP进程中断验证见后端verify_analysis_flow.py，需先停止占用同库单进程锁的常规API。

M1不提供正式故障分类、包络取证、工程报告、工单或生产部署。下一步M2先设计和验证轴承工具，再接入正式Agent。
