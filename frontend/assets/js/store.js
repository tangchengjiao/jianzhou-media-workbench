/* 全局状态：Vue.reactive 集中 store + 全局事件总线。 */
(function () {
  const { reactive } = Vue;

  window.store = reactive({
    page: 'workbench',
    theme: localStorage.getItem('jz-theme') || 'light',
    runtime: { demo_mode: true },
    status: {},
    usage: null,
    pageError: null,
    avatarChar: '舟',
    insightSelection: { sourceId: '', analysisId: '' },
    navGroups: [
      { name: '基础', items: [{ page: 'profile', label: 'IP档案' }, { page: 'account', label: '账号运营' }] },
      { name: '洞察', items: [{ page: 'radar', label: '内容雷达' }, { page: 'extraction', label: '内容提取' }, { page: 'analysis', label: '内容拆解' }] },
      { name: '创作', items: [{ page: 'creation', label: '创作工坊' }, { page: 'visuals', label: '视觉工厂' }] },
      { name: '运营', items: [{ page: 'plan', label: '内容计划' }, { page: 'channels', label: '渠道运营' }] },
      { name: '复盘', items: [{ page: 'retro', label: '数据复盘' }, { page: 'growth', label: '成长记录' }] },
    ],
    pageTitles: {
      workbench: '工作总台', profile: 'IP档案', account: '账号运营', radar: '内容雷达',
      extraction: '内容提取', analysis: '内容拆解', creation: '创作工坊', visuals: '视觉工厂', plan: '内容计划', channels: '渠道运营',
      retro: '数据复盘', growth: '成长记录', packages: '资料中心', settings: '系统设置',
    },
  });

  /* 简单事件总线：跨组件打开弹层 */
  const listeners = {};
  window.bus = {
    on(evt, fn) { (listeners[evt] = listeners[evt] || []).push(fn); },
    emit(evt, payload) { (listeners[evt] || []).forEach((fn) => fn(payload)); },
  };

  /* Toast */
  window.toast = function (msg, type) {
    const root = document.getElementById('toastRoot');
    if (!root) return;
    const message = String(msg || '操作完成');
    const duplicate = Array.from(root.children).find((item) => item.dataset.message === message);
    if (duplicate) duplicate.remove();
    while (root.children.length >= 4) root.firstElementChild.remove();
    const t = document.createElement('div');
    t.className = 'toast ' + (type || 'ok');
    t.dataset.message = message;
    t.textContent = message;
    root.appendChild(t);
    setTimeout(() => { t.style.opacity = '0'; t.style.transition = 'opacity .3s'; setTimeout(() => t.remove(), 300); }, 3600);
  };

  /* 全局错误处理：Typed Error → 错误弹层 */
  window.handleError = function (err) {
    if (err && err.code && err.trace_id) {
      bus.emit('open-error', err);
    } else {
      toast((err && err.message) || '未知错误', 'err');
    }
  };

  window.recordPageError = function (error, info) {
    const detail = {
      page: store.page,
      title: store.pageTitles[store.page] || store.page,
      message: String((error && error.message) || error || '页面运行时异常'),
      info: String(info || ''),
      stack: String((error && error.stack) || '').slice(0, 4000),
      occurred_at: new Date().toISOString(),
    };
    store.pageError = detail;
    try {
      const history = JSON.parse(localStorage.getItem('jz-page-errors') || '[]');
      history.unshift(detail);
      localStorage.setItem('jz-page-errors', JSON.stringify(history.slice(0, 20)));
    } catch (_) { /* 本地诊断写入失败不覆盖原始异常 */ }
    return detail;
  };

  /* 主题 */
  window.applyTheme = function (theme) {
    document.documentElement.setAttribute('data-theme', theme);
    localStorage.setItem('jz-theme', theme);
  };
  applyTheme(store.theme);

  /* 状态刷新（顶栏） */
  window.refreshTopbar = async function () {
    try {
      const [status, usage, runtime] = await Promise.all([
        api.get('/api/workbench/status'),
        api.get('/api/usage/summary'),
        api.get('/api/settings/runtime'),
      ]);
      store.status = status;
      store.usage = usage;
      store.runtime = runtime;
      store.avatarChar = status.greeting_name === '陈早茶' ? '茶' : '舟';
    } catch (e) { /* 顶栏失败不打扰 */ }
  };
})();
