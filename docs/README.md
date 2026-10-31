# VibraDiag 设计文档导航

本目录为项目设计文档的统一入口。按编号推进，每次讨论集中解决一个阶段；未完成的后续设计不提前当作已确定方案。

## 文档顺序与状态

| 顺序 | 文档 | 当前状态 |
| --- | --- | --- |
| 00 | [项目定义](00-project-charter.md) | v1.1 范围基线草案，后续随评审记录修订 |
| 01 | [需求规格](01-requirements.md) | v0.9 完整草案；已覆盖功能、非功能、评测与试用验收，待评审和后续实测 |
| 02 | [总体设计](02-system-design.md) | v0.3 草案；含工程包结构及前后端分离的当前实现边界，完整试用能力待实施 |
| 03 | [版本计划与验收](03-milestones.md) | M1通过、M2探索范围有条件通过，M3至T04b1已实施，M4—M6待实施 |
| 04 | [第一轮详细设计](04-first-iteration.md) | v0.1 M1设计及实施补充；T01—T05和本机阶段验收已完成 |
| 05 | [M1本机操作指南](05-m1-local-guide.md) | 登录、导入、确认、分析/历史、迁移与验证步骤 |
| 06 | [M2轴承工具详细设计](06-m2-tool-design.md) | v1.2；M2本机探索工具交付完工，有条件通过；正式诊断资格保留阻断 |
| 07 | [M3 Agent详细设计](07-m3-agent-design.md) | v0.9；T04b1已接方法模型图，数值模型编排待T04b2 |
| 08 | [知识发布与检索设计](08-knowledge-design.md) | v0.4；T03b2索引检索、T03c方法任务回执绑定与引用已实现；相关性待评测 |
| 09 | [正式模型调用与受限决策设计](09-model-runtime-design.md) | v0.2；T04a网关及T04b1方法闭环已实现，数值编排待T04b2 |

编号按阅读顺序排列。M1本机阶段已通过；M2-T02b2工具配置、运行API和受控产物完成，T03a匹配数值核心完成，T03b已接匹配API/事实/探索证据及最小界面，T04a比较核心完成，T04b双输入服务/页面已完成，T05a公开数据清单/开发基线已完成，T05b1实体分组/验证准入已完成，T05b2条件性研究适配/特征已完成，M2本机探索范围已结项，M3-T01契约及T02a持久任务/真实规则执行已实现，T02b1已验证官方检查点及子进程终止，T02b2已接业务图/权限围栏，T02b3已接受控数值子进程，T03a文本发布、T03b1真实Embedding及T03b2授权索引检索已实现，T03c已接方法任务知识引用，下一步T04模型决策与生成。文档状态、软件阶段和工厂试用准入分别记录。

附录（不属于主开发流程）：

- [开源项目验证记录](appendices/open-source-verification.md)：调研与局部复用评估资料，不代表 VibraDiag 基于该项目开发。
- [项目定义 v1.0 历史原文](appendices/project-charter-v1.0.md)：保留原始冻结版本，供比较修订。
- [需求规格 v0.1 历史原文](appendices/requirements-v0.1.md)：保留最初需求骨架。
- [总体设计 v0.1 历史原文](appendices/system-design-v0.1.md)：保留最初模块与Agent流程草案。
- [首轮详细设计历史草案](appendices/first-iteration-early-draft.md)：保留早期SQLite方案，不作为当前实现依据。
- [模型选型与对比记录](appendices/model-selection.md)：本地对比及失败记录、DeepSeek API接入与当前开发组合（DeepSeek Flash＋本地BGE-M3）；正式诊断准入仍待扩大评测。

## 当前进度

已完成：原始设计梳理、项目定位讨论、候选开源项目调研、JYD-iBearing 本地验证。

