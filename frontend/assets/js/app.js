/* 应用入口：哈希路由、主题切换、导航折叠、顶栏状态。 */
(function () {
  const { createApp } = Vue;

  const ICONS = {
    workbench: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"><rect x="3" y="3" width="7" height="9" rx="1.5"/><rect x="14" y="3" width="7" height="5" rx="1.5"/><rect x="14" y="12" width="7" height="9" rx="1.5"/><rect x="3" y="16" width="7" height="5" rx="1.5"/></svg>',
    profile: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"><rect x="3" y="5" width="18" height="14" rx="2"/><circle cx="9" cy="11" r="2.2"/><path d="M6 16c.6-1.4 1.7-2 3-2s2.4.6 3 2M14.5 10h4M14.5 13.5h4"/></svg>',
    account: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"><path d="M5 19a9 9 0 1 1 14 0"/><path d="M12 15l4-4"/><circle cx="12" cy="15" r="1.2"/></svg>',
    radar: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"><circle cx="12" cy="12" r="8.5"/><circle cx="12" cy="12" r="4.5"/><circle cx="12" cy="12" r="1" fill="currentColor"/><path d="M12 12L18 6"/></svg>',
    extraction: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3l8 4.5-8 4.5-8-4.5L12 3z"/><path d="M4 12l8 4.5 8-4.5"/><path d="M4 16.5L12 21l8-4.5"/></svg>',
    analysis: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"><circle cx="6" cy="6" r="2.5"/><circle cx="6" cy="18" r="2.5"/><circle cx="18" cy="12" r="2.5"/><path d="M8.2 7.2l7.3 3.6M8.2 16.8l7.3-3.6"/></svg>',
    creation: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M14 5l5 5L8 21H3v-5L14 5z"/><path d="M12 7l5 5"/></svg>',
    plan: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"><rect x="3" y="5" width="18" height="16" rx="2"/><path d="M3 9h18M8 3v4M16 3v4M8 14h3M8 17h6"/></svg>',
    retro: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"><path d="M4 19V5M4 19h16"/><path d="M8 15l3-4 3 2 4-6"/></svg>',
    growth: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M3 17l6-6 4 4 7-8"/><path d="M14 7h6v6"/></svg>',
    packages: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3l8 4v10l-8 4-8-4V7l8-4z"/><path d="M4 7l8 4 8-4M12 11v10"/></svg>',
    settings: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"><circle cx="12" cy="12" r="3"/><path d="M12 2v3M12 19v3M2 12h3M19 12h3M4.9 4.9l2.1 2.1M17 17l2.1 2.1M19.1 4.9L17 7M7 17l-2.1 2.1"/></svg>',
  };

  const PAGE_COMPONENTS = {
    workbench: 'PageWorkbench', profile: 'PageProfile', account: 'PageAccount',
    radar: 'PageRadar', extraction: 'PageExtraction', analysis: 'PageAnalysis',
    creation: 'PageCreation', plan: 'PagePlan', retro: 'PageRetro',
    growth: 'PageGrowth', packages: 'PagePackages', settings: 'PageSettings',
  };

  const app = createApp({
    data() {
      return {
        store, icons: ICONS,
        collapsed: { 基础: true, 洞察: true, 创作: true, 复盘: true },
      };
    },
    computed: {
      page() { return store.page; },
      pageTitle() { return store.pageTitles[store.page] || ''; },
      theme() { return store.theme; },
      runtime() { return store.runtime; },
      status() { return store.status; },
      usage() { return store.usage; },
      avatarChar() { return store.avatarChar; },
      navGroups() { return store.navGroups; },
      currentPageComponent() { return PAGE_COMPONENTS[store.page] || 'PageWorkbench'; },
      dateLabel() {
        const d = new Date();
        const wk = ['周日', '周一', '周二', '周三', '周四', '周五', '周六'][d.getDay()];
        return `${d.getMonth() + 1}月${d.getDate()}日 ${wk}`;
      },
    },
    methods: {
      go(p) {
        store.page = p;
        location.hash = '#/' + p;
        // 导航默认只展开当前工作组
        const g = store.navGroups.find((g) => g.items.some((i) => i.page === p));
        Object.keys(this.collapsed).forEach((k) => { this.collapsed[k] = true; });
        if (g) this.collapsed[g.name] = false;
        document.querySelector('.content') && (document.querySelector('.content').scrollTop = 0);
      },
      toggleTheme() {
        store.theme = store.theme === 'light' ? 'dark' : 'light';
        applyTheme(store.theme);
        api.put('/api/settings', { section: 'appearance', values: { theme: store.theme } }).catch(() => {});
      },
    },
    async mounted() {
      window.__app = this;
      const h = (location.hash || '').replace('#/', '');
      this.go(PAGE_COMPONENTS[h] ? h : 'workbench');
      await refreshTopbar();
      setInterval(refreshTopbar, 15000);
      window.addEventListener('hashchange', () => {
        const p = (location.hash || '').replace('#/', '');
        if (PAGE_COMPONENTS[p] && p !== store.page) this.go(p);
      });
      document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') document.querySelectorAll('.modal.open').forEach((m) => m.classList.remove('open'));
      });
    },
  });

  // 全局 mixin：让所有组件模板都能访问 store / bus / toast 等
  app.mixin({
    data() { return { store, bus }; },
    methods: {
      toast: (m, t) => window.toast(m, t),
      handleError: (e) => window.handleError(e),
      refreshTopbar: () => window.refreshTopbar(),
    },
  });

  // 注册页面组件
  app.component('PageWorkbench', PageWorkbench);
  app.component('PageProfile', PageProfile);
  app.component('PageAccount', PageAccount);
  app.component('PageRadar', PageRadar);
  app.component('PageExtraction', PageExtraction);
  app.component('PageAnalysis', PageAnalysis);
  app.component('PageCreation', PageCreation);
  app.component('PagePlan', PagePlan);
  app.component('PageRetro', PageRetro);
  app.component('PageGrowth', PageGrowth);
  app.component('PagePackages', PagePackages);
  app.component('PageSettings', PageSettings);
  // 注册全局弹层
  app.component('PlanModal', PlanModal);
  app.component('ErrorModal', ErrorModal);
  app.component('TraceModal', TraceModal);
  app.component('RuleModal', RuleModal);
  app.component('SwitchModal', SwitchModal);
  app.component('PublishModal', PublishModal);

  try {
    app.mount('#app');
  } catch (error) {
    console.error('Jianzhou UI failed to mount', error);
    const root = document.getElementById('app');
    if (root) {
      root.removeAttribute('v-cloak');
      root.innerHTML = `
        <main style="max-width:720px;margin:12vh auto;padding:28px;font-family:system-ui;color:#272622">
          <h1 style="font-size:24px;margin:0 0 12px">界面加载失败</h1>
          <p style="line-height:1.7;color:#6b6962">本地服务已经启动，但前端没有成功挂载。请关闭窗口后重新启动；如果问题仍然存在，请到系统日志中查看错误。</p>
          <p style="font-size:13px;color:#8a877f">日志位置：%LOCALAPPDATA%/JianzhouMediaWorkbench/logs/app.log</p>
        </main>`;
    }
  }
})();
