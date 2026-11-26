# Bedrock精确端点评测与GPU选模修复

2026-11-26，us-east-1。目录 `72c68c7564790cd4`；直接评测 `ce73abe40e311da92266e0ac`。

128个Bedrock型号全部有覆盖说明：**112个有实际测量记录，16个因账户、容量或零配额受限**。测量记录包含失败，不能解释为112个都能成功执行。保留60个型号的公开评测映射，默认配置不再因缺少外部映射而被排除。

原始记录1456条；最终统计**1446条**。另保留2条早期非可移植工具探测、8条已修正请求/题意的原记录。没有伪造容量变体或受限端点成绩。

## 路由修复

- GPU先判断任务属性，再读取全部合格配置的直接实测、外部参考、费用、延迟和上次模型，返回目标配置。后端不再用同批次过滤、程序Top-K、非劣排序或固定Agent名单覆盖选择。
- 每组最多6个配置，组内结果再由GPU比较。能力、调用权限、配额、上下文、预算和短期故障仍是硬约束。没有要求每个型号轮流被选中。
- 多轮沿用和明确限流后的替换都由GPU决定；保存候选、每轮选择、选模耗时、调用次数、实际目标和错误。
- 64个精确端点完成可移植工具协议往返；最终回答金额是否正确另计。新增向量、Sonic 2.5、13种图片编辑和Pegasus视频适配。

## 测量口径与分项

这是可见开发测试集，小样本且各类样本数不同，不能作为生产成功率、独立泛化准确率或跨类别总榜。接口成功、内容正确和严格按要求输出分开。文本通常上限4096输出Token，早期Llama 3和Palmyra Vision使用已记录的2048上限；提供商默认采样/推理；语音上限1024，图片测试记录操作参数和seed。P95包含实际调用，一条工具或向量测量可能包括多次API调用。

内容规范化只处理可识别最终答案或代码围栏；不能确定时保留待复核。严格成绩保留格式失败。代码在无网络、非root、只读且有资源上限的容器中执行功能测试；固定镜像摘要 `sha256:70bccd58c0249b1e4af68344b4f0902e03c92ac0336ac80b347281210f789700`。

| 分项 | 测量型号 | 有效记录 | 内容正确 / 全部记录 | 待复核 | 严格通过 / 全部记录 | 调用错误 |
|---|---:|---:|---:|---:|---:|---:|
| 通用指令与抽取 | 83 | 331 | 248/331 | 0 | 248/331 | 2 |
| 多轮记忆 | 79 | 79 | 64/79 | 0 | 64/79 | 1 |
| 推理与行业核算 | 82 | 482 | 275/482 | 106 | 256/482 | 5 |
| 工具调用与结果使用 | 79 | 79 | 57/79 | 0 | 57/79 | 8 |
| 代码功能 | 79 | 237 | 181/237 | 28 | 108/237 | 2 |
| 英语语音生成 | 2 | 2 | 2/2 | 0 | 2/2 | 0 |
| 图片理解 | 48 | 192 | 96/192 | 94 | 96/192 | 2 |
| 视频理解 | 5 | 5 | 4/5 | 1 | 4/5 | 0 |
| 向量检索 | 9 | 18 | 18/18 | 0 | 18/18 | 0 |
| 文档重排 | 1 | 2 | 2/2 | 0 | 2/2 | 0 |
| 语音转写 | 2 | 2 | 2/2 | 0 | 2/2 | 0 |
| 安全分类 | 2 | 4 | 4/4 | 0 | 4/4 | 0 |
| 图片编辑 | 13 | 13 | 6/13 | 0 | 6/13 | 0 |

汇总是观测计数，不用于模型总排名。部分接口首次报不支持后停止后续题，不能把每个型号当作完成了相同数量的全部场景。图片理解包含94条待复核，推理106条、代码28条；它们没有被偷偷算成通过，也不等同于已判内容错误。

