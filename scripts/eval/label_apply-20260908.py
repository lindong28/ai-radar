#!/usr/bin/env python3
"""T4 漏判标注：把 120 条 prefilter 拒绝样本逐条打上 AI 相关性标签。

标注者 = Claude Code（用户 2026-09-08 指派代做）。四值口径：
  ai  正文本身讲 AI/ML（技术、产品、研究、AI 产业或 AI 政策）
  org 正文不讲 AI，但发布方是 AI 公司/实验室/AI 垂直媒体的**机构账号**，
      故可算「AI 业界动态」。个人账号的私人感想不算 org。
  no  正文不讲 AI，且发布方不是 AI 专属机构
  unk 正文太薄判不了（裸链接、几个词的感叹）——prefilter 看到的也就这些
"""
import json, sys, math, collections

SRC = '/private/tmp/claude-501/-Users-lindong-research-ai-radar/d626d068-26ab-410e-93c7-5237e5db8717/scratchpad/label-sample.json'
OUT = '/Users/lindong/research/ai-radar/data/eval-fit/prefilter-negatives/agent-labels-20260908.jsonl'

# idx -> (label, 一句理由)
L = {
0:('unk','正文只有 emoji + t.co 链接'),
1:('no','公众号读者运营，无 AI'),
2:('no','平台工程/DevOps，非 AI'),
3:('unk','裸 t.co 链接'),
4:('no','港口收购财经新闻'),
5:('unk','正文仅「5.6 Sol*」——像模型代号但 8 字符判不实'),
6:('no','邀请码推广，无 AI 内容'),
7:('unk','正文仅数字「200,000」+ 链接'),
8:('org','PixVerse（AI 视频公司）宣传自家生成片段，正文未提 AI'),
9:('no','Pro 订阅拉新，无 AI'),
10:('unk','仅「Source: 链接」'),
11:('no','汽车行业，非 AI'),
12:('no','babylon.js 游戏复刻，非 AI'),
13:('no','赛车事故'),
14:('no','4S 店销售，非 AI'),
15:('org','OpenAI Developers 发的应用大赛获奖公告，正文讲音频串流 app'),
16:('no','欧股行情'),
17:('no','猪油渣消费品类'),
18:('no','Ethan Mollick 个人政治观点（UBI）'),
19:('no','时间循环纪录片'),
20:('no','Nathan Lambert 个人写作感想'),
21:('no','德国网络吐槽'),
22:('unk','仅 emoji + 链接'),
23:('org','SemiAnalysis 晶圆代工产能模型——AI 基建上游，正文本身讲代工'),
24:('no','婚恋市场，非 AI'),
25:('no','爱沙尼亚选举'),
26:('org','Anthropic Newsroom 对政府官员言论的声明；标题未点 AI'),
27:('no','新加坡餐厅'),
28:('no','两性话题'),
29:('no','草间弥生讣告'),
30:('org','Anthropic 联合创始人评教宗通谕；标题未点 AI'),
31:('no','个人生活（宠物/隐私政策玩笑）'),
32:('unk','仅「just -17」+ 链接'),
33:('no','儿科医生短缺'),
34:('no','与 #001 同文，公众号读者运营'),
35:('no','海绵宝宝亚文化'),
36:('unk','裸链接'),
37:('no','就业数据与股市'),
38:('org','Replit（AI 编程公司）产品分析功能营销，正文未提 AI'),
39:('org','OpenAI 官方招聘/团队动态，正文未提 AI'),
40:('no','个人持仓与估值，非 AI'),
41:('no','Airbnb 股价'),
42:('unk','仅「For more: 链接」'),
43:('unk','仅「Source: 链接」'),
44:('no','太空轨道转移飞行器融资，非 AI'),
45:('no','个人职业感想'),
46:('no','心理健康科普'),
47:('ai','Hugging Face Spaces 上做蛋白质可视化——ML 平台内容'),
48:('no','房地产'),
49:('no','猪周期与牧原估值'),
50:('unk','仅「enjoy your reset」+ 链接'),
51:('no','棒球比赛'),
52:('no','Emad 谈物理统一理论，非 AI'),
53:('no','sqlite/DuckDB 工具发版，正文与 AI 无关'),
54:('no','邮寄选票司法新闻'),
55:('no','NASA 气候工程，非 AI'),
56:('no','财报电话会'),
57:('org','Google AI 博客讲 Google Images 25 周年；视觉搜索是 ML 但正文按产品写'),
58:('no','综合早报'),
59:('org','Perplexity 产品「Portable Computer」上手引导，正文只有一句+链接'),
60:('no','读书笔记（计划 vs 实验）'),
61:('no','教育话题'),
62:('no','闲鱼二手交易见闻'),
63:('unk','仅「Source 2: 链接」'),
64:('unk','裸链接'),
65:('no','个税与居间合规争论'),
66:('no','英特尔股价与产能（综合站点财经稿）'),
67:('no','中东局势'),
68:('org','Runway（AI 视频公司）转发抽奖活动，正文是活动规则'),
69:('org','Google AI 博客讲用搜索选家居；正文纯产品导购'),
70:('unk','仅 emoji + 链接'),
71:('unk','仅「明天在 newsletter 里细讲」+ 链接'),
72:('no','社会阶层随笔'),
73:('unk','仅「Source 链接」'),
74:('unk','仅「20 more minutes」+ 链接'),
75:('no','Emad 谈 Poincaré 代数，非 AI'),
76:('no','波音出售 eVTOL 子公司'),
77:('no','影视公司股价'),
78:('no','火星地壳研究'),
79:('no','sqlite-utils 发版，正文与 AI 无关'),
80:('unk','裸链接'),
81:('no','《奥德赛》读书随笔'),
82:('org','OpenClaw（AI 产品）预告发版说明，正文无实质'),
83:('no','股债行情'),
84:('no','X 广告分成规则解读，非 AI'),
85:('no','A 股吐槽'),
86:('unk','仅「Source 链接」'),
87:('unk','仅「Full video 链接」'),
88:('no','地铁客流'),
89:('no','与 #084 同文'),
90:('no','「Thanks friends!!」正文完整但无 AI'),
91:('unk','裸链接'),
92:('org','Sam Altman「we support business privacy」+链接；OpenAI CEO 机构性发声'),
93:('no','中文推特社群运营'),
94:('unk','仅「sorry, what?!」+ 两条链接'),
95:('no','识别蠢人清单'),
96:('no','mp2rss 会员到期通知——抓取链路噪声，不是内容'),
97:('no','汽车一致性监管'),
98:('no','越南低价游'),
99:('org','OpenClaw 发版预热「The wait is almost over」'),
100:('no','美国工人储蓄调查'),
101:('unk','裸链接'),
102:('no','加密代币投放'),
103:('no','B站海外创作激励讨论'),
104:('unk','仅「full video 链接」'),
105:('no','咸菜零食化'),
106:('no','代谢与营养访谈'),
107:('unk','仅「Enjoy your -25% rate upgrade」'),
108:('unk','仅「Related tweet: 链接」'),
109:('no','技术博客写作访谈，非 AI'),
110:('no','凉山教育事件'),
111:('org','SemiAnalysis 讲 IREN 数据中心的原住民土地致谢；数据中心是 AI 基建'),
112:('no','比特币行情'),
113:('no','读博建议，个人感想'),
114:('no','a16z 谈「无国界创业者」，非 AI 专属机构且正文非 AI'),
115:('unk','仅「ok who told you」+ 链接'),
116:('no','越南海关法'),
117:('no','G20 峰会'),
118:('org','OpenClaw「HA! We beat GTA6!」自家产品热度'),
119:('ai','TechCrunch AI：Gamma 收购 Lica，收购方组建 research team'),
}

