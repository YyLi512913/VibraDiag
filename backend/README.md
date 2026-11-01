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

`.env`位于本目录，环境变量优先于本地文件；不通过API或前端返回密钥。M1已有PostgreSQL、身份、采集、持久基础分析及正式前端；M3已提供持久规则任务及中断恢复；正式模型Agent图、报告/工单闭环仍待实现。

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

迁移包含0001—0004的16张基础表，0005完成产物保护、0006文件校验/身份保护、0007分析快照保护、0008新增legacy_imports映射账本，M1为17张业务表；0009再新增组件/轴承版本/context三张，0010再新增tool_profiles/tool_runs/tool_artifacts/tool_idempotency_records，0011新增analysis_facts/evidence_items并扩展匹配父引用，0012新增comparison_declarations，0013新增四张Agent任务表，当前31张业务表；重复升级安全。已执行版本不修改，后续新增迁移；当前不提供破坏性downgrade。迁移脚本给应用账号授权并撤销其对迁移版本表及历史表的修改权。不要只运行SQLAlchemy create_all代替迁移。

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

## M3-T01 正式Agent契约（未接运行API）

`agents/formal_contracts.py`定义任务/恢复、候选选择、预算及未发布草稿；`formal_policy.py`提供无IO的预算/候选/引用检查。现有`agents/graph.py`仍是M0原型，本轮没有连接正式任务API、Worker或模型。`export_m3_agent_contract.py`导出机器契约，`verify_m3_t01.py`核对实际JUnit与源码。见[07设计](../docs/07-m3-agent-design.md)，下一步T02a持久任务及真实授权执行。

## M3-T02a 持久任务与规则执行基础

`POST /api/v1/agent-tasks`接受goal、description及measurement_revision_id；比较还需comparison_declaration_id。目标为inspect_signal、bearing_observations、compare_measurements、explain_method。写操作要求登录Cookie、X-CSRF-Token及Idempotency-Key，创建202，同命令重放200；GET列表和详情仅对发起人开放，并重查输入范围/来源。原API文档`/docs`可用于本机调试，目前没有任务前端。

缺确认、目标或配置时状态waiting_information；详情提供question.id及可选choices。先用既有采集/工程操作补齐条件，再POST /agent-tasks/{id}/resume，输入expected_version、question_id、answer，使用新幂等键。choices存在时answer须为提供的UUID，普通文字不能确认输入或覆盖参数。POST /agent-tasks/{id}/cancel接受expected_version和幂等键，版本冲突409。会话失效需重新登录，再由同一操作者受控恢复。

API启动默认开启一个托管规则消费者，`VIBRADIAG_AGENT_WORKER_ENABLED=false`可关闭调度但保留任务API。仍只能一个API进程连接同一数据库，M1/M2共享数值并发2；不支持多进程Worker。每人最多32个未结束任务，恢复最多20次。工具预留与调用键持久化，恢复不重置预算。容量429自动退避一次，未知响应重放原键；明确失败/中断需显式恢复，产生关联retry_of的新调用。

响应明确标记rules_scaffold_v1、model_called=false、formal_graph_executed=false。草稿始终unpublished_draft，规则文本不作故障分类；正式知识尚未接入，explain_method返回有缺失原因的refused。取消和120秒预算为步骤边界的协作控制，不能硬终止数值计算；重启时按持久tick保守计时，包括停机时间。完整持久图、对账与隔离/硬预算由T02b推进。

验证命令（从根目录，先完成测试，脚本使用独立测试库且只终止自建API）：

```bat
.venv\Scripts\python.exe -m pytest -c backend\pyproject.toml backend\tests -q --junitxml=experiments\m3-t02a-tests.xml
.venv\Scripts\python.exe -m pytest -c backend\pyproject.toml backend\tests\integration\test_agent_tasks.py -q --junitxml=experiments\m3-t02a-task-tests.xml
.venv\Scripts\python.exe backend\scripts\verify_m3_task_flow.py
```

详见[T02a验证与限制](../docs/acceptance/M3-T02a-verification.md)。T01契约与回执保留为当时的设计快照，不覆盖其未实现标记。

## M3-T02b1 官方检查点兼容性验证

锁定可选依赖组`checkpoint-probe`：langgraph-checkpoint-postgres 3.2.0及psycopg-pool 3.3.3，完整requirements.lock.txt包含此组。仅安装backend包时可用`uv pip install --python .venv/Scripts/python.exe -e "backend[checkpoint-probe]"`安装；完整开发环境继续按锁文件同步。

