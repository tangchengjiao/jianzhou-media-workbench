/* 全局状态：Vue.reactive 集中 store + 全局事件总线。 */
(function () {
  const { reactive } = Vue;

  window.store = reactive({
    page: 'workbench',
    theme: localStorage.getItem('jz-theme') || 'light',
    runtime: { demo_mode: true },
    status: {},
    usage: null,
    avatarChar: '舟',
    navGroups: [
      { name: '基础', items: [{ page: 'profile', label: 'IP档案' }, { page: 'account', label: '账号运营' }] },
      { name: '洞察', items: [{ page: 'radar', label: '内容雷达' }, { page: 'extraction', label: '内容提取' }, { page: 'analysis', label: '内容拆解' }] },
      { name: '创作', items: [{ page: 'creation', label: '创作工坊' }, { page: 'plan', label: '内容计划' }] },
      { name: '复盘', items: [{ page: 'retro', label: '数据复盘' }, { page: 'growth', label: '成长记录' }] },
    ],
    pageTitles: {
      workbench: '工作总台', profile: 'IP档案', account: '账号运营', radar: '内容雷达',
      extraction: '内容提取', analysis: '内容拆解', creation: '创作工坊', plan: '内容计划',
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
    const t = document.createElement('div');
    t.className = 'toast ' + (type || 'ok');
    t.textContent = msg;
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
