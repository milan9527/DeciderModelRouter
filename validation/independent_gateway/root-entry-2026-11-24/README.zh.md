# 根域名直接访问验收

入口：https://d3eipg2g3l3an3.cloudfront.net/

CloudFront 默认首页从 `index.html` 改为 `gateway/index.html`，直接读取私有 S3 中的网关页面。HTML 使用 `/gateway/` 绝对资源路径；不通过重定向页、浏览器脚本或 HTML refresh 修改地址。

[实测记录](verification.json)确认根路径返回 HTTP 200，没有 Location 和 HTML refresh，五个前端文件与本地发布版本的 SHA-256 一致。Chromium 登录前后均停留在根路径；切换页面只增加 hash。20 个模板正常显示，历史刷新后仍可见，已有评测和账户管理可以打开，登录退出正常，无页面错误或失败的网络请求。

本次只发布静态资源和切换默认首页，ECS 保持 revision 3。验证只提交登录和退出请求，GPU 推理与 Bedrock 调用均为零。相关部署测试 21 项通过；本地开发服务的根页面及五个资源也均直接返回 200。

页面截图：[登录页](root-login.png)、[工作台](root-workspace.png)。部署恢复记录保存在本机 `.deployment/root-entry-20261124-233756/`。
