# Backend

FastAPI后端使用Python `src` 布局，包名为 `vibradiag`，入口为 `vibradiag.main:app`。

- `api/`：路由、HTTP错误映射、依赖注入；不放信号算法和供应商密钥。
- `schemas/`：HTTP输入契约，与Agent输出契约区分。
- `services/`：分析、诊断与检索用例，依赖存储端口，不依赖FastAPI。
- `domain/`：不依赖HTTP的业务错误/不变量；后续加入实际领域实体，不提前创建空表模型。
- `analysis/`：确定性信号计算，可脱离模型和HTTP测试。
- `agents/`：LangGraph状态/节点与模型输出契约。
- `infrastructure/`：DeepSeek/Ollama协议、当前向量索引和本地存储适配。
- `core/`：配置与路径，不存真实密钥常量。
- `tests/unit/`、`tests/integration/`：数值/约束与API集成测试。
- `scripts/`：真实模型验证与实验命令；调用云端的脚本会消耗额度。

项目目前使用根目录 `.venv` 作为工作区Python环境。安装和命令从项目根目录执行：

```bat
uv pip sync --python .venv\Scripts\python.exe backend\requirements.lock.txt
uv pip install --python .venv\Scripts\python.exe --no-deps -e backend
.venv\Scripts\python.exe -m uvicorn vibradiag.main:app --host 127.0.0.1 --port 8010
.venv\Scripts\python.exe -m pytest -c backend\pyproject.toml backend\tests -q
```

`.env`位于本目录，环境变量优先于本地文件；不通过API或前端返回密钥。M1已有PostgreSQL、身份、采集、持久基础分析及正式前端；完整报告/工单闭环与Agent任务恢复仍待实现。

## PostgreSQL与基础账号

本机已配置PostgreSQL18.6，开发库vibradiag_dev、测试库vibradiag_test。每库独立迁移/应用账号，均无superuser/createdb/createrole；迁移账号拥有其数据库，应用账号只持有必要表权限。新的环境由本地DBA创建对应数据库和账号，参照.env.example填写四个URL，不使用postgres作为应用账号。PostgreSQL17仍是原参考目标，尚未实测；pgvector留M3。

从仓库根执行：

```bat
.venv\Scripts\python.exe backend\scripts\migrate.py
.venv\Scripts\python.exe backend\scripts\manage.py create-org --name MyFactory
.venv\Scripts\python.exe backend\scripts\manage.py create-user --org ORGANIZATION_UUID --username admin --roles admin
.venv\Scripts\python.exe backend\scripts\manage.py create-user --org ORGANIZATION_UUID --username engineer --roles engineer
```

create-user交互输入12—1024字符的应用密码，也支持私有进程环境VIBRADIAG_INITIAL_PASSWORD；数据库口令和应用登录密码分开。CLI是可信本机运维入口，不提供公开注册。create-asset、grant-asset、set-roles、set-user-status的参数见 `manage.py --help` 及子命令帮助；变更与审计同事务，禁用用户/调整角色会撤销其会话。管理员只有显式业务角色及数据范围时才有相应业务权。

迁移包含0001—0004的16张基础表，0005完成产物保护、0006文件校验/身份保护、0007分析快照保护、0008新增legacy_imports映射账本，M1为17张业务表；0009再新增组件/轴承版本/context三张，0010再新增tool_profiles/tool_runs/tool_artifacts/tool_idempotency_records，0011新增analysis_facts/evidence_items并扩展匹配父引用，当前26张业务表；重复升级安全。已执行版本不修改，后续新增迁移；当前不提供破坏性downgrade。迁移脚本给应用账号授权并撤销其对迁移版本表及历史表的修改权。不要只运行SQLAlchemy create_all代替迁移。

登录接口 `POST /api/v1/auth/login` 输入organization_id、username、password；返回csrf_token并设置HttpOnly/SameSite=Strict Cookie。写请求需X-CSRF-Token。`GET /auth/me`、`POST /auth/logout`及授权分页资产/测点接口已实现。共享HTTPS默认Secure Cookie；仅本机HTTP开发设置VIBRADIAG_COOKIE_SECURE=false。Vite保留Host，走同源/api代理；直接跨源登录被拒绝。基础限流为单进程每来源IP每分钟20次，不能当作生产分布式限流。

