# VibraDiag｜基于 Agent 的旋转机械振动故障诊断系统

面向旋转机械振动故障排查，以电机轴承为首期对象的独立开发项目。采用前后端分离架构，逐步构建可追溯分析、Agent取证、知识检索与人工复核闭环。当前M1已通过本机验收，M2探索工具及M3受限Agent后端本机范围有条件通过，尚未达到工厂共享试用准入。

## 目前可运行的链路

正式工作台：登录 → CSV校验/预览 → 版本化参数确认 → 全量基础指标/幅度谱 → 授权历史读取与下载。已完成原型显式迁移、请求幂等/中断标记和本机性能基线。

“模型实验（开发）”保留DeepSeek工具调用及本地开发检索，仍读取原型数据，尚未接入正式取证闭环。M2已提供包络、理论频率、探索匹配和条件比较；正式故障定类、知识审核界面和工单仍待实现；后端已具备可选受限模型Agent取证决策，实测质量与失败见后文T05记录。见[操作指南](docs/05-m1-local-guide.md)与[M1验收](docs/acceptance/M1-acceptance.md)。

**M2软件交付完工，本机探索工具范围有条件通过**，见[M2总验收](docs/acceptance/M2-acceptance.md)。四类工具API、来源追溯、双输入比较和最小工作台齐备；匹配算法为frequency-matching-v2，已修复不显著多峰误标歧义。18项验收有完整追溯，公开数据/正式诊断资格保留明确限制，不能据此称工厂试用已通过。[M3-T01设计与契约](docs/acceptance/M3-T01-verification.md)与[T02a持久任务/规则执行基础](docs/acceptance/M3-T02a-verification.md)已实现：任务API、挂起恢复、真实授权工具、调用账本及进程重启对账；[T02b2](docs/acceptance/M3-T02b2-verification.md)已将新任务接入分步LangGraph持久规则图，加入任务绑定、当前身份与租约围栏；后续T03/T04已接入正式RAG及可选模型决策，实测质量见后文。当前44张业务表，另有4张检查点表，单API/单托管消费者。[T02b3](docs/acceptance/M3-T02b3-verification.md)已将五类纯数值核心接入可终止子进程，统一共享两名额；数值超时/任务取消会终止对应计算，宿主文件/数据库收尾仍独立处理。[T03a](docs/acceptance/M3-T03a-verification.md)已提供独立MD/TXT原文、版本、显式审核发布与撤回；向量检索由T03b2实现，方法任务知识引用由T03c接入，尚未生成完整回答。

## 工程结构

采用单仓库管理、前后端独立工程的结构，后端是分层模块化单体，不预拆微服务。

```text
VibraDiag/
├─ backend/                    # Python工程、后端配置、模型实验脚本和测试
│  ├─ src/vibradiag/
│  │  ├─ main.py               # FastAPI装配入口
│  │  ├─ api/v1/               # 版本化HTTP路由
│  │  ├─ schemas/              # API输入契约
│  │  ├─ services/             # 业务用例与存储端口
│  │  ├─ domain/               # 业务错误/不变量，后续实际领域实体
│  │  ├─ analysis/             # 确定性信号分析
│  │  ├─ agents/               # Agent图、节点与输出契约
│  │  ├─ infrastructure/       # 模型、检索及存储适配
│  │  └─ core/                 # 配置与运行路径
│  ├─ tests/unit/              # 数值与约束测试
│  ├─ tests/integration/       # API集成测试
│  └─ scripts/                 # 模型验证和对比脚本
├─ frontend/                   # Vue3＋TypeScript＋Vite独立工程
│  └─ src/
│     ├─ components/           # 可复用交互与图表组件
│     ├─ composables/          # 工作台状态及用例调用
│     ├─ api/                  # HTTP客户端
│     ├─ types/                # 前端接口类型
│     └─ styles/               # 样式
├─ scripts/                    # 开发启动、文档检查等仓库级工具
├─ deploy/                     # 部署边界说明；正式部署文件待实现
├─ docs/                       # 按顺序维护的设计文档
├─ data/                       # 开发知识与评测样例
├─ experiments/                # 实验结果与历史失败记录
├─ research/                   # 本地调研目录，仅说明文件入库
└─ .runtime/                   # 忽略入库的原始上传、索引与运行结果
```

真实密钥和数据库连接保存在 `backend/.env`；前端不持有密钥。数据库使用SQLAlchemy Core连接/事务及Alembic显式SQL迁移；原型JSON/NPZ尚未迁移。M3已提供持久规则任务，持久规则图与数值隔离已接入，方法任务已接知识引用，模型决策与逐字摘录可显式开启，默认rules。

## 本机启动

使用Python3.11工作区环境 `.venv`。后端依赖保存在 `backend/requirements.lock.txt`，前端依赖保存在 `frontend/package-lock.json`；克隆后按以下命令安装。