M1已执行基线：135项后端测试及前端类型/构建通过；实际浏览器、迁移、空schema重建和性能基线已验证，见[M1验收](acceptance/M1-acceptance.md)。120000点100次合成分析0失败，P95约0.484s；这不是现场故障效果或M6性能准入。历史记录：[T04](acceptance/M1-T04-verification.md)、[T03](acceptance/M1-T03-verification.md)、[T02](acceptance/M1-T02-verification.md)。

当前进度：**M3-T04b1可选方法模型图已实现**，见[T02a记录](acceptance/M3-T02a-verification.md)、[T02b1验证](acceptance/M3-T02b1-verification.md)、[T02b2接入](acceptance/M3-T02b2-verification.md)、[T02b3隔离](acceptance/M3-T02b3-verification.md)、[T03a发布](acceptance/M3-T03a-verification.md)及[08知识设计](08-knowledge-design.md)、[07设计](07-m3-agent-design.md)。新增任务API、命令幂等、调用账本、挂起恢复、取消围栏与托管消费者，调用M1/M2真实授权工具。当前44张业务表、4张检查点表；新任务运行分步持久规则图，旧任务保留原执行器。五类纯数值核心在受控子进程运行，知识文本已具备版本/显式审核/撤回；固定身份的Embedding、pgvector索引与授权检索已实现，方法任务知识引用已接入，DeepSeek决策与生成仍未接入。M2正式诊断条件继续阻断。

本机原型已整理为 `backend/` 和 `frontend/` 两个独立工程。后端采用src包布局，路由、服务、Agent、分析和基础设施分开；前端使用Vue3/TypeScript/Vite，组件、API客户端和状态分开。模型验证脚本位于backend/scripts，仓库级工具位于scripts；实际运行结果仍保存在experiments，原文件和索引仍保存在忽略入库的.runtime。

尚未完成：M3—M6实现/验收及M2遗留的正式诊断发布条件，包括包络故障取证、正式Agent/模型准入、完整权限管理、隔离执行及报告/工单闭环。`research/`不是产品底座，`experiments/`保存实际测试结果。

## 后续设计需要承接的讨论结论

- 主要用途是求职展示，同时按未来工厂受控试用准备要求设计。
- 当前没有真实工厂设备、现场数据和工程师参与；公开试验数据验证与现场效果验证分别表述。
- 核心价值是确定性分析工具、可追溯证据、Agent 取证/拒答与人工复核闭环。
- VibraDiag 独立设计与开发；参考开源调研，具体复用代码按模块验证，最终技术选型放在总体设计中确定。
- 首发设备范围、支持格式、数值定义、评测划分及指标，需要写入相应文档后再作为正式约束。
- 原始文档中的“已冻结”描述保留历史含义；对范围和指标的修改应记录新版本，不静默覆盖历史决策。

## 推进方式

1. 每个阶段先说明目标和待解决问题。
2. 讨论后更新对应文档及版本记录。
3. 在该阶段形成可用于下一步的结论后继续推进。
4. 开发阶段按“详细设计 → 编码 → 测试 → 验收 → 更新文档”形成小闭环。
5. 新发现若影响上游设计，记录原因并修订相关文档。

## 文件位置

- 统一设计文档：`F:/CodexDcouments/Codex/VibraDiag/docs/`。
- 开源仓库、测试脚本、数据及日志：`F:/CodexDcouments/Codex/VibraDiag/research/`。
- 原 `F:/vibradiag/docs/` 中两份设计文档已迁入本目录；旧目录只保留迁移说明。


T03c最新推进：[验收记录](acceptance/M3-T03c-verification.md)。新方法任务已接授权检索、回执重附着与动态引用校验，下一步T04；知识前端仍归M4。

T04a最新推进：[验收记录](acceptance/M3-T04a-verification.md)。统一外部/本地调用、固定每任务配置及受控模型轮已实现，现有规则图不消费其输出；下一步T04b，界面与试用准入仍按M4/M6推进。

T04b1最新推进：[验收记录](acceptance/M3-T04b1-verification.md)。方法任务可显式启用模型选择、授权检索与逐字摘录闭环；旧图保留，下一步T04b2数值候选编排。
