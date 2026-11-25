"""General-purpose gateway taxonomy; historical comparison prompts stay frozen."""

WORKFLOWS = {'customer_support': {'label': '直接答复客户',
                      'description': "Answer a customer's product, service, order or account "
                                     'question using supplied facts; resolve a concrete support '
                                     'issue.',
                      'steps': ['读取订单事实', '组织状态说明', '生成客服答复']},
 'coding': {'label': '编写代码与测试用例',
            'description': 'Turn software requirements into implementation code and test cases, or '
                           'debug existing code.',
            'steps': ['理解代码需求', '编写或修复代码', '给出测试用例']},
 'reasoning': {'label': '推理与分析',
               'description': 'Solve mathematics or logic, analyze data, compare options, plan '
                              'under constraints, or reconcile facts and explain discrepancies.',
               'steps': ['整理问题与条件', '推理或计算', '形成分析结论']},
 'embedding': {'label': '生成检索向量',
               'description': 'Convert text to embedding vectors for a semantic-search index or '
                              'query.',
               'steps': ['读取待索引文本', '生成文本向量']},
 'general_text': {'label': '通用对话与文本',
                  'description': 'Reply in text: greetings, casual conversation, thanks, everyday '
                                 'questions, explanations, advice, creative writing, translation, '
                                 'summaries or rewrites. Also handle unclear or minimal messages '
                                 'by asking for clarification.',
                  'steps': ['理解对话或文本需求', '回答、生成文本或澄清']},
 'vision': {'label': '理解图像',
            'description': 'Inspect actual visual content in an input image. A text question about '
                           'images or photography alone is not image understanding.',
            'steps': ['读取图像', '分析图像内容']},
 'video_understanding': {'label': '理解视频',
                         'description': 'Watch an attached or explicitly referenced video and '
                                        'describe, summarize or answer questions about its visual '
                                        'content. Output is text, not a new video.',
                         'steps': ['读取视频', '理解时序内容', '生成文字答复']},
 'audio_understanding': {'label': '理解或转写音频',
                         'description': 'Listen to an attached audio recording to transcribe, '
                                        'summarize or answer questions. Output is text, not '
                                        'synthesized speech.',
                         'steps': ['读取录音', '识别与理解语音', '生成文字答复']},
 'image': {'label': '生成或编辑图像',
           'description': 'Explicitly create or edit an actual image as output, not a written '
                          'description, story or explanation about images.',
           'steps': ['理解图像需求', '生成或编辑图像']},
 'video': {'label': '生成视频',
           'description': 'Explicitly produce an actual video as output, not write a video script '
                          'or discuss a video.',
           'steps': ['理解视频需求', '生成视频']},
 'speech': {'label': '处理语音',
            'description': 'Explicitly process an audio recording, transcribe audio, or synthesize '
                           'audible speech. Requires actual audio input or output; a typed '
                           'greeting or text conversation is general_text.',
            'steps': ['读取语音需求', '处理语音']},
 'rerank': {'label': '重排检索结果',
            'description': 'Reorder a supplied set of retrieved documents by relevance to a query.',
            'steps': ['读取查询与文档', '计算相关性并重排']}}