```bat
uv venv --python 3.11 .venv
uv pip sync --python .venv\Scripts\python.exe backend\requirements.lock.txt
uv pip install --python .venv\Scripts\python.exe --no-deps -e backend
npm --prefix frontend ci
.venv\Scripts\python.exe scripts\dev.py
```

访问前端 http://127.0.0.1:5173/ ，后端文档 http://127.0.0.1:8010/docs 。开发启动脚本同时启动两个独立进程，按Ctrl+C停止它们；也可以按各工程README分别启动。

数据库初始化、迁移和账号命令见[后端说明](backend/README.md)。当前机器使用独立的vibradiag_dev/vibradiag_test数据库；应用账号与迁移账号均不是超级用户。本地初始化账号记录在忽略入库的 `.runtime/local-access.json`，仅供开发。

默认页面为有身份/范围约束的正式数据工作台。模型实验需显式设置 `VIBRADIAG_ENABLE_PROTOTYPE=true` 才开放实验接口，默认关闭；本机私有配置已启用。模型实验仍是开发入口，不继承正式工作台的完整业务流程。

旧页面的实验分析已移到 `/api/v1/prototype/analyses`；正式 `/api/v1/analyses` 使用已确认revision的JSON请求并要求登录/CSRF和Idempotency-Key。变更后须重启后端并刷新页面。M1只允许一个API进程连接同一数据库执行分析；暂不使用多worker。详见[本轮验证](docs/acceptance/M1-T04-verification.md)。

通用模型默认使用 `deepseek/deepseek-flash`；密钥只在 `backend/.env` 中填写（已忽略）。参考 `backend/.env.example`，由用户明确启用 `VIBRADIAG_ALLOW_EXTERNAL_LLM=true`，不在网页填写或返回密钥。

Ollama 应在 `127.0.0.1:11434` 运行，已安装：

- `qwen3.5:2b` 或 `qwen3.5:0.8b`（可选本地对照，不再是默认通用模型）；
- `bge-m3:567m`（当前检索基线）或 `qwen3-embedding:0.6b`（对照候选）。

默认配置会向 DeepSeek 官方 API 发送问题、必要参数、特征及检索片段；不默认上传原始 CSV 或完整波形。Embedding 仍在本机运行。禁用外部调用时 API 任务明确失败，不静默切换模型；可显式选择本地对照。

只能绑定回环地址；不要在无身份权限的状态下改为共享网络监听。

使用页面提供的合成 CSV 时填写 4096 Hz、m/s²；文件含已知 128 Hz 正弦，仅用于检查计算与交互，不代表设备故障。

正式元数据保存在PostgreSQL，原文件/产物在 `.runtime/m1_data`（或显式数据根）；原型文件保持在原目录。刷新可恢复同一标签页上次授权记录；文件名不决定存储路径。生产生命周期与完整Worker恢复仍需继续实现。

## 测试与对比

```bat
.venv\Scripts\python.exe -m pytest -c backend\pyproject.toml backend\tests -q
npm --prefix frontend run build
.venv\Scripts\python.exe backend\scripts\benchmark_retrieval.py
.venv\Scripts\python.exe backend\scripts\benchmark_agent.py
.venv\Scripts\python.exe backend\scripts\check_deepseek.py
.venv\Scripts\python.exe backend\scripts\verify_deepseek.py
.venv\Scripts\python.exe backend\scripts\check_secret_hygiene.py
node scripts\check-docs.cjs
```

实验结果保存到 `experiments/`。重跑会覆盖当前结果，正式实验应先归档旧结果。原始首轮 Agent 失败结果已归档为 `agent-dev-results-round1.json`。`verify_deepseek.py` 会实际调用外部API并消耗账户额度；普通单元测试不会调用真实云端模型。

历史实验中的旧源码路径/hash是当时真实快照，不因目录重构而改写；重新运行的脚本记录新路径。早期单文件API/HTML及第三方检出仅在本地research保留，不随仓库上传，也不是应用入口。

检索实验是10张开发自编卡片和30条开发标注问题；Agent实验是3个自编工具路由案例，均不是独立留出集或故障准确率评测。单位测试中的模拟输出只用于检验约束；模型对比脚本调用真实本地模型。

## 文档

- [文档导航](docs/README.md)
- [完整需求](docs/01-requirements.md)
- [总体设计草案](docs/02-system-design.md)
- [M3 Agent详细设计](docs/07-m3-agent-design.md)
- [知识发布与检索设计](docs/08-knowledge-design.md)
- [模型选型与对比记录](docs/appendices/model-selection.md)

[T03b2](docs/acceptance/M3-T03b2-verification.md)已接真实本地BGE-M3、pgvector 0.8.6、不可变索引代与权限过滤后的精确检索；失败重建保留旧索引，撤回后不进入新检索，历史原文保留。模型/索引状态与诊断正确性分开，方法任务知识引用由T03c接入。

