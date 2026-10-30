# 04 首轮详细设计：数据基础与可追溯分析

> 版本：v0.1（M1详细设计草案）
> 日期：2026-10-29
> 上游：[需求v0.9](01-requirements.md)、[总体设计v0.3](02-system-design.md)、[版本计划M1](03-milestones.md)
> 状态：M1-T01—T05及本机限定范围验收已完成，见[M1记录](acceptance/M1-acceptance.md)。本文替代早期SQLite/10MiB草案。
> 目标：在基础授权约束下导入CSV、确认参数、计算并重读有版本与来源的分析结果。

## 1. 范围与退出条件

本阶段实现：PostgreSQL基础、最小账号/范围授权、CSV及不可变文件、采集参数版本、导入预览/确认、基础分析、结果读取、基础审计与前后端链路。

不实现：包络/轴承故障定类、知识审核与pgvector索引、完整Agent持久恢复、报告审批、工单/复测、大屏或通用资产管理。DeepSeek及现有实验工具保留为明确标识的开发功能，不以原型输出替代本阶段分析事实。

退出门槛为M1-AT01—M1-AT05；已按实际执行形成[M1验收记录](acceptance/M1-acceptance.md)，不代表M2—M6通过。

## 2. 业务流程

1. 登录，服务端确定组织、角色与访问范围。
2. 上传单列CSV，填写采样率/单位，可选授权设备/测点及本次工况。
3. 校验并生成不可变文件与采集revision，展示样本数、单位、时长、有限波形预览和质量提示。
4. 用户核对当前revision并确认输入；确认仅表示采用这些参数，不代表传感器校准、现场真实性或工程报告复核。
5. 发起基础分析，服务端再校验权限、revision/确认关系、文件完整性与容量。
6. 全量计算指标和幅度谱，登记产物与配置，返回分析ID及概要。
7. 刷新/重启后可读；改参数创建新revision，不覆盖旧结果。

上传与计算拆为两个接口。M1基础计算先采用有界同步执行：先落running记录并释放数据库事务，计算后再提交结果；不将完整Agent诊断放在同步HTTP请求中。

## 3. 输入、参数与确认契约

### 3.1 CSV与资源限制

| 项目 | 规则 |
| --- | --- |
| 文件 | CSV，UTF-8，可带BOM；保留原始字节 |
| 表头 | 严格为唯一一列 `value`；不猜列、不跳过前10行 |
| 数据行 | 一个十进制/科学计数法有限数值；允许两侧空格/制表符及普通CSV引号；拒绝内部空行、多列和字段换行 |
| 数字词法 | ASCII数字、可选正负号/小数点/指数；拒绝NaN/Inf、十六进制、日期、千分位/下划线；字段最多64字符 |
| 文件大小 | ≤32MiB，上传流读取即限额 |
| 点数 | 1024—1000000，超限拒绝而非截断 |
| 结尾 | 允许文件结束换行，其前的内部空白记录仍为错误 |
| 文件名 | 仅作展示元数据，实际路径由服务端生成 |

顺序：认证/限流 → 大小/编码 → CSV结构/词法 → 有限数值与点数 → 元数据 → 可保存预览。失败不产生ready记录；可留不含完整原数据的失败审计摘要。

### 3.2 元数据

| 字段 | 类型/必填 | 约束 |
| --- | --- | --- |
| sampling_rate_hz | float64，是 | 有限正数，无默认；时间轴/计算无法表示时拒绝 |
| acceleration_unit | enum，是 | g或m/s2，展示m/s²；换算系数9.80665版本化 |
| asset_id、measurement_point_id | UUID，否 | 有权且有效的同组织对象；测点属于所选设备 |
| direction | enum，否 | horizontal_radial / vertical_radial / axial / other / unknown；other需说明 |
| rpm | float64，否 | 本次采集有限正数，不默认用额定值 |
| load_value、load_unit或load_note | 可空 | 数值与单位成对；工艺描述另存；缺失不当0 |
| acquired_at | 带时区时间，否 | 不以上传时间替代，不静默猜测时区 |
| source_type | enum，是 | public_dataset / synthetic / field_upload / unknown；来源声明不是系统证实 |
| source_note | 有界文本，否 | 来源说明/公开记录定位，不自动请求外链 |

字段来源保存 `{origin, raw_reference, confirmed_by, confirmed_at}`；origin区分用户填写、可信元数据、台账参考和未知。核对不等于专业校准，来源不明默认unknown。

