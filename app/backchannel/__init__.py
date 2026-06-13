"""本地快速接话层(Local Backchannel Layer)。

在主 LLM 返回前显示一句很短的角色化过渡反应(字幕 + 表情 + 可选预合成语音)。

模块划分:
- models:词表常量与数据类(标签、模板、变体、清单)
- manifest:角色包 backchannels manifest 的加载与校验
- classifier:规则分类器(用户意图 + 情绪)
- probe_classifier:bge 句向量 + 标注数据训练出的逻辑回归头(意图分类)
- hybrid_classifier:rules-first 混合分类器(规则头部 + probe 尾部)
- model_cache:句向量底座(bge)的本地缓存检测与 ZIP 导入
- resolver:模板匹配(相位 > 精确 > 同意图 > 兜底)与防重复轮换
"""
