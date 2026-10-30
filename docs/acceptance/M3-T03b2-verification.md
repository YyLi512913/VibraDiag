# M3-T03b2 版本化向量索引与授权精确检索

> 日期：2026-10-31；接续[T03b1](M3-T03b1-verification.md)。Agent引用留T03c。

## 交付

- 显式[维护脚本](../../backend/scripts/setup_vector_extension.py)在开发/测试库的独立vibradiag_extensions schema安装vector 0.8.6；重复执行复用原扩展，已有位置/版本不符则拒绝，不自动迁移或升级。维护连接与应用/迁移角色分离，不由API或Alembic安装扩展；应用/迁移角色仅有扩展schema的USAGE、没有CREATE。维护URL仅从进程环境读取，不写入项目配置、日志或回执。
- 0016新增knowledge_index_runs、knowledge_chunk_vectors、knowledge_active_indexes、knowledge_retrieval_runs四表。当前42张业务表及4张检查点表；扩展schema独立于业务空schema重建与回滚。
- 固定BGE-M3实际digest、1024维、原文输入/float32-L2/cosine策略及实际Ollama版本。索引代保存源/文本/块清单/向量hash；来源、向量及已结束索引代不可改写，数据库检查向量维度、范数、块/版本/组织绑定及完整块数。
- 已发布版本才能由具备显式审核能力的工程师建索引；双文本CAS加索引头CAS、幂等键。构建先登记processing，外部推理期间不占数据库连接；完成时重新验证身份、授权、发布、来源及CAS，再在单事务中写入全部向量、ready和活动头。失败保留原索引，记录failed与固定错误码；相同键不重新执行。
- 精确cosine检索先通过组织、设备权限、当前发布、设备/部件适用范围、活动索引身份及运行版本过滤，再由SQL MATERIALIZED候选集计算距离；仅在已授权候选中按文本hash去重。编码/排名后重查权限和发布，再返回原文锚点、来源元数据、索引代及相似度。
- 开发内容默认排除，include_developer=true仅允许当前有审核能力的工程师，返回developer_verification模式。普通读者只得到范围内已发布且已索引的内容。原文和历史块独立于模型在线情况；新发布版未建索引时不回退到已撤回旧版。撤回保留来源及旧检索账本，但不再参与新检索。

## 接口与运行

路径独立于原型，均为/api/v1/knowledge-indexes：

| 方法/路径 | 约束 |
| --- | --- |
| POST /revisions/{id}/build | Cookie、CSRF、Idempotency-Key；expected_version、expected_document_version、expected_index_version（首次0）、reason |
| GET /revisions/{id} | 当前授权版本的最近20次构建及活动头，不回传向量 |
| GET /runs/{id} | 当前原文读取权限；运行状态、来源/模型指纹、数量、错误码及活动头 |
| POST /search | Cookie、CSRF；query、可选asset_id、equipment、component、limit（1—5）、include_developer；每次查询独立记账，不是业务写命令，无幂等键 |
| GET /retrievals/{id} | 仅本组织原发起人；历史查询/过滤/命中ID/分数账本，无正文；不是新检索，使用来源仍须通过当前授权接口解析 |

新POST JSON在解析前限制16KiB，包括无Content-Length的流。单API继续要求既有租约；本阶段同步构建一次最多128块、每批8块、推理/提交阶段预算120秒、每进程一个构建及一个正式Embedding请求。查询编码阶段总预算60秒；最多10000个已授权候选，超过则明确拒绝，不截断伪作完整top-k。查询事务设置5秒statement_timeout和2秒lock_timeout；这不是整个HTTP或Ollama服务端的硬实时保证。

已过期的processing构建由同版本下一次显式新键重试标记interrupted/failed，旧键仅返回原账本。不存在自动后台索引消费或自动模型重试。新草稿不影响原发布索引；新发布替换后，旧版马上退出新查询，即使新索引尚未完成。