数据库测试使用VIBRADIAG_TEST_DATABASE_URL与VIBRADIAG_TEST_MIGRATION_DATABASE_URL，缺失时明确skip；与开发数据库同名会失败。测试会向专用测试库添加带随机ID的测试记录，不清空开发库。缺少测试配置时不能宣称数据库验收通过。

## 正式采集接口（M1-T03）

`POST /api/v1/measurements`发送multipart：file为单列value的UTF-8 CSV，metadata为JSON字符串，例如 `{"sampling_rate_hz":1024,"acceleration_unit":"m/s2","source_type":"synthetic"}`。需登录Cookie和X-CSRF-Token，上传后只生成已校验数据/初始参数版本，不自动启动诊断。文件≤32MiB、1024—1000000点；metadata有界且拒绝未知字段。

`GET /measurements`、`GET /measurements/{id}`提供授权列表/详情；详情包含raw单位预览、样本数、hash和当前revision。`POST /measurements/{id}/revisions`输入完整metadata、reason、expected_current_revision_id和row_version；冲突409，不覆盖旧版本。`GET /measurements/{id}/revisions`提供有权限的历史。`POST /measurements/{id}/confirmations`输入revision_id，仅能确认当前版本；每次修订需重新确认，确认不等于工程复核。`GET /measurements/{id}/original`验证hash后下载原始字节。以上路径均置于/api/v1下。

新文件默认存 `.runtime/m1_data`，可通过VIBRADIAG_DATA_ROOT显式配置私有数据根，不能放frontend/public。暂存失败自动清理；文件落盘后事务失败只留下无引用文件，运行以下只读对账命令识别，不自动删除：

```bat
.venv\Scripts\python.exe backend\scripts\migrate.py
.venv\Scripts\python.exe backend\scripts\reconcile_files.py
.venv\Scripts\python.exe backend\scripts\verify_measurement_flow.py
```

verify_measurement_flow读取私有.runtime/local-access.json中的local-engineer开发账号，使用临时回环HTTP服务创建一条明确标为synthetic的数据，验证参数版本及API进程重启后重读，结果保存experiments/m1-t03-http-smoke.json；不调用云端模型。应用仍限制本机，前端正式流程尚待M1-T05。

## 正式基础分析接口（M1-T04）

`POST /api/v1/analyses` JSON：measurement_revision_id、confirmation_id、config_profile=basic-v1，可选retry_of。需要Idempotency-Key和X-CSRF-Token；确认必须属于本人及当前参数版本。首次接受返回201及资源状态，计算/存储失败也会建立failed资源，客户端必须检查status/error_code；同键重放终态200，运行中202，同键不同请求409，容量满429。显式重试使用新键，可关联同版本失败/中断记录；不会自动重跑。

`GET /analyses/{id}`返回版本快照、指标、状态、处理配置和产物链接，较新参数存在时is_current_revision=false。`GET /analyses/{id}/series?kind=waveform|spectrum`只提供最多2000点的展示数组；全谱由`GET /analyses/{id}/artifacts/amplitude_spectrum`下载数值NPZ（allow_pickle=False读取）。artifact仅允许result、waveform_preview、amplitude_spectrum、manifest四种类型。每个入口都检查权限和文件hash，数组不塞入任务概要。

正式数值代码位于analysis/basic.py，原型signal.py保留实验用途。幅度谱与指标不等于故障诊断。原型上传/读取移到/api/v1/prototype/analyses，Agent暂时仍读取原型记录；不能把正式ID交给原型诊断就视为完整接入。

API启动取得数据库会话锁并将遗留running标interrupted，故M1不能使用uvicorn多worker或同时开两个同库API。分析并发2，无持久排队；租约丢失须重启。实际HTTP/进程终止验证命令为 `.venv\Scripts\python.exe backend\scripts\verify_analysis_flow.py`，请在没有其他新版API运行时执行；该脚本创建合成数据并只终止自己启动的临时API。M3再实现独立Worker与完整任务恢复。

`GET /api/v1/analyses?measurement_id=...`提供授权分页分析历史，按历史版本设备范围过滤。前端正式流程已在M1-T05接入，参见docs/05-m1-local-guide.md。

原型迁移用 `backend/scripts/import_legacy.py --source DIRECTORY --actor ENGINEER_UUID --batch NAME`，默认dry-run；加--apply才复制原CSV及未确认参数，--manifest可提供显式元数据修正。按组织/来源目录/legacy ID去重，内容/参数变化冲突，不自动接收旧指标或诊断。可信CLI使用短期运维会话/审计，不输出密码。

