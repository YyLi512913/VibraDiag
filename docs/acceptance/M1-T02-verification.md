# M1-T02 持久基础与身份验证记录

日期：2026-10-29。范围：M1-T02子任务验证，不是整个M1验收，也不是工厂试用记录。

## 实现与环境

Windows本机现有PostgreSQL18.6（server_version_num=180006），Python工作区.venv。原设计参考PostgreSQL17，本次复用本机18，不宣称17兼容验证已完成；后续参考部署环境须重跑。SQLAlchemy2.1.6、psycopg3.3.6、Alembic1.20.1、argon2-cffi25.1.0，完整依赖见backend/requirements.lock.txt。

开发库vibradiag_dev与测试库vibradiag_test隔离；各有独立迁移及应用账号，无superuser/createdb/createrole权限。四批基础迁移共16表，0005另加强完成产物元数据不可变。连接凭据仅存私有backend/.env；本机管理员与工程师使用独立开发账号，访问资料仅在忽略入库的.runtime/local-access.json，不在此记录密码。

已实现登录、会话摘要、8小时有效期、注销/撤销、Argon2id（64MiB、time_cost=3、parallelism=4）、CSRF、授权资产/测点查询、采集权限谓词及可信运维CLI。旧分析/诊断/检索接口默认关闭；本机私有配置仍显式允许实验页面，不能共享部署。

## 实际执行

| 检查 | 实际结果 |
| --- | --- |
| backend/scripts/migrate.py，开发库 | 升级head成功，应用权限设置成功 |
| 测试fixture重复升级测试库两次 | 成功，无重复建表错误 |
| pytest -c backend/pyproject.toml backend/tests -q | 40 passed in 5.78s，0 skipped |
| 其中test_postgres_identity.py | 18个真实PostgreSQL用例，包括参数化边界；其余22个为现有原型回归 |
| check_secret_hygiene.py | API密钥及4个数据库密码未发现源代码/文档/结果泄漏；私有环境已忽略入库 |

覆盖的数据库/接口检查：16表存在；应用非超级用户且不能建表或改迁移历史；组织复合外键；测点/设备关系；current_revision不能跨采集记录；非有限/非正采样率拒绝；审计与revision不可修改；completed必须具备4类ready产物；完成结果/产物禁止改写；登录错误与响应脱敏；Cookie凭据摘要；CSRF；注销、过期与用户禁用；角色/设备授权即时撤销；同组织两用户/跨组织/管理员访问范围；采集需同时满足记录和设备范围；登录限流；实验接口关闭。

新增完成结果测试时曾出现SQLAlchemy把SQL字符串中的JSON冒号解析为参数，导致测试自身失败（1 failed/39 passed）。已改为jsonb_build_object构造，重跑40项全部通过；不是绕过数据库不可变检查。

## 边界与下一步

本次未做完整M1-AT04验收：原文件/图/正式分析API尚待M1-T03/T04实现，后续须对每条实际路由验证资源链，而不能仅依赖现有权限谓词测试。历史revision的设备范围也须在对应读取服务中重新检查。前端登录属于M1-T05；目前无前端登录页面。

尚未实现正式CSV导入/版本确认、分析持久化、原型迁移、性能基线、Worker恢复、pgvector、生产限流及HTTPS部署。未跑现场数据或真实工程师复核。下一步M1-T03：文件存储、CSV校验、采集revision与输入确认接口。