d = json.load(open(SRC))
its = d['items']
assert len(its) == 120 and len(L) == 120, (len(its), len(L))

with open(OUT,'w') as f:
    for n,i in enumerate(its):
        lab,why = L[n]
        f.write(json.dumps({**i,'idx':n,'label':lab,'label_why':why,
                            'labeler':'claude-code','labeled_at':'2026-09-08',
                            'standard':'我方 prefilter 该不该放行（不是 AIHOT 会不会收）'},
                           ensure_ascii=False)+'\n')

def wilson(k,n,z=1.96):
    if n==0: return (0.0,0.0)
    p=k/n; d=1+z*z/n; c=(p+z*z/(2*n))/d
    h=z*math.sqrt(p*(1-p)/n+z*z/(4*n*n))/d
    return (max(0,c-h),min(1,c+h))

for st,N in (('A',d['NA']),('B',d['NB'])):
    sub=[L[n][0] for n,i in enumerate(its) if i['stratum']==st]
    c=collections.Counter(sub); n=len(sub)
    print(f"\n=== 层 {st}  n={n}  层总体 N={N} ===")
    print('  ', dict(c))
    strict_k=c['ai']; strict_n=c['ai']+c['no']
    broad_k=c['ai']+c['org']; broad_n=c['ai']+c['org']+c['no']
    for name,k,nn in (('严格（只算正文讲 AI）',strict_k,strict_n),
                      ('宽口径（机构账号算 AI 业界）',broad_k,broad_n)):
        lo,hi=wilson(k,nn)
        print(f"   {name}: 漏判 {k}/{nn} = {k/nn*100:.1f}%  95%CI [{lo*100:.1f}%, {hi*100:.1f}%]")
    # 把 unk 全算成"拒得对"的保守下界（分母 = 全部 n）
    for name,k in (('严格',strict_k),('宽口径',broad_k)):
        lo,hi=wilson(k,n)
        print(f"   {name}·unk 全记正确拒绝: {k}/{n} = {k/n*100:.1f}%  95%CI [{lo*100:.1f}%, {hi*100:.1f}%]")
    if st=='A':
        for name,k,nn in (('严格',strict_k,strict_n),('宽口径',broad_k,broad_n)):
            lo,hi=wilson(k,nn)
            print(f"   → 外推到 A 层总体 {N} 条：漏判约 {k/nn*N:.0f} 条  [{lo*N:.0f}, {hi*N:.0f}]")
print(f"\n写入 {OUT}")
