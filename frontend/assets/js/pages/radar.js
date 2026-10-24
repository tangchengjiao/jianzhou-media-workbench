/* 内容雷达完整闭环：雷达信号 + 对标账号入口 + 作品监控墙 + 作者分析。 */
(function () {
  const go = (page) => { if (window.__app) window.__app.go(page); };

  window.PageRadar = {
    data() {
      return {
        section: 'signals',
        signals: [],
        benchmarks: [],
        sources: [],
        capabilities: [],
        backgroundJobs: [],
        datasource: { mode: 'demo', configured: true, provider: 'demo_ds' },
        signalFilter: 'all',
        platformFilter: 'all',
        workFilter: 'all',
        selected: null,
        loading: true,
        detailLoading: false,
        scanningAll: false,
        addOpen: false,
        adding: false,
        analyzing: false,
        sourceOpen: false,
        sourceSaving: false,
        sourceProbing: false,
        sourceProbe: null,
        manualOpen: false,
        manualSaving: false,
        manualForm: { category: 'manual', title: '', url: '', platform: '手动录入', note: '' },
        backfillCount: 20,
        pollTimers: {},
        addError: null,
        form: { platform: 'douyin', link: '', scan_count: 30, note: '' },
        sourceForm: { kind: 'tikhub_hot', name: '抖音热榜', platform: 'douyin', frequency_hours: 24, url: '', keywords: '' },
        fallbackPlatforms: [
          { id: 'douyin', label: '抖音', group: '国内主流', id_label: '主页链接 / sec_user_id', placeholder: '粘贴抖音主页或分享链接，也可输入 sec_user_id' },
          { id: 'xhs', label: '小红书', group: '国内主流', id_label: '主页链接 / user_id', placeholder: '粘贴小红书用户主页、分享链接或 user_id' },
          { id: 'channels', label: '视频号', group: '国内主流', id_label: 'finder username / 视频号ID', placeholder: '输入 v2_…@finder 或 sph… 视频号ID' },
          { id: 'bilibili', label: 'B站', group: '国内主流', id_label: '空间链接 / UID', placeholder: '粘贴 space.bilibili.com 主页链接或数字 UID' },
          { id: 'kuaishou', label: '快手', group: '国内主流', id_label: '主页链接 / eid', placeholder: '粘贴快手 profile 主页、分享链接或 eid' },
          { id: 'weibo', label: '微博', group: '国内主流', id_label: '主页链接 / UID', placeholder: '粘贴微博用户主页链接或数字 UID' },
          { id: 'tiktok', label: 'TikTok', group: '海外主流', id_label: '主页链接 / 用户名 / secUid', placeholder: '粘贴 TikTok @用户名主页或输入 secUid' },
        ],
        signalFilters: [
          ['all', '全部'], ['hot', '对标高表现'], ['new', '对标新作'],
          ['platform_hot', '平台热榜'], ['external', '行业数据源'],
          ['kw', '关键词'], ['fb', '用户反馈'], ['revive', '历史起量'],
          ['manual', '手动关注'],
        ],
      };
    },
    computed: {
      filteredSignals() {
        return this.signalFilter === 'all' ? this.signals
          : this.signals.filter((item) => item.category === this.signalFilter);
      },
      activeSignalMeta() {
        const fallback = { id: this.signalFilter, name: '雷达信号', description: '真实同步完成后会显示在这里', cta: '配置数据源' };
        if (this.signalFilter === 'all') return fallback;
        return this.capabilities.find((item) => item.id === this.signalFilter) || fallback;
      },
      filteredBenchmarks() {
        return this.platformFilter === 'all' ? this.benchmarks
          : this.benchmarks.filter((item) => item.platform === this.platformFilter);
      },
      filteredWorks() {
        if (!this.selected) return [];
        let works = (this.selected.works || []).slice();
        if (this.workFilter === 'hot') works = works.filter((item) => item.is_hot);
        if (this.workFilter === 'likes') works.sort((a, b) => b.likes - a.likes);
        if (this.workFilter === 'score') works.sort((a, b) => b.score - a.score);
        return works;
      },
      platformOptions() {
        return (this.datasource.platforms && this.datasource.platforms.length)
          ? this.datasource.platforms : this.fallbackPlatforms;
      },
      selectedPlatform() {
        return this.platformOptions.find((item) => item.id === this.form.platform)
          || this.platformOptions[0];
      },
      platformFilters() {
        const used = new Set(this.benchmarks.map((item) => item.platform));
        return this.platformOptions.filter((item) => used.has(item.id));
      },
      estimatedPages() {
        return Math.max(1, Math.ceil((Number(this.form.scan_count) || 1) / 20));
      },
      activeJobs() {
        return this.backgroundJobs.filter((job) => ['QUEUED', 'RUNNING', 'CANCEL_REQUESTED'].includes(job.status));
      },
    },
    async mounted() {
      await this.loadAll();
      const params = new URLSearchParams(location.search);
      const view = params.get('radar');
      if (view === 'benchmarks') this.section = 'benchmarks';
      if (view === 'add') {
        this.openAdd();
        const platform = params.get('platform');
        const link = params.get('link');
        const count = Number(params.get('scan_count'));
        if (this.platformOptions.some((item) => item.id === platform)) this.form.platform = platform;
        if (link) this.form.link = link;
        if (Number.isInteger(count) && count >= 1 && count <= 500) this.form.scan_count = count;
        ['radar', 'platform', 'link', 'scan_count'].forEach((key) => params.delete(key));
        const query = params.toString();
        history.replaceState({}, '', location.pathname + (query ? '?' + query : '') + location.hash);
      }
      const benchmarkId = params.get('benchmark');
      if (benchmarkId) await this.openBenchmark(benchmarkId);
      this.activeJobs.forEach((job) => this.watchJob(job.id));
    },
    beforeUnmount() { Object.values(this.pollTimers).forEach((timer) => clearTimeout(timer)); },
    methods: {
      go,
      async loadAll() {
        this.loading = true;
        try {
          const [signals, benchmarks, datasource, sources, jobs, capabilities] = await Promise.all([
            api.get('/api/radar/signals'),
            api.get('/api/benchmarks'),
            api.get('/api/datasource/status'),
            api.get('/api/radar/sources'),
            api.get('/api/benchmarks/jobs'),
            api.get('/api/radar/capabilities'),
          ]);
          this.signals = signals;
          this.benchmarks = benchmarks;
          this.datasource = datasource;
          this.sources = sources;
          this.backgroundJobs = jobs;
          this.capabilities = capabilities;
        } catch (error) { handleError(error); }
        this.loading = false;
      },
      openAdd() {
        this.addError = null;
        this.form = { platform: 'douyin', link: '', scan_count: 30, note: '' };
        this.addOpen = true;
        this.$nextTick(() => document.getElementById('benchmarkLinkInput')?.focus());
      },
      async addBenchmark() {
        if (!this.form.link.trim()) {
          this.addError = { code: 'VALIDATION_FAILED', message: '请先粘贴主页链接或账号 ID。' };
          return;
        }
        const count = Number(this.form.scan_count);
        if (!Number.isInteger(count) || count < 1 || count > 500) {
          this.addError = { code: 'VALIDATION_FAILED', message: '抓取条数必须是 1 到 500 之间的整数。' };
          return;
        }
        this.adding = true;
        this.addError = null;
        try {
          const result = await api.post('/api/benchmarks', {
            ...this.form, scan_count: count, link: this.form.link.trim(),
          });
          this.addOpen = false;
          this.section = 'benchmarks';
          toast(result.note);
          this.backgroundJobs.unshift({ id: result.job_id, task_type: 'benchmark_onboard', status: result.status, progress: 0, current_step: '排队中', input_json: { title: '添加对标账号' } });
          this.watchJob(result.job_id, true);
        } catch (error) {
          this.addError = error;
        }
        this.adding = false;
      },
      async openBenchmark(id) {
        this.section = 'benchmarks';
        this.detailLoading = true;
        this.workFilter = 'all';
        try {
          this.selected = await api.get('/api/benchmarks/' + encodeURIComponent(id));
          await api.post('/api/benchmarks/' + encodeURIComponent(id) + '/read');
          const index = this.benchmarks.findIndex((item) => item.id === id);
          if (index >= 0) this.benchmarks[index].has_update = 0;
        } catch (error) { handleError(error); }
        this.detailLoading = false;
      },
      closeDetail() { this.selected = null; },
      async scanBenchmark(item) {
        const id = item.id || item;
        try {
          const result = await api.post('/api/benchmarks/' + encodeURIComponent(id) + '/scan');
          toast(result.note);
          this.backgroundJobs.unshift({ id: result.job_id, task_type: 'benchmark_refresh', status: result.status, progress: 0, current_step: '排队中', input_json: { bm_id: id, title: '刷新最新作品' } });
          this.watchJob(result.job_id);
        } catch (error) {
          handleError(error);
        }
      },
      async backfillBenchmark() {
        if (!this.selected) return;
        const count = Number(this.backfillCount);
        if (!Number.isInteger(count) || count < 1 || count > 200) return toast('续抓数量需为 1–200', 'warn');
        try {
          const result = await api.post('/api/benchmarks/' + encodeURIComponent(this.selected.id) + '/backfill', { count });
          toast(result.note);
          if (result.job_id) {
            this.backgroundJobs.unshift({ id: result.job_id, task_type: 'benchmark_backfill', status: result.status, progress: 0, current_step: '排队中', input_json: { bm_id: this.selected.id, title: '继续抓取历史作品' } });
            this.watchJob(result.job_id);
          }
        } catch (error) { handleError(error); }
      },
      watchJob(jobId, autoOpen) {
        if (!jobId || this.pollTimers[jobId]) return;
        const poll = async () => {
          try {
            const job = await api.get('/api/jobs/' + encodeURIComponent(jobId));
            const index = this.backgroundJobs.findIndex((item) => item.id === jobId);
            if (index >= 0) this.backgroundJobs.splice(index, 1, job); else this.backgroundJobs.unshift(job);
            if (['SUCCEEDED', 'FAILED', 'CANCELLED'].includes(job.status)) {
              delete this.pollTimers[jobId];
              if (job.status === 'SUCCEEDED') {
                toast((job.output_json && job.output_json.note) || '后台任务已完成');
                await this.loadAll();
                const bmId = job.output_json && (job.output_json.id || job.output_json.bm_id);
                if (bmId && store.page === 'radar' && this.section === 'benchmarks' && (autoOpen || (this.selected && this.selected.id === bmId))) await this.openBenchmark(bmId);
              } else if (job.error) {
                handleError(job.error);
              }
              return;
            }
          } catch (error) { delete this.pollTimers[jobId]; return; }
          this.pollTimers[jobId] = setTimeout(poll, 900);
        };
        this.pollTimers[jobId] = setTimeout(poll, 150);
      },
      async saveMonitor() {
        if (!this.selected) return;
        try {
          const result = await api.put('/api/benchmarks/' + encodeURIComponent(this.selected.id) + '/monitor', {
            enabled: !!this.selected.monitor_enabled,
            frequency_hours: Number(this.selected.monitor_frequency_hours) || 24,
          });
          this.selected.next_run_at = result.next_run_at;
          toast(result.note);
        } catch (error) { handleError(error); }
      },
      openSource(kind = 'tikhub_hot') {
        const names = { tikhub_hot: '抖音热榜', external_feed: '行业数据源', keyword_watch: '关键词监听' };
        this.sourceForm = { kind, name: names[kind] || '新数据源', platform: 'douyin', frequency_hours: 24, url: '', keywords: '' };
        this.sourceProbe = null;
        this.sourceOpen = true;
      },
      async probeSource() {
        if (!this.sourceForm.url.trim()) return toast('请先填写网站、RSS 或 API 地址', 'warn');
        this.sourceProbing = true;
        this.sourceProbe = null;
        try {
          this.sourceProbe = await api.post('/api/radar/sources/probe', { url: this.sourceForm.url.trim() });
          this.sourceForm.url = this.sourceProbe.resolved_url;
          if (!this.sourceForm.name || this.sourceForm.name === '行业数据源') this.sourceForm.name = this.sourceProbe.connector === 'aihot' ? 'AI Hot' : '行业订阅';
          toast(`连接成功：识别为 ${this.sourceProbe.source_type}`);
        } catch (error) { handleError(error); }
        this.sourceProbing = false;
      },
      async addSource() {
        this.sourceSaving = true;
        try {
          const config = { limit: 20, keywords: this.sourceForm.keywords.split(/[，,]/).map((x) => x.trim()).filter(Boolean) };
          if (this.sourceForm.kind === 'external_feed') config.url = this.sourceForm.url.trim();
          if (this.sourceProbe) Object.assign(config, this.sourceProbe.config || {});
          const source = await api.post('/api/radar/sources', { ...this.sourceForm, config, platform: ['tikhub_hot','keyword_watch'].includes(this.sourceForm.kind) ? this.sourceForm.platform : null });
          this.sourceOpen = false;
          await this.loadAll();
          toast(source.note);
        } catch (error) { handleError(error); }
        this.sourceSaving = false;
      },
      async syncSource(source) {
        try {
          const result = await api.post('/api/radar/sources/' + encodeURIComponent(source.id) + '/sync');
          toast(result.note);
          this.backgroundJobs.unshift({ id: result.job_id, task_type: 'radar_source_sync', status: result.status, progress: 0, current_step: '排队中', input_json: { title: '同步雷达源' } });
          this.watchJob(result.job_id);
        } catch (error) { handleError(error); }
      },
      signalCount(category) {
        return category === 'all' ? this.signals.length : this.signals.filter((item) => item.category === category).length;
      },
      sourceKind(source) {
        return { tikhub_hot: '平台热榜', external_feed: source.config.connector === 'aihot' ? 'AIHot 连接器' : '行业连接器', keyword_watch: '关键词监听' }[source.kind] || source.kind;
      },
      configureSignalFilter() {
        if (['hot','new'].includes(this.signalFilter)) { this.section = 'benchmarks'; return; }
        if (this.signalFilter === 'platform_hot') return this.openSource('tikhub_hot');
        if (this.signalFilter === 'external') return this.openSource('external_feed');
        if (this.signalFilter === 'kw') return this.openSource('keyword_watch');
        if (['manual','fb'].includes(this.signalFilter)) return this.openManual(this.signalFilter);
        if (this.signalFilter === 'revive') return this.deriveHistorical();
        this.section = 'sources';
      },
      openManual(category = 'manual') {
        this.manualForm = { category, title: '', url: '', platform: '手动录入', note: '' };
        this.manualOpen = true;
      },
      async addManualSignal() {
        if (!this.manualForm.title.trim()) return toast('请填写内容或反馈摘要', 'warn');
        this.manualSaving = true;
        try {
          const result = await api.post('/api/radar/signals/manual', this.manualForm);
          this.manualOpen = false; toast(result.note); await this.loadAll();
          this.signalFilter = this.manualForm.category;
        } catch (error) { handleError(error); }
        this.manualSaving = false;
      },
      async deriveHistorical() {
        try { const result = await api.post('/api/radar/signals/derive-historical-rise'); toast(result.note); await this.loadAll(); this.signalFilter = 'revive'; }
        catch (error) { handleError(error); }
      },
      async removeSource(source) {
        if (!confirm(`删除数据源「${source.name}」？已沉淀的信号和文案不会删除。`)) return;
        try { const result = await api.delete('/api/radar/sources/' + encodeURIComponent(source.id)); toast(result.note, 'warn'); await this.loadAll(); } catch (error) { handleError(error); }
      },
      async scanAll() {
        this.scanningAll = true;
        try {
          const result = await api.post('/api/benchmarks/scan-all');
          toast(result.note);
          if (result.job_id) setTimeout(() => this.loadAll(), 2200);
        } catch (error) { handleError(error); }
        this.scanningAll = false;
      },
      async removeBenchmark(item) {
        const name = item.name || item.uid;
        if (!confirm(`确认删除对标账号「${name}」及本机缓存的作品数据？`)) return;
        try {
          const result = await api.delete('/api/benchmarks/' + encodeURIComponent(item.id));
          toast(result.note, 'warn');
          this.selected = null;
          await this.loadAll();
        } catch (error) { handleError(error); }
      },
      async analyzeAuthor() {
        if (!this.selected) return;
        this.analyzing = true;
        try {
          const result = await api.post('/api/benchmarks/' + encodeURIComponent(this.selected.id) + '/analyze');
          this.selected.analysis = { engine: result.engine, result, created_at: new Date().toISOString() };
          toast(result.cached ? '已读取上次沉淀的作者分析（作品数据未变化）' : '作者分析已完成并追加保存');
        } catch (error) { handleError(error); }
        this.analyzing = false;
      },
      async workSignal(work) {
        return api.post(
          '/api/benchmarks/' + encodeURIComponent(this.selected.id)
          + '/works/' + encodeURIComponent(work.video_id) + '/to-signal',
        );
      },
      async extractWork(work) {
        try {
          const signal = await this.workSignal(work);
          const plan = await api.post('/api/extraction/plans', { signal_id: signal.signal_id });
          bus.emit('open-plan', plan);
        } catch (error) { handleError(error); }
      },
      async topicFromWork(work) {
        try {
          const signal = await this.workSignal(work);
          await api.post('/api/radar/signals/' + signal.signal_id + '/to-topic');
          await api.post('/api/creation/projects', {
            title: work.title.replace(/[《》]/g, ''),
            source_chain: ['对标账号 #' + this.selected.id, '作品 #' + work.video_id],
          });
          toast('已带着对标来源链创建选题候选');
          go('creation');
        } catch (error) { handleError(error); }
      },
      async extractSignal(signal) {
        try {
          const plan = await api.post('/api/extraction/plans', { signal_id: signal.id });
          bus.emit('open-plan', plan);
        } catch (error) { handleError(error); }
      },
      async signalAction(signal, action, noteType) {
        try {
          const result = await api.post(`/api/radar/signals/${signal.id}/${action}`);
          toast(result.note, noteType);
          this.signals = await api.get('/api/radar/signals');
        } catch (error) { handleError(error); }
      },
      async signalToTopic(signal) {
        try {
          await api.post('/api/radar/signals/' + signal.id + '/to-topic');
          await api.post('/api/creation/projects', {
            title: signal.title.replace(/[《》]/g, ''), source_chain: ['雷达 #' + signal.id],
          });
          toast('已创建选题候选并保留来源链');
          go('creation');
        } catch (error) { handleError(error); }
      },
      metricsText(metrics) {
        const m = metrics || {};
        const parts = [];
        if (m.rank) parts.push('热榜 #' + this.num(m.rank));
        if (m.heat) parts.push('热度 ' + this.num(m.heat));
        if (m.plays) parts.push('播放 ' + this.num(m.plays));
        if (m.likes) parts.push('点赞 ' + this.num(m.likes));
        if (m.comments) parts.push('评论 ' + this.num(m.comments));
        if (m.collects) parts.push('收藏 ' + this.num(m.collects));
        return parts.join(' · ');
      },
      num(value) {
        const n = Number(value) || 0;
        if (n >= 100000000) return (n / 100000000).toFixed(1) + '亿';
        if (n >= 10000) return (n / 10000).toFixed(n >= 100000 ? 0 : 1) + '万';
        return String(n);
      },
      date(timestamp) {
        if (!timestamp) return '—';
        return new Date(Number(timestamp) * 1000).toLocaleDateString('zh-CN', { month: 'numeric', day: 'numeric' });
      },
      coverSource(work) {
        return String(work.cover_display_url || work.cover || '');
      },
      coverFailed(work) {
        work.cover_failed = true;
      },
      initial(item) { return (item.name || item.uid || '号').slice(0, 1); },
      platformName(platform) {
        const item = this.platformOptions.find((entry) => entry.id === platform);
        return item ? item.label : platform;
      },
      canExtract(platform) { return store.runtime.demo_mode || platform === 'douyin'; },
      syncLabel(status) {
        return { ready: '已同步', syncing: '同步中', error: '同步失败', idle: '待同步' }[status] || '待同步';
      },
      signalBadge(signal) {
        return signal.category === 'hot' ? 'b-pri'
          : signal.category === 'fb' ? 'b-warn'
            : signal.category === 'manual' ? 'b-neu' : 'b-info';
      },
    },
    template: `
    <section class="radar-page">
      <div class="page-head radar-head">
        <div>
          <div class="page-title">内容雷达</div>
          <div class="page-q">先盯住谁，再判断他的哪些内容值得研究。</div>
        </div>
        <div class="radar-head-actions">
          <button class="btn" v-if="benchmarks.length" :disabled="scanningAll" @click="scanAll">{{ scanningAll ? '正在创建巡检…' : '刷新全部对标' }}</button>
          <button class="btn btn-pri" id="addBenchmarkBtn" @click="openAdd">+ 添加对标账号</button>
        </div>
      </div>

      <div class="radar-modebar" role="tablist" aria-label="内容雷达视图">
        <button role="tab" :aria-selected="section==='signals'" :class="{active:section==='signals'}" @click="section='signals';selected=null">雷达信号 <b>{{ signals.length }}</b></button>
        <button role="tab" :aria-selected="section==='benchmarks'" :class="{active:section==='benchmarks'}" @click="section='benchmarks'">对标账号 <b>{{ benchmarks.length }}</b></button>
        <button role="tab" :aria-selected="section==='sources'" :class="{active:section==='sources'}" @click="section='sources';selected=null">数据源与监听规则 <b>{{ sources.length }}</b></button>
        <span class="radar-provider">数据源：{{ datasource.provider }} · {{ datasource.mode==='demo' ? '演示模式' : (datasource.configured ? '真实模式已配置' : '真实模式待配置') }}</span>
      </div>

      <div class="callout c-warn" v-if="store.runtime.demo_mode">
        <span>演示</span><span>当前账号、作品和指标由 <b>demo_ds</b> 生成，仅用于验证交互链路。关闭演示模式并配置数据源后，同一个入口才会拉取公开真实数据。</span>
      </div>
      <div class="callout c-warn" v-else-if="!datasource.configured">
        <span>配置</span><span>真实模式已开启，但数据源尚未配置。系统不会生成替代作品。<button class="text-btn" @click="go('settings')">前往系统设置配置 API Key</button></span>
      </div>
      <div class="radar-job-strip" v-if="activeJobs.length" role="status" aria-live="polite">
        <b>后台任务 {{ activeJobs.length }} 个</b>
        <span v-for="job in activeJobs.slice(0,3)" :key="job.id">{{ (job.input_json && job.input_json.title) || job.task_type }} · {{ job.current_step || '排队中' }} · {{ job.progress || 0 }}%</span>
        <button class="btn btn-sm" @click="section='benchmarks'">查看任务相关页面</button>
      </div>

      <template v-if="section==='signals'">
        <div class="radar-toolbar">
          <div class="chips" role="tablist" aria-label="雷达信号分类"><button class="chip" role="tab" v-for="filter in signalFilters" :key="filter[0]" :aria-selected="signalFilter===filter[0]" :class="{active:signalFilter===filter[0]}" @click="signalFilter=filter[0]">{{ filter[1] }} <b>{{ signalCount(filter[0]) }}</b></button></div>
          <button class="btn" @click="configureSignalFilter">{{ activeSignalMeta.cta }} →</button>
        </div>
        <div class="empty radar-empty" v-if="!loading && !filteredSignals.length">
          <b>{{ signalFilter==='all' ? '还没有雷达信号' : activeSignalMeta.name + '尚无结果' }}</b><span>{{ activeSignalMeta.description }}</span>
          <div><button class="btn btn-pri" @click="configureSignalFilter">{{ activeSignalMeta.cta }}</button><button class="btn" v-if="signalFilter==='all'" @click="section='sources'">管理数据源与规则</button></div>
        </div>
        <div class="grid g2" v-else>
          <article class="signal-card" v-for="signal in filteredSignals" :key="signal.id" :class="{ignored:signal.status==='ignored'}">
            <div class="sc-top">
              <span class="badge" :class="signalBadge(signal)">{{ signal.source_type }}</span>
              <span>{{ signal.platform }}</span><span class="sc-author">{{ signal.author }}</span>
              <span>{{ signal.published_at }} 发布</span>
              <span class="badge" :class="signal.status==='new'?'b-info':'b-neu'">{{ signal.status_label }}</span>
            </div>
            <div class="sc-title">{{ signal.title }}</div>
            <div class="sc-metrics">{{ metricsText(signal.metrics) }}</div>
            <div class="sc-rows"><span v-if="signal.rel_perf">相对表现 <b>{{ signal.rel_perf }}</b></span><span>相关度 {{ signal.relevance }}%</span><span>{{ signal.timeliness }}</span></div>
            <div class="sc-foot">
              <span class="sc-src">{{ signal.source }} · #{{ signal.id }}</span>
              <div class="sc-actions">
                <button class="btn btn-sm btn-pri" @click="extractSignal(signal)">提取文案</button>
                <button class="btn btn-sm" @click="signalToTopic(signal)">转为选题</button>
                <button class="btn btn-sm" v-if="signal.status==='new'" @click="signalAction(signal,'observe')">加入观察</button>
                <a class="btn btn-sm" v-if="signal.url" :href="signal.url" target="_blank" rel="noreferrer">打开原作</a>
                <button class="btn btn-sm btn-ghost" v-if="signal.status!=='ignored'" @click="signalAction(signal,'ignore','warn')">忽略</button>
              </div>
            </div>
          </article>
        </div>
      </template>

      <template v-else-if="section==='benchmarks'">
        <div v-if="detailLoading" class="loading">正在读取账号作品…</div>
        <template v-else-if="selected">
          <div class="benchmark-detail-bar">
            <button class="btn" @click="closeDetail">← 返回对标账号</button>
            <div class="detail-actions">
              <a class="btn" v-if="selected.link" :href="selected.link" target="_blank" rel="noreferrer">打开原主页 ↗</a>
              <button class="btn" @click="scanBenchmark(selected)">刷新最新</button>
              <label class="backfill-control"><input class="input" type="number" min="1" max="200" v-model.number="backfillCount" aria-label="继续抓取历史作品条数"><button class="btn" :disabled="!selected.has_more" @click="backfillBenchmark">{{ selected.has_more ? '继续抓取历史' : '已抓取全部' }}</button></label>
              <button class="btn btn-pri" :disabled="analyzing" @click="analyzeAuthor">{{ analyzing ? '正在分析…' : '分析这个作者' }}</button>
            </div>
          </div>
          <article class="benchmark-profile">
            <div class="benchmark-avatar"><img v-if="typeof selected.avatar==='string' && selected.avatar" :src="selected.avatar" :alt="selected.name + ' 的头像'"><span v-else>{{ initial(selected) }}</span></div>
            <div class="benchmark-identity"><div><span class="badge b-info">{{ selected.platform_label }}</span><span class="benchmark-name">{{ selected.name || selected.uid }}</span><span class="badge" :class="selected.sync_status==='error'?'b-err':'b-ok'">{{ syncLabel(selected.sync_status) }}</span></div><p>{{ selected.bio || '该账号暂无公开简介' }}</p><small class="mono">{{ selected.uid }}</small></div>
            <div class="benchmark-kpis"><span><b>{{ num(selected.fans) }}</b>粉丝</span><span><b>{{ num(selected.likes_count) }}</b>获赞</span><span><b>{{ selected.works_count }}</b>已监控</span><span><b>{{ selected.hot_count }}</b>高表现</span><span><b>{{ selected.post_gap_days || '—' }}</b>天/条</span></div>
          </article>
          <div class="benchmark-cache-state">
            <span>已缓存 <b>{{ selected.works_count }}</b> 条</span><span>平台公开作品 {{ selected.video_count || '未知' }}</span><span>最早抓取至 {{ selected.oldest_fetched_at ? new Date(selected.oldest_fetched_at).toLocaleDateString('zh-CN') : '尚未记录' }}</span><span>{{ selected.has_more ? '仍有历史作品可继续抓取' : '已到平台返回末尾' }}</span>
          </div>
          <div class="benchmark-monitor-config">
            <label><input type="checkbox" v-model="selected.monitor_enabled"> 自动监控最新作品</label>
            <label>每 <input class="input" type="number" min="1" max="168" v-model.number="selected.monitor_frequency_hours"> 小时</label>
            <button class="btn btn-sm" @click="saveMonitor">保存监控频率</button>
            <span>下次运行：{{ selected.next_run_at ? new Date(selected.next_run_at).toLocaleString('zh-CN') : '未安排' }}</span>
          </div>
          <div class="callout c-warn" v-if="selected.sync_status==='error'"><span>失败</span><span><b>{{ selected.last_error_code }}</b> · {{ selected.last_error_message }}。没有写入替代作品；修复配置后点击“刷新作品”即可从失败点重试。</span></div>

          <div class="radar-toolbar">
            <div><b>作品监控墙</b><span class="mini-note"> · 同账号中位综合分 ×2 判定高表现</span></div>
            <div class="chips"><button class="chip" :class="{active:workFilter==='all'}" @click="workFilter='all'">全部 {{ selected.works_count }}</button><button class="chip" :class="{active:workFilter==='hot'}" @click="workFilter='hot'">只看高表现 {{ selected.hot_count }}</button><button class="chip" :class="{active:workFilter==='likes'}" @click="workFilter='likes'">按点赞</button><button class="chip" :class="{active:workFilter==='score'}" @click="workFilter='score'">按综合分</button></div>
          </div>
          <div class="empty radar-empty" v-if="!filteredWorks.length"><b>还没有作品数据</b><span>首次同步失败或账号没有公开作品。</span><button class="btn" @click="scanBenchmark(selected)">立即重试同步</button></div>
          <div class="benchmark-work-wall" :class="'platform-' + selected.platform" v-else>
            <article class="benchmark-work" v-for="work in filteredWorks" :key="work.video_id">
              <a class="benchmark-cover" :class="'platform-' + selected.platform" :href="work.url || selected.link" target="_blank" rel="noreferrer" :aria-label="'打开原作品：' + (work.title || '无标题作品')">
                <img v-if="coverSource(work) && !work.cover_failed" :src="coverSource(work)" alt="" aria-hidden="true" loading="lazy" @error="coverFailed(work)">
                <span class="benchmark-cover-fallback" v-else><small>封面暂不可用</small><b>{{ (work.title || '无标题作品').slice(0,28) }}</b></span>
                <i v-if="work.is_hot">高表现</i><em>{{ num(work.likes) }} 赞</em>
              </a>
              <div class="benchmark-work-body"><b :title="work.title">{{ work.title || '无标题作品' }}</b><p>{{ date(work.create_time) }} · 评 {{ num(work.comments) }} · 藏 {{ num(work.collects) }} · 转 {{ num(work.shares) }}</p><p>综合分 {{ num(work.score) }}</p><div><button class="btn btn-sm btn-pri" v-if="canExtract(selected.platform)" @click="extractWork(work)">提取文案</button><button class="btn btn-sm" @click="topicFromWork(work)">转为选题</button><a class="btn btn-sm" :href="work.url || selected.link" target="_blank" rel="noreferrer">原作 ↗</a></div></div>
            </article>
          </div>

          <article class="card benchmark-analysis" v-if="selected.analysis && selected.analysis.result">
            <div class="card-head"><div class="card-title">作者分析 <span class="card-sub">{{ selected.analysis.engine }}</span></div><button class="btn btn-sm" @click="analyzeAuthor">重新分析</button></div>
            <div class="card-body">
              <div class="analysis-kpis" v-if="selected.analysis.result.metrics"><span>分析作品 <b>{{ selected.analysis.result.metrics.works_analyzed }}</b></span><span>高表现率 <b>{{ selected.analysis.result.metrics.hot_ratio }}%</b></span><span>平均点赞 <b>{{ num(selected.analysis.result.metrics.avg_likes) }}</b></span><span>收藏/点赞 <b>{{ selected.analysis.result.metrics.collect_rate }}%</b></span></div>
              <div class="analysis-grid" v-if="selected.analysis.result.reading"><div><small>定位判断</small><p>{{ selected.analysis.result.reading.positioning }}</p></div><div><small>内容公式</small><p>{{ selected.analysis.result.reading.content_formula }}</p></div><div><small>值得学习</small><ul><li v-for="item in selected.analysis.result.reading.learn_from" :key="item">{{ item }}</li></ul></div><div><small>不要照搬</small><ul><li v-for="item in selected.analysis.result.reading.avoid" :key="item">{{ item }}</li></ul></div></div>
            </div>
          </article>
          <div class="callout c-info" v-else><span>分析</span><span>作品数据已经就绪。点击“分析这个作者”，系统会基于实际作品指标总结定位、内容公式、可借鉴机制与照搬风险。</span></div>
        </template>

        <template v-else>
          <div class="radar-toolbar"><div class="chips"><button class="chip" :class="{active:platformFilter==='all'}" @click="platformFilter='all'">全部 {{ benchmarks.length }}</button><button class="chip" v-for="platform in platformFilters" :key="platform.id" :class="{active:platformFilter===platform.id}" @click="platformFilter=platform.id">{{ platform.label }}</button></div><span class="mini-note">添加后按你填写的条数建立作品缓存</span></div>
          <div class="empty radar-empty" v-if="!loading && !filteredBenchmarks.length"><b>还没有对标账号</b><span>粘贴同行主页链接，系统会拉取公开资料和作品，建立可以持续刷新的监控墙。</span><button class="btn btn-pri" @click="openAdd">添加第一个对标账号</button></div>
          <div class="benchmark-account-wall" v-else>
            <article class="benchmark-account" v-for="item in filteredBenchmarks" :key="item.id" :class="{fresh:item.has_update,error:item.sync_status==='error'}">
              <div class="benchmark-account-head"><div class="benchmark-avatar small"><img v-if="typeof item.avatar==='string' && item.avatar" :src="item.avatar" :alt="(item.name || item.uid) + ' 的头像'"><span v-else>{{ initial(item) }}</span></div><div><div class="benchmark-name">{{ item.name || item.uid }} <span class="account-new" v-if="item.has_update">有新作</span></div><div class="mini-note">{{ item.platform_label }} · {{ item.uid }}</div></div><span class="badge sync-badge" :class="item.sync_status==='error'?'b-err':item.sync_status==='syncing'?'b-warn':'b-ok'">{{ syncLabel(item.sync_status) }}</span></div>
              <p class="benchmark-bio">{{ item.bio || '该账号暂无公开简介' }}</p>
              <div class="benchmark-card-kpis"><span><b>{{ num(item.fans) }}</b>粉丝</span><span><b>{{ item.works_count }}</b>作品</span><span><b>{{ item.hot_count }}</b>高表现</span><span><b>{{ item.scan_count || 30 }}</b>目标条数</span></div>
              <div class="benchmark-error" v-if="item.sync_status==='error'"><b>{{ item.last_error_code }}</b><span>{{ item.last_error_message }}</span></div>
              <div class="benchmark-card-actions"><button class="btn btn-pri" @click="openBenchmark(item.id)">打开监控墙</button><button class="btn" @click="scanBenchmark(item)">刷新</button><button class="btn btn-ghost" @click="removeBenchmark(item)">删除</button></div>
            </article>
          </div>
        </template>
      </template>

      <template v-else>
        <div class="radar-toolbar"><div><b>数据源与监听规则</b><span class="mini-note"> · 每个入口都对应真实采集方式、运行状态和同步记录</span></div><div class="source-toolbar-actions"><button class="btn" @click="openSource('keyword_watch')">+ 关键词监听</button><button class="btn btn-pri" @click="openSource('external_feed')">+ 添加连接器</button></div></div>
        <div class="callout c-info"><span>组合</span><span>平台热榜负责通用热点，行业连接器负责垂直信息，关键词规则负责主动检索；用户反馈和手动关注由你随时录入，历史起量依据真实发布快照计算。</span></div>
        <div class="empty radar-empty" v-if="!sources.length"><b>还没有热点数据源</b><span>添加平台热榜或你所在行业的 RSS/JSON Feed，真实同步后才会生成信号。</span><button class="btn btn-pri" @click="openSource">添加第一个数据源</button></div>
        <div class="source-wall" v-else>
          <article class="source-card" v-for="source in sources" :key="source.id">
            <div><span class="badge b-info">{{ sourceKind(source) }}</span><span class="badge" :class="source.status==='error'?'b-err':source.status==='syncing'?'b-warn':'b-ok'">{{ syncLabel(source.status) }}</span></div>
            <h3>{{ source.name }}</h3><p class="source-address">{{ source.platform ? platformName(source.platform) : source.config.url }}</p>
            <div class="source-meta"><span>每 {{ source.frequency_hours }} 小时</span><span>上次：{{ source.last_run_at ? new Date(source.last_run_at).toLocaleString('zh-CN') : '未同步' }}</span><span v-if="source.runs && source.runs.length">最近：获取 {{ source.runs[0].fetched }} · 新增 {{ source.runs[0].created }} · 去重 {{ source.runs[0].deduplicated }}</span></div>
            <div class="benchmark-error" v-if="source.last_error"><b>同步失败</b><span>{{ source.last_error }}</span></div>
            <div class="source-actions"><button class="btn btn-pri" @click="syncSource(source)">立即同步</button><button class="btn btn-ghost" @click="removeSource(source)">删除源</button></div>
          </article>
        </div>
      </template>

      <div class="modal benchmark-add-modal" :class="{open:addOpen}" role="dialog" aria-modal="true" aria-labelledby="benchmarkAddTitle" @click.self="addOpen=false">
        <div class="modal-card">
          <div class="modal-head"><div class="modal-title" id="benchmarkAddTitle">添加对标账号</div><button class="modal-x" aria-label="关闭" @click="addOpen=false">×</button></div>
          <div class="modal-body">
            <div class="callout c-info"><span>公开</span><span>只同步账号公开资料和作品元数据。添加后可查看作品墙、筛选高表现内容并进行作者分析。</span></div>
            <div class="platform-support" aria-label="支持的平台">
              <button v-for="platform in platformOptions" :key="platform.id" type="button" class="platform-support-item" :class="{active:form.platform===platform.id}" @click="form.platform=platform.id">{{ platform.label }}</button>
            </div>
            <div class="grid g2">
              <label class="field"><span>平台</span><select id="benchmarkPlatform" class="input" v-model="form.platform"><option v-for="platform in platformOptions" :key="platform.id" :value="platform.id">{{ platform.group }} · {{ platform.label }}</option></select></label>
              <label class="field"><span>抓取作品条数</span><input id="benchmarkScanCount" class="input" type="number" min="1" max="500" step="1" inputmode="numeric" v-model.number="form.scan_count" aria-describedby="scanCountHelp"><small id="scanCountHelp">可填写 1–500，约 {{ estimatedPages }} 次分页请求；平台无更多作品时会提前停止。</small></label>
            </div>
            <label class="field"><span>{{ selectedPlatform.id_label }}</span><input id="benchmarkLinkInput" class="input" v-model="form.link" :placeholder="selectedPlatform.placeholder" @keyup.enter="addBenchmark"><small>不同平台使用不同账号标识；系统会先解析账号，再开始计费抓取。</small></label>
            <label class="field"><span>备注（可选）</span><input class="input" v-model="form.note" placeholder="例如：核心对标 / 选题参考"></label>
            <div class="inline-error" v-if="addError"><b>{{ addError.code }}</b><span>{{ addError.message }}</span><button class="text-btn" v-if="addError.code==='CONFIG_MISSING_API_KEY'" @click="addOpen=false;go('settings')">前往系统设置</button></div>
            <p class="mini-note" v-if="store.runtime.demo_mode">当前是演示模式：提交后生成明确标记的演示账号和作品。真实数据必须关闭演示模式并配置 API Key。</p>
          </div>
          <div class="modal-foot"><button class="btn" :disabled="adding" @click="addOpen=false">取消</button><button class="btn btn-pri" :disabled="adding" @click="addBenchmark">{{ adding ? '正在提交…' : '添加并后台同步' }}</button></div>
        </div>
      </div>
      <div class="modal benchmark-add-modal" :class="{open:sourceOpen}" role="dialog" aria-modal="true" aria-labelledby="radarSourceTitle" @click.self="sourceOpen=false">
        <div class="modal-card"><div class="modal-head"><div class="modal-title" id="radarSourceTitle">添加热点数据源</div><button class="modal-x" aria-label="关闭" @click="sourceOpen=false">×</button></div>
          <div class="modal-body">
            <label class="field"><span>连接方式</span><select class="input" v-model="sourceForm.kind" @change="sourceProbe=null"><option value="tikhub_hot">TikHub 平台热点/热搜</option><option value="external_feed">行业网站 / RSS / JSON / API</option><option value="keyword_watch">TikHub 关键词监听</option></select></label>
            <div class="grid g2"><label class="field"><span>名称</span><input class="input" v-model="sourceForm.name" placeholder="例如：抖音热点榜"></label><label class="field"><span>同步频率（小时）</span><input class="input" type="number" min="1" max="168" v-model.number="sourceForm.frequency_hours"></label></div>
            <label class="field" v-if="sourceForm.kind!=='external_feed'"><span>平台</span><select class="input" v-model="sourceForm.platform"><option value="douyin">抖音</option><option value="xhs">小红书</option><option value="tiktok" v-if="sourceForm.kind==='keyword_watch'">TikTok</option><option value="bilibili" v-if="sourceForm.kind==='tikhub_hot'">B站</option><option value="kuaishou" v-if="sourceForm.kind==='tikhub_hot'">快手</option><option value="weibo" v-if="sourceForm.kind==='tikhub_hot'">微博</option><option value="zhihu" v-if="sourceForm.kind==='tikhub_hot'">知乎</option></select></label>
            <label class="field" v-if="sourceForm.kind==='external_feed'"><span>网站首页、RSS 或 API 地址</span><div class="source-probe-row"><input class="input" v-model="sourceForm.url" placeholder="例如：https://aihot.virxact.com/"><button class="btn" type="button" :disabled="sourceProbing" @click="probeSource">{{ sourceProbing ? '正在检测…' : '检测并预览' }}</button></div><small>普通首页会先探测 RSS/API；无法稳定接入时会明确说明，不会假装保存成功。</small></label>
            <div class="source-preview" v-if="sourceProbe"><div><span class="badge b-ok">连接成功</span><b>{{ sourceProbe.source_type }}</b><small>{{ sourceProbe.resolved_url }}</small></div><ol><li v-for="item in sourceProbe.sample" :key="item.id || item.url">{{ item.title }}</li></ol></div>
            <label class="field"><span>{{ sourceForm.kind==='keyword_watch' ? '监听关键词（必填）' : '相关关键词（可选）' }}</span><input class="input" v-model="sourceForm.keywords" placeholder="例如：AI工具, 内容创作, 智能体"><small v-if="sourceForm.kind==='keyword_watch'">最多 10 个；每次运行会调用所选平台的真实搜索接口。</small></label>
          </div><div class="modal-foot"><button class="btn" @click="sourceOpen=false">取消</button><button class="btn btn-pri" :disabled="sourceSaving" @click="addSource">{{ sourceSaving ? '保存中…' : '保存数据源' }}</button></div>
        </div>
      </div>
      <div class="modal" :class="{open:manualOpen}" role="dialog" aria-modal="true" aria-labelledby="manualSignalTitle" @click.self="manualOpen=false">
        <div class="modal-card" style="max-width:600px"><div class="modal-head"><div class="modal-title" id="manualSignalTitle">{{ manualForm.category==='fb' ? '录入用户反馈' : '添加手动关注' }}</div><button class="modal-x" aria-label="关闭" @click="manualOpen=false">×</button></div>
          <div class="modal-body"><div class="callout c-info"><span>沉淀</span><span>{{ manualForm.category==='fb' ? '记录评论、客户问题或私域反馈，后续可以继续转选题与拆解。' : '保存临时发现的内容链接和判断，不依赖自动抓取。' }}</span></div><label class="field"><span>{{ manualForm.category==='fb' ? '反馈摘要' : '关注内容标题' }}</span><textarea class="input" rows="3" v-model="manualForm.title"></textarea></label><div class="grid g2"><label class="field"><span>来源平台</span><input class="input" v-model="manualForm.platform" placeholder="例如：视频号评论"></label><label class="field"><span>原始链接（可选）</span><input class="input" v-model="manualForm.url" placeholder="https://..."></label></div><label class="field"><span>备注（可选）</span><textarea class="input" rows="2" v-model="manualForm.note"></textarea></label></div>
          <div class="modal-foot"><button class="btn" @click="manualOpen=false">取消</button><button class="btn btn-pri" :disabled="manualSaving" @click="addManualSignal">{{ manualSaving ? '保存中…' : '保存到雷达' }}</button></div></div>
      </div>
    </section>`,
  };
})();
