'use strict';
(() => {
  let material, identity, current, drafts = {}, baseline = {}, storageKey;
  let saveQueue = Promise.resolve(), saveError = '';
  const $ = id => document.getElementById(id);
  const el = (tag, text, cls) => {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    if (cls) node.className = cls;
    return node;
  };
  const notice = text => { $('notice').textContent = text; };
  const name = label => material.labels[label] || '无有效分类';
  const baseDraft = c => ({case_id:c.case_id,input_sha256:c.input_sha256,status:'pending',acceptable_labels:[],reason:''});
  const getDraft = c => drafts[c.case_id] || baseDraft(c);
  const latest = c => c.c5_observations.at(-1);
  const differs = c => latest(c).status !== 'ok' || latest(c).label !== c.aihot.label;
  const modelRows = (c, reviewer) => material.model_reviews.filter(r => r.reviewer.name === reviewer)
    .flatMap(r => r.judgments.filter(j => j.case_id === c.case_id).map(j => ({review:r, judgment:j})));
  function validateBallot(b) {
    if (b.format !== 'ai-radar-category-review-ballot-v1' || b.batch_id !== material.metadata.batch_id ||
        b.material_identity !== identity.material_identity || b.material_sha256 !== identity.material_sha256)
      throw new Error('反馈来自不同材料或模型意见版本；请先导出旧页反馈，不自动迁移。');
    if (!Array.isArray(b.judgments) || b.judgments.length !== material.cases.length) throw new Error('反馈题数不匹配');
    const seen = new Set();
    for (const j of b.judgments) {
      const c = material.cases.find(c => c.case_id === j.case_id);
      if (!c || seen.has(j.case_id) || j.input_sha256 !== c.input_sha256 ||
          !['pending','reviewed','uncertain'].includes(j.status) || typeof j.reason !== 'string' ||
          !Array.isArray(j.acceptable_labels) || new Set(j.acceptable_labels).size !== j.acceptable_labels.length ||
          j.acceptable_labels.some(x => !Object.hasOwn(material.labels,x)) ||
          (j.status === 'reviewed' ? !j.acceptable_labels.length : j.acceptable_labels.length))
        throw new Error('反馈有未知题目、标签、重复记录或输入不一致');
      seen.add(j.case_id);
    }
  }
  function ballot() {
    return {format:'ai-radar-category-review-ballot-v1',batch_id:material.metadata.batch_id,
      material_identity:identity.material_identity,material_sha256:identity.material_sha256,
      exported_at:new Date().toISOString(),judgments:material.cases.map(c => getDraft(c))};
  }
  const sameVote = (a,b) => a.status===b.status && a.reason===b.reason &&
    [...a.acceptable_labels].sort().join('|')===[...b.acceptable_labels].sort().join('|');
  function save() {
    saveQueue = saveQueue.then(async () => {
      if (!navigator.locks) throw new Error('浏览器不支持安全的多页草稿保存；本页判断仍可导出');
      await navigator.locks.request(storageKey, () => {
        const raw=localStorage.getItem(storageKey), remote={};
        if(raw){const b=JSON.parse(raw);validateBallot(b);for(const j of b.judgments) remote[j.case_id]=j;}
        const merged={}, conflicts=[];
        for(const c of material.cases){
          const old=baseline[c.case_id]||baseDraft(c), local=getDraft(c), other=remote[c.case_id]||baseDraft(c);
          if(!sameVote(local,old)&&!sameVote(other,old)&&!sameVote(local,other)) conflicts.push(c.case_id);
          merged[c.case_id]=sameVote(local,old)?other:local;
        }
        if(conflicts.length) throw new Error(`另一页面对同题有不同草稿（${conflicts.join(', ')}）；未覆盖任一方。请分别导出两页反馈后再关闭`);
        const b={...ballot(),judgments:material.cases.map(c=>merged[c.case_id])};
        localStorage.setItem(storageKey,JSON.stringify(b));
        drafts=merged;baseline=structuredClone(merged);saveError='';
      });
    }).catch(e=>{saveError=e.message;notice('草稿未保存：'+saveError+'。可复制或下载当前页反馈。');}).then(()=>{
      renderList();
      const c=material.cases.find(c=>c.case_id===current), d=c&&getDraft(c);
      if(d){
        for(const cb of document.querySelectorAll('.choices input')) cb.checked=d.acceptable_labels.includes(cb.value);
        if($('human-reason')&&$('human-reason').value!==d.reason) $('human-reason').value=d.reason;
        if($('vote-status')) $('vote-status').textContent=d.status==='reviewed'?`已评：${d.acceptable_labels.map(name).join(' / ')}`:d.status==='uncertain'?'暂无法判断（不产生有效标签）':'未评（未采用任何人的标签）';
      }
    });
    return saveQueue;
  }
  function filtered() {
    const q = $('search').value.toLocaleLowerCase();
    const f = $('filter').value;
    return material.cases.filter(c => (!q || [c.case_id,c.input.title,c.input.content_text].join(' ').toLocaleLowerCase().includes(q)) &&
      (f === 'all' || (f === 'latest' && differs(c)) || (f === 'claude-pending' && !modelRows(c,'claude').length) || getDraft(c).status === f));
  }
  function renderList() {
    const rows = filtered(), done = material.cases.filter(c => getDraft(c).status === 'reviewed').length;
    $('progress').textContent = `已评 ${done} / ${material.cases.length}`;
    $('count').textContent = `当前筛选 ${rows.length} 道 · 草稿仅保存在本浏览器，需导出交回`;
    const nav = $('case-list'); nav.replaceChildren();
    for (const c of rows) {
      const b = el('button'); b.setAttribute('aria-current',String(c.case_id === current));
      const idx = material.cases.indexOf(c)+1;
      b.append(el('span',String(idx).padStart(2,'0'),'num'),el('span',c.input.title,'nav-title'));
      b.append(el('small',` · ${{pending:'未评',reviewed:'已评',uncertain:'暂无法判断'}[getDraft(c).status]}`));
      b.onclick = () => openCase(c.case_id);nav.append(b);
    }
    if (!rows.length) nav.append(el('p','没有符合筛选的新闻。','empty'));
  }
  function card(who, labels, reason, provenance) {
    const node = el('section',undefined,'card');
    node.append(el('h3',who),el('div',labels,'labels'),el('div',reason,'reason'),el('div',provenance,'provenance'));
    return node;
  }
  function modelCard(c, reviewer) {
    const rows = modelRows(c,reviewer), label = reviewer === 'codex' ? 'Codex 的复核' : 'Claude 的复核';
    if (!rows.length) return card(label,'待补充','尚未提交判断。没有用其他模型的意见代填。','模型意见，不是人评');
    const last = rows.at(-1), j = last.judgment, r = last.review;
    const node = card(label,j.status === 'uncertain' ? '暂无法判断' : j.acceptable_labels.map(name).join(' / '),j.reason,
      `${r.created_at} · ${r.reviewer.model || '模型精确 ID 未记录'} · ${r.review_id} · ${r.reviewer.method}`);
    if (rows.length > 1) {
      const d = el('details');d.append(el('summary',`另有 ${rows.length-1} 批历史意见（不覆盖）`));
      for (const old of rows.slice(0,-1)) d.append(el('pre',JSON.stringify(old,null,2)));
      node.append(d);
    }
    return node;
  }
  function openCase(id, updateHash = true) {
    const c = material.cases.find(c => c.case_id === id) || material.cases[0];
    current = c.case_id;
    if (updateHash) history.replaceState(null,'',`#${current}`);
    renderList();const main = $('main');main.replaceChildren();
    main.append(el('div','“分歧”仅指 C5 某次输出与 AIHOT 不同，不代表 C5 必然错。请按新闻内容选择一个或多个可接受类别；模型意见不是人评，也不会自动改 gold。','intro'));
    const meta = el('div',undefined,'case-meta');meta.append(el('span',`第 ${material.cases.indexOf(c)+1} / ${material.cases.length} 道`),el('span',c.case_id),el('span',differs(c)?'最新全量仍不一致':'仅历史运行不一致','badge warn'));main.append(meta,el('h2',c.input.title));
    const article = el('section',undefined,'article'), actions = el('div',undefined,'article-actions');
    actions.append(el('h3','原始新闻'));
    const expand = el('button','展开全文');expand.onclick = () => {const yes=article.classList.toggle('expanded');expand.textContent=yes?'收起全文':'展开全文';};actions.append(expand);
    try {const url = new URL(c.input.url);if (['http:','https:'].includes(url.protocol)) {const a=el('a','打开原始链接 ↗');a.href=url.href;a.target='_blank';a.rel='noopener noreferrer';actions.append(a);}} catch {}
    article.append(actions,el('p',`${c.input.source_name || c.input.source_id || '未知来源'} · ${c.input.author || '作者未记录'} · ${c.input.published_at || '发布时间未记录'}`,'muted'),el('div',c.input.content_text || '档案中没有正文，请结合标题；未补抓外链。','article-text'));
    main.append(article);
    const context=el('details');context.append(el('summary','查看该题完整输入（含引用信息，以原调用为准）'));
    context.append(el('pre',c.prompts[latest(c).prompt_sha256].user));main.append(context);
    const o = latest(c), model = material.source.runs[o.run_id].model;
    const grid = el('div',undefined,'grid');grid.append(card('AIHOT 原参照',name(c.aihot.label),'归档记录的是页面分类，未记录 AIHOT 的分类理由。','原参考标签，尚未经你逐题确认'),card('AI RADAR / C5 · DeepSeek',name(o.label),o.reason || '原运行未记录理由',`${o.run_id} · ${model} · status=${o.status} · Atlas 最新覆盖该题的运行；非线上版本声明`),modelCard(c,'codex'),modelCard(c,'claude'));main.append(grid);
    const details = el('details');details.append(el('summary',`查看 C5 完整输入 prompt 与 ${c.c5_observations.length} 次历史输出`));
    for (const obs of [...c.c5_observations].reverse()) {
      const row = el('section',undefined,'history-row');
      row.append(el('strong',`${obs.run_id} · ${name(obs.label)} · ${obs.label===c.aihot.label?'与 AIHOT 一致':'与 AIHOT 不同'}`),el('p',obs.reason || '未记录理由','reason'));
      const pd=el('details');pd.append(el('summary','原调用的 system / user prompt'));const p=c.prompts[obs.prompt_sha256];pd.append(el('pre',`SYSTEM\n${p.system}\n\nUSER\n${p.user}`));row.append(pd);details.append(row);
    }
    main.append(details);
    const vote = el('section',undefined,'vote');vote.append(el('h3','你的判断'),el('p','可以多选：例如同时接受“论文”和“教程”，表示输出其中任一个都可以。只作用于这条新闻的分类。','muted'));
    const status = el('p',undefined);status.id='vote-status';
    const fs=el('fieldset');fs.append(el('legend','合适的分类标签（多选）'));const choices=el('div',undefined,'choices');
    function updateStatus() {const d=getDraft(c);status.textContent=d.status==='reviewed'?`已评：${d.acceptable_labels.map(name).join(' / ')}`:d.status==='uncertain'?'暂无法判断（不产生有效标签）':'未评（未采用任何人的标签）';}
    for (const [key,value] of Object.entries(material.labels)) {
      const lab=el('label',undefined,'choice'), cb=el('input');cb.type='checkbox';cb.value=key;cb.name='category';cb.checked=getDraft(c).acceptable_labels.includes(key);
      cb.onchange=()=>{const selected=[...choices.querySelectorAll('input:checked')].map(x=>x.value);drafts[c.case_id]={...getDraft(c),acceptable_labels:selected,status:selected.length?'reviewed':'pending'};save();updateStatus();};lab.append(cb,el('span',value));choices.append(lab);
    }
    fs.append(choices);vote.append(status,fs);updateStatus();
    const rl=el('label','你的理由（可选）');rl.htmlFor='human-reason';const reason=el('textarea');reason.id='human-reason';reason.placeholder='为什么这些分类合适？是否存在信息缺失或边界问题？';reason.value=getDraft(c).reason;
    reason.oninput=()=>{drafts[c.case_id]={...getDraft(c),reason:reason.value};save();};vote.append(rl,reason);
    const controls=el('div',undefined,'actions');
    for(const [text,state] of [['暂无法判断','uncertain'],['恢复为未评','pending']]) {const b=el('button',text);b.onclick=()=>{drafts[c.case_id]={...getDraft(c),status:state,acceptable_labels:[]};save();openCase(c.case_id);};controls.append(b);}
    vote.append(controls);main.append(vote);
    const pager=el('div',undefined,'pager');const index=material.cases.indexOf(c);
    for(const [text,delta] of [['← 上一道',-1],['下一道 →',1]]) {const b=el('button',text);b.disabled=!material.cases[index+delta];b.onclick=()=>{openCase(material.cases[index+delta].case_id);window.scrollTo({top:0,behavior:'instant'});};pager.append(b);}main.append(pager);
  }
  async function download() {
    await save();
    const blob=new Blob([JSON.stringify(ballot(),null,2)+'\n'],{type:'application/json'}),url=URL.createObjectURL(blob),a=el('a');a.href=url;a.download='category-feedback.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);notice('已下载当前页全部反馈。'+(saveError?'注意未合并其它页面：'+saveError:'包含未评状态，下载不等于已入库。'));
  }
  async function start() {
    const [mr,ir]=await Promise.all([fetch('material.json'),fetch('identity.json')]);
    if(!mr.ok||!ir.ok) throw new Error('材料文件无法读取');material=await mr.json();identity=await ir.json();
    storageKey='category-review:'+identity.material_sha256;
    let restored=false, restoreError='';
    try {const raw=localStorage.getItem(storageKey);if(raw){const b=JSON.parse(raw);validateBallot(b);drafts=Object.fromEntries(b.judgments.map(j=>[j.case_id,j]));baseline=structuredClone(drafts);restored=true;}} catch(e){restoreError='草稿未自动恢复：'+e.message;}
    $('search').oninput=renderList;$('filter').onchange=renderList;
    $('copy').disabled=false;$('download').disabled=false;
    $('copy').onclick=async()=>{await save();const text=JSON.stringify(ballot(),null,2);try{await navigator.clipboard.writeText(text);notice('已复制当前页全部反馈。'+(saveError?'注意未合并其它页面：'+saveError:'粘贴到对话即可一次交回；当前页面不会自行写入 Git。'));}catch{$('export-text').value=text;$('export-dialog').showModal();$('export-text').focus();$('export-text').select();}};
    $('download').onclick=download;$('close-export').onclick=()=>$('export-dialog').close();
    $('restore').onchange=async e=>{try{const file=e.target.files[0];if(!file)return;const b=JSON.parse(await file.text());validateBallot(b);if(Object.values(drafts).some(j=>j.status!=='pending'||j.reason)&&!window.confirm('用导入文件替换本页草稿？请先导出需要保留的草稿。其它页面同题冲突不会被覆盖。'))return;await saveQueue;drafts=Object.fromEntries(b.judgments.map(j=>[j.case_id,j]));await save();openCase(current);notice(saveError?'已加载到当前页，但草稿未保存：'+saveError:'反馈草稿已恢复，尚未写入仓库。');}catch(err){notice('未导入：'+err.message);}finally{e.target.value='';}};
    window.onhashchange=()=>openCase(location.hash.slice(1),false);
    openCase(location.hash.slice(1),false);
    notice(restoreError||`${material.cases.length} 道历史分歧（最新全量仍不一致 ${material.cases.filter(differs).length} 道）。${restored?'已恢复本浏览器草稿。':'尚未给你预选任何标签。'}完成后复制全部反馈或下载 JSON 一次交回。`);
  }
  start().catch(e=>notice('加载失败：'+e.message));
})();