### 3.3 修订与并发

原文件不可变；单位、采样率、工况或关联变化创建新revision，保留parent和原因。修订须提供预期current_revision_id/row_version，服务端锁定采集记录后原子插入并切换current指针；并发旧版本请求返回409。

输入确认另存并绑定revision/操作者，新revision默认未确认。M1新分析只接受当前revision的有效确认；旧版可读历史分析，不用旧确认误启动新分析。将来显式重算历史版本时另定义操作。

## 4. PostgreSQL与首批迁移

### 4.1 技术和账号

PostgreSQL17，持久层采用SQLAlchemy2.x、psycopg3、Alembic方向；实施前锁定实际兼容版本。应用非超级用户，迁移用独立受控账号，凭据仅注入后端环境或backend/.env。

实施补充：本机已有PostgreSQL18.6，M1-T02复用独立数据库验证18；17参考环境尚未实测。实际锁定SQLAlchemy2.1.6、psycopg3.3.6、Alembic1.20.1、argon2-cffi25.1.0；采用SQLAlchemy Core及显式SQL迁移。0001—0004之后另增0005保护完成产物历史，不修改已执行的迁移。

ID用UUID、时间用timestamptz、内容hash用SHA256。JSONB仅存有明确schema的来源、配置与结果，不将整个业务塞进无约束JSON。M1不建向量表；pgvector仍在M3落实，不引入Milvus。

### 4.2 迁移分组

| 迁移 | 表 |
| --- | --- |
| 0001_identity_audit | organizations、users、role_assignments、auth_sessions、audit_events |
| 0002_assets_files | assets、measurement_points、asset_grants、file_objects |
| 0003_measurement_versions | measurements、measurement_grants、measurement_revisions、input_confirmations |
| 0004_analysis | analysis_runs、analysis_artifacts、idempotency_records |

共16张基础表，不对应16套管理页面。组织/用户/初始授权先用CLI维护，前端只做登录、必要选择、导入和分析。部件/轴承参数表留M2。

### 4.3 字段与约束

| 表 | 核心字段 | 约束 |
| --- | --- | --- |
| organizations | id、name、status、created_at | 单部署初始化一个组织 |
| users | id、organization_id、username_normalized、password_hash、status、row_version | 组织内用户名唯一，无明文密码 |
| role_assignments | user_id、organization_id、role、granted_by、created_at | 同用户/角色唯一，role限四类既定角色 |
| auth_sessions | id、user_id、organization_id、token_hash、csrf_hash、expires_at、revoked_at | 只存凭据摘要 |
| assets | id、organization_id、asset_code、name、equipment_type、rated_rpm、status、row_version | 组织内asset_code唯一，额定与本次转速分开 |
| measurement_points | id、organization_id、asset_id、point_code、name、status、row_version | 同设备point_code唯一，引用后只停用 |
| asset_grants | organization_id、user_id、asset_id、can_read、can_write | 用户/设备唯一，写权不超读范围 |
| file_objects | id、organization_id、storage_key、sha256、byte_size、state、created_by、created_at | 存储键唯一，state为ready/corrupt/missing/archived |
| measurements | id、organization_id、file_object_id、owner_id、original_filename、current_revision_id、state、row_version、uploaded_at | current指针须指向本记录revision |
| measurement_grants | organization_id、measurement_id、user_id、can_read、can_write、granted_by | 记录/用户唯一，关联设备不自动扩权 |
| measurement_revisions | id、organization_id、measurement_id、revision_no、parent_id、参数与关联、source_type、parameter_sources、reason、created_by、created_at | 记录内版本号唯一，内容不可变，未知不写伪0 |
| input_confirmations | id、organization_id、revision_id、actor_id、created_at、revoked_at | 同revision/actor唯一，不冒充专业复核 |
| analysis_runs | id、organization_id、measurement_revision_id、confirmation_id、requested_by、status、algorithm_version、config_hash、config_snapshot、started_at、finished_at、metrics、warnings、error_code | 确认与revision一致，状态/结果受约束 |
| analysis_artifacts | id、organization_id、analysis_id、kind、storage_key、sha256、byte_size、format、manifest、state | 存储键唯一，大数组不入图状态 |
| idempotency_records | organization_id、actor_id、operation、key_hash、request_hash、resource_id、state、response_summary、created_at | 同作用域/键唯一，同键不同内容409 |
| audit_events | id、organization_id、actor_id、action、object_type、object_id、object_version、request_id、timestamp、details | 应用只追加，不存密码、密钥、Cookie或确认原文 |

