'use strict';
(() => {
  const objects = window.EVAL_REPORT?.objects;
  const detail = document.getElementById('object-detail');
  if (!Array.isArray(objects) || objects.length !== 4) {
    detail.textContent = '逐对象报告数据未能加载；请查阅同目录 objects-and-metrics.md。';
    return;
  }
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const table = (heads, rows) => `<div class="table-wrap"><table><thead><tr>${heads.map(h => `<th>${esc(h)}</th>`).join('')}</tr></thead><tbody>${rows.map(row => `<tr>${row.map(c => `<td>${esc(c)}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`;
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
    detail.innerHTML = `<article>
      <div class="object-header" id="${esc(id)}"><div class="object-meta">${esc(id)} · 精简设计</div><h3>${esc(o.title)}</h3><p>${esc(o.subtitle)}</p></div>
      <nav class="layer-nav" aria-label="${esc(id)} 三层定位"><a href="#${id}-L1">L1 计算逻辑</a><a href="#${id}-L2">L2 数据资产</a><a href="#${id}-L3">L3 最小约束</a></nav>
      <div class="layer" id="${id}-L1"><div class="layer-heading"><span>L1</span><h3>计算逻辑</h3></div>
      <div class="slot"><h4>① 优化对象与边界</h4>${table(['要素','定义'],[['输入',o.input],['AIHOT 参考',o.reference],['输出',o.output]])}<p>${esc(o.boundary)}</p></div>
      <div class="slot"><h4>② 从原始数据建立题集</h4>${table(['要素','建题方法'],o.questions)}
      ${o.fields ? table(['独立字段题集','选样与参考','指标 / 判断目标','方式'],o.fields) : ''}
      <details><summary>一题的概念示意（不是实际样本或最终格式）</summary><pre>${esc(JSON.stringify(o.example,null,2))}</pre></details></div>
      <div class="slot"><h4>③ 判官</h4><p>${esc(o.judge)}</p></div>
      <div class="slot"><h4>④ 自动化指标</h4>${o.metrics.length ? table(['指标','计算','含义'],o.metrics) : '<p>分类与标签用上方字段表中的确定性指标；文本指标须随判官方案确定，不另堆相似度代理指标。</p>'}<p class="callout">${esc(o.caveat)}</p></div>
      <div class="slot"><h4>⑤ 判官校验</h4><p>${esc(o.calibration)}</p></div></div>
      <div class="layer" id="${id}-L2"><div class="layer-heading"><span>L2</span><h3>数据资产</h3></div><p>${esc(o.l2)}</p></div>
      <div class="layer" id="${id}-L3"><div class="layer-heading"><span>L3</span><h3>最小约束</h3></div><p>${esc(o.l3)}</p></div>
      <p class="notice"><strong>尚未完成：</strong>${esc(o.pending)}</p><p><a href="#shared">四个对象共用哪些记录与规则？</a></p></article>`;
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