`scripts/m3_checkpoint_probe.py`与`verify_m3_checkpoints.py`是验证代码，不被API或任务执行器导入。脚本要求专用测试库及独立迁移/应用角色，在随机且带归属标记的schema内创建4张官方检查点表和1张验证账本；结束时只清理该schema，业务表保持0013/31张。不在应用启动调用PostgresSaver.setup，不提供任意thread_id读取或删除接口。

```bat
.venv\Scripts\python.exe -m pytest -c backend\pyproject.toml backend\tests -q --junitxml=experiments\m3-t02b1-tests.xml
.venv\Scripts\python.exe backend\scripts\verify_m3_checkpoints.py
uv pip check --python .venv\Scripts\python.exe
```

真实子进程验证：首次运行动态中断，下一进程恢复后在节点提交前阻塞，父进程超时终止自建进程，再由新进程从持久resume继续。解析正弦基础计算登记一次，节点会重入三次；这证明需要幂等账本，不是检查点天然提供exactly-once。0.5秒终止门槛从子进程就绪开始计时，排除解释器启动；不是业务API硬超时或设备效果指标。

显式限制反序列化类型并禁用pickle；官方严格模式仍可能把未批准构造对象变成普通数据，业务图恢复必须另做状态校验。原始Saver不含组织/操作者/租约授权，T02b2完成任务绑定及读写围栏后才能接正式图；业务跨进程容量和委托身份另行实现。详见[验证记录](../docs/acceptance/M3-T02b1-verification.md)。

## M3-T02b2 业务持久规则图

新任务默认rules_graph_v1 / rules-graph-v1，旧rules_scaffold_v1任务继续原版本。任务创建事务生成不可变服务器thread绑定；任务详情只返回图版本/检查点数量，不提供原始检查点或任意thread_id访问。LangGraph PostgreSQL依赖现已提升为主依赖；checkpoint-probe组保留作为前一阶段安装命令的兼容别名。

迁移0014建立agent_graph_bindings及public_agent_checkpoints内4张官方表，当前32张业务表，另有4张检查点表。先用迁移账号执行`backend/scripts/migrate.py`再重启API；运行账号不能自动setup或修改迁移版本/删除图历史。新建规则图仍只有一个托管消费者，不支持另起多API或独立Worker绕过总容量。

每次检查点读写重查当前身份、输入和绑定；短事务对任务行FOR SHARE，再由数据库触发器核对running与租约。旧Saver在取消/重启后失效。图状态只含任务/版本和运行光标，拒绝未知字段/节点、pickle及超限载荷；预算和真实结果始终重读业务表。缺信息保留待执行节点，受控resume后invoke(None)继续；不把浏览器回答直接传给图Command。

测试覆盖“工具完成但检查点未提交”与“图完成但业务终态未提交”两个窗口，恢复复用来源而不重复计算。完整执行/限制见[T02b2记录](../docs/acceptance/M3-T02b2-verification.md)。

```bat
.venv\Scripts\python.exe -m pytest -c backend\pyproject.toml backend\tests -q --junitxml=experiments\m3-t02b2-tests.xml
.venv\Scripts\python.exe -m pytest -c backend\pyproject.toml backend\tests\integration\test_agent_tasks.py -q --junitxml=experiments\m3-t02b2-task-tests.xml
.venv\Scripts\python.exe backend\scripts\verify_m3_durable_flow.py
```

formal_graph_executed=true仅表示持久规则图已运行，model_called仍false。模型决策、正式知识、任务前端和数值进程硬取消尚未接入；下一步T02b3处理业务数值隔离、委托身份与跨进程总容量。

## M3-T02b3 数值子进程

默认`VIBRADIAG_NUMERIC_BACKEND=process`，五类纯数值核心独立解释器执行，API保留全部授权/来源/产物/事务。`VIBRADIAG_NUMERIC_TIMEOUT_MS`为100—120000ms，默认120000，Agent任务可用剩余预算会进一步缩短。Windows先将暂停进程纳入Job再运行，默认Job提交内存上限1GiB、最多2个进程（启动器/解释器）；环境清理并固定数值库单线程。

所有M1/M2子进程共用现有API两名额，不在子进程各建容量池；单API租约仍必需。数值超时由独立看门狗终止，任务取消终止对应Job并等待收尾。异常/超时保存failed来源，无空成功；正常结束清理本次UUID IPC目录。强制API退出时内核终止数值进程，可能留下私有IPC文件，生产保留/对账策略留M6。