被引用对象除主键外提供 `(id, organization_id)` 唯一约束，外键同时校验组织；用户、设备、测点、文件和版本不得跨组织。

- 测点须属于所选asset，current_revision/parent_revision须属于同一measurement，确认须绑定分析的revision；用复合外键/约束落实，不只依赖前端。
- 采样率/rpm在数据库与应用两侧校验正值和有限范围，排除NaN/Infinity；不误用仅针对日期时间的isfinite检查float。
- 已引用文件、revision、确认与分析禁止级联删除，物理清理不在M1常规入口开放。
- completed须有结束时间、合法结果及ready产物；failed/interrupted须有error_code，running没有伪完成时间。
- 状态、current指针、范围变更用行锁/row_version；参数和结果内容新建版本，审计与对应业务变化同事务提交。

## 5. 文件与数据库一致性

### 5.1 存储布局

```text
<configured_data_root>/
  staging/<upload_uuid>.part
  originals/<organization_uuid>/<file_uuid>.csv
  analyses/<organization_uuid>/<analysis_uuid>/
    result.json
    waveform-preview.json
    amplitude-spectrum.npz
    manifest.json
```

数据根不在前端public/dist下。NPZ只含数值、关闭pickle；JSON禁止NaN/Inf。manifest记录输入hash/revision、算法/配置及各产物hash/大小。

### 5.2 上传与分析提交

上传：暂存/限额/hash → 内容校验 → 生成受控原始路径并原子落盘 → 数据库事务建立file/measurement/revision和审计 → 提交后可读。

分析：落running记录并提交 → 释放事务，核对原文件hash并计算 → 写齐/验证产物 → 事务登记artifact、completed、审计及幂等结果。

文件与数据库不是同一事务。落盘但事务失败的文件是无引用孤立物，不视为有效记录；事务提交但响应丢失则按幂等键取回原对象。失败run有明确状态，部分产物不冒充completed。

M1不做跨用户物理去重，避免存在性泄漏和权限耦合。相同字节允许独立受控file对象；将来去重也不共享业务访问权。

新分析核对原文件；读取结果/下载核对相应来源、manifest与产物，缺失/hash不符时标明不可验证并停止作为合法分析展示。验收计时包含必要IO，不能跳过完整性校验达标。

### 5.3 中断和清理

M1只允许单API进程、并发2个基础计算，无持久队列；满时429。正式多进程执行与持久队列留M3，原型模型功能不参与M1正式容量验收。

重启将遗留running标interrupted并审计，显式重试生成新analysis ID。保证完成结果可重读，不声称计算自动续跑。

对账命令先只报告孤立/缺失/损坏项。清理默认dry-run，只能显式处理配置根内、无引用且达到保留阈值的暂存物，不自动删除历史原文件。

## 6. 最小身份与资源授权

组织/账号/角色由受控CLI初始化，不公开注册、不预置默认密码；密码交互输入或运行密钥注入，不放命令行参数/日志。采用Argon2散列方向，具体库和参数实施时锁定，不自创算法。

会话用高熵随机凭据，仅存摘要，默认8小时，可注销/撤销；每请求检查用户/会话状态。Cookie采用HttpOnly、SameSite与CSRF机制，共享HTTPS必须Secure，仅本机HTTP开发模式允许显式关闭Secure。

管理员不自动拥有业务读取/工程审批权。角色、设备范围撤销后新请求立即生效，后台配置快照不能绕过当前权限。

未关联文件：同组织活跃账号且为上传者或有显式measurement读授权；写入/修订另需角色及写权。关联设备后还须具备asset范围：设备读权不自动开放所有文件，原文件拥有者也不能绕过撤销的设备范围。

点检员可导入/确认自己的用户输入并在授权范围发起基础分析；工程师可维护必要台账、核对关联及修订授权数据。输入确认不授予报告复核/工单权。

列表、文件、图表、分析、输入确认和发起计算复用同一资源链校验。UUID不是凭据；无权对象采用一致404策略避免存在性泄漏。必须测“换ID也无法越权”，不能只隐藏按钮。

## 7. 基础计算与数值验收

### 7.1 定义

设转换为m/s²后的float64信号为x，共N点，均值μ，周期Hann窗 `w[n]=0.5−0.5cos(2πn/N)`。

