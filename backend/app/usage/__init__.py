"""流量统计(token usage)子模块。

参照 cc-switch 的会话用量同步链路：解析本机各 AI 编程智能体的会话日志，
提取每次模型调用的 token 消耗，归一后入库，供仪表盘聚合查询。

与 cc-switch 的差异（有意简化）：
- 单一数据来源（会话日志），不存在"代理+会话"双写，故无需跨源指纹去重；
- token 语义在**写入侧**一次归一（input 不含 cache，cache 单列），
  读侧聚合无需 cc-switch 的 input_token_semantics 三套 CASE 表达式；
- 本地量级(万级明细)即席 GROUP BY 毫秒级，第一期不做 daily rollup/归档。
"""