图片编辑6/13通过的是限定的尺寸、颜色或透明度检查，不是审美质量。其余7次都返回图片，但未通过限定检查；原始图像已保留。两条保真放大/扩图用例先前提示颜色与保留原图要求矛盾，修正题意后复测，原结果仍在归档中。视频4/5完成助手画面对照，Pegasus 1.2额外音频断言保留待评。语音2/2通过独立Voxtral回译，不能外推到自然度或其他语言。

## 参数适配补测

83个文本端点接受性探测发现19个较低输出上限，取请求上限与端点上限的较小值，当前GPU证据卡和执行记录均有实际上限。两项早期Llama 3及Palmyra Vision以2048输出预算重新运行，共49条记录，替代4条旧参数失败；旧记录保留。Palmyra对短探测提示仍报参数错误，但共同题集在2048预算下返回内容，全部结果均保留，不把一次成功推广为稳定性保证。此项参数探测不加入内容质量分母。

## GPU实际控制实验

窗口修复：12张完整卡片在Decider超出4096-token上限、H2O调用失败。压缩重复口径并改为每组6张后，1400字中文请求和最近对话预览在Liquid/H2O/Decider均成功，实际输入2900/2779/2762 tokens。原始结果见`gpu-window-fixed.jsonl`。当前请求不截断；选模仅预览最近两条历史、每条最多200字，目标执行保留会话上下文。

首次实机Liquid测试（12配置分组的中间版本）：82个型号、104个配置全部参与，10次分组判断后选择Claude Sonnet 4.6，GPU服务自报耗时合计约2.2秒。最终版本每组6个配置：85个型号、107个配置全部参与，22次分组调用选择Llama 3.3 70B，内网总耗时2.925秒（`gpu-full-limits.json`）；完整线上结果见发布验证。这是一次功能与延迟测量，不保证所有任务相同速度或全局最优。

交换两个匿名候选的代码正确率，保持费用和延迟相同：Liquid和H2O各2/2选择较高者；Decider为1/2，未在此对照中稳定随证据换选。此小对照不是准确率榜单；默认继续使用Liquid。原始结果全部保留。

Liquid单候选检查：文本、放大图片、英语语音均选择执行；“查找保险文档”的Marengo请求选择不执行，明确改为“生成查询向量”后选择执行。这说明任务表达也影响决策，不能把提供证据理解成模型判断必然正确。

软件验证：537项完整Python测试及后续40项Agent/证据回归通过；浏览器验证覆盖登录、分组选择、候选证据、持久历史、错误、流式、回放、模型页和手机宽度，浏览器错误为0。浏览器使用35次合成GPU调用，不作为模型性能数据。线上发布及实际业务调用另见本目录`live-validation.json`。

## 上线复核与Agent协议

普通网关7/7完成：自由问候、订单信息保存与跨模型追问、16384请求下Llama 4使用8192上限、代码、新补测GPT-6.2-Sol、Marengo 3.0向量。连续追问正确返回CEDAR-731和129，乘法检查返回391，向量为512维；这些检查不等同于全场景质量验收。每个合格配置都在GPU决策记录中出现。原始运行见[线上记录](live-runs.json.gz)。

Agent中间版本出现过GPT-5.6-Terra附带`reasoningContent`导致适配器拒收。已复现块类型并按已验证的可移植工具协议修复：正式文本/工具保留，提供商推理元数据不跨模型转发，仅记录块数与原始用量。修复后相同业务请求真实调用完成。见[复现](agent-content-block-reproduction.json)、[适配器实测](agent-portable-real.json)、[中间版本失败记录](agent-before-portable-fix.json.gz)。最终ECS revision 22的Runtime复测**4/4轮完成**：GPT-5.4 → GPT-5.6-Terra → GPT-6.2-Sol，5次目标调用、3次工具调用、2次换模型，读取1,506缓存Token。第二、三轮的推理元数据各1块被规范处理。完成与切换不等于业务答案全部正确。见[最终Agent记录](agent-final.json.gz)及[AWS状态](live-validation.json)。