| 输出 | 定义 |
| --- | --- |
| mean | mean(x) |
| rms | sqrt(mean(x²))，含DC |
| ac_rms | sqrt(mean((x−μ)²)) |
| absolute_peak | max(abs(x)) |
| peak_to_peak | max(x)−min(x) |
| crest_factor | absolute_peak/rms，零rms时null及原因 |
| pearson_kurtosis | mean((x−μ)^4)/mean((x−μ)^2)^2；不减3、不做无偏修正；零方差null |
| frequency_hz[k] | k·fs/N |
| amplitude[k] | abs(rFFT((x−μ)·w))[k]/sum(w)，内部正频率bin翻倍，DC及偶数N的Nyquist不翻倍 |

原型peak字段迁为清晰的absolute_peak；兼容别名须标记，不能当作两个指标。指标输出value、unit及不可用原因，不用NaN或0代替不可计算项。

处理整个文件，无插值、重采样、限幅或冲击删除。时长N/fs，最后样本时刻(N−1)/fs，bin间隔fs/N不等于实际可分辨能力。

### 7.2 稳定性与退化情况

采用缩放/稳定矩计算降低平方和四次幂溢出/下溢，不能将中间量溢出变成假0或Inf。换算或派生量仍超float64范围时，明确NUMERIC_RANGE_UNSUPPORTED或将对应指标标不可用，不记设备故障。

零信号的均值/RMS/峰值为0，峰值因子/峭度null；恒定非零的原始RMS/峰值可用、AC RMS为0、峭度null。仅提示动态信息不足，不诊断健康/传感器故障。

极近零尺度不按固定工业阈值删信号；浮点分辨率不足时说明限制。M1不生成轴承频率、包络、故障定类或烈度分级，数值验收不依赖模型。

### 7.3 固定容差

用于尺度受控的合成测试，不是传感器精度或任意离格频率估计精度：

| 类别 | 判定 |
| --- | --- |
| 均值/RMS/AC RMS/峰值/峰峰值 | abs(actual−expected) ≤ 1e-10＋1e-9·abs(expected)，单位m/s² |
| 峰值因子/Pearson峭度 | ≤1e-9＋1e-8·abs(expected) |
| bin中心已知频率 | bin索引正确，所列测试下频率绝对误差≤1e-9 Hz |
| bin中心正弦峰值幅度 | ≤1e-10＋1e-8·abs(expected)，单位m/s² |
| 同输入/版本重复 | 按对应项容差一致，不要求跨硬件二进制逐位相同 |

离格正弦检查Hann泄漏与定位限制，不强求最大bin幅值等于连续频率真实幅值，不用补零宣称增加采集信息。

### 7.4 展示和产物

默认预览不超过2000点，分桶保留局部极值并标抽样；完整数据/频谱保留，预览不改变计算段。缩放/频率筛选只改变展示。

大数组不放列表响应，另行授权读取/导出；显示单位、处理配置、片段和参数版本。界面明确“基础分析完成，未进行故障诊断”。

## 8. HTTP接口与错误契约

统一前缀 `/api/v1`；组织和操作者从会话解析，客户端不能指定任意组织。请求与审计关联request_id。以下为目标接口，现有原型尚未全部支持。

| 方法与路径 | 输入与行为 |
| --- | --- |
| POST /auth/login | 组织标识、用户名、密码；建立会话并返回CSRF令牌；失败统一提示 |
| POST /auth/logout；GET /auth/me | 撤销当前会话；返回当前身份、角色与可用操作 |
| GET /assets；GET /assets/{id}/measurement-points | 仅返回可访问范围，分页且限制page_size |
| POST /measurements | multipart的file与metadata JSON；校验、保存、返回201及初始revision/预览 |
| GET /measurements；GET /measurements/{id} | 授权列表/详情；概要不携带完整数组 |
| GET /measurements/{id}/revisions | 不可变参数历史及当前指针 |
| POST /measurements/{id}/revisions | 完整参数、reason、expected_current_revision_id及row_version；并发冲突409 |
| POST /measurements/{id}/confirmations | revision_id；仅当前版本，记录本人确认；重复确认返回已有记录 |
| POST /analyses | measurement_revision_id、confirmation_id、config_profile=basic-v1；要求Idempotency-Key |
| GET /analyses/{id} | 状态、版本、指标、警告与产物链接；区分当前参数与旧版本结果 |
| GET /analyses/{id}/series | 授权的有限预览，指定允许的series类型；返回抽样说明 |
| GET /analyses/{id}/artifacts/{kind} | 白名单产物类型，禁止路径参数直接映射磁盘 |
| GET /measurements/{id}/original | 授权下载原CSV；安全Content-Disposition及审计 |