`compute_execution`执行回执记录process、协议、有效期限、PID和耗时，可在正式来源结果中核对。无需给子进程数据库或模型凭据。固定JSON/NPZ协议不支持pickle、动态函数/任意文件路径。数值进程为同用户可信代码隔离，未提供OS文件/网络安全沙箱；Windows已验证，Linux分支待验证。宿主准备/IO/入库不属于可硬中断数值进程，不能按此宣称整个HTTP请求硬实时。

历史故障注入使用专用测试库限定的inline_test；真实子进程专项显式process。inline_test和故障场景都不是公开请求字段，生产不能启用或自动回退。

```bat
.venv\Scripts\python.exe -m pytest -c backend\pyproject.toml backend\tests -q --junitxml=experiments\m3-t02b3-tests.xml
.venv\Scripts\python.exe -m pytest -c backend\pyproject.toml backend\tests\integration\test_numeric_process.py -q --junitxml=experiments\m3-t02b3-process-tests.xml
.venv\Scripts\python.exe backend\scripts\verify_m3_isolation_flow.py
```

详细实际验证及限制见[T02b3记录](../docs/acceptance/M3-T02b3-verification.md)。数据库仍0014/32业务表与4检查点表，重启API后生效。下一步正式知识发布/检索。

## M3-T03a 文本知识版本与发布

迁移0015新增6表，当前38张业务表加4张检查点表。新正式路径为`/api/v1/knowledge-documents`，独立于旧原型`/api/v1/knowledge/search`；无需启用原型。

工程师上传UTF-8 MD/TXT（5MiB、20万LF行、4096块上限），multipart恰好file＋metadata JSON，写操作要求Cookie、CSRF、Idempotency-Key。元数据包含title/source_note/equipment_scope/component_scope/content_version/usage_rights/content_origin/visibility/reason；设备范围另需asset_id。修订路径`/{document_id}/revisions`另需expected_document_version。原字节保留，规范化正文以固定策略分块，原文下载仅text/plain/attachment。

`/revisions/{id}/submit|reject|publish|withdraw`需要expected_version、expected_document_version和reason。工程师只可导入/修订/提交自己的文档；发布/退回/撤回还需要显式审核能力。管理员POST`/review-grants`须复核本人password，指定同组织工程师user_id、enabled、expected_version（首次0）及理由；管理员无默认正文或发布权。密码不进入命令或事件。

新草稿保留原发布版；新发布原子替换旧发布并标撤回历史。`/revisions/{id}/chunks`与`/chunks/{id}`提供稳定UUID、标题路径、行/字符位置和hash，撤回后仍可在原权限下追溯。列表按权限过滤后分页，草稿不泄漏给他人；撤销审核/设备权限立即影响后续请求。

这是文本发布基础，没有向量排序或模型调用。所有formal_retrieval_available=false，Agent方法解释仍拒答。已有本地BGE-M3和pgvector扩展接入在T03b，T03c才接Agent引用。开发内容不自动成为正式语料，元数据的来源/许可声明不等于独立认证。

```bat
.venv\Scripts\python.exe backend\scripts\migrate.py
.venv\Scripts\python.exe -m pytest -c backend\pyproject.toml backend\tests -q --junitxml=experiments\m3-t03a-tests.xml
.venv\Scripts\python.exe -m pytest -c backend\pyproject.toml backend\tests\unit\test_knowledge_text.py backend\tests\integration\test_knowledge_documents.py -q --junitxml=experiments\m3-t03a-knowledge-tests.xml
.venv\Scripts\python.exe backend\scripts\verify_knowledge_publication.py
```

详见[08设计](../docs/08-knowledge-design.md)及[T03a记录](../docs/acceptance/M3-T03a-verification.md)。知识前端留M4；本轮用本机API文档和HTTP脚本操作。

## M3-T03b1 固定Embedding身份

正式索引将使用独立LocalEmbedding适配器，配置见config/embedding-bge-m3.json；固定本机BGE-M3的实际digest、1024维与输入/规范化策略。仅允许回环Ollama，不自动下载、切换或混用旧原型NPZ。当前只提供适配器和验证脚本，尚未接知识API/后台索引或Agent。

从仓库根目录先运行专项测试，再运行真实本地模型检查：

```bat
.venv\Scripts\python.exe -m pytest -c backend\pyproject.toml backend\tests\unit\test_formal_embedding.py backend\tests\integration\test_vector_readiness.py -q --junitxml=experiments\m3-t03b1-embedding-tests.xml
.venv\Scripts\python.exe backend\scripts\verify_formal_embedding.py
```