## 逐型号覆盖

单元格为内容正确/记录数；`+N待评`表示尚待复核，调用错误保留在分母。不同型号的默认推理行为不同；每项只绑定表内精确Bedrock端点。

| 型号 | 状态 | 文本 | 推理 | 代码 | 图片理解 | 工具 | 多轮 | 专项 / 限制 |
|---|---|---:|---:|---:|---:|---:|---:|---|
| `ai21.jamba-1-5-large-v1:0` | 有测量 | 1/3 | — | — | — | — | — | 错误：ResourceNotFoundException×1 |
| `ai21.jamba-1-5-mini-v1:0` | 有测量 | 0/4 | 0/6 +5待评 | 1/3 +1待评 | — | 0/1 | 0/1 |  |
| `amazon.nova-2-5-sonic` | 有测量 | — | — | — | — | — | — | 英语语音生成 1/1 |
| `amazon.nova-2-lite-v1:0` | 有测量 | 1/4 | 0/6 +5待评 | 3/3 | 2/4 +2待评 | 1/1 | 0/1 | 视频理解 1/1 |
| `amazon.nova-2-lite-v1:0:256k` | 受限 | — | — | — | — | — | — | 需要预置容量或部署 |
| `amazon.nova-2-multimodal-embeddings-v1:0` | 有测量 | — | — | — | — | — | — | 向量检索 2/2 |
| `amazon.nova-2-sonic-v1:0` | 有测量 | — | — | — | — | — | — | 英语语音生成 1/1 |
| `amazon.nova-lite-v1:0` | 有测量 | 1/4 | 0/6 +4待评 | 2/3 | 2/4 +2待评 | 0/1 | 0/1 | 视频理解 1/1 |
| `amazon.nova-lite-v1:0:24k` | 受限 | — | — | — | — | — | — | 需要预置容量或部署 |
| `amazon.nova-lite-v1:0:300k` | 受限 | — | — | — | — | — | — | 需要预置容量或部署 |
| `amazon.nova-micro-v1:0` | 有测量 | 2/4 | 0/6 +2待评 | 2/3 | — | 0/1 | 1/1 |  |
| `amazon.nova-micro-v1:0:128k` | 受限 | — | — | — | — | — | — | 需要预置容量或部署 |
| `amazon.nova-micro-v1:0:24k` | 受限 | — | — | — | — | — | — | 需要预置容量或部署 |
| `amazon.nova-pro-v1:0` | 有测量 | 2/4 | 2/6 +2待评 | 2/3 | 1/4 +3待评 | 1/1 | 0/1 | 视频理解 1/1 |
| `amazon.nova-pro-v1:0:24k` | 受限 | — | — | — | — | — | — | 需要预置容量或部署 |
| `amazon.nova-pro-v1:0:300k` | 受限 | — | — | — | — | — | — | 需要预置容量或部署 |
| `amazon.titan-embed-g1-text-02` | 受限 | — | — | — | — | — | — | 账户可用性未确认 |
| `amazon.titan-embed-image-v1` | 有测量 | — | — | — | — | — | — | 向量检索 2/2 |
| `amazon.titan-embed-image-v1:0` | 受限 | — | — | — | — | — | — | 需要预置容量或部署 |
| `amazon.titan-embed-text-v1` | 有测量 | — | — | — | — | — | — | 向量检索 2/2 |
| `amazon.titan-embed-text-v1:2:8k` | 受限 | — | — | — | — | — | — | 需要预置容量或部署 |
| `amazon.titan-embed-text-v2:0` | 有测量 | — | — | — | — | — | — | 向量检索 2/2 |
| `amazon.titan-embed-text-v2:0:8k` | 受限 | — | — | — | — | — | — | 需要预置容量或部署 |
| `anthropic.claude-fable-5` | 有测量 | 3/4 | 6/6 | 3/3 | 2/4 +2待评 | 1/1 | 1/1 |  |
| `anthropic.claude-fable-5-1` | 有测量 | 4/4 | 6/6 | 3/3 | 2/4 +2待评 | 1/1 | 1/1 |  |
| `anthropic.claude-fable-5-2` | 有测量 | 4/4 | 6/6 | 3/3 | 2/4 +2待评 | 1/1 | 1/1 |  |
| `anthropic.claude-haiku-4-5-20251001-v1:0` | 有测量 | 2/4 | 4/6 | 3/3 | 2/4 +2待评 | 1/1 | 1/1 |  |
| `anthropic.claude-haiku-5-5` | 有测量 | 4/4 | 6/6 | 3/3 | 2/4 +2待评 | 1/1 | 1/1 |  |
| `anthropic.claude-opus-4-1-20250805-v1:0` | 有测量 | 4/4 | 2/3 | — | 2/4 +2待评 | — | — | 错误：ResourceNotFoundException×1 |
| `anthropic.claude-opus-4-5-20251101-v1:0` | 有测量 | 4/4 | 3/6 +2待评 | 2/3 +1待评 | 2/4 +2待评 | 1/1 | 1/1 |  |
| `anthropic.claude-opus-4-6-v1` | 有测量 | 4/4 | 4/6 +2待评 | 2/3 | 2/4 +2待评 | 1/1 | 1/1 |  |
| `anthropic.claude-opus-4-7` | 有测量 | 4/4 | 6/6 | 3/3 | 2/4 +2待评 | 1/1 | 1/1 |  |
| `anthropic.claude-opus-4-8` | 有测量 | 4/4 | 5/6 | 3/3 | 2/4 +2待评 | 1/1 | 1/1 |  |
| `anthropic.claude-opus-5` | 有测量 | 4/4 | 5/6 +1待评 | 2/3 | 2/4 +2待评 | 1/1 | 1/1 |  |
| `anthropic.claude-opus-5-5` | 有测量 | 4/4 | 6/6 | 3/3 | 2/4 +2待评 | 1/1 | 1/1 |  |
| `anthropic.claude-sonnet-4-5-20250929-v1:0` | 有测量 | 4/4 | 5/6 +1待评 | 2/3 | 2/4 +2待评 | 1/1 | 1/1 |  |
| `anthropic.claude-sonnet-4-6` | 有测量 | 4/4 | 1/6 +4待评 | 3/3 | 2/4 +2待评 | 1/1 | 1/1 |  |
| `anthropic.claude-sonnet-5` | 有测量 | 4/4 | 6/6 | 3/3 | 2/4 +2待评 | 1/1 | 1/1 |  |
| `anthropic.claude-sonnet-5-5` | 有测量 | 4/4 | 5/6 +1待评 | 3/3 | 2/4 +2待评 | 1/1 | 1/1 |  |
| `cohere.embed-english-v3` | 有测量 | — | — | — | — | — | — | 向量检索 2/2 |
| `cohere.embed-english-v3:0:512` | 受限 | — | — | — | — | — | — | 需要预置容量或部署 |
| `cohere.embed-multilingual-v3` | 有测量 | — | — | — | — | — | — | 向量检索 2/2 |
| `cohere.embed-multilingual-v3:0:512` | 受限 | — | — | — | — | — | — | 需要预置容量或部署 |
| `cohere.embed-v4:0` | 有测量 | — | — | — | — | — | — | 向量检索 2/2 |
| `cohere.rerank-v3-5:0` | 有测量 | — | — | — | — | — | — | 文档重排 2/2 |
| `deepseek.r1-v1:0` | 有测量 | 3/4 | 5/6 +1待评 | 3/3 | — | 0/1 | 1/1 | 错误：ValidationException×1 |
| `deepseek.v3.2` | 有测量 | 3/4 | 2/6 +3待评 | 3/3 | — | 1/1 | 1/1 |  |
| `google.gemma-3-12b-it` | 有测量 | 2/4 | 2/6 | 2/3 | 2/4 +2待评 | 0/1 | 1/1 |  |
| `google.gemma-3-27b-it` | 有测量 | 3/4 | 3/6 +1待评 | 2/3 | 2/4 +2待评 | 0/1 | 1/1 |  |
| `google.gemma-3-4b-it` | 有测量 | 2/4 | 0/6 +1待评 | 2/3 | 2/4 +2待评 | 0/1 | 1/1 |  |
| `meta.llama3-1-70b-instruct-v1:0` | 有测量 | 3/4 | 0/6 +5待评 | 0/3 +3待评 | — | 0/1 | 1/1 |  |
| `meta.llama3-1-8b-instruct-v1:0` | 有测量 | 2/4 | 0/6 +4待评 | 1/3 +2待评 | — | 1/1 | 1/1 |  |
| `meta.llama3-3-70b-instruct-v1:0` | 有测量 | 4/4 | 0/1 | — | — | — | — | 错误：ResourceNotFoundException×1 |
| `meta.llama3-70b-instruct-v1:0` | 有测量 | 4/4 | 0/6 +3待评 | 0/3 +3待评 | — | 0/1 | 1/1 | 错误：ValidationException×1 |
| `meta.llama3-8b-instruct-v1:0` | 有测量 | 2/4 | 0/6 +4待评 | 1/3 +1待评 | — | 0/1 | 1/1 | 错误：ValidationException×1 |
| `meta.llama4-maverick-17b-instruct-v1:0` | 有测量 | 3/4 | 0/6 +4待评 | 3/3 | 2/4 +2待评 | 1/1 | 1/1 |  |
| `meta.llama4-scout-17b-instruct-v1:0` | 有测量 | 4/4 | 2/6 +2待评 | 2/3 | 2/4 +2待评 | 0/1 | 1/1 | 错误：ResourceNotFoundException×1 |
| `minimax.minimax-m2` | 有测量 | 4/4 | 6/6 | 3/3 | — | 1/1 | 1/1 |  |
| `minimax.minimax-m2.1` | 有测量 | 3/4 | 6/6 | 3/3 | — | 1/1 | 1/1 |  |
| `minimax.minimax-m2.5` | 有测量 | 4/4 | 6/6 | 3/3 | — | 1/1 | 1/1 |  |
| `mistral.devstral-2-123b` | 有测量 | 3/4 | 4/6 | 2/3 | — | 1/1 | 1/1 |  |
| `mistral.magistral-small-2509` | 有测量 | 1/4 | 0/6 +3待评 | 3/3 | 2/4 +2待评 | 0/1 | 1/1 |  |
| `mistral.ministral-3-14b-instruct` | 有测量 | 1/4 | 1/6 +1待评 | 3/3 | 2/4 +2待评 | 1/1 | 0/1 |  |
| `mistral.ministral-3-3b-instruct` | 有测量 | 0/4 | 0/6 +2待评 | 1/3 +1待评 | 1/4 +3待评 | 1/1 | 0/1 |  |
| `mistral.ministral-3-8b-instruct` | 有测量 | 2/4 | 1/6 +2待评 | 2/3 | 1/4 +3待评 | 1/1 | 0/1 |  |
| `mistral.mistral-7b-instruct-v0:2` | 有测量 | 1/4 | 0/6 +5待评 | 0/3 | — | 0/1 | 1/1 | 错误：ValidationException×1 |
| `mistral.mistral-large-2402-v1:0` | 有测量 | 2/4 | 0/6 +5待评 | 1/3 +1待评 | — | 0/1 | 0/1 |  |
| `mistral.mistral-large-3-675b-instruct` | 有测量 | 2/4 | 4/6 +1待评 | 3/3 | 2/4 +2待评 | 1/1 | 1/1 |  |
| `mistral.mistral-small-2402-v1:0` | 有测量 | 3/4 | 0/6 +5待评 | 2/3 +1待评 | — | 0/1 | 1/1 |  |
| `mistral.mixtral-8x7b-instruct-v0:1` | 有测量 | 2/4 | 0/6 +5待评 | 1/3 +2待评 | — | 0/1 | 0/1 | 错误：ValidationException×1 |
| `mistral.pixtral-large-2502-v1:0` | 有测量 | 4/4 | 1/6 +4待评 | 2/3 | 2/4 +2待评 | 1/1 | 0/1 |  |
| `mistral.voxtral-mini-3b-2507` | 有测量 | 0/4 | 0/6 +3待评 | 2/3 +1待评 | — | 0/1 | 1/1 | 语音转写 1/1 |
| `mistral.voxtral-small-24b-2507` | 有测量 | 2/4 | 2/6 +1待评 | 2/3 | — | 0/1 | 0/1 | 语音转写 1/1 |
| `moonshot.kimi-k2-thinking` | 有测量 | 4/4 | 5/6 | 0/3 +3待评 | — | 0/1 | 0/1 |  |
| `moonshotai.kimi-k2.5` | 有测量 | 4/4 | 5/6 +1待评 | 2/3 +1待评 | 2/4 +2待评 | 1/1 | 1/1 |  |
| `moonshotai.kimi-k3` | 受限 | — | — | — | — | — | — | 账户推理配额为 0：Cross-region model inference tokens per minute for Moonshot AI Kimi K3 （L-51FF35D5；核验于 2026-11-26T01:58:10.100472+00:00）。配额恢复后需刷新目录。 |
| `moonshotai.kimi-k3.1` | 受限 | — | — | — | — | — | — | 账户推理配额为 0：Cross-region model inference tokens per minute for Moonshot AI Kimi K3.1 （L-B37A8021；核验于 2026-11-26T01:58:10.100472+00:00）。配额恢复后需刷新目录。 |
| `nvidia.nemotron-nano-12b-v2` | 有测量 | 3/4 | 1/6 +3待评 | 3/3 | 3/4 +1待评 | 1/1 | 0/1 |  |
| `nvidia.nemotron-nano-3-30b` | 有测量 | 2/4 | 1/6 +1待评 | 2/3 +1待评 | — | 1/1 | 1/1 |  |
| `nvidia.nemotron-nano-9b-v2` | 有测量 | 0/4 | 3/6 +2待评 | 0/3 +2待评 | — | 0/1 | 0/1 |  |
| `nvidia.nemotron-super-3-120b` | 有测量 | 3/4 | 4/6 | 3/3 | — | 1/1 | 1/1 |  |
| `openai.gpt-5.4` | 有测量 | 4/4 | 6/6 | 3/3 | 4/4 | 1/1 | 1/1 |  |
| `openai.gpt-5.5` | 有测量 | 4/4 | 6/6 | 3/3 | 2/4 +2待评 | 1/1 | 1/1 |  |
| `openai.gpt-5.6-luna` | 有测量 | 4/4 | 6/6 | 2/3 +1待评 | 2/4 +2待评 | 1/1 | 1/1 |  |
| `openai.gpt-5.6-sol` | 有测量 | 4/4 | 6/6 | 3/3 | 2/4 +2待评 | 1/1 | 1/1 |  |
| `openai.gpt-5.6-terra` | 有测量 | 3/4 | 6/6 | 3/3 | 3/4 +1待评 | 1/1 | 1/1 |  |
| `openai.gpt-6-astra` | 有测量 | 4/4 | 6/6 | 3/3 | 2/4 +2待评 | 1/1 | 1/1 |  |
| `openai.gpt-6-luna` | 有测量 | 4/4 | 6/6 | 3/3 | 2/4 +2待评 | 1/1 | 1/1 |  |
| `openai.gpt-6-sol` | 有测量 | 4/4 | 6/6 | 3/3 | 2/4 +2待评 | 1/1 | 1/1 |  |
| `openai.gpt-6.1-sol` | 有测量 | 4/4 | 6/6 | 3/3 | 2/4 +2待评 | 1/1 | 1/1 |  |
| `openai.gpt-6.1-terra` | 有测量 | 4/4 | 6/6 | 3/3 | 3/4 +1待评 | 1/1 | 1/1 |  |
| `openai.gpt-6.2-astra` | 有测量 | 4/4 | 6/6 | 3/3 | 2/4 +2待评 | 1/1 | 1/1 |  |
| `openai.gpt-6.2-sol` | 有测量 | 4/4 | 6/6 | 3/3 | 2/4 +2待评 | 1/1 | 1/1 |  |
| `openai.gpt-oss-120b-1:0` | 有测量 | 4/4 | 6/6 | 3/3 | — | 1/1 | 1/1 |  |
| `openai.gpt-oss-20b-1:0` | 有测量 | 4/4 | 6/6 | 2/3 | — | 1/1 | 1/1 |  |
| `openai.gpt-oss-safeguard-120b` | 有测量 | — | — | — | — | — | — | 安全分类 2/2 |
| `openai.gpt-oss-safeguard-20b` | 有测量 | — | — | — | — | — | — | 安全分类 2/2 |
| `qwen.qwen3-32b-v1:0` | 有测量 | 2/4 | 4/6 | 2/3 | — | 1/1 | 1/1 |  |
| `qwen.qwen3-coder-30b-a3b-v1:0` | 有测量 | 3/4 | 2/6 +2待评 | 2/3 | — | 1/1 | 1/1 |  |
| `qwen.qwen3-coder-next` | 有测量 | 3/4 | 3/6 +1待评 | 2/3 | — | 1/1 | 1/1 |  |
| `qwen.qwen3-next-80b-a3b` | 有测量 | 3/4 | 4/6 +1待评 | 3/3 | — | 1/1 | 1/1 |  |
| `qwen.qwen3-vl-235b-a22b` | 有测量 | 4/4 | 5/6 | 3/3 | 1/4 +3待评 | 1/1 | 1/1 |  |
| `stability.stable-conservative-upscale-v1:0` | 有测量 | — | — | — | — | — | — | 图片编辑 1/1 |
| `stability.stable-creative-upscale-v1:0` | 有测量 | — | — | — | — | — | — | 图片编辑 0/1 |
| `stability.stable-fast-upscale-v1:0` | 有测量 | — | — | — | — | — | — | 图片编辑 1/1 |
| `stability.stable-image-control-sketch-v1:0` | 有测量 | — | — | — | — | — | — | 图片编辑 0/1 |
| `stability.stable-image-control-structure-v1:0` | 有测量 | — | — | — | — | — | — | 图片编辑 0/1 |
| `stability.stable-image-erase-object-v1:0` | 有测量 | — | — | — | — | — | — | 图片编辑 1/1 |
| `stability.stable-image-inpaint-v1:0` | 有测量 | — | — | — | — | — | — | 图片编辑 0/1 |
| `stability.stable-image-remove-background-v1:0` | 有测量 | — | — | — | — | — | — | 图片编辑 1/1 |
| `stability.stable-image-search-recolor-v1:0` | 有测量 | — | — | — | — | — | — | 图片编辑 1/1 |
| `stability.stable-image-search-replace-v1:0` | 有测量 | — | — | — | — | — | — | 图片编辑 0/1 |
| `stability.stable-image-style-guide-v1:0` | 有测量 | — | — | — | — | — | — | 图片编辑 0/1 |
| `stability.stable-outpaint-v1:0` | 有测量 | — | — | — | — | — | — | 图片编辑 1/1 |
| `stability.stable-style-transfer-v1:0` | 有测量 | — | — | — | — | — | — | 图片编辑 0/1 |
| `twelvelabs.marengo-embed-2-7-v1:0` | 有测量 | — | — | — | — | — | — | 向量检索 2/2 |
| `twelvelabs.marengo-embed-3-0-v1:0` | 有测量 | — | — | — | — | — | — | 向量检索 2/2 |
| `twelvelabs.pegasus-1-2-v1:0` | 有测量 | — | — | — | — | — | — | 视频理解 0/1 +1待评 |
| `twelvelabs.pegasus-1-5-v1:0` | 有测量 | — | — | — | — | — | — | 视频理解 1/1 |
| `writer.palmyra-vision-7b` | 有测量 | 3/4 | 2/6 +1待评 | 0/3 +3待评 | 1/4 +3待评 | 0/1 | 1/1 | 错误：ValidationException×1 |
| `writer.palmyra-x4-v1:0` | 有测量 | 4/4 | 2/4 | — | — | — | — | 错误：ResourceNotFoundException×1 |
| `writer.palmyra-x5-v1:0` | 有测量 | 3/4 | 4/6 | 1/3 | — | 0/1 | 0/1 | 错误：ThrottlingException×5 |
| `zai.glm-4.7` | 有测量 | 4/4 | 4/6 | 3/3 | — | 1/1 | 1/1 |  |
| `zai.glm-4.7-flash` | 有测量 | 2/4 | 3/6 | 3/3 | — | 1/1 | 1/1 |  |
| `zai.glm-5` | 有测量 | 4/4 | 5/6 | 3/3 | — | 1/1 | 1/1 |  |
| `zai.glm-5.3` | 受限 | — | — | — | — | — | — | 账户推理配额为 0：Cross-region model inference tokens per minute for Z.ai GLM 5.3 （L-4A544964；核验于 2026-11-26T01:58:10.100472+00:00）。配额恢复后需刷新目录。 |
| `xai.grok-4.6` | 有测量 | 3/4 | 6/6 | 3/3 | 2/4 +2待评 | 1/1 | 1/1 |  |
| `xai.grok-4.7` | 有测量 | 2/4 | 5/6 | 3/3 | 2/4 | 1/1 | 1/1 | 错误：ServiceUnavailableException×4 |

