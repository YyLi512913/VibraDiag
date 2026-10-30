# Frontend

独立Vue3＋TypeScript＋Vite应用。组件、HTTP客户端、请求类型、交互状态及样式分别维护。

```bat
npm ci
npm run dev
npm run build
```

开发地址：http://127.0.0.1:5173 。Vite将同源 `/api` 与 `/health` 代理到后端8010；浏览器不直接调用DeepSeek/Ollama，不持有API Key，也不需要放开任意来源CORS。

构建输出为 `dist/`，可独立部署为静态资源。`npm run preview` 在4173预览构建并使用相同本地API代理；它不是生产部署方式。

默认入口现为M1正式数据工作台：登录、授权记录、CSV元数据、参数确认/修订、分析/图表/下载、分页历史、刷新恢复及幂等重试。“模型实验（开发）”单独保留原型，不把实验诊断当正式报告。

会话Cookie为HttpOnly；CSRF及未收到响应的请求跟踪保留在标签页sessionStorage，恢复索引按组织/用户区分，不在localStorage保存业务结果或密码。localStorage仅记组织ID/用户名等非凭据。新标签页缺少操作上下文时要求重新登录；重新登录/注销/会话失效会清空当前显示，注销还会撤销服务端当前会话。

这是M1功能界面，完整证据/报告/工程复核交互仍在M4设计，尚非最终视觉设计。当前用原生canvas展示有限预览，极值与小/大量级坐标按缩放绘制；全量分析仍由后端完成。