脚本还只读检查开发/测试库的扩展安装与账号权限，不更改扩展、schema或角色。vector未安装时记录阻塞，不能视作pgvector检索已完成；接入步骤见[设计08](../docs/08-knowledge-design.md)和[T03b1记录](../docs/acceptance/M3-T03b1-verification.md)。重复运行覆盖同名本阶段回执，历史冻结回执不改写。

## M3-T03b2 索引构建与授权检索

先由独立维护连接设置仅当前进程使用的VIBRADIAG_VECTOR_ADMIN_URL，针对开发/测试库分别运行scripts/setup_vector_extension.py，随后移除维护变量；维护、迁移和应用URL须指向同一目标库。扩展固定0.8.6，位于独立vibradiag_extensions schema；应用/迁移角色无该schema的CREATE权限。之后按原迁移账号运行scripts/migrate.py到0016，重启API。API不会自动安装扩展。

POST /api/v1/knowledge-indexes/revisions/{id}/build要求显式审核能力、已发布原文、Cookie/CSRF/Idempotency-Key，输入expected_version、expected_document_version、expected_index_version（首次0）及reason。同步构建上限128块/每批8块，processing先提交，成功原子写入向量/ready/活动头，失败保留原索引。返回201表示本次构建已登记，必须核对status和error_code，不等于索引成功；相同键重放200不重算。过期processing用新键显式重试。

POST /api/v1/knowledge-indexes/search输入query和可选asset_id/equipment/component/limit（1—5）/include_developer。需要当前业务身份与CSRF，每次独立查询记账，无业务幂等键；JSON上限16KiB。默认排除开发内容，include_developer仅显式审核工程师可用。先过滤当前发布、组织/设备/适用范围、活动索引模型/运行版本，再精确cosine排序，已授权文本hash去重，最多10000候选；无合格候选不调用模型，超限明确拒绝。

GET /knowledge-indexes/revisions/{id}和/runs/{id}按原文范围授权；GET /retrievals/{id}仅原发起人可读历史账本，无正文，不代替当前引用授权。原文/块响应新增active_index_id/index_version/latest_index_status等；失败重建可以同时显示活动ready和最新failed。已发布并不等于索引完成，原文下载不依赖模型在线。Agent方法解释仍拒答正式知识未接入，T03c再处理。

```bat
.venv\Scripts\python.exe -m pytest -c backend\pyproject.toml backend\tests -q --junitxml=experiments\m3-t03b2-tests.xml
.venv\Scripts\python.exe -m pytest -c backend\pyproject.toml backend\tests\integration\test_knowledge_indexes.py -q --junitxml=experiments\m3-t03b2-index-tests.xml
.venv\Scripts\python.exe backend\scripts\verify_knowledge_retrieval.py
```

详见[T03b2验收](../docs/acceptance/M3-T03b2-verification.md)。上述命令从仓库根目录执行，HTTP验证须在pytest结束后运行，使用专用测试库和真实本地模型。旧阶段回执保留历史范围，新记录不覆盖旧结果。

## M3-T03c 方法任务知识引用

迁移0017后重启API，新explain_method任务使用rules_knowledge_v1/rules-knowledge-v1；数值与既有方法任务保留原图版本。创建任务时可提供knowledge_scope={equipment,component,asset_id?}，仅方法任务可用；不能绑定measurement_revision_id。没有范围会挂起并提供有限组织范围选项，恢复时answer必须是已提供的选项ID；设备范围须创建时显式提供且有读取权。

查询超过2000字符或不符合纯文本约束时挂起knowledge_query_required，恢复显式输入合规短查询，普通回答不改变设备/采集参数。search_published_knowledge占用工具预留，固定排除开发内容，受任务剩余120秒预算和当前租约限制。503挂起等待显式重试；回执已提交而响应未知时，同call_id恢复不重新调用Embedding。

任务详情新增knowledge_references、embedding_called和draft_validity.knowledge_current；model_called仍表示LLM决策，本轮false。有来源也因相关性尚未准入而返回completed_degraded/knowledge_relevance_not_qualified，无来源拒答。资料正文标untrusted_content，固定观察不复制原文建议。撤回、索引头变化或撤权后隐藏草稿/资料或拒绝任务范围访问，历史账本和原文独立保留。

