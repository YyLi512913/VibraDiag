# Deployment boundary

目标为前端静态资源、FastAPI、Worker、PostgreSQL＋pgvector及本地Embedding服务分离部署，由同源反向代理提供访问。

当前本目录仅说明部署边界，尚无经过验证的生产Compose。数据库基础迁移已在backend/migrations实现并在本机PostgreSQL18.6验证；正式部署、备份恢复及参考环境验收仍待完成，不以开发代理替代生产反向代理。

- 对外仅开放受控业务入口。
- DeepSeek密钥仅注入后端/Worker；前端构建产物不包含密钥。
- 原文件/产物、数据库及备份使用各自明确的数据卷和恢复方案。
- 当前开发版本仅监听127.0.0.1，不能直接用于共享工厂试用。
