# 归因假设

> [Developer] · 此台账用于干预前写预测，不代替原始实验记录。

| hypothesis_id | claim / applies_to | differential_prediction | contrast | observation / status | next_diagnostic / owner |
| --- | --- | --- | --- | --- | --- |
| DATA-TIME-01 | 旧O1按抓取窗输入、按AIHOT新增窗标记，会把早已收录新闻误作负例 | 若是时间口径错，则标为负例的raw应能在同次API的更早timeline找到；若真未收录，则找不到 | 相同source/URL、同批API，仅扩大用于查证的timeline，不改模型 | 88条旧匹配及4条晚匹配，supported；用户已改定±12小时规则 | 实施新规则并核正/负/待定数；本session |
| PIPELINE-RULE-01 | 固定pool后只调精选threshold可复用推理与比较链路 | 若规则/推理解耦，则模型调用为0、O1预测不变；O4只受规则影响，而无参考精选仍不能被判为改善 | baseline完整池 vs threshold=7.5，其余参数不变 | 链路预测成立：两轮0调用、O1同值、四对象accepted=false；无质量提升结论 | 已完成诊断，未采纳或部署；质量迭代待完整预测与新增精选参考 |

后续假设沿这六栏追加。尚无被采纳的模型或阈值优化；不能由此数据缺陷归因到模型能力。