```bat
.venv\Scripts\python.exe backend\scripts\migrate.py
.venv\Scripts\python.exe backend\scripts\export_m3_agent_contract.py
.venv\Scripts\python.exe -m pytest -c backend\pyproject.toml backend\tests -q --junitxml=experiments\m3-t03c-tests.xml
.venv\Scripts\python.exe -m pytest -c backend\pyproject.toml backend\tests\integration\test_agent_knowledge.py -q --junitxml=experiments\m3-t03c-agent-tests.xml
.venv\Scripts\python.exe backend\scripts\verify_agent_knowledge.py
```

实际进程验证须等所有pytest结束；使用专用测试库和真实本地模型。探针只在自建进程暂停/终止及恢复指定任务，普通API/消费者不加载故障模式。详见[T03c验收](../docs/acceptance/M3-T03c-verification.md)，下一步T04模型决策和生成，知识界面仍留M4。

Embedding活动按已提交回执确认：embedding_activity区分confirmed_called、not_called和unknown。取消或失败时没有成功回执不能据此断言模型从未执行；model_called仍单独表示LLM决策。

## M3-T04a 受控模型网关

迁移0018新增模型配置绑定和轮次，当前44张业务表、4检查点表。内部agent_model_service仅为方法任务准备已授权最小上下文，模型轮在网络请求前占用8轮/120秒预算；云端/本地统一校验候选ID和原文摘录。本轮无公开任意prompt/试验接口，规则图不自动调用模型或消费建议。

VIBRADIAG_FORMAL_LLM_PROVIDER显式为deepseek或ollama；默认deepseek，云端还须VIBRADIAG_ALLOW_EXTERNAL_LLM=true及私有key。配置见config/formal-models.json，首次模型轮绑定配置/提示策略hash，既有绑定不因默认配置变更而切换；显式要求换供应商冲突，失败不切本地。T04b再把模型决策接入新图，T05再判定本地工业能力。

模型调用正文仅问题、有限候选ID或最多5个source_id及每块前400字符；不外发CSV、路径、会话、角色或数据库凭据。摘录必须逐字来自给定片段，不代表相关性/专家认可。完成轮可重放，失败/未知不自动修复或重试；取消和恢复会封闭未完成模型轮。任务详情只展示model_rounds的状态/脱敏身份/用量，不回传原始prompt或失败正文。

```bat
.venv\Scripts\python.exe backend\scripts\migrate.py
.venv\Scripts\python.exe -m pytest -c backend\pyproject.toml backend\tests -q --junitxml=experiments\m3-t04a-tests.xml
.venv\Scripts\python.exe -m pytest -c backend\pyproject.toml backend\tests\unit\test_formal_model_gateway.py backend\tests\integration\test_agent_models.py -q --junitxml=experiments\m3-t04a-model-tests.xml
.venv\Scripts\python.exe backend\scripts\verify_formal_models.py
```

真实脚本须等全部pytest结束，使用专用测试库与同一单进程租约，会实际调用DeepSeek API和本机Qwen并记录各自成功/失败；不是生产图自主执行或模型效果排名。详见[09设计](../docs/09-model-runtime-design.md)及[T04a验收](../docs/acceptance/M3-T04a-verification.md)。

## M3-T04b1 可选方法模型图

迁移0019后重启API。VIBRADIAG_METHOD_EXECUTOR默认为rules；显式model后，新explain_method任务使用model_method_v1/model-method-v1，并在创建时固定VIBRADIAG_FORMAL_LLM_PROVIDER对应的配置及策略hash。既有方法/数值任务不升级，用户不能通过请求覆盖执行器。云端外发仍须原有开关和私有key。

模型选择服务器候选后才检索，选择结束则拒答；有有效来源才摘录，结果须逐字匹配给定片段，提交前再验证当前来源/角色。摘录失败保留明确标注的资料上下文，不伪装模型回答；模型失败/未知不自动重试、不切供应商。完成模型轮和工具结果在重启后复用，取消及撤回阻止迟到或过期草稿。

```bat
.venv\Scripts\python.exe backend\scripts\migrate.py
.venv\Scripts\python.exe backend\scripts\export_m3_agent_contract.py
.venv\Scripts\python.exe -m pytest -c backend\pyproject.toml backend\tests -q --junitxml=experiments\m3-t04b1-tests.xml
.venv\Scripts\python.exe -m pytest -c backend\pyproject.toml backend\tests\integration\test_model_method_graph.py -q --junitxml=experiments\m3-t04b1-model-graph-tests.xml
.venv\Scripts\python.exe backend\scripts\verify_model_method_graph.py
```