## 原始证据与复现

- [汇总与检查](summary.json)、[完整路由证据快照](evidence.json)、[目录与共同题集](measurement-manifest.json)。
- [全部原始记录](observations.jsonl.gz)、[内容/图像/语音/视频复核](reviews.json.gz)、[生成媒体](media-artifacts.tar.gz)、[图片总览](image-montage.jpg)、[视频采样画面](video-frames.jpg)。图片总览保留第一批输出，修正后的两张图见媒体归档。
- [最终107配置GPU实测](gpu-full-limits.json)、[上下文容量实测](gpu-window-fixed.jsonl)、[83端点参数探测](parameter-limits.json)。
- [初期候选GPU实测](gpu-full-probe.jsonl)、[三种决策模型控制实验](gpu-controls.jsonl)、[明确向量任务复测](gpu-embedding-explicit.json)。
- [AWS区域按需价快照](aws-pricing-us-east-1.json.gz)：精确且无歧义匹配30个型号；其他价格是明确标注的外部参考或未知，不代替真实账单。
- [当前路由与复现命令](../../../docs/BEDROCK_CLASSIFIED_ROUTING.zh.md)、[源码](../../../scripts/measure_bedrock_catalog.py)、[专项接口](../../../scripts/measure_bedrock_specialized.py)、[汇总器](../../../scripts/compile_bedrock_evidence.py)。

历史87场景报告、分类参考标签和业务校准文件保留原样。这次提供直接端点评测依据，不声称旧报告通过率已提高，也不将本次小样本替代独立业务校准。
