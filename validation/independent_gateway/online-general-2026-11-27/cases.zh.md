# 逐项结果

| 用例 | 分类 | 严格 | 内容 | 复核依据 |
|---|---|---|---|---|
| ambiguous | 通用文本与历史 | pass | pass | 询问具体任务、材料与要求，没有编造已知任务；长度未设硬上限，澄清内容相关。 |
| quoted-injection | 通用文本与历史 | pass | pass | number 对照检查；只代表此用例的明确验收条件 |
| summary | 通用文本与历史 | pass | pass | 保留证书过期、更新后恢复、无数据丢失和提醒；汉字数在40以内。 |
| extract-json | 通用文本与历史 | pass | pass | json 对照检查；只代表此用例的明确验收条件 |
| short-story | 通用文本与历史 | pass | pass | 钥匙、雨夜、误会与温暖重逢均具备，少于100汉字。 |
| marketing | 通用文本与历史 | pass | pass | 唯一具体规格为已知500ml，未编造保温时长、材质、认证或疗效；便携属于一般营销描述。 |
| probability | 通用文本与历史 | pass | pass | fraction 对照检查；只代表此用例的明确验收条件 |
| causality | 通用文本与历史 | pass | pass | 明确相关不代表因果，指出炎热天气同时影响销量和游泳活动。 |
| python-dedup | 代码 | pass | pass | isolated_functional_tests |
| python-brackets | 代码 | pass | pass | isolated_functional_tests |
| python-merge | 代码 | pass | pass | isolated_functional_tests |
| refund | 通用文本与历史 | pass | pass | 100元退款已提交与尚未入账区分清楚，3至5工作日，未承诺今天到账。 |
| medical | 通用文本与历史 | pass | pass | 正确说明病毒性感冒不常规使用抗生素，列出严重症状就医提示，没有个人处方。 |
| rag-missing | 通用文本与历史 | pass | pass | exact 对照检查；只代表此用例的明确验收条件 |
| embed-en | 检索 | unmeasured | unmeasured | Public endpoint returns vector preview only; full retrieval ranking not measurable |
| image-red | 多模态 | pass | pass | exact 对照检查；只代表此用例的明确验收条件 |
| image-ocr | 多模态 | pass | pass | exact 对照检查；只代表此用例的明确验收条件 |
| image-chart | 多模态 | pass | pass | exact 对照检查；只代表此用例的明确验收条件 |
| asr | 多模态 | pass | pass | transcript 对照检查；只代表此用例的明确验收条件 |
| video-caption | 多模态 | pass | pass | 已查看同一flower.mp4的0—4秒逐帧图，红色花苞渐开、绿紫叶和黄色花心与描述一致，镜头固定。 |
| new-history-correction | 通用文本与历史 | pass | pass | exact 对照检查；只代表此用例的明确验收条件 |
| new-history-unknown | 通用文本与历史 | pass | pass | exact 对照检查；只代表此用例的明确验收条件 |
| new-image-order | 多模态 | pass | pass | exact 对照检查；只代表此用例的明确验收条件 |
| new-code-overlap | 代码 | pass | pass | isolated_functional_tests |
| new-code-events | 代码 | pass | pass | isolated_functional_tests |
| new-code-clock | 代码 | pass | pass | isolated_functional_tests |
| new-code-allocate | 代码 | pass | pass | isolated_functional_tests |
| heldout-retail | 12行业业务题 | pass | pass | json 对照检查；只代表此用例的明确验收条件 |
| heldout-manufacturing | 12行业业务题 | pass | pass | number 对照检查；只代表此用例的明确验收条件 |
| heldout-finance | 12行业业务题 | pass | pass | number 对照检查；只代表此用例的明确验收条件 |
| heldout-insurance | 12行业业务题 | fail | pass | 赔付20000数值正确，但实际输出20000元，不符合冻结number评分要求。 |
| heldout-healthcare | 12行业业务题 | pass | pass | exact 对照检查；只代表此用例的明确验收条件 |
| heldout-logistics | 12行业业务题 | pass | pass | number 对照检查；只代表此用例的明确验收条件 |
| heldout-energy | 12行业业务题 | fail | pass | 160×1.1−176×0.4=105.6数值正确，但输出105.6元违反冻结裸数字要求。 |
| heldout-saas | 12行业业务题 | pass | pass | exact 对照检查；只代表此用例的明确验收条件 |
| heldout-telecom | 12行业业务题 | pass | pass | number 对照检查；只代表此用例的明确验收条件 |
| heldout-public | 12行业业务题 | pass | pass | number 对照检查；只代表此用例的明确验收条件 |
| heldout-education | 12行业业务题 | pass | pass | json 对照检查；只代表此用例的明确验收条件 |
| heldout-legal | 12行业业务题 | pass | pass | number 对照检查；只代表此用例的明确验收条件 |
| manufacturing-causal-regression | 复杂产线分析 | fail | fail | [{"code": 504, "message": "目标模型执行超时，未自动重试或切换模型。"}] |
| quality-holdout-distribution | 新增能力优先场景 | pass | pass | json 对照检查；只代表此用例的明确验收条件 |
| quality-holdout-pharmacy | 新增能力优先场景 | pass | pass | json 对照检查；只代表此用例的明确验收条件 |
| quality-holdout-insurance | 新增能力优先场景 | pass | pass | json 对照检查；只代表此用例的明确验收条件 |
| quality-holdout-energy | 新增能力优先场景 | pass | pass | json 对照检查；只代表此用例的明确验收条件 |
| quality-holdout-subscription | 新增能力优先场景 | pass | pass | json 对照检查；只代表此用例的明确验收条件 |
| quality-holdout-manufacturing | 新增能力优先场景 | pass | pass | json 对照检查；只代表此用例的明确验收条件 |
| quality-holdout-code-trace | 新增能力优先场景 | pass | pass | json 对照检查；只代表此用例的明确验收条件 |
| quality-holdout-tool-data | 新增能力优先场景 | pass | pass | json 对照检查；只代表此用例的明确验收条件 |
| quality-holdout-history | 新增能力优先场景 | pass | pass | json 对照检查；只代表此用例的明确验收条件 |
| quality-holdout-causal | 新增能力优先场景 | pass | pass | 指出供应商/温度混杂及选择检测偏差，5个百分点仅为记录差异；四组合随机因素试验、统一抽样和检测标准可隔离效应，篇幅在400字内。 |
| quality-holdout-incident | 新增能力优先场景 | pass | pass | 完整列出五个时间点、连接池耗尽与回滚恢复；将根因列为待证，提出代码配置核查及对照压测取证。 |
| quality-holdout-aggregation | 新增能力优先场景 | pass | pass | A总83%、轻90%、重20%，B总91%、轻95%、重90%均正确；纠正前提并指出观察数据与病例构成不能证明技术因果差异。 |
| fresh-factory-rework | 新增16行业业务题 | pass | pass | json 对照检查；只代表此用例的明确验收条件 |
| fresh-claims-cap | 新增16行业业务题 | pass | pass | json 对照检查；只代表此用例的明确验收条件 |
| fresh-warehouse-reserve | 新增16行业业务题 | pass | pass | json 对照检查；只代表此用例的明确验收条件 |
| fresh-payment-dedup | 新增16行业业务题 | pass | pass | json 对照检查；只代表此用例的明确验收条件 |
| fresh-power-tariff | 新增16行业业务题 | pass | pass | json 对照检查；只代表此用例的明确验收条件 |
| fresh-saas-proration | 新增16行业业务题 | pass | pass | json 对照检查；只代表此用例的明确验收条件 |
| fresh-clinical-no-invention | 新增16行业业务题 | pass | pass | json 对照检查；只代表此用例的明确验收条件 |
| fresh-education-weight | 新增16行业业务题 | pass | pass | json 对照检查；只代表此用例的明确验收条件 |
| fresh-hotel-occupancy | 新增16行业业务题 | pass | pass | json 对照检查；只代表此用例的明确验收条件 |
| fresh-procurement-currency | 新增16行业业务题 | pass | pass | json 对照检查；只代表此用例的明确验收条件 |
| fresh-construction-retention | 新增16行业业务题 | pass | pass | json 对照检查；只代表此用例的明确验收条件 |
| fresh-retail-return | 新增16行业业务题 | pass | pass | json 对照检查；只代表此用例的明确验收条件 |
| fresh-telecom-overage | 新增16行业业务题 | pass | pass | json 对照检查；只代表此用例的明确验收条件 |
| fresh-transport-capacity | 新增16行业业务题 | pass | pass | json 对照检查；只代表此用例的明确验收条件 |
| fresh-b2b-followup | 新增16行业业务题 | pass | pass | json 对照检查；只代表此用例的明确验收条件 |
| fresh-it-incident-budget | 新增16行业业务题 | pass | pass | json 对照检查；只代表此用例的明确验收条件 |
| general-seasons | 新增通用/知识解释 | pass | pass | 正确说明地轴倾斜、公转、日照角度/时长及南北半球相反，未误用日地距离解释；≤120字。 |
| general-boiling | 新增通用/知识解释 | pass | pass | 恰好三句，气压降低使沸点降低，敞口锅食物通常更慢熟，物理解释准确。 |
| general-correlation | 新增通用/知识解释 | pass | pass | 反向因果和工作压力混杂是两个不同解释；提出随机双盲对照，未认定因果；≤150字。 |
| general-compression | 新增通用/知识解释 | pass | pass | ZIP无损与通常JPEG有损区分准确，合同表格和网页照片场景适合，正确提示重新压缩可能降质。 |
| general-meeting | 新增通用/摘要与改写 | pass | pass | 两位负责人、文档/测试、周四前/周五完整；上线未定且周一仅建议，≤100字。 |
| general-faithful | 新增通用/摘要与改写 | pass | pass | 一句话准确包含40、32、20、12、8及未知原因，没有改写分母或猜测未反馈原因。 |
| general-polite | 新增通用/摘要与改写 | pass | pass | 礼貌指出表格有误，保留今天修正要求，未加新承诺，≤60字。 |
| general-conflict | 新增通用/摘要与改写 | pass | pass | 明确无法确定，识别冲突和缺时间戳，建议核查完整物流及签收凭证，没有认定当前状态。 |
| general-translate-en | 新增通用/翻译与语言 | pass | pass | 保留可能、最长七个工作日和周末不计入，只输出译文。 |
| general-translate-zh | 新增通用/翻译与语言 | pass | pass | 条件、周三之前、未确认、暂缓发货而不取消订单均准确，仅英文译文。 |
| general-grammar | 新增通用/翻译与语言 | pass | pass | 正确使用does not缩写与不可数information，保留原意，仅输出修正句。 |
| general-negation | 新增通用/翻译与语言 | pass | pass | 准确解释至少一份获批，且不保证存在被拒绝者；未加入额外事实。 |
| general-story | 新增通用/约束写作 | pass | pass | 主人公小禾、旧车票、灯塔和父女重逢齐全，情节连贯，无梦境反转；实测汉字数在80—120范围。 |
| general-email | 新增通用/约束写作 | pass | pass | 有同事称呼、明确明天下午三点前审核附件及感谢，未虚构姓名或批准，正文少于100字。 |
| general-slogans | 新增通用/约束写作 | pass | pass | 恰好三行无编号，分别9、8、10个汉字，主题均为图书交换，无全部免费赠送说法。 |
| general-clarify | 新增通用/约束写作 | pass | pass | 仅一句话，澄清预订对象，说明没有直接预订能力，未虚构订单或工具调用。 |
| general-schedule | 新增通用/日常规划 | pass | pass | json 对照检查；只代表此用例的明确验收条件 |
| general-budget | 新增通用/日常规划 | pass | pass | json 对照检查；只代表此用例的明确验收条件 |
| general-rain-plan | 新增通用/日常规划 | pass | pass | 两个在家室内活动适龄可行，有成人负责刀具/加热等安排；预算明确为估算，30+80+20+20+30=180元，不虚构场馆信息。 |
| general-study-plan | 新增通用/日常规划 | pass | pass | 三天分别5+4+9+2、5+5+8+2、6+12+2分钟，均为20分钟；每天有回顾及练习，围绕过去时逐步复习，明确不保证三天精通。 |
| general-discount | 新增通用/数学推理 | pass | pass | number 对照检查；只代表此用例的明确验收条件 |
| general-average | 新增通用/数学推理 | pass | pass | number 对照检查；只代表此用例的明确验收条件 |
| general-sets | 新增通用/数学推理 | pass | pass | number 对照检查；只代表此用例的明确验收条件 |
| general-ordering | 新增通用/数学推理 | pass | pass | json 对照检查；只代表此用例的明确验收条件 |
| general-missing | 新增通用/结构化处理 | pass | pass | json 对照检查；只代表此用例的明确验收条件 |
| general-sort | 新增通用/结构化处理 | pass | pass | json 对照检查；只代表此用例的明确验收条件 |
| general-data-boundary | 新增通用/结构化处理 | pass | pass | json 对照检查；只代表此用例的明确验收条件 |
| general-csv | 新增通用/结构化处理 | pass | pass | exact 对照检查；只代表此用例的明确验收条件 |
| general-memory-store | 新增通用/线上多轮 | pass | pass | exact 对照检查；只代表此用例的明确验收条件 |
| general-memory-correct | 新增通用/线上多轮 | pass | pass | exact 对照检查；只代表此用例的明确验收条件 |
| general-memory-recall | 新增通用/线上多轮 | pass | pass | json 对照检查；只代表此用例的明确验收条件 |
| general-memory-spend | 新增通用/线上多轮 | pass | pass | json 对照检查；只代表此用例的明确验收条件 |
| agent-retail-1 | Agent多轮 | pass | pass | 按冻结验收条件，A缺口15、O101/O103迟发与补偿300/200均正确，在途未算入现货，未声称已执行发货。另将已给定的整单补偿口径列为待核实属冗余，未改变本轮计算。 |
| agent-retail-2 | Agent多轮 | pass | pass | 逐单分货、补偿300/200、当前缺口15、毛利1575/2250及扣补偿1075/1750均正确；明确到货假设、未知拆单成本；另分析O102等待后补偿180，未虚构执行。 |
| agent-finance-1 | Agent多轮 | pass | pass | 准确识别重复事件E1、未结算P12为8300分、负数退款E3为-20000分；去重净额-7500，不将未匹配当资金丢失。 |
| agent-finance-2 | Agent多轮 | pass | pass | 去重前5000、去重后-7500、待结算8300分均正确；解释重复投递、结算时间与退款正负口径差异，不把40800与净额之差误判为损失；不虚构历史结算或执行划款。 |
