/* 页面：内容雷达 / 内容提取 / 内容拆解 */
(function () {
  const go = (p) => { window.__app && window.__app.go(p); };
  const JOB_BADGE = {
    QUEUED: ['b-neu', '排队中'], RUNNING: ['b-pri pulse', '执行中'],
    WAITING_CONFIRMATION: ['b-warn', '待你决策'], SUCCEEDED: ['b-ok', '成功'],
    FAILED: ['b-err', '失败'], CANCELLED: ['b-neu', '已取消'],
    CANCEL_REQUESTED: ['b-warn', '取消中'], DRAFT: ['b-neu', '草稿'],
  };
  const STEP_LABELS = {
    resolve_source: '解析来源', check_cache: '检查缓存', fetch_native_text: '获取原生文本',
    fetch_caption: '获取原生字幕', download_media: '下载媒体', extract_audio: '提取音频',
    transcribe_segments: 'ASR 转写分片', validate_transcript: '校验转写', persist_assets: '保存资产',
    complete: '完成', load_transcript: '加载转写', context_resolve: '上下文解析',
    llm_analyze: '深度拆解', schema_validate: 'Schema 校验', persist: '写库',
    fetch_pages: '评论分页', validate: '校验', clustering: '聚类中',
    awaiting_asr_authorization: '等待 ASR 授权决策',
  };

  /* ================= 内容雷达 ================= */
  window.PageRadar = {
    data() {
      return {
        signals: [], filter: 'all',
        filters: [
          ['all', '全部'], ['hot', '对标高表现'], ['new', '对标新作'], ['kw', '关键词'],
          ['fb', '用户反馈'], ['revive', '历史起量'], ['manual', '手动收藏'],
        ],
      };
    },
    async mounted() { await this.load(); },
    methods: {
      go,
      async load() {
        try { this.signals = await api.get('/api/radar/signals'); }
        catch (e) { handleError(e); }
      },
      metricsText(m) {
        const parts = [];
        if (m.plays) parts.push('播放 ' + m.plays);
        if (m.reads) parts.push('阅读 ' + m.reads);
        if (m.likes) parts.push('点赞 ' + m.likes);
        if (m.comments) parts.push('评论 ' + m.comments);
        if (m.collects) parts.push('收藏 ' + m.collects);
        if (m.note) parts.push(m.note);
        if (m.quote) parts.push('典型评论：“' + m.quote + '”');
        if (m.new_plays) parts.push('新增播放 ' + m.new_plays + ' · 新增收藏 ' + m.new_collects);
        if (m.search_ratio) parts.push('搜索流量占比 ' + m.search_ratio);
        return parts.join(' · ');
      },
      async extract(s) {
        try {
          const plan = await api.post('/api/extraction/plans', { signal_id: s.id });
          bus.emit('open-plan', plan);
        } catch (e) { handleError(e); }
      },
      async fetchComments(s) {
        try {
          const r = await api.post('/api/extraction/comment-jobs', {
            source_content_id: s.content_id || '', title: s.title, range: '热门 200 条', after: '保存后聚类',
          });
          toast(r.note);
          go('extraction');
        } catch (e) { handleError(e); }
      },
      async observe(s) { try { const r = await api.post('/api/radar/signals/' + s.id + '/observe'); toast(r.note); await this.load(); } catch (e) { handleError(e); } },
      async ignore(s) { try { const r = await api.post('/api/radar/signals/' + s.id + '/ignore'); toast(r.note, 'warn'); await this.load(); } catch (e) { handleError(e); } },
      async noSimilar(s) { try { const r = await api.post('/api/radar/signals/' + s.id + '/no-similar'); toast(r.note, 'warn'); await this.load(); } catch (e) { handleError(e); } },
      async toTopic(s) {
        try {
          await api.post('/api/radar/signals/' + s.id + '/to-topic');
          await api.post('/api/creation/projects', { title: s.title.replace(/[《》]/g, ''), source_chain: ['雷达 #' + s.id] });
          toast('已创建选题候选并转入创作工坊');
          go('creation');
        } catch (e) { handleError(e); }
      },
      async toAnalysis(s) {
        if (!s.content_id) { toast('拆解需先完成提取。已为你预建拆解草稿', 'warn'); return; }
        try {
          const plan = await api.post('/api/analysis/plans', { source_content_id: s.content_id, title: s.title, source_chain: ['雷达 #' + s.id] });
          if (confirm(`拆解计划：${plan.model} · 预计 ¥${plan.cost_est}（演示）\n确认后创建拆解任务`)) {
            const r = await api.post('/api/analysis/jobs', { source_content_id: s.content_id, title: s.title, source_chain: ['雷达 #' + s.id] });
            toast(r.note); go('analysis');
          }
        } catch (e) { handleError(e); }
      },
    },
    computed: {
      filtered() { return this.filter === 'all' ? this.signals : this.signals.filter((s) => s.category === this.filter); },
    },
    template: `
    <section>
      <div class="page-head">
        <div><div class="page-title">内容雷达</div><div class="page-q">最近有什么内容值得我进一步看？</div></div>
        <div class="mini-note">自动同步仅公开元数据 · {{ store.runtime.demo_mode ? '演示数据' : '' }}</div>
      </div>
      <div class="callout c-pri"><span>⚡</span><span><b>高成本动作不会自动执行：</b>提取文案、获取评论、深度拆解需你逐条主动触发，不会因页面滚动、卡片展开或系统刷新而运行。</span></div>
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:14px;flex-wrap:wrap;gap:10px">
        <div class="chips"><button class="chip" v-for="f in filters" :key="f[0]" :class="{active:filter===f[0]}" @click="filter=f[0]">{{ f[1] }}</button></div>
        <select class="input" style="width:140px"><option>综合推荐</option><option>最新发布</option><option>相对表现</option><option>IP 相关度</option></select>
      </div>

      <div class="grid g2">
        <div class="signal-card" v-for="s in filtered" :key="s.id" :class="{ignored: s.status==='ignored'}">
          <div class="sc-top">
            <span class="badge" :class="s.category==='hot'?'b-pri':s.category==='fb'?'b-warn':s.category==='revive'?'b-ok':s.category==='manual'?'b-neu':'b-info'">{{ s.source_type }}</span>
            <span>{{ s.platform }}</span><span class="sc-author">{{ s.author }}</span>
            <span>{{ s.published_at }} 发布</span>
            <span class="badge" :class="s.status==='new'?'b-info':'b-neu'">{{ s.status_label }}</span>
            <a v-if="s.url" :href="s.url" target="_blank" class="trace" title="原始链接">链接 ↗</a>
          </div>
          <div class="sc-title">{{ s.title }}</div>
          <div><span class="sc-metrics">{{ metricsText(s.metrics) }}</span></div>
          <div class="sc-rows">
            <span v-if="s.rel_perf">相对表现 <b>{{ s.rel_perf }}</b></span>
            <span>相关度<i class="relbar"><i :style="{width:s.relevance+'%'}"></i></i>{{ s.relevance }}%</span>
            <span>时效 <span class="badge b-warn">{{ s.timeliness }}</span></span>
          </div>
          <div class="sc-foot">
            <span class="sc-src">来源 {{ s.source }} · 抓取于 {{ s.fetched_at.slice(5,16).replace('T',' ') }} · #{{ s.id }}</span>
            <div class="sc-actions">
              <button class="btn btn-sm btn-pri" v-if="s.status!=='processed'" @click="extract(s)">提取文案</button>
              <button class="btn btn-sm" @click="fetchComments(s)">获取评论</button>
              <button class="btn btn-sm" v-if="s.category!=='kw'&&s.category!=='fb'" @click="toAnalysis(s)">深度拆解</button>
              <button class="btn btn-sm" @click="toTopic(s)">转为选题</button>
              <button class="btn btn-sm" v-if="s.status==='new'" @click="observe(s)">加入观察</button>
              <button class="btn btn-sm btn-ghost" v-if="s.status!=='ignored'" @click="ignore(s)">忽略</button>
              <button class="btn btn-sm btn-ghost" @click="noSimilar(s)">不再推荐相似</button>
            </div>
          </div>
        </div>
      </div>
    </section>`,
  };

  /* ================= 内容提取 ================= */
  window.PageExtraction = {
    data() { return { jobs: [], assets: null, priority: [] }; },
    async mounted() {
      await this.load();
      this._t = setInterval(this.loadJobs, 1500);
      bus.on('jobs-changed', this.loadJobs);
    },
    unmounted() { clearInterval(this._t); },
    methods: {
      go,
      async load() {
        await this.loadJobs();
        try {
          this.priority = await api.get('/api/extraction/priority');
          this.assets = await api.get('/api/extraction/assets/SC-121');
        } catch (e) { handleError(e); }
      },
      async loadJobs() { try { this.jobs = await api.get('/api/extraction/jobs'); } catch (e) {} },
      badge(j) { const b = JOB_BADGE[j.status] || ['b-neu', j.status]; return { cls: b[0], label: j.status === 'RUNNING' ? `执行中 ${j.progress}%` : b[1] }; },
      stepLabel(j) { return STEP_LABELS[j.current_step] || j.current_step || '—'; },
      async openPlan() {
        try {
          const plan = await api.post('/api/extraction/plans', { signal_id: 'SG-2031' });
          bus.emit('open-plan', plan);
        } catch (e) { handleError(e); }
      },
      async cancel(j) { try { const r = await api.post('/api/extraction/jobs/' + j.id + '/cancel'); toast(r.note, 'warn'); await this.loadJobs(); } catch (e) { handleError(e); } },
      async retry(j) { try { const r = await api.post('/api/extraction/jobs/' + j.id + '/retry'); toast(r.note); await this.loadJobs(); } catch (e) { handleError(e); } },
      showError(j) { if (j.error) bus.emit('open-error', j.error); },
      async authorizeAsr(j) {
        try {
          await api.post('/api/extraction/jobs', {
            plan: { source: { title: j.input_json.title, signal_id: j.input_json.signal_id }, caption_available: false, cost_est: 0, cost: {}, steps: [] },
            authorizations: { asr_when_no_caption: true },
          });
          toast('已明确授权 ASR，任务已重新排队');
          await this.loadJobs();
        } catch (e) { handleError(e); }
      },
      layerStatus(a, key) {
        const v = a[key];
        return v && v.exists ? '✓' : '—';
      },
    },
    template: `
    <section>
      <div class="page-head">
        <div><div class="page-title">内容提取</div><div class="page-q">我需要从这条内容中拿到哪些原始材料？</div></div>
        <button class="btn btn-pri" @click="openPlan">+ 新建提取</button>
      </div>
      <div class="callout c-pri"><span>⚡</span><span>未点击前，系统<b>不会</b>自动下载媒体或转写。执行前展示完整计划与成本；已缓存内容不会重复付费处理。</span></div>

      <div class="grid g31 section-gap">
        <div class="card">
          <div class="card-head"><div class="card-title">提取任务 <span class="card-sub">状态机驱动 · 不存在自动变成功</span></div></div>
          <div class="card-body">
            <div class="empty" v-if="!jobs.length">暂无提取任务</div>
            <div class="task-row" v-for="j in jobs" :key="j.id" :class="{failed: j.status==='FAILED'}">
              <div class="tr-body">
                <div class="tr-title">《{{ j.input_json.title }}》
                  <span class="badge" :class="badge(j).cls"><span class="sdot" v-if="j.status==='RUNNING'"></span>{{ badge(j).label }}</span></div>
                <div class="tr-meta">
                  阶段：{{ stepLabel(j) }}
                  <template v-if="j.status==='FAILED' && j.error"> · {{ j.error.code }} · 部分产物已保留，<b>未</b>标记为完整转写</template>
                  <template v-if="j.status==='SUCCEEDED'"> · 费用 ¥{{ j.cost_actual.toFixed(2) }}</template>
                  <template v-if="j.status==='WAITING_CONFIRMATION'"> · 平台无原生字幕，等待你明确授权 ASR</template>
                </div>
              </div>
              <div class="tr-prog" v-if="j.status==='RUNNING'"><div class="progress"><i :style="{width:j.progress+'%'}"></i></div></div>
              <button class="btn btn-sm btn-ghost" v-if="j.status==='RUNNING'||j.status==='QUEUED'" @click="cancel(j)">取消</button>
              <button class="btn btn-sm btn-pri" v-if="j.status==='WAITING_CONFIRMATION'" @click="authorizeAsr(j)">授权 ASR 并继续</button>
              <button class="btn btn-sm btn-danger" v-if="j.status==='FAILED'" @click="showError(j)">查看错误</button>
              <button class="btn btn-sm" v-if="j.status==='FAILED'" @click="retry(j)">从失败点重试</button>
              <button class="btn btn-sm btn-pri" v-if="j.status==='SUCCEEDED' && j.task_type==='extraction'" @click="go('analysis')">去拆解</button>
            </div>
          </div>
        </div>

        <div>
          <div class="card section-gap">
            <div class="card-head"><div class="card-title">文本来源优先级</div></div>
            <div class="card-body">
              <ol class="priority-list"><li v-for="p in priority" :key="p">{{ p }}</li></ol>
              <div class="mini-note" style="margin-top:10px">这不是静默兜底：平台无字幕时<b>不会</b>自动进入 ASR，除非你在本次任务中明确勾选。平台描述字段永远不会被标记为完整转写。</div>
            </div>
          </div>
          <div class="card" v-if="assets">
            <div class="card-head"><div class="card-title">资产分层 <span class="card-sub">《{{ assets.title }}》#{{ assets.id }}</span></div></div>
            <div class="card-body" style="padding-top:4px">
              <div class="asset-layer"><span class="badge" :class="assets.assets.metadata&&assets.assets.metadata.exists?'b-ok':'b-neu'">{{ layerStatus(assets.assets,'metadata') }}</span><span style="flex:1">来源元数据</span><span class="mini-note">metadata.json</span></div>
              <div class="asset-layer"><span class="badge" :class="assets.assets.media&&assets.assets.media.exists?'b-ok':'b-neu'">{{ layerStatus(assets.assets,'media') }}</span><span style="flex:1">原始媒体</span><span class="mini-note">{{ assets.assets.media && assets.assets.media.note }}</span></div>
              <div class="asset-layer"><span class="badge" :class="assets.assets.caption&&assets.assets.caption.exists?'b-ok':'b-neu'">{{ layerStatus(assets.assets,'caption') }}</span><span style="flex:1">原生字幕 <span class="badge b-neu" v-if="assets.assets.caption&&assets.assets.caption.exists">不可覆盖</span></span>
                <span class="trace" v-if="assets.assets.caption&&assets.assets.caption.exists" @click="bus.emit('open-trace',{title:'原生字幕',src:assets.assets.caption.trace,quote:assets.assets.caption.text})">追溯</span></div>
              <div class="asset-layer"><span class="badge" :class="assets.assets.asr&&assets.assets.asr.exists?'b-ok':'b-neu'">{{ layerStatus(assets.assets,'asr') }}</span><span style="flex:1">原始 ASR 转写</span><span class="mini-note">{{ assets.assets.asr && assets.assets.asr.note }}</span></div>
              <div class="asset-layer"><span class="badge" :class="assets.assets.clean&&assets.assets.clean.exists?'b-ok':'b-neu'">{{ layerStatus(assets.assets,'clean') }}</span><span style="flex:1">清洗文案 <span class="badge b-neu" v-if="assets.assets.clean&&assets.assets.clean.exists">版本化</span></span>
                <span class="trace" v-if="assets.assets.clean&&assets.assets.clean.exists" @click="bus.emit('open-trace',{title:'清洗文案',src:assets.assets.clean.trace,quote:assets.assets.clean.text})">追溯</span></div>
            </div>
          </div>
        </div>
      </div>
    </section>`,
  };

  /* ================= 内容拆解 ================= */
  window.PageAnalysis = {
    data() { return { analyses: [], pending: [], patterns: [], active: null }; },
    async mounted() { await this.load(); this._t = setInterval(this.load, 2500); },
    unmounted() { clearInterval(this._t); },
    methods: {
      go,
      async load() {
        try {
          const r = await api.get('/api/analysis/list');
          this.analyses = r.analyses; this.pending = r.pending_sources;
          if (!this.active && this.analyses.length) this.active = this.analyses[0];
          else if (this.active) {
            const cur = this.analyses.find((a) => a.id === this.active.id);
            if (cur) this.active = cur;
          }
          this.patterns = await api.get('/api/analysis/patterns');
        } catch (e) { /* 轮询静默 */ }
      },
      async startAnalysis(sc) {
        try {
          await api.post('/api/analysis/plans', { source_content_id: sc.id, title: sc.title });
          const r = await api.post('/api/analysis/jobs', { source_content_id: sc.id, title: sc.title, source_chain: ['提取 #' + sc.id] });
          toast('拆解计划已确认：深度模型 · 预计 ¥0.35（演示）· 任务 ' + r.job_id);
        } catch (e) { handleError(e); }
      },
      async savePattern() {
        try {
          const r = await api.post('/api/analysis/patterns', { name: '归因翻转-汇报场景', note: '来源：拆解 #' + (this.active && this.active.id) });
          toast(r.note); await this.load();
        } catch (e) { handleError(e); }
      },
      async toCreation() {
        try {
          const r = await api.post('/api/creation/projects', {
            title: '汇报被打断？先改顺序',
            source_chain: ['拆解 #' + this.active.id, '选题 #T-96'],
          });
          toast('已携带拆解 #' + this.active.id + ' 创建创作项目');
          go('creation');
        } catch (e) { handleError(e); }
      },
      patternBadge(p) {
        return p.status === 'verified' ? ['b-ok', '本人已验证'] : p.status === 'draft' ? ['b-neu', '草稿'] : ['b-warn', '待验证'];
      },
    },
    template: `
    <section>
      <div class="page-head">
        <div><div class="page-title">内容拆解</div><div class="page-q">这条内容的可复用结构是什么？</div></div>
        <div class="mini-note">拆解 ≠ 仿写：本页只回答「原内容是什么」</div>
      </div>

      <div class="grid g31 section-gap">
        <div class="card" v-if="active">
          <div class="card-head"><div class="card-title">《{{ active.source_title }}》 <span class="badge b-ok">拆解完成</span></div>
            <span class="mini-note">拆解 #{{ active.id }} · {{ active.model }} · ¥{{ active.cost.toFixed(2) }}</span></div>
          <div class="card-body" style="padding-top:6px">
            <div class="src-chain" style="margin-bottom:12px">来源链：
              <span class="node" @click="go('radar')">雷达 #SG-2031</span><em>→</em>
              <span class="node" @click="go('extraction')">提取 #{{ active.source_content_id }}</span><em>→</em>
              <span class="node">拆解 #{{ active.id }}</span>
            </div>
            <div class="ana-row"><div class="ana-label">核心选题</div>{{ active.result.topic }} <span class="cite">00:41</span></div>
            <div class="ana-row"><div class="ana-label">目标受众 / 用户问题</div>{{ (active.result.target_audience||[]).join('、') }} · “{{ active.result.user_problem }}”</div>
            <div class="ana-row" v-if="active.result.hook"><div class="ana-label">开头钩子（{{ active.result.hook.cite }}）</div>“{{ active.result.hook.text }}” —— {{ active.result.hook.mechanism }}</div>
            <div class="ana-row"><div class="ana-label">内容结构</div>
              <div class="struct"><div class="seg" v-for="seg in active.result.structure" :key="seg.name" :style="{flex:seg.weight, background:seg.color}"><b>{{ seg.name }}</b><span>{{ seg.span }}</span></div></div></div>
            <div class="ana-row" v-if="active.result.key_turns&&active.result.key_turns.length"><div class="ana-label">关键转折</div>“{{ active.result.key_turns[0].text }}” <span class="cite">{{ active.result.key_turns[0].cite }}</span></div>
            <div class="ana-row"><div class="ana-label">论据与案例</div>{{ (active.result.evidence||[]).join('；') }}</div>
            <div class="ana-row"><div class="ana-label">情绪机制</div>{{ (active.result.emotional_mechanisms||[]).join('、') }}</div>
            <div class="ana-row" v-if="active.result.golden_quotes&&active.result.golden_quotes.length"><div class="ana-label">金句与记忆点</div>“{{ active.result.golden_quotes[0].text }}” <span class="cite">{{ active.result.golden_quotes[0].cite }}</span></div>
            <div class="ana-row"><div class="ana-label">结尾动作 / 评论触发点</div>{{ active.result.cta }}</div>
            <div class="ana-row"><div class="ana-label" style="color:var(--ok)">可借鉴部分</div>{{ (active.result.reusable||[]).join(' · ') }}</div>
            <div class="ana-row"><div class="ana-label" style="color:var(--err)">不应照搬部分</div>{{ (active.result.non_reusable||[]).join(' · ') }}</div>
            <div style="display:flex;gap:8px;margin-top:12px;flex-wrap:wrap">
              <button class="btn btn-pri" @click="toCreation">转为我的选题</button>
              <button class="btn" @click="savePattern">保存为模式卡</button>
              <button class="btn btn-ghost" @click="toast('组合拆解为 P1 能力：可加入对比组（高/中/低表现样本）')">加入对比组</button>
            </div>
          </div>
        </div>
        <div class="card" v-else><div class="loading">加载中</div></div>

        <div>
          <div class="card section-gap">
            <div class="card-head"><div class="card-title">待拆解来源</div></div>
            <div class="card-body" style="padding-top:4px">
              <div class="empty" v-if="!pending.length">暂无待拆解内容</div>
              <div class="kv-row" v-for="sc in pending" :key="sc.id">
                <span>《{{ sc.title }}》</span>
                <button class="btn btn-sm btn-pri" @click="startAnalysis(sc)">拆解</button>
              </div>
              <div class="mini-note" style="margin-top:6px">拆解将调用深度分析模型（演示 ¥0.35），确认后才执行。</div>
            </div>
          </div>
          <div class="card section-gap">
            <div class="card-head"><div class="card-title">内容模式卡</div></div>
            <div class="card-body" style="display:flex;flex-direction:column;gap:10px;padding-top:4px">
              <div class="pattern-card" v-for="p in patterns" :key="p.id">
                <b style="font-size:12.5px">{{ p.name }}</b>
                <span class="badge" :class="patternBadge(p)[0]">{{ patternBadge(p)[1] }}</span>
                <div class="mini-note" style="margin-top:5px">来源样本 {{ p.samples }} · 置信度 {{ p.confidence }} · {{ p.fit }} · {{ p.note }}</div>
              </div>
            </div>
          </div>
          <div class="callout c-info" style="margin-bottom:0"><span>ⓘ</span><span><b>组合拆解（P1）：</b>同时选择高/中/低表现样本，比较开头、结构、时长与评论差异。</span></div>
        </div>
      </div>
    </section>`,
  };
})();
