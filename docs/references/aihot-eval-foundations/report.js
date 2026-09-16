'use strict';
(() => {
  const objects = window.EVAL_REPORT?.objects;
  const detail = document.getElementById('object-detail');
  if (!Array.isArray(objects) || objects.length !== 5) {
    detail.textContent = '逐对象报告数据未能加载；请查阅同目录 objects-and-metrics.md。';
    return;
  }
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const badge = value => `<span class="status ${/尚未|部分|拟|待/.test(value) ? 'pending' : ''}">${esc(value)}</span>`;
  const table = (heads, rows) => `<div class="table-wrap"><table><thead><tr>${heads.map(h => `<th>${esc(h)}</th>`).join('')}</tr></thead><tbody>${rows.map(row => `<tr>${row.map(c => `<td>${esc(c)}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`;
  const list = (rows, cls='next-list') => `<ul class="${cls}">${rows.map(v=>`<li>${esc(v)}</li>`).join('')}</ul>`;
  let selected = 'O1';
  const highlight = id => {
    const object = objects.find(o => o.id === id);
    document.querySelectorAll('[data-node]').forEach(button => button.classList.toggle('path-active', object.nodes.includes(button.dataset.node)));
  };
  function render(id) {
    const o = objects.find(obj => obj.id === id);
    if (!o) return;
    selected = id;
    document.querySelectorAll('#object-tabs button').forEach(b => b.setAttribute('aria-pressed', String(b.dataset.object === id)));
    document.querySelectorAll('.sidebar a').forEach(a => a.classList.toggle('current', a.hash === `#${id}`));
    highlight(id);
    detail.innerHTML = `<article><div class="object-header" id="${esc(o.id)}"><div class="object-meta">${esc(o.id)} · ${esc(o.kind)} · ${esc(o.dimensions.join(' / '))}</div><h3>${esc(o.title)}</h3><p>${esc(o.subtitle)}</p></div>
      <div class="two-columns"><div><h4>输入</h4><p>${esc(o.input)}</p></div><div><h4>输出</h4><p>${esc(o.output)}</p></div></div>
      <p><strong>对象边界：</strong>${esc(o.boundary)}</p><p><strong>覆盖：</strong>${esc(o.coverage)}</p><p class="callout"><strong>解释边界：</strong>${esc(o.limitations)}</p>
      <nav class="layer-nav" aria-label="${esc(id)} 三层定位"><a href="#${id}-L1">L1 计算逻辑</a><a href="#${id}-L2">L2 数据资产</a><a href="#${id}-L3">L3 治理</a></nav>
      <div class="layer" id="${id}-L1"><div class="layer-heading"><span>L1</span><h3>计算逻辑 · 五个槽位</h3></div>
      <div class="flow" aria-label="本对象的拟议评测流程"><span>原始题目 + 隔离的参照</span><b>→</b><span>${esc(id)} 输出 / 失败记录</span><b>→</b><span>${id === 'O3' || id === 'O5' ? '确定性比较 + 语义判官（未设计）' : '确定性比较器（待实现）'}</span><b>→</b><span>逐维指标 + 覆盖 / 失败</span></div>
      ${o.l1.map((slot,i) => `<div class="slot"><div class="slot-head"><h4>${esc(slot.name)}</h4>${badge(slot.status)}</div><p>${esc(slot.summary)}</p>${i > 1 && slot.rows?.length ? table(['字段 / 要素','角色 / 类型','含义、来源与用途'],slot.rows) : ''}</div>`).join('')}
      <h3>从原始数据到这个对象的题集</h3><p><strong>题目单位：</strong>${esc(o.question.unit)}</p><ol class="question-steps">${o.question.steps.map(s=>`<li>${esc(s)}</li>`).join('')}</ol>
      <p class="small muted">${esc(o.question.count)} · 定义依据：${esc(o.question.source)}</p>
      ${table(['题目要素','类型 / 角色','来源与含义'],o.question.fields)}
      <details><summary>查看一题的概念示意（非真实样本、非最终 schema）</summary><pre>${esc(o.question.example)}</pre></details>
      <h3>自动化指标：怎么算，不能说明什么</h3><div class="metrics">${o.metrics.map(m=>`<article class="metric"><div class="slot-head"><h4>${esc(m.name)}</h4>${badge(m.role)}</div><dl><dt>消费输入</dt><dd>${esc(m.inputs)}</dd><dt>计算 / 比较方法</dt><dd>${esc(m.calculation)}</dd><dt>方向与分母</dt><dd>${esc(m.direction)}；${esc(m.denominator)}</dd><dt>解释限制</dt><dd>${esc(m.limit)}</dd></dl></article>`).join('')}</div></div>
      <div class="layer" id="${id}-L2"><div class="layer-heading"><span>L2</span><h3>数据资产 · 七类留存</h3></div><p>下表是本对象的资产设计，不是已经生成的运行文件或成绩。</p>${table(['资产类别','设计状态','拟保留内容','尚缺什么'],o.l2.map(a=>[a.name,a.status,a.plan,a.gap]))}</div>
      <div class="layer" id="${id}-L3"><div class="layer-heading"><span>L3</span><h3>治理 · 五个主题</h3></div><p>这些原则约束本对象的读数与归因；没有把原则存在写成自动门禁已经生效。</p>${table(['治理主题','设计状态','本对象的约束','尚缺什么'],o.l3.map(a=>[a.name,a.status,a.plan,a.gap]))}</div>
      <h3>这个对象下一步仍需设计</h3>${list(o.next)}
      <details><summary>定义依据与追溯</summary><ul class="evidence-list">${o.evidence.map(e=>`<li><a href="${esc(e.href)}">${esc(e.label)}</a> — ${esc(e.note)}</li>`).join('')}</ul></details></article>`;
  }
  const tabs = document.getElementById('object-tabs');
  tabs.innerHTML = objects.map(o=>`<button type="button" data-object="${esc(o.id)}" aria-pressed="false">${esc(o.id)} · ${esc(o.title)}</button>`).join('');
  document.querySelectorAll('[data-object]').forEach(b => {
    b.addEventListener('click', () => { if(location.hash === `#${b.dataset.object}`){render(b.dataset.object);document.getElementById(b.dataset.object).scrollIntoView();}else{location.hash = b.dataset.object;} });
    b.addEventListener('pointerenter', () => highlight(b.dataset.object));
    b.addEventListener('pointerleave', () => highlight(selected));
    b.addEventListener('focus', () => highlight(b.dataset.object));
    b.addEventListener('blur', () => highlight(selected));
  });
  function navigate() {
    const hash = location.hash.slice(1);
    const id = hash.split('-')[0];
    if(objects.some(o=>o.id === id)) {
      if(selected !== id || !detail.children.length) render(id);
      requestAnimationFrame(()=>document.getElementById(hash)?.scrollIntoView());
    }
  }
  render('O1');
  window.addEventListener('hashchange', navigate);
  navigate();
})();
