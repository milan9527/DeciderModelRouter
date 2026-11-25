# 默认决策模型切换为 Liquid

2026-11-25，独立网关默认分类器改为 `LiquidAI/d1-2B`。后端未指定 `provider` 时使用 Liquid；前端从目录接口读取默认值，刷新后路由表单与分类评测表单默认选中 Liquid。显式选择 Decider 或 H2O 仍然有效。

43项相关软件测试通过，覆盖网关、增量发布和独立服务部署；ARM64镜像验证了默认 Liquid 与显式 H2O 覆盖。代码检查通过。

[正式域名验证](verification.json)通过：一次未传 `provider` 的请求真实调用 Liquid，返回七项任务属性，再调用 Nova Micro 完整回复 `Hello!`，记录保存到数据库。共一次GPU分类、一次Bedrock调用；浏览器检查未额外执行推理。根路径返回200、无跳转，默认路由及评测选项为Liquid，三个分类器均保留，无页面错误。

[原始请求与事件](live-run.json)记录本次GPU HTTP往返95.639ms、后端总计710.168ms。这是单次部署验证，不是新的速度排名或效果评测。原87场景的输出与分数没有调整。

沿用现有独立GPU服务，没有新建或重启GPU。ECS镜像、发布版本与CloudFront刷新结果见[AWS发布验证](aws-verification.json)。
