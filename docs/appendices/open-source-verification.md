# JYD-iBearing 本地验证报告

日期：2026-10-29。上游：https://github.com/kipp7/JYD-iBearing

验证提交：`ce6c77520485a79571b22597e1047f84532c56b2`。本轮未修改上游受版本控制的源代码。

## 结论

可复用部分界面和分析代码，但原版不满足直接作为可试用诊断系统的要求。建议作为参考和局部复用来源；如采用整个项目，需要先修复连接、配置、数据契约和诊断验证问题。

## 环境与执行

- Windows；Node 22.23.2、npm 10.9.8、Python 3.11.15。
- Python 独立环境：`research/ibearing/.venv`，按原 requirements.txt 安装，`uv pip check` 通过，45 个包。
- 前端：`npm ci --ignore-scripts --no-audit --no-fund` 成功，1292 个包；出现 React 19 与旧组件 peer dependency 警告。未执行依赖生命周期脚本。
- `npm run build`：编译通过，最终预渲染失败；`/guzhangtongji` 缺少 Supabase 配置。
- `npm run dev -- --hostname 127.0.0.1 --port 3100`：启动成功。浏览器可进入频谱分析页面；首次编译超过首次导航的 30 秒等待，随后页面正常呈现。
- 后端用真实 Flask app.test_client 执行接口与文件上传，不依赖伪造模型响应；不是完整浏览器到后端的端到端通过。
- 没有配置 Supabase，没有调用 DeepSeek 或验证 AI 报告生成，没有执行完整诊断准确率评测。

## 实测结果

| 项目 | 结果 |
| --- | --- |
| 正弦信号 RMS | 128 Hz、幅值 1、4096 Hz 采样，256 点窗口：0.7071067811865476，与理论值相符 |
| FFT 峰值 | 128 Hz 正确；原始 FFT 幅值约 2048，未经物理幅值归一化 |
| STFT | HTTP 200，返回结果；本轮未做完整数值校验 |
| CWT | HTTP 200，返回图像；本轮未做图像内容准确性校验 |
| 浏览器上传 | 频谱页上传合成 CSV 后 POST 到前端 `/api/spectrum`，HTTP 404；缺少后端转发配置 |
| 短数据时域接口 | 4096 点时生成窗口少于 32，预处理返回 None 后报 HTTP 500 |
| 采样率缺失 | 仍返回成功，默认按 1024 Hz 分析；可能产生错误频率轴 |
| 采样率为零 | HTTP 500，除零错误 |
| 过短信号 | 20 点，HTTP 200、success=true，但 results 为空 |
| VMD | HTTP 500，缺少 vmdpy；原依赖清单未包含该包 |
| 缺少文件 | HTTP 400，符合基本校验预期 |
| 时域清洗 | [0,11,-12,1] 被处理成 [0,1]，直接删除超阈值样本 |

## CWRU 单记录验证

官方索引：https://engineering.case.edu/bearingdatacenter/12k-drive-end-bearing-fault-data

原始文件：https://engineering.case.edu/sites/default/files/105.mat

SHA256：`f80b0ea04fd06b372a0eaec7c056543ea37e4bb4727a5b173d2a5bacd2aa9cab`

取 `X105_DE_time` 前 32768 点，以单列 `value` CSV 导入，采样率 12000 Hz。官方标签：0.007 英寸内圈故障、0 hp。频谱接口和时域/推理接口均返回 200。

接口返回“7mm外圈故障”，分数 `0.9999980926513672`，与来源标签不符。原因尚未定位，可能涉及类别映射、训练约定、输入格式或模型泛化，不能据单样本判断总体准确率。此分数也不能视为校准后的可信概率。

代码中“7mm / 14mm / 21mm”标签还存在损伤尺寸单位表达问题，不能等同于 CWRU 的 0.007 / 0.014 / 0.021 英寸。

纠正前期静态检索：仓库实际包含两份已跟踪 `.pt` 权重，模型权重并未缺失；最初文件搜索遗漏了它们。

## 其他静态发现

- AI 报告提示词写入固定“立即停机”“剩余寿命预计 <24 小时”等内容，必须重写并约束证据来源。
- 模型预处理固定跳过 CSV 前 10 行，取前 10 列，按 32 个窗口处理，与通用 CSV 上传契约不一致。
- 包络谱当前对截取片段直接做 Hilbert 变换，仍需独立验证选频、窗函数、幅值标定和峰值匹配。
- 上传文件路径直接拼接用户文件名，应在复用前修复路径与覆盖处理；本轮未执行越界写入测试。

## 复现材料

以下文件统一保留在工作区 [research 目录](../../research/)，相对路径以该目录为基准；本报告作为调研附录存放于 `docs/appendices/`，不属于 VibraDiag 主设计步骤。

- `verify_ibearing.py`：合成信号与异常输入验证。
- `ibearing-verification-results.json`：结构化结果。
- `verify_ibearing_cwru.py`、`ibearing-cwru-results.json`：公开记录验证及结果。
- `verification-data/105.mat`、`verification-data/105-DE-first32768.csv`：原始文件和转换文件。
- `ibearing-build.log`、`ibearing-dev.log`、`ibearing-backend.log`、`ibearing-cwru.log`：执行日志。
- `ibearing-browser-results.json`：浏览器上传请求 HTTP 404 的证据。截图尝试超时，未取得截图，不将截图作为验证依据；已完成 DOM 与网络检查，关闭本次测试页并结束浏览器任务。

命令在 `research` 目录执行：

```bat
ibearing\.venv\Scripts\python.exe verify_ibearing.py
ibearing\.venv\Scripts\python.exe verify_ibearing_cwru.py
```

## 建议下一步

保留上游作为可追溯参考。VibraDiag 先建立独立信号分析服务和数据契约，复用经过验证的算法与图表组件；再加入证据模型、Agent 取证和人工复核。不要把当前模型输出与报告提示词直接带入产品。