原文/块响应的vector_index表示存储索引状态，latest_index_status可单独显示失败重建；formal_retrieval_available不表示现场来源真实性、相关性已评测或模型实时健康。运行版本改变时查询不混用旧索引，需重新构建；配置损坏不应阻断历史原文读取。所有相似度都不是故障概率，relevance_qualified=false。

维护部署时先由维护人员在进程环境设置VIBRADIAG_VECTOR_ADMIN_URL，针对目标库运行setup_vector_extension.py，移除该临时环境变量，再按普通迁移账号运行migrate.py；测试库同样先安装扩展。维护脚本要求三个连接指向同一数据库；不提供网页输入维护凭据的入口。实际本机两个库均已安装并重复检查。

```bat
.venv\Scripts\python.exe backend\scripts\setup_vector_extension.py
.venv\Scripts\python.exe backend\scripts\migrate.py
.venv\Scripts\python.exe -m pytest -c backend\pyproject.toml backend\tests -q --junitxml=experiments\m3-t03b2-tests.xml
.venv\Scripts\python.exe -m pytest -c backend\pyproject.toml backend\tests\integration\test_knowledge_indexes.py -q --junitxml=experiments\m3-t03b2-index-tests.xml
.venv\Scripts\python.exe backend\scripts\verify_knowledge_retrieval.py
```

## 验证

[索引专项](../../backend/tests/integration/test_knowledge_indexes.py)使用真实PostgreSQL/pgvector与模拟模型输出检验应用约束：发布/审核门槛、向量维度与精确排序、开发排除、普通读者、账本归属、授权内去重、失败/幂等/CAS/并发、构建中撤回/撤权、查询中撤回/设备撤权、入库失败全事务回滚、不可变历史、候选/块/JSON上限、运行版本变更、不完整任务新键恢复、CSRF和失败审计。模拟模型不能作为真实模型效果证据。

[真实HTTP脚本](../../backend/scripts/verify_knowledge_retrieval.py)在测试库自建回环API，实际上传/显式审核/发布开发自编MD，调用真实本地BGE-M3建库与查询；独立NumPy复算数据库保存向量的cosine，核对返回分数。随后在自建API配置不可达本机模型地址，验证真实连接失败不破坏原索引、搜索明确失败；重启恢复后核对旧索引、原查询账本、新索引原子切换、新发布版待索引、撤回排除及原文历史。只终止脚本自己持有句柄的API进程。

结果及源码指纹见[双库维护幂等与权限回执](../../experiments/m3-t03b2-extension-setup.json)、[HTTP回执](../../experiments/m3-t03b2-retrieval.json)、[全量JUnit](../../experiments/m3-t03b2-tests.xml)与[专项JUnit](../../experiments/m3-t03b2-index-tests.xml)。原T03a/T03b1回执保留为当时快照，不改写历史已测状态。

完整后端回归665项通过，0失败、0错误、0跳过，耗时422.62秒；最终索引19项另行复验，并复验原文、Embedding、数据库迁移/权限、CSV入口及流式JSON边界，见[边界回归](../../experiments/m3-t03b2-boundary-tests.xml)。独立HTTP脚本在pytest结束后执行，避免与单API数据库租约竞争。

## 边界与下一步

本轮证明软件存储、权限和调用链路；语料为开发自编集成测试内容，其中正常检索案例声明internal_note以验证普通读取，并不代表独立专家/厂家认可或真实现场知识。当前没有召回留出集、相关性阈值准入、ANN/混合检索/reranker、Agent引用、知识前端、后台构建恢复或工厂试用验收。

下一步T03c将受控检索与真实块引用接入Agent：任务预算、输入适用范围、当前授权/发布重查、引用来源校验和非可信文本边界仍需落实。DeepSeek动作决策留T04；不能用本次top-k、HTTP成功或应用内自审代替诊断正确率或RAG效果。

依据：[pgvector官方说明](https://github.com/pgvector/pgvector)、[PostgreSQL扩展权限说明](https://www.postgresql.org/docs/current/sql-createextension.html)。项目锁定本机已验证0.8.6，不随网站版本自动升级。