`backend/scripts/benchmark_m1.py`在独立测试库/文件根执行100次120000点HTTP分析、百万点及2并发/429检查；输出包含所有请求延迟、失败计数和实际Uvicorn进程峰值工作集，不含DB/浏览器/模型总内存。`source_manifest.py`记录不含私有文件的源码SHA256。完整M1证据位于docs/acceptance/M1-acceptance.md。

## M2纯数值模块（T02a）

analysis/envelope.py与bearing_frequencies.py已实现，参数在schemas/tools.py严格校验，结果为domain/tool_results.py中的ok/unavailable/failed；非法参数抛ToolInputError。数组与JSON概要分离，不生成已注册的执行/证据ID，不查库或调用模型。

直接验证：`.venv\Scripts\python.exe backend\scripts\verify_m2_numerics.py`；结果为experiments/m2-t02a-numerics.json。完整回归218项，见docs/acceptance/M2-T02a-verification.md。该脚本只做合成数值/百万点冒烟，耗时不含HTTP与持久化，不能和M1基线直接比较。

checked/来源/qualification是未来服务构造的内部参数，不是公共调用方可自证的授权。evidence_preconditions_satisfied只说明当前数值声明满足核对条件；uncertainty_complete不代表已完成频谱匹配。T02b仍需从授权台账、context与已查证资格记录构造输入。T02b1/b2现已从授权台账/context构造频率参数，并接正式工具运行接口，T03b再扩展匹配API和最小页面，当前26张业务表；profile诊断资格发布仍待后续验证。

## M2目标与上下文（T02b1）

`scripts/manage_diagnostics.py --help`提供component、bearing-revision、context、read-context四种本机工程操作。密码交互输入，写操作读取有界JSON声明；身份、核对时间、rpm和关联关系由服务端查证，操作与审计同事务。需工程角色及相应设备/采集范围，管理员无隐含业务权。用法、schema与边界见[实施记录](../docs/acceptance/M2-T02b1-verification.md)。

完整后端241项测试通过，新增23项；只读回执由`backend/scripts/verify_m2_contexts.py`生成。这是T02b1历史基线；T02b2运行服务已完成，见下文。

## M2正式工具运行（T02b2）

新增`POST /api/v1/tool-runs`及授权列表/详情/series/artifact接口。包络请求tool_name=analyze_envelope，引用当前measurement_revision_id、本人confirmation_id、profile_id，可选context_id，mode默认为exploratory。理论频率tool_name=calculate_bearing_frequencies，引用相同输入/确认和context_id；不接收额外数值覆盖。

`manage_diagnostics.py envelope-profile`登记同设备/采样率/算法版本的不可变探索配置；当前不能发布qualified配置，diagnostic_evidence会返回unavailable及缺失条件。运行的completed与数值结果ok/unavailable分开。工具产物独立于M1，按工具/状态验证清单，单文件≤64MiB；失败/中断无有效产物，落盘未提交文件由reconcile_files只读报告。

M1/M2共享总并发2和单API租约，遗留running在启动时恢复interrupted。同键重放原资源，冲突409；显式重试新键/新ID，可附retry_of。新增26项，完整267项通过；真实HTTP覆盖120000/1000000点、混合并发/429、API强制终止及恢复重试。复现脚本`backend/scripts/verify_m2_tool_flow.py`只使用专用测试库和隔离合成数据根，不能与测试同时运行。详细契约和限制见[实施记录](../docs/acceptance/M2-T02b2-verification.md)。下一步T03匹配/事实/证据与最小界面。

## M2匹配数值核心（T03a）

`analysis/matching.py`已实现纯数值频率匹配，`schemas/matching.py`是内部输入契约，不是公开请求。匹配同一输入/context的包络和理论结果，可补完整基础谱；保留窗口、不确定量、局部参考、峰/共享ID、重合/转频/拍频歧义。返回四类观察状态，不输出故障分类、注册EV或证据资格。已有tool-runs仍只接包络/理论频率。

新增44项，完整311项通过。`.venv\Scripts\python.exe backend\scripts\verify_m2_matching.py`生成纯数值及百万点回执，详见[实施记录](../docs/acceptance/M2-T03a-verification.md)。这是T03a历史基线；T03b已接匹配API、事实/探索证据与最小界面，当前26张业务表。

## 匹配运行与探索证据（T03b）

