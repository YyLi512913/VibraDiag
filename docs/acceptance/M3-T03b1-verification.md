# M3-T03b1 固定身份的真实Embedding适配

> 日期：2026-10-31；属于T03b的第一步，完整向量索引与检索尚未交付。

## 交付范围

新增独立于原型NPZ检索的[Embedding适配器](../../backend/src/vibradiag/infrastructure/retrieval/embedding.py)及[模型配置](../../backend/config/embedding-bge-m3.json)。使用本机已有BGE-M3 dense模型，固定实际manifest digest、1024维、原文输入策略、float32/L2规范化和cosine身份指纹。模型别名不是版本；编码前后检查实际tag digest，不匹配则拒绝，不自动下载、换模型或沿用旧缓存。

仅允许无凭据的本机回环Ollama HTTP服务，禁用代理环境和重定向；输入每批1—8段、每段最多2000字符，正文直接输入，不加入Qwen查询前缀。truncate=false，CPU/4线程为请求设置；响应流限制2MiB，校验条数、1024维、数值类型、有限值、float32可表示性及非零范数。单进程同时一个请求，忙时429；错误只返回固定错误码和脱敏说明。

本轮不接API、不保存正式索引、不改知识发布响应或Agent动作。代码可供下一步受控索引调用，旧原型检索实现保持独立。

## 验证方法

[专项测试](../../backend/tests/unit/test_formal_embedding.py)覆盖正常身份/原文输入、非法身份、回环限制、无效输入不调用模型、错误向量、编码前后tag变化、错误/重定向/超大响应、错误脱敏、超时/截止时间、容量与失败后名额释放。[数据库集成测试](../../backend/tests/integration/test_vector_readiness.py)使用专用PostgreSQL测试库，以应用账号在只读事务中检查扩展和权限。

[真实模型脚本](../../backend/scripts/verify_formal_embedding.py)读取固定配置，以开发自编MD的两个真实文本块和一条问题调用模型，再重复编码问题；记录真实输出维度、有限值、L2范数、重复向量cosine、耗时及输入/向量hash。另用错误digest实际验证拒绝。调用前后检查Ollama版本/能力/模型身份；原始向量仅放入被忽略的.runtime，公开[回执](../../experiments/m3-t03b1-embedding.json)保存指纹和统计。

脚本对开发/测试库的应用、迁移连接分别执行只读检查，输出PostgreSQL/vector版本及角色能力，不输出连接、账号或密码。不会执行CREATE EXTENSION、DDL、GRANT或更改角色。应用账号继续无DDL能力。

```bat
.venv\Scripts\python.exe -m pytest -c backend\pyproject.toml backend\tests\unit\test_formal_embedding.py backend\tests\integration\test_vector_readiness.py -q --junitxml=experiments\m3-t03b1-embedding-tests.xml
.venv\Scripts\python.exe backend\scripts\verify_formal_embedding.py
node scripts\check-docs.cjs
.venv\Scripts\python.exe backend\scripts\check_secret_hygiene.py
```

测试项数及执行结果以[专项JUnit](../../experiments/m3-t03b1-embedding-tests.xml)、[后端回归JUnit](../../experiments/m3-t03b1-tests.xml)和真实模型回执为准；不复写T03a或更早的冻结实验。

本轮完整后端回归646项通过，0失败、0错误、0跳过，耗时328.80秒；最终专项44项复验通过。普通测试使用模拟模型响应检验契约，真实本地模型调用由独立脚本执行并记录，不混作召回效果样本。

## 结果解释与下一步

本机首次实测：两个正文块加一条问题共3×1024个float32值，均为有限值且L2范数通过校验；编码批次约5.81秒，重复问题约0.25秒。这是单次开发调用的墙钟记录，包含本次模型加载情况，不作为P95或硬件容量承诺；重跑以JSON回执为准。44项专项检查通过。

开发库与测试库均为PostgreSQL18.6，可用vector版本0.8.6，但均未安装。数据库元数据表明该扩展安装要求superuser且不是trusted；现有应用/迁移账号均非超级用户，应用账号无public DDL。这是进入正式向量DDL前须解决的环境条件，不通过提升运行账号权限绕过。

这是模型适配兼容性检查，不是召回率、诊断准确率、模型对比排名或工厂效果验证。重复问题cosine接近1只说明本机此次编码基本一致，不表示问题命中正确知识。

本机vector扩展是否安装、默认版本、安装是否要求superuser/trusted均按数据库实际读取。下一步通过受控数据库维护连接，在独立vibradiag_extensions schema安装扩展并授予必要USAGE，使其与public业务schema重建隔离；再用原迁移账号建立索引身份、不可变向量和构建状态。安装脚本须检查既有扩展位置并支持幂等，不擅自移动既有扩展；不向运行账号赋予扩展安装/DDL权限，也不在API启动时自动安装。随后实现发布/组织/设备/适用范围过滤后的精确cosine检索、构建失败保留旧索引、原子切换和真实撤回/撤权测试，再进入T03c Agent引用。

适配器的HTTP超时和流截止检查不终止Ollama服务端推理；并发限制仅在当前Python进程内，不是跨服务资源隔离。tag前后检查依赖可信本地服务，不能证明服务端权重或CPU实际执行，也不能排除检查之间的短暂标签替换；服务部署权限须另管控。应用层硬取消/索引后台恢复与整体容量待下一步明确。

参考：[Ollama embed接口](https://docs.ollama.com/api/embed)、[BGE-M3官方模型卡](https://huggingface.co/BAAI/bge-m3)、[pgvector官方说明](https://github.com/pgvector/pgvector)。只使用BGE-M3 dense输出；未实现其sparse/multi-vector能力或ANN索引。