[T03c](docs/acceptance/M3-T03c-verification.md)已将授权检索接入方法任务：显式范围澄清、真实块引用、任务/调用回执绑定、进程恢复重附着及撤回后的草稿失效。输出仍是待复核资料，未调用DeepSeek决策或生成答案；下一步T04。

[T04a](docs/acceptance/M3-T04a-verification.md)已建立DeepSeek/本地Qwen的统一受控网关、每任务模型绑定和预算账本；校验候选ID与逐字摘录，无静默回退。此阶段尚未让正式图消费模型建议，下一步T04b。配置与边界见[09设计](docs/09-model-runtime-design.md)。

[T04b1](docs/acceptance/M3-T04b1-verification.md)已提供可选方法模型图：VIBRADIAG_METHOD_EXECUTOR=model对新方法任务启用模型候选选择、授权检索和逐字摘录草稿；分发默认rules，旧任务/数值图保留。配置在受理时固定，模型/工具完成回执可在恢复时复用；相关性未准入，不输出诊断结论。下一步T04b2数值候选编排。

[T04b2](docs/acceptance/M3-T04b2-verification.md)已提供可选数值模型图：VIBRADIAG_NUMERIC_EXECUTOR=model对新数值任务启用受控候选选择与实际工具执行，最多5工具/6决策；参数、父结果与引用由服务器验证。分发默认rules，方法配置独立，草稿不代表正式诊断。下一步T05对照评测。

[T05a](docs/acceptance/M3-T05a-verification.md)新增正式数值链路的规则/单次模型路由/逐步Agent开发对照，协议与源码运行前冻结，记录真实工具覆盖、引用链接、状态、耗时及token；不作为故障准确率或工厂准入。设计见[10评测设计](docs/10-agent-evaluation-design.md)。

[T05b](docs/acceptance/M3-T05b-verification.md)扩展了基于实际不确定量的反馈对照，并提供同样支持条件的规则基线；取消/撤确认单独计为生命周期边界。波形hash与来源族审计阻止将新仿真配置写成独立设备留出。真实首轮Agent功能3/4，原始F02提前结束保留；下一步T05c先核查修正，再补方法/比较评测。

[T05c1](docs/acceptance/M3-T05c1-verification.md)针对实际F02的停止条件混用新增数值v2：VIBRADIAG_NUMERIC_EXECUTOR=model_v2对新任务提供缺项作用域和实际频率不确定量；旧model/v1及方法策略保留，默认rules。新回归仅为已知开发样例，不覆盖原始失败或独立准入。

T05c1真实新版本开发回归8/8通过，保留旧F02失败及rules默认；下一步T05c2方法/比较任务评测。

[T05c2b](docs/acceptance/M3-T05c2b-verification.md)已完成固定方法语料与双输入比较首轮实测。事后审计：方法功能2/4、生命周期0/1、规则比较4/4；K01/K04/K05决策结构错误被安全拦截，不能当作有依据的资料拒答。原始回执保留，默认rules；下一步T05c3补最小校验诊断并以新版本回归，M3尚未结项。

[T05c3a](docs/acceptance/M3-T05c3a-verification.md)已接模型校验失败的脱敏类型/字段位置，旧回执为空且不可改写；全量828项通过。保持原策略的新观察采到4次choose_action额外字段错误，方法功能1/4、生命周期0/1，未取得质量准入。下一步T05c3b新方法字段协议与版本化回归。

[T05c3b](docs/acceptance/M3-T05c3b-verification.md)新增可显式启用的model_method_v2字段协议，保留旧方法/数值策略与默认rules。全量850项通过；新版本真实已知开发回归功能5/5、生命周期2/2、0次schema错误，旧失败保留。下一步收口M3本机条件验收，再进入M4知识与Agent工作台；独立语义及工厂准入仍未通过。


[T05d：M3本机条件验收](docs/acceptance/M3-acceptance.md)已完成证据收口：14个软件核验项可追溯至既有850项全量，新增审计器19项通过；完整M3语义/诊断及工厂资格仍未通过。[M4-T01a只读任务工作台](docs/acceptance/M4-T01a-verification.md)已实现共享登录、本人分页/详情及授权来源预览，12项状态测试、类型/构建和15项浏览器流程通过；窄屏最终视觉验收待补。[M4-T01b任务写交互](docs/acceptance/M4-T01b-verification.md)已接创建、回答、恢复及取消，保留原幂等命令并处理版本冲突；21项前端测试、类型/构建和10项真实浏览器流程通过。下一步按[11设计](docs/11-m4-workbench-design.md)接M4-T02知识管理页面，完整M4及工厂准入仍待完成。