`POST /api/v1/tool-runs`新增tool_name=match_frequency_evidence，引用当前revision/本人confirmation/context和envelope_run_id、frequency_run_id、basic_analysis_id，配置固定matching-v1。服务端校验父运行同输入/context、权限、确认和完整产物hash，保存父版本/指纹/清单快照，并在完成事务中登记不可变facts/evidence。读取匹配或解析ID时重新校验完整来源。

新增`GET /api/v1/facts/{id}`、`/evidence/{id}`及`/tool-runs/options/{measurement_id}`。fact/evidence ID是授权解析引用；共同原文件/revision/context和父事实可追溯。所有材料限定exploratory_observation，数据库禁止evidence_eligible=true；输入确认撤销保留历史并标有效性限制，撤权/篡改阻断读取。

前端最小工具区已接配置/上下文选择、三个父结果、包络/理论标线、匹配/歧义和来源查看；响应丢失后刷新/同键重放通过。完整后端332项、前端类型/构建通过；`backend/scripts/verify_m2_matching_flow.py`提供专用测试库的12万/百万点真实HTTP验证。详情见[T03b记录](../docs/acceptance/M2-T03b-verification.md)。下一步T04前后比较。

## 前后比较数值核心（T04a）

`analysis/comparison.py`提供compare_measurements，`schemas/comparison.py`定义内部采集/来源/负载容差/测量不确定量快照。先检查同目标/测点/方向、源采集关系、采样/处理、带宽/采集链、采集顺序和稳态转速负载，再计算基础指标及可选完整包络的有单位差值。可比/不可比/同源分别记录，零基线不算百分比，不作维修有效判断。

T04a新增62项的历史结果见[T04a记录](../docs/acceptance/M2-T04a-verification.md)。T04b现已接入受控声明CLI、双输入运行API与工作台，数据库0012/27张业务表，完整417项测试通过，见[T04b记录](../docs/acceptance/M2-T04b-verification.md)。`backend/scripts/verify_m2_comparison_flow.py`复现真实HTTP、百万点双输入及进程恢复。公开请求只选择声明和双侧确认，不接受客户端checked；下一步T05公开数据基线。

## 公开数据离线评测（T05a）

[固定清单及复现命令](../datasets/public-data-v1/README.md)包括CWRU与Paderborn的28条开发通道。`prepare_public_data.py`核验/提取官方固定资产，`benchmark_public_data.py`运行完整通道形状基线，`verify_public_data_reproduction.py`验证隔离缓存重新提取。单位/采样缺口保留，不将代理幅度当物理量；validation/sealed_test信号被开发运行器拒绝。无新增业务表或上传接口。详见[T05a记录](../docs/acceptance/M2-T05a-verification.md)，下一步T05b独立实体评测。

## 固定验证与封存（T05b1）

[数据v2](../datasets/public-data-v2/README.md)冻结三个验证和三个封存轴承。`prepare_holdout_data.py`仅允许提取验证成员；`validate_public_readiness.py`按冻结协议检查24条验证通道，保留单位/时间轴阻断，不计算诊断准确率；`verify_holdout_reproduction.py`在隔离缓存拦截解压调用验证测试归档未进入提取器。详细边界见[T05b1记录](../docs/acceptance/M2-T05b-verification.md)。下一步T05b2研究适配及相对特征评测。

## 条件性时间轴与形状研究（T05b2）

`verify_time_adaptation.py`检查固定候选的18组合成真值，`benchmark_time_adaptation.py --verify-replay`只处理既有开发/验证Paderborn记录并检查重放一致性。候选、协议和源码在公开特征执行前冻结，原始严格准入及测试封存保持。40/48条完成条件性相对特征，8条按固定门槛拒绝；没有生成合格诊断profile。详见[T05b2记录](../docs/acceptance/M2-T05b2-verification.md)。

## M2结项与M3承接

[M2总验收](../docs/acceptance/M2-acceptance.md)在本机探索工具范围有条件通过。`export_m2_tool_contract.py`导出当前请求schema/四类工具版本与权限边界；新匹配执行为frequency-matching-v2（阈值配置仍matching-v1），旧结果不重算。`verify_m2_release_baseline.py`复现规则/邻带基线，三个HTTP验证脚本支持--receipt与--tests-report以保留历史，`verify_m2_acceptance.py`核验18项实际测试、源码指纹、构建与资源证据。公开严格匹配覆盖0/52，诊断profile发布数0；M3仅先接探索/澄清/拒答，正式诊断仍有发布阻断。
