# 通用输入与多模态验收

正式入口：https://d3eipg2g3l3an3.cloudfront.net/ 。ECS revision 4，Python 3.12 ARM64，镜像摘要见 [构建记录](image-build.json)。未修改 GPU 服务或重启原 GPU。

[软件检查](software-tests.txt)：458 项通过；[本地浏览器](local-browser-verification.json)使用三个合成服务、6 次合成分类请求、0 次真实推理。合成时延不用于性能结论。

[正式浏览器检查](browser-verification.json)通过：自由输入、35 个模板、上传、历史图片解码、音视频元数据加载、刷新后的记录保留、390px 布局以及五个线上资源的 SHA-256 均已核对，没有 JavaScript 错误。该检查没有发起模型调用。[AWS 状态](aws-resources.json)确认 ECS 完成滚动更新、CloudFront 刷新完成、私有媒体匿名访问为 403，新容器没有匹配的错误日志。

## 正式域名调用

[验收记录](verification.json)保存 10 次真实 H2O 分类和 8 次 Bedrock 目标调用。上传测试 PNG、WAV、MP4，通过认证 API 读取并核对 SHA-256。10 个场景的分类、执行状态、保存与预期一致；其中两项预期为无路由。没有失败重试或切换目标模型。

| 场景 | 实际目标 | 目标耗时 | 结果 |
| --- | --- | ---: | --- |
| `hi` | GPT-6 Luna | 904 ms | 普通问候回复 |
| 中文日常问题 | GPT-6 Luna | 993 ms | 完整文本 |
| 图片理解 | Nova 2 Lite | 818 ms | 完整文本，但颜色回答错误 |
| 视频理解 | Nova 2 Lite | 2907 ms | 完整文本，未独立验收语义 |
| 音频转写 | Voxtral Mini 3B | 327 ms | 与测试语句一致 |
| 英语语音生成 | Nova 2 Sonic | 4685 ms | WAV 与文字 |
| 去背景 | Stability Remove Background | 359 ms | PNG，未评测编辑质量 |
| 文档重排 | Cohere Rerank 3.5 | 183 ms | 正确文档排名第一 |
| 视频生成 | 无 | — | 目录没有可用型号，0 次目标调用 |
| 纯文生图 | 无 | — | 目录没有可用型号，0 次目标调用 |

耗时是一次请求的后端观测，媒体目标耗时包括私有存储读写，不能推导稳定 P95、吞吐量或费用。每条运行的原始事件与实际用量分别保存在同名 JSON；媒体完整费用未知。

## 语义验收与限制

[独立记录的人工验收](business-review.json)对五条具有明确判定条件的可见测试输入逐条核验，4 条通过、1 条失败。Nova 2 Lite 将 256×256 纯红色 PNG 描述为黑色，此条已写入数据库为“人工验收不通过”。执行完整不等于回答正确，没有用单条失败改写能力评分或生产校准。其余场景仅验证传输、接口返回与文件完整性，未宣称生成质量通过。

[GPU 属性探针](gpu-classification-probe.json)记录新版提示词的九条测试。类别与测试意图相符，原生媒体处理不再误标需要外部工具；英文短问候的语言字段仍可能为 `other`，不宣称七项属性全部正确。模板不用于匹配。这些是可见开发输入，不是保留测试集。

当前区域没有可调用的纯文生图、视频生成型号；接入的 Nova 2 Sonic 不支持中文语音。Nova 2.5 Sonic 在接入实验中未完成有界语音输出，未加入当前可执行适配器。原生 SDK、流关闭及推理端点问题在部署前修正；本报告的调用数量只覆盖上述正式域名批次，不包含此前调试请求。

## 测试材料与复现

PNG 为程序生成的纯红色图片；输入 WAV 为真实 Nova 2 Sonic 生成的英语语句 “Hello, welcome to the model gateway.”。视频来自 [MDN CC0 flower.mp4](https://interactive-examples.mdn.mozilla.net/media/cc0-videos/flower.mp4)。本目录仅提交校验和与输出记录，媒体留在认证后的私有存储。

使用私有凭据文件和同类测试媒体运行：

```bash
python -m scripts.check_gateway_media_live \
  --url https://YOUR_DISTRIBUTION.cloudfront.net \
  --credentials .deployment/RELEASE/admin-credentials.json \
  --image red.png --audio speech.wav --video flower.mp4 \
  --output validation/independent_gateway/general-media-UNIQUE
```

脚本固定最多 10 次分类和 8 次目标尝试；真实请求计费，记录结果后才判断验收是否通过，不会自动重跑。上述语义验收另行保存。
