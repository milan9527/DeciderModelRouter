# AWS价格缺口补全 · 2026-11-26

新增价格快照 `9c633e91bc31b859f9a288d7`，文本路由仍为匿名LiveBench、Liquid固定适配器和两次GPU调用。本次只改变价格依据、费用核算及展示，不改变质量成绩、运行实测或权限/配额；未设置费用上限时候选集合保持不变，不修改GPU权重。

## 覆盖变化

| 范围 | 更新前 | 更新后 |
|---|---:|---:|
| 可估价文本配置 | 70/106 | **106/106** |
| 精确AWS价格的文本配置 | 旧单表部分覆盖 | **103/106** |
| 文本型号数量 | 84 | 84 |
| 全目录有精确AWS原始计价记录的型号 | — | **109/128** |
| 上批74份已保存输出可按新价格核算 | 6/74 | **71/74** |

103个配置使用与调用端点匹配的AWS价格；Qwen3 Next 80B、Grok4.6、Grok4.7这3个配置仍保留原有外部参考价格，明确标注，不伪装成Runtime精确报价。当前AWS价格表只找到其Mantle报价，未在缺乏依据时跨接口套用。完整目录还包含无调用端点的预置容量变体、零配额或未验证型号，不能将128全部视为线上候选。[逐配置覆盖](report.json) · [未匹配型号](coverage.json)。

109是拥有原始计价记录的型号数，不代表109个型号的任意请求都能算出总费：图片按张、音视频按秒或专用Token、检索按请求等，需要与接口实际返回用量对应。没有用量时不估成0，也不把秒/图片/请求单价当Token单价。

## 缺口原因及官方来源

旧同步只读取`AmazonBedrock`一个价格产品目录，并以显示名称直接匹配；GPT和较新Claude在`AmazonBedrockFoundationModels`目录中。此次同时读取两个AWS官方us-east-1价格文件，保存产品SKU、rateCode、发布时间、币种、计量单位和完整来源：

- [AmazonBedrock官方原始价格](https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AmazonBedrock/current/us-east-1/index.json)，归档[AmazonBedrock.json.gz](AmazonBedrock.json.gz)，发布时间2026-11-25。
- [AmazonBedrockFoundationModels官方原始价格](https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AmazonBedrockFoundationModels/current/us-east-1/index.json)，归档[AmazonBedrockFoundationModels.json.gz](AmazonBedrockFoundationModels.json.gz)，发布时间2026-11-23。
- [AWS Bedrock价格页](https://aws.amazon.com/bedrock/pricing/)，保留[页面快照](pricing-page.html.gz)，用于核对促销说明。

型号别名逐项固定，例如Nova 2 Lite→Nova 2.0 Lite、Voxtral Mini 3B 2507→Voxtral Mini 1.0。Titan使用价格表的`titanModel`属性。Stability的13种图像操作按具体用量名称匹配，不能共用最便宜的图片费率。模糊匹配不参与自动补价。

## 具体价格与计价规则

以下为当前实际`us.*`调用对应的美国跨区域标准服务档、短上下文，单位USD／百万Token：

| 型号 | 输入 | 输出 | 缓存读取 |
|---|---:|---:|---:|
| GPT-6.2 Astra | 8.80 | 44.00 | 0.44 |
| GPT-6.2 Sol | 2.20 | 11.00 | 0.11 |
| GPT-6.1 Sol | 2.20 | 11.00 | 0.11 |
| Claude Fable 5.2 | 8.80 | 44.00 | 0.22 |

Global价格与美国跨区域价格分别匹配，不能用Astra的Global输入8/输出40来估算当前US端点。Batch、Flex、Priority、Ultrafast、预置和自定义模型价格不混入标准调用；原始单位按1K、1M或单Token精确换算。

GPT输入**超过272,000 Token**时，整次请求使用长上下文输入/输出/缓存费率；Haiku5.5的阈值是**100,000 Token**。阈值和对应AWS模型卡链接见[context-thresholds.json](context-thresholds.json)。Converse的`inputTokens`不含缓存读写，用普通输入+缓存读+缓存写判断上下文档位，再分别计价；缓存写5分钟、30分钟和1小时费率分开保存，本项目仅采用实际默认缓存时长。缺缓存费率时保持未知。

GPT-5.6 Sol现行价格有促销期限，截至2026-11-30。保存`valid_until=2026-12-01T00:00:00Z`，到期后旧快照不再用于报价，必须重新同步并发布；不会凭空推算非促销价。价格是公开目录参考，不包括路由GPU、网络、工具、合同折扣及税项，不是账单或账户花费上限。

## 已保存用量核算

[repriced-history.json](repriced-history.json)仅将新快照应用到上一轮已保存的74份输出，**没有修改DynamoDB历史、没有重新调用目标，也不是当时实际账单**。可计算从6份增至71份，已知部分合计$0.84223311；覆盖范围不同，不能与旧已知合计直接得出涨价或省钱结论。

仍未知的3份：Cohere向量输出和Pegasus视频输出没有用量；Grok4.6记录有81个缓存读Token，但目前没有经过Runtime精确匹配的缓存费率，保留未知。Voxtral原生接口返回386输入和9输出Token，按该型号Token费率核算，未把音频秒数冒充Token。

## 验证和维护

**681项软件回归通过**，包括价格单位、美国/Global隔离、批处理和加速价格排除、长上下文阈值、缓存拆账、促销到期、精确预算边界、图片/视频非Token计量与已有会话/Agent流程。未设置费用上限的对照中，候选ID和顺序保持完全一致；设置显式费用上限时，候选是否超限会随新价格重新核验；全部非价格证据逐字段核验不变，109个型号从归档官方来源重新生成的价格与发布快照一致。[测试日志](tests-final.log) · [数据核验](data-verification.json)。

AWS实际部署及线上/浏览器核验见[live/README.zh.md](live/README.zh.md)。7次成功目标调用的保存费用逐项复算一致，两轮Agent完成；另有1次产线目标调用选Sol6.1 max后超时，最终用量与费用未知，已如实保留。GPU服务没有重启或训练。本次不重新宣称72项质量通过率；此前71/72属于价格更新前的定向回归，价格变化可能改变模型选择。

最终ECS revision33镜像摘要`bd5fff2f076fc2ef44ec0847579d0d307104fe36d5afc67d818cfc99838cb202`，CloudFront刷新`I83ODN9DW96J8ZZBVPSWSQZ0O5`完成；[发布记录](release.json)、[公网部署核验](deployment-verification.json)、[稳定版本API核验](final-api.json)和[镜像源码核验](image-source-verification.json)均留档。

刷新脚本：[scripts/refresh_bedrock_prices.py](../../../scripts/refresh_bedrock_prices.py)。在仓库根目录准备一个来源目录，放入本报告的`context-thresholds.json`，执行：

```bash
python -m scripts.refresh_bedrock_prices --sources <来源目录> --download --output <待审核证据JSON>
```

它下载两个官方目录，保持所有质量/运行证据不变，输出候选证据及coverage.json；新增长上下文型号缺少已核验阈值或同计价范围出现冲突时拒绝生成，需先核对官方模型卡。核对结果后将文件作为`app/bedrock_evaluations.json`构建并发布ECS；重新测量模型时也应在编译实测证据之后执行该价格更新步骤。此命令不自动部署、不调用模型。
