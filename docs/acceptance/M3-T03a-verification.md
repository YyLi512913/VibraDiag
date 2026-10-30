# M3-T03a 知识原文、版本与发布基础

> 日期：2026-10-30；设计：[08 v0.1](../08-knowledge-design.md)。
> 本轮仅文本发布/授权基础；未接Embedding、pgvector索引、语义排序、正式Agent知识引用或知识前端。

## 交付

- 0015迁移新增文档、版本、块、显式审核授权、幂等命令和事件6表；当前38张业务表，另有4张检查点表。
- UTF-8 MD/TXT独立导入，5MiB/20万行/4096块限制；原字节保留BOM/CRLF，规范化文本1800字符块及稳定UUID/标题/行/字符/hash锚点。
- 文档current与published指针分离；草稿→待审核→发布→撤回，支持退回及新版本替换；新草稿或无效修订上传不破坏原发布版。
- 工程师没有隐含发布权；当前管理员密码复核后显式授予审核能力，审核者仍须工程师角色及设备范围；管理员无隐含正文读取权。
- 写命令CAS/幂等/审计同事务，来源及块不可改写；撤回保留历史引用，权限/授权撤销即时重查。MD/HTML/外链只作文本，不渲染、执行或下载。

## 专项检查

[原文分块单元测试](../../backend/tests/unit/test_knowledge_text.py)10项：BOM/CRLF、标题/代码围栏、Unicode和长行定位、格式/编码/二进制/大小/行数/块数拒绝。

[知识API集成测试](../../backend/tests/integration/test_knowledge_documents.py)15项，使用独立PostgreSQL测试库及每例隔离组织：

| 场景 | 结果要求 |
| --- | --- |
| 导入/下载/块解析 | 原字节与sha一致，位置可回到规范化原文，下载attachment/text/plain/nosniff |
| 草稿/待审核 | 他人未授权404，提交后需当前审核授权才可读 |
| 发布/撤回 | 原发布可读，新草稿不泄漏；撤回不进新候选，旧块仍有撤回标记和原文 |
| 审核能力 | 工程师不能自授，管理员密码错误403；非工程师/跨组织目标拒绝，撤销后不能继续审核 |
| 重放/并发 | 原键返回同版本；不同内容冲突；并发提交/发布仅一次成功，过期版本409 |
| 新旧版本 | 新发布替换旧发布并保留历史；无效修订不改原发布；错误文档指针被数据库拒绝 |
| 范围/内容 | 组织、设备、角色、设备/部件适用过滤；开发内容默认排除，SQL权限过滤先于分页 |
| 非可信输入 | CSRF/未知覆盖字段/坏编码/超限拒绝；恶意正文原样保存且不会发HTTP请求 |
| 密码与审计 | 授权重放不重启已撤销能力，密码不入事件/命令载荷 |

完整后端602项通过，0失败、0跳过；最终知识专项25项复验及实际HTTP另行记录，统计见[全量JUnit](../../experiments/m3-t03a-tests.xml)及[知识专项JUnit](../../experiments/m3-t03a-knowledge-tests.xml)。空schema重建覆盖39张主schema表（含Alembic）及4张检查点表，回滚后两份schema均消失。

## 真实HTTP

[verify_knowledge_publication.py](../../backend/scripts/verify_knowledge_publication.py)创建带随机身份的合成组织，在专用测试库启动自建回环API：

1. MD原文/BOM/CRLF、幂等重放、草稿私有、提交后无审核权403。
2. 管理员密码复核授予审核权，另一工程账号发布；管理员本人仍不能直接读正文。
3. 读取已发布原字节和块，新草稿不替换旧版；第二版发布后旧块仍稳定解析为撤回历史。
4. 停止并重启自建API后重读来源；撤回后活动发布列表/内部发布候选为空，旧引用保留。
5. 撤销审核能力后，原登录会话不能继续读取待审核正文或发布。

实际回执：[publication.json](../../experiments/m3-t03a-publication.json)。记录来源hash、实际重启、权限行为和源码字节指纹；语料是开发自编测试文本，不是标准/厂家手册或现场专家审核证据。

```bat
.venv\Scripts\python.exe -m pytest -c backend\pyproject.toml backend\tests -q --junitxml=experiments\m3-t03a-tests.xml
.venv\Scripts\python.exe -m pytest -c backend\pyproject.toml backend\tests\unit\test_knowledge_text.py backend\tests\integration\test_knowledge_documents.py -q --junitxml=experiments\m3-t03a-knowledge-tests.xml
.venv\Scripts\python.exe backend\scripts\verify_knowledge_publication.py
node scripts\check-docs.cjs
.venv\Scripts\python.exe backend\scripts\check_secret_hygiene.py
```

## 发现与边界

待审核被退回后恢复草稿私有，审核者不能借回执继续取正文；退回命令返回最小状态并支持幂等重放。Unicode行分隔符不额外当LF，修正了原文切片与行号可能不一致的情况。重复判断使用规范化文本，原始字节hash单独保留。

文本状态4种，processing/failed索引运行状态留T03b。published_candidates是发布/权限/适用范围预过滤，**不是向量检索**；所有版本/块仍返回vector_index=not_integrated或formal_retrieval_available=false。Agent的explain_method仍拒答“正式知识未接入”，不把可读文本当作已完成RAG。

本机pgvector可用性仅只读检查，未修改扩展或赋予应用DDL能力。下一步T03b启用受控索引、真实Embedding身份及权限过滤后的相似度检索，T03c接Agent引用。软件发布动作不认证来源/使用授权真实性或审核者职业资格，独立专业审核和工厂试用仍未通过。