创建分析时原子检查当前revision及确认、登记幂等键和running资源，然后计算。计算期间用户修改参数不会重写运行快照；该结果仍指向原revision，界面提示已有新参数。接受之后发生权限撤销，最终提交及后续读取再次检查；撤权不得借产物地址绕过。

首次完成返回201；同键同请求已完成返回200和相同analysis_id；仍运行返回202及Location；已失败/中断返回200及终态，不暗中重跑。相同键但请求摘要不同返回409。显式重试使用新键、新analysis_id，并关联retry_of。幂等键按组织、操作者、操作域隔离；数据库唯一约束保证并发只产生一个运行。键最长128字符，存摘要，M1保留到关联运行删除策略正式实施，不先做自动过期。

指标结构示例：`{"rms":{"value":1.0,"unit":"m/s2","status":"available","reason":null}}`。不可用项value为null、status为unavailable且给原因；结果包含算法版本、配置摘要、原文件SHA256、revision_id、处理时间及warnings。严格JSON，不发送NaN/Infinity。

错误结构为 `{"error":{"code":"CSV_INVALID_VALUE","message":"第12行不是有限数值","field":"value","row":12},"request_id":"..."}`。不回显文件正文、密码、堆栈或磁盘路径。未登录401；已知操作无角色403；不可见资源统一404；版本/幂等冲突409；超文件限额413；参数/CSV校验422；容量满429并带Retry-After；服务/存储不可用503；内部异常500。失败不能生成completed结果。

## 9. 代码职责与原型迁移

沿用backend/src/vibradiag和frontend/src，不另建平行系统。路由负责HTTP解析/会话，service负责授权、版本和事务编排，analysis仅接收已验证数值及参数；LLM不参与输入确认、指标计算和权限判定。

| 位置 | M1改动 |
| --- | --- |
| core/config.py、paths.py | 数据库连接、存储根、限制、会话及开发模式；启动校验 |
| schemas/ | 拆分采集、版本、确认、分析、身份及错误契约 |
| infrastructure/persistence/ | SQLAlchemy模型、仓储、事务及Alembic迁移；文件存储独立适配器 |
| services/ | 新增采集/身份服务；分析服务编排版本快照、幂等、产物和审计 |
| analysis/signal.py | 稳定指标、规范FFT和预览；纯函数可独立验收 |
| api/v1/ | 新采集/身份路由及拆分后的分析接口，统一权限依赖 |
| frontend/src/ | 登录、上传预览、确认、版本切换、分析状态和结果；更新类型/API适配 |
| backend/scripts/ | 组织/用户/资产初始化、旧数据迁移与完整性核查CLI |

现有JSON/NPZ保留原地，不在启动时自动导入。迁移CLI先dry-run清单，再在指定组织/操作者下显式导入：校验原文件和参数，记录legacy来源及原ID；缺少可靠采样率/单位的记录只列问题，不补造默认值。旧结果标legacy，未经新算法验收不当作M1正式结果；重算须新确认和新运行。记录迁移批次、映射和失败项，重复导入同批次幂等。备份旧目录，核对后再切换；不删除原文件。

现有上传即计算接口不继续作为正式入口；前后端同一版本切换，文档注明开发原型API破坏性变更。开发实验Agent只接受明确允许的实验数据路径，不能绕过正式数据授权读取；共享部署默认关闭实验入口。M1结果暂不直接充当已完成M3的Agent业务输入。

## 10. 验收用例与证据

每组对应03中的阶段验收编号。实现时记录命令、环境、实际输出、失败原因及产物；本表是计划，不是通过记录。