真实脚本要等全部pytest结束，使用专用测试库、真实DeepSeek与本地Qwen；API与探针交替持有同一租约，不是允许多执行器。数值任务仍保留规则图，开放式回答/数值模型编排/效果准入与界面留后续。详见[T04b1验收](../docs/acceptance/M3-T04b1-verification.md)。

## M3-T04b2 可选数值模型图

VIBRADIAG_NUMERIC_EXECUTOR=rules/model独立控制新数值任务，分发默认rules。model模式使用model_numeric_v1 / model-numeric-v1，受理时固定数值提示策略和模型配置。基础、包络、理想频率、绑定包络、匹配及比较使用既有真实服务；只有条件满足才提供候选，模型不生成参数。最多5工具/6决策，完成回执及检查点重入不重复调用；服务器组装带实际引用的未发布草稿。

真实验证命令为.venv\Scripts\python.exe backend\scripts\verify_model_numeric_graph.py，须等全部pytest结束，使用专用测试数据库及已授权DeepSeek额度。数值任务不代表故障分类或诊断资格，详见[T04b2验收](../docs/acceptance/M3-T04b2-verification.md)。下一步T05对照评测。

## M3-T05a 正式开发对照

benchmark_agent.py仍为M0历史实验。正式对照使用backend/scripts/benchmark_formal_agent.py --allow-paid-model-calls，在专用测试数据库运行规则、一次工作流路由及正式Agent。先完成pytest；命令固定DeepSeek、单次轮换三组，运行前冻结配置/源码，已有公开回执拒绝覆盖。结果记录客观开发预期、来源、开销及token，缺货币价格/账单时不估算金额。单次路由是离线评测适配器，其模型调用单独回执并计入预算，不冒充生产图账本。详见[T05a验收](../docs/acceptance/M3-T05a-verification.md)。

## M3-T05b 反馈型对照及准入审计

backend/scripts/benchmark_agent_feedback.py --allow-paid-model-calls使用新冻结反馈协议，运行条件规则、可选择相同规则的单次路由及原生产Agent。无需生产迁移或更改提示；真实工具输出后决定停止/继续。故障注入只包装自有任务dispatch且退出恢复，托管消费者关闭，不能并发使用。必须先完成pytest。报告区分功能/生命周期与开发分区，独立实体/标注缺失时明确阻断准入。见[T05b验收](../docs/acceptance/M3-T05b-verification.md)。

## M3-T05c1 条件作用域v2

新任务可显式配置VIBRADIAG_NUMERIC_EXECUTOR=model_v2，绑定model_numeric_v2/model-numeric-v2与numeric_decision_v2；model仍表示原v1，默认rules。0021扩展图/阶段/绑定允许值，不新增表。上下文按实际结果/模式分类执行阻断、频率匹配、诊断发布及未知缺项，原缺项保留，合法拒答不强制执行。旧提示hash/配置与检查点不升级。

完成pytest后运行backend/scripts/benchmark_agent_scoped.py --allow-paid-model-calls，只对新Agent跑8条已知开发回归，旧基线不重测。仅验证DeepSeek，本地较长上下文字节限制及质量未准入。详见[T05c1验收](../docs/acceptance/M3-T05c1-verification.md)。


T05c2b已接方法固定语料与双输入比较评测运行器，见[首轮实测及审计](../docs/acceptance/M3-T05c2b-verification.md)。真实模型结果未通过：方法功能2/4、生命周期0/1；规则比较4/4。结构错误被安全拦截不等于有依据的内容拒答，保留原回执，下一步T05c3最小失败诊断及新版本回归。

T05c3a增加可空model_rounds.validation_diagnostics响应字段，需先升级到0023_diagnostics_null_guard，业务表数不变。只保存新schema失败的固定类型及脱敏位置，不含原始正文/错误值，旧记录为null。全量828项通过，真实新观察仍未取得方法质量准入，见[诊断与实测记录](../docs/acceptance/M3-T05c3a-verification.md)；旧提示策略不变，下一步T05c3b新版本字段协议。

T05c3b需迁移到0024_method_model_v2；VIBRADIAG_METHOD_EXECUTOR=model_v2对新方法任务使用独立字段协议，model保留v1、rules仍默认，与数值开关分开。全量850项及新版本真实7条已知开发回归通过，见[实测边界](../docs/acceptance/M3-T05c3b-verification.md)。本地模型新策略未实测，不宣称与DeepSeek效果等价。