| 编号 | 可执行检查与判定 |
| --- | --- |
| M1-AT01 | 1024点、fs=1024Hz、x=2sin(2π64n/1024)：均值0、RMS与AC RMS为√2、峰值2、峰峰值4、峰值因子√2、Pearson峭度1.5、64Hz幅值2；按7.3容差 |
| M1-AT01 | 上述信号加DC=3：均值3、原RMS√11、AC RMS√2、绝对峰值5、峰峰值4；谱去DC后64Hz幅值2；g输入按9.80665换算后核对 |
| M1-AT01 | 零、常数、负常数、奇偶长度、Nyquist余弦、离格正弦、极小/大尺度；与独立公式/高精度参考比较，验证零方差及数值范围行为 |
| M1-AT02 | BOM、引号、科学计数法正确导入；坏编码、错表头、多列、空行、NaN/Inf、超长数值、非有限采样率、缺单位拒绝且不留ready记录 |
| M1-AT02 | 1023/1024/1000000/1000001点与32MiB两侧边界；超限流式中止；CSV数值行错误正确定位；文件名/路径注入无效 |
| M1-AT03 | 修改参数产生新revision；旧确认不能用于新版本；并发更新仅一方成功；旧结果及快照不变；文件/产物hash损坏显式失败 |
| M1-AT03 | API/数据库重启后可读；在文件落盘、DB提交和计算阶段注入失败；running恢复为interrupted，无completed孤儿；重复请求/重试按第8节 |
| M1-AT04 | 同组织两用户及跨组织用户，逐个验证列表、详情、原文件、图、产物、确认、计算；撤权即时生效；admin无工程角色不能工程确认 |
| M1-AT04 | 未登录、过期/撤销会话、CSRF缺失、猜ID、关联未授权资产、跨组织外键、并发幂等；敏感信息不进响应或日志 |
| M1-AT05 | 浏览器登录→上传→预览→确认→分析→刷新→修订→重算→读旧结果；错误提示可操作且不误称故障诊断；旧数据dry-run/导入/重复导入可追踪 |
| M1-AT05 | 声明CPU/RAM/OS/版本后，记录100次固定输入的成功率、延迟P50/P95及峰值内存；含1m点输入、2并发与超容量429；先记录基线，不预填达标 |

数值参考不能复用被测实现的FFT归一化/矩计算函数。安全集成测试使用真实PostgreSQL事务/约束，不能仅用SQLite或mock证明。文件失败测试使用临时存储根，不触及开发原始数据。性能结果承接需求中的最终指标，M1只建立可重复基线；不将单机结果外推现场效果。

## 11. 实施顺序与依赖

1. M1-T02：配置与依赖锁定；本地PostgreSQL开发配置、四批迁移、仓储/事务；CLI初始化和会话/授权/审计骨架，先通过真实数据库约束及两用户测试。
2. M1-T03：文件存储、CSV校验、采集revision及确认接口；完成边界、并发、hash和失败清理检查。
3. M1-T04：稳定数值工具、分析快照/幂等、产物与重启中断处理；通过AT01和对应AT03。
4. M1-T05：前端流程、原型数据迁移CLI、联调；运行全部M1用例并记录性能基线。
5. 验收：建立M1实际记录，修复阻断项，更新03状态后才进入M2。未通过项保留，不能以文档完成替代软件验收。

SQLAlchemy、psycopg、Alembic和Argon2依赖已于M1-T02固定并更新锁文件，开发/测试库已执行迁移。开发用数据库与旧原型目录隔离；.env.example只含占位配置，凭据只在私有环境中。共享试用部署、备份恢复及全套安全验收仍属于M6，不由开发启动脚本代替。

## 12. 当前交付与版本记录

已交付M1-T02—T05：16张基础表、身份/范围、CSV/参数确认、基础指标/幅度谱、快照/产物、幂等与中断标记、正式前端及迁移/基线。0008另增legacy_imports账本，共17张业务表。135项测试、浏览器、迁移/重建与性能基线已形成[M1本机验收](acceptance/M1-acceptance.md)。历史[T02](acceptance/M1-T02-verification.md)、[T03](acceptance/M1-T03-verification.md)、[T04](acceptance/M1-T04-verification.md)保留。下一步M2-T01。

| 日期 | 版本 | 变更 |
| --- | --- | --- |
| 2026-10-29 | v0.1 | 按当前需求与M1计划重写；旧稿保存在[历史首轮草案](appendices/first-iteration-early-draft.md) |
| 2026-10-29 | v0.1实施补充 | 记录M1-T02真实数据库版本、依赖与子任务验证；未提高整阶段完成状态 |
| 2026-10-29 | v0.1实施补充 | M1-T03后端数据链路完成，100项测试及实际HTTP/重启验证；完整M1仍待验收 |
| 2026-10-29 | v0.1实施补充 | M1-T04正式基础分析完成；129项测试、前端构建及实际终止/重试验证；完整M1仍待验收 |
| 2026-10-29 | v0.1阶段验收 | M1-T05前端/迁移/基线完成，135项测试及本机M1限定范围验收通过；M2—M6尚未通过 |
