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
          const costText = store.runtime.demo_mode ? `演示估算 ¥${plan.cost_est}` : '费用以 Provider 实际账单为准';
          if (confirm(`拆解计划：${plan.model} · ${costText}\n确认后创建拆解任务`)) {
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
    data() { return { jobs: [], assets: null, priority: [], library: [], signals: [], uploadFile: null,
      uploadTitle: '', uploading: false, uploadDragActive: false, loadError: null, pickerOpen: false,
      selectedSignalId: '', libraryOpen: false, transcriptOpen: false, selectedAssetId: '', assetLoading: false,
      duplicateCount: 0, cleaningJobs: false }; },
    computed: {
      visibleJobs() {
        const groups = new Map();
        const active = new Set(['RUNNING', 'QUEUED', 'WAITING_CONFIRMATION', 'CANCEL_REQUESTED']);
        for (const job of this.jobs) {
          const input = job.input_json || {};
          const key = input.account_work_id ? `${job.task_type}:account:${input.account_work_id}`
            : input.signal_id ? `${job.task_type}:signal:${input.signal_id}`
            : input.asset_id ? `${job.task_type}:asset:${input.asset_id}`
            : input.platform && input.video_id ? `${job.task_type}:work:${input.platform}:${input.video_id}`
            : input.source_content_id || job.output_json?.source_content_id ? `${job.task_type}:source:${input.source_content_id || job.output_json.source_content_id}`
            : `${job.task_type}:title:${String(input.title || job.id).replace(/\s/g, '').toLowerCase()}`;
          const items = groups.get(key) || [];
          items.push(job); groups.set(key, items);
        }
        return [...groups.values()].map((items) => {
          const current = items.find((item) => active.has(item.status)) || items[0];
          return { ...current, history_count: Math.max(0, items.length - 1) };
        });
      },
    },
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
          this.library = await api.get('/api/extraction/assets');
          this.signals = await api.get('/api/radar/signals');
          const wanted = store.insightSelection.sourceId;
          const initial = this.library.find((item) => item.source_content_id === wanted) || this.library[0];
          if (initial && !this.selectedAssetId) await this.selectAsset(initial.source_content_id);
        } catch (e) { handleError(e); }
      },
      async loadJobs() {
        try {
          const [jobs, duplicateSummary] = await Promise.all([
            api.get('/api/extraction/jobs'), api.get('/api/extraction/jobs/duplicates/summary'),
          ]);
          this.jobs = jobs; this.duplicateCount = duplicateSummary.duplicate_count || 0; this.loadError = null;
          const done = this.jobs.find(j => j.task_type === 'extraction' && j.status === 'SUCCEEDED' && j.output_json.source_content_id);
          if (done && !this.assets) {
            this.assets = await api.get('/api/extraction/assets/' + done.output_json.source_content_id);
          }
        } catch (e) { this.loadError = e; }
      },
      badge(j) { const b = JOB_BADGE[j.status] || ['b-neu', j.status]; return { cls: b[0], label: j.status === 'RUNNING' ? `执行中 ${j.progress}%` : b[1] }; },
      stepLabel(j) { return STEP_LABELS[j.current_step] || j.current_step || '—'; },
      openPlan() {
        this.selectedSignalId = (this.signals.find((item) => item.status === 'new') || this.signals[0] || {}).id || '';
        this.pickerOpen = true;
      },
      async createSignalPlan() {
        if (!this.selectedSignalId) { toast('请先选择一条雷达信号', 'warn'); return; }
        try {
          const plan = await api.post('/api/extraction/plans', { signal_id: this.selectedSignalId });
          this.pickerOpen = false;
          bus.emit('open-plan', plan);
        } catch (e) { handleError(e); }
      },
      chooseUpload(event) { this.setUploadFile(event.target.files && event.target.files[0]); },
      setUploadFile(file) {
        if (!file) return;
        const allowed = file.type.startsWith('audio/') || file.type === 'video/mp4' || /\.(wav|mp3|m4a|mp4)$/i.test(file.name);
        if (!allowed) return toast('请选择 WAV、MP3、M4A 或 MP4 文件', 'warn');
        if (file.size > 10 * 1024 * 1024) return toast('文件超过 10 MB，请压缩后重试', 'warn');
        this.uploadFile = file;
        if (!this.uploadTitle.trim()) this.uploadTitle = file.name.replace(/\.[^.]+$/, '');
      },
      onUploadDrop(event) {
        this.uploadDragActive = false;
        this.setUploadFile(event.dataTransfer && event.dataTransfer.files && event.dataTransfer.files[0]);
      },
      clearUpload() {
        this.uploadFile = null;
        const input = document.getElementById('extractionMediaFile');
        if (input) input.value = '';
      },
      formatBytes(value) {
        const bytes = Number(value || 0);
        return bytes >= 1024 * 1024 ? `${(bytes / 1024 / 1024).toFixed(1)} MB` : `${Math.max(1, Math.round(bytes / 1024))} KB`;
      },
      async uploadAndPlan() {
        if (!this.uploadFile) return toast('请先选择一个 10 MB 以内的音频文件', 'warn');
        this.uploading = true;
        try {
          const form = new FormData(); form.append('file', this.uploadFile);
          form.append('title', this.uploadTitle.trim() || this.uploadFile.name);
          form.append('platform', 'local');
          const uploaded = await api.upload('/api/extraction/assets/upload', form);
          const plan = await api.post('/api/extraction/plans', {
            asset_id: uploaded.asset_id, title: this.uploadTitle.trim() || this.uploadFile.name,
            platform: 'local',
          });
          bus.emit('open-plan', plan);
          await this.load();
          toast(uploaded.deduplicated ? '检测到相同素材，已复用原始资产' : '音频已安全保存，等待确认转写计划');
        } catch (e) { handleError(e); }
        this.uploading = false;
      },
      async cancel(j) { try { const r = await api.post('/api/extraction/jobs/' + j.id + '/cancel'); toast(r.note, 'warn'); await this.loadJobs(); } catch (e) { handleError(e); } },
      async retry(j) { try { const r = await api.post('/api/extraction/jobs/' + j.id + '/retry'); toast(r.note); await this.loadJobs(); } catch (e) { handleError(e); } },
      async deleteJob(j) {
        if (!confirm(`删除任务记录《${j.input_json.title || j.id}》？\n原文、媒体与拆解资产不会删除。`)) return;
        try { const r = await api.delete('/api/extraction/jobs/' + encodeURIComponent(j.id)); toast(r.note); await this.loadJobs(); }
        catch (e) { handleError(e); }
      },
      async cleanupDuplicates() {
        if (!this.duplicateCount || !confirm(`清理 ${this.duplicateCount} 条重复任务记录？\n每组保留当前最有效记录，内容资产不受影响。`)) return;
        this.cleaningJobs = true;
        try { const r = await api.post('/api/extraction/jobs/duplicates/cleanup'); toast(r.note); await this.loadJobs(); }
        catch (e) { handleError(e); }
        this.cleaningJobs = false;
      },
      showError(j) { if (j.error) bus.emit('open-error', j.error); },
      async authorizeAsr(j) {
        try {
          const result = await api.post('/api/extraction/jobs/' + encodeURIComponent(j.id) + '/authorize-asr');
          toast(result.note);
          await this.loadJobs();
        } catch (e) { handleError(e); }
      },
      layerStatus(a, key) {
        const v = a[key];
        return v && v.exists ? '✓' : '—';
      },
      async selectAsset(sourceId) {
        this.selectedAssetId = sourceId;
        this.assetLoading = true;
        this.transcriptOpen = false;
        try {
          const detail = await api.get('/api/extraction/assets/' + sourceId);
          if (this.selectedAssetId !== sourceId) return;
          this.assets = detail;
          store.insightSelection.sourceId = sourceId;
        } catch (e) { handleError(e); }
        if (this.selectedAssetId === sourceId) this.assetLoading = false;
      },
      copyTranscript() {
        const text = this.assets && this.assets.transcript && this.assets.transcript.text;
        if (!text) return toast('这条内容还没有完整原文', 'warn');
        navigator.clipboard && navigator.clipboard.writeText(text);
        toast('原始文案已复制');
      },
      viewAssetAnalysis(item) {
        store.insightSelection.sourceId = item.source_content_id;
        store.insightSelection.analysisId = item.latest_analysis_id || '';
        go('analysis');
      },
    },
    template: `
    <section>
      <div class="page-head">
        <div><div class="page-title">内容提取</div><div class="page-q">我需要从这条内容中拿到哪些原始材料？</div></div>
        <button class="btn btn-pri" @click="openPlan">+ 新建提取</button>
      </div>
      <div class="callout c-pri"><span>⚡</span><span>未点击前，系统<b>不会</b>自动下载媒体或转写。执行前展示完整计划与成本；已缓存内容不会重复付费处理。</span></div>

      <div class="card section-gap upload-audio-card">
        <div class="card-head"><div class="card-title">上传真实音频 <span class="card-sub">WAV / MP3 / M4A / MP4 · 最大 10 MB</span></div></div>
        <div class="card-body">
          <div class="upload-audio-grid">
            <label class="field upload-title-field"><span>内容标题</span><input class="input" v-model="uploadTitle" placeholder="用于后续拆解与创作"></label>
            <div class="field"><span>音频或含音频的视频</span>
              <div class="media-file-drop" :class="{ready:uploadFile,dragging:uploadDragActive}"
                   @dragenter.prevent="uploadDragActive=true" @dragover.prevent="uploadDragActive=true"
                   @dragleave.prevent="uploadDragActive=false" @drop.prevent="onUploadDrop">
                <input id="extractionMediaFile" class="file-input-hidden" type="file" accept="audio/*,video/mp4,.wav,.mp3,.m4a,.mp4" @change="chooseUpload">
                <label class="media-file-trigger" for="extractionMediaFile">
                  <b>{{ uploadFile ? uploadFile.name : '拖拽文件到这里，或点击选择' }}</b>
                  <span>{{ uploadFile ? formatBytes(uploadFile.size) + ' · 已通过格式与大小检查' : '支持 WAV / MP3 / M4A / MP4，单个文件不超过 10 MB' }}</span>
                </label>
                <button class="btn btn-sm btn-ghost media-file-clear" v-if="uploadFile" type="button" aria-label="移除已选文件" @click="clearUpload">移除</button>
              </div>
            </div>
          </div>
          <div class="upload-audio-actions">
            <div class="mini-note">上传后按 SHA-256 去重；原始文件与原始转写不可覆盖。执行 ASR 前仍需在计划弹层明确授权。</div>
            <button class="btn btn-pri" :disabled="uploading || !uploadFile" @click="uploadAndPlan">{{ uploading ? '正在上传并校验…' : '上传并生成转写计划' }}</button>
          </div>
        </div>
      </div>

      <div class="grid g31 section-gap">
        <div class="card">
          <div class="card-head"><div class="card-title">提取任务 <span class="card-sub">状态机驱动 · 不存在自动变成功</span></div><button class="btn btn-sm btn-ghost" v-if="duplicateCount" :disabled="cleaningJobs" @click="cleanupDuplicates">{{ cleaningJobs ? '清理中…' : '清理重复记录 ' + duplicateCount }}</button></div>
          <div class="card-body">
            <div class="inline-error" role="alert" v-if="loadError"><b>{{ loadError.code }}</b><span>{{ loadError.message }}</span><button class="btn btn-sm" @click="loadJobs">重试</button></div>
            <div class="empty" v-if="!jobs.length">暂无提取任务</div>
            <div class="task-row" v-for="j in visibleJobs" :key="j.id" :class="{failed: j.status==='FAILED'}">
              <div class="tr-body">
                <div class="tr-title">《{{ j.input_json.title }}》
                  <span class="badge" :class="badge(j).cls"><span class="sdot" v-if="j.status==='RUNNING'"></span>{{ badge(j).label }}</span></div>
                <div class="tr-meta">
                  阶段：{{ stepLabel(j) }}
                  <template v-if="j.status==='FAILED' && j.error"> · {{ j.error.code }} · 部分产物已保留，<b>未</b>标记为完整转写</template>
                  <template v-if="j.status==='SUCCEEDED'"> · 费用 ¥{{ j.cost_actual.toFixed(2) }}</template>
                  <template v-if="j.attempt_count"> · 第 {{ j.attempt_count }}/{{ j.max_attempts }} 次尝试</template>
                  <template v-if="j.status==='WAITING_CONFIRMATION'"> · 平台无原生字幕，等待你明确授权 ASR</template>
                  <template v-if="j.history_count"> · 已合并 {{ j.history_count }} 条历史运行记录</template>
                </div>
              </div>
              <div class="tr-prog" v-if="j.status==='RUNNING'"><div class="progress"><i :style="{width:j.progress+'%'}"></i></div></div>
              <button class="btn btn-sm btn-ghost" v-if="j.status==='RUNNING'||j.status==='QUEUED'" @click="cancel(j)">取消</button>
              <button class="btn btn-sm btn-pri" v-if="j.status==='WAITING_CONFIRMATION'" @click="authorizeAsr(j)">授权 ASR 并继续</button>
              <button class="btn btn-sm btn-danger" v-if="j.status==='FAILED'" @click="showError(j)">查看错误</button>
              <button class="btn btn-sm" v-if="j.status==='FAILED' && (!j.error || j.error.retryable!==false)" @click="retry(j)">从失败点重试</button>
              <button class="btn btn-sm btn-pri" v-if="j.status==='SUCCEEDED' && j.task_type==='extraction'" @click="go('analysis')">去拆解</button>
              <button class="btn btn-sm btn-ghost" v-if="['SUCCEEDED','FAILED','CANCELLED'].includes(j.status)" @click="deleteJob(j)">删除记录</button>
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

      <div class="card section-gap asset-library-card collapsible-section">
        <button type="button" class="collapsible-head" :aria-expanded="libraryOpen" @click="libraryOpen=!libraryOpen">
          <span><b>本地内容资产库</b> <span class="badge b-neu">{{ library.length }} 条</span><small>提取一次，原文和拆解结果持续保留</small></span>
          <span>{{ libraryOpen ? '收起' : '展开查看' }} <i>{{ libraryOpen ? '▴' : '▾' }}</i></span>
        </button>
        <div class="card-body asset-library-body" v-if="libraryOpen">
          <div class="empty" v-if="!library.length">暂无已沉淀内容；完成一次提取后会永久出现在这里。</div>
          <div role="button" tabindex="0" class="asset-library-row" :class="{active:selectedAssetId===item.source_content_id}"
               v-for="item in library" :key="item.source_content_id" @click="selectAsset(item.source_content_id)" @keydown.enter="selectAsset(item.source_content_id)">
            <span class="asset-library-main"><b>《{{ item.title }}》</b><small>{{ item.platform || '本地' }} · {{ item.text_kind }} · {{ item.text_chars }} 字 · {{ item.analysis_count }} 次拆解</small><em v-if="item.text_preview">{{ item.text_preview }}</em></span>
            <span class="asset-library-actions"><span class="badge" :class="item.text_available?'b-ok':'b-warn'">{{ item.text_available ? '原文已保存' : '待完整转写' }}</span><button type="button" class="btn btn-sm" @click.stop="viewAssetAnalysis(item)">{{ item.analysis_count ? '查看拆解' : '去拆解' }}</button></span>
          </div>
          <div class="asset-transcript-shell" v-if="assets">
            <div class="loading compact" v-if="assetLoading">正在读取这条内容的本地原文…</div>
            <button type="button" class="transcript-summary" v-else :aria-expanded="transcriptOpen" @click="transcriptOpen=!transcriptOpen">
              <span><b>当前原文 · 《{{ assets.title }}》</b><small>{{ assets.transcript.kind }} · {{ assets.transcript.chars }} 字 · {{ assets.analyses.length }} 次拆解</small></span>
              <span>{{ transcriptOpen ? '收起原文' : '展开完整原文' }}</span>
            </button>
            <div class="persisted-transcript compact-transcript" v-if="transcriptOpen && assets.transcript&&assets.transcript.available">
              <div class="persisted-transcript-head"><span>完整原文已本地持久化</span><button class="btn btn-sm" type="button" @click="copyTranscript">复制原文</button></div>
              <pre>{{ assets.transcript.text }}</pre>
            </div>
            <div class="empty compact" v-else-if="transcriptOpen">这条来源只有元数据，尚未形成完整原文。请重新提取并授权 ASR。</div>
          </div>
        </div>
      </div>

      <div class="modal extraction-source-modal" :class="{open:pickerOpen}" role="dialog" aria-modal="true" aria-labelledby="extractionSourceTitle" @click.self="pickerOpen=false">
        <div class="modal-card"><div class="modal-head"><div class="modal-title" id="extractionSourceTitle">选择提取来源</div><button class="modal-x" aria-label="关闭" @click="pickerOpen=false">×</button></div><div class="modal-body"><div class="callout c-info"><span>来源</span><span>只会为你选中的真实雷达信号创建计划，不再使用固定示例 ID。</span></div><label class="field"><span>雷达信号</span><select class="input" id="extractionSignalSelect" v-model="selectedSignalId"><option value="" disabled>请选择</option><option v-for="signal in signals" :key="signal.id" :value="signal.id">{{ signal.platform }} · {{ signal.author }} · {{ signal.title }}</option></select></label><div class="empty" v-if="!signals.length">内容雷达暂无可提取信号，请先添加对标账号或创建信号。</div></div><div class="modal-foot"><button class="btn" @click="pickerOpen=false">取消</button><button class="btn btn-pri" :disabled="!selectedSignalId" @click="createSignalPlan">生成执行计划</button></div></div>
      </div>
    </section>`,
  };

  /* ================= 内容拆解 ================= */
  window.PageAnalysis = {
    data() { return { analyses: [], pending: [], patterns: [], comparison: [], active: null, activeSource: null,
      originalOpen: false, researchOpen: false, compareOpen: false, compareBucket: 'high', compareNote: '',
      compareSaving: false, patternOpen: false, patternName: '', patternNote: '', patternSaving: false }; },
    async mounted() { await this.load(); this._t = setInterval(this.load, 2500); },
    unmounted() { clearInterval(this._t); },
    methods: {
      go,
      async load() {
        try {
          const r = await api.get('/api/analysis/list');
          this.analyses = r.analyses; this.pending = r.pending_sources;
          if (!this.active && this.analyses.length) {
            const wantedAnalysis = store.insightSelection.analysisId;
            const wantedSource = store.insightSelection.sourceId;
            const initial = this.analyses.find((a) => a.id === wantedAnalysis) ||
              this.analyses.find((a) => a.source_content_id === wantedSource) || this.analyses[0];
            await this.selectAnalysis(initial);
          }
          else if (this.active) {
            const cur = this.analyses.find((a) => a.id === this.active.id);
            if (cur) this.active = cur;
          }
          this.patterns = await api.get('/api/analysis/patterns');
          this.comparison = await api.get('/api/analysis/comparison-items');
        } catch (e) { /* 轮询静默 */ }
      },
      async selectAnalysis(item) {
        if (!item) return;
        this.active = item;
        this.originalOpen = false;
        store.insightSelection.analysisId = item.id;
        store.insightSelection.sourceId = item.source_content_id;
        try {
          const detail = await api.get('/api/analysis/detail/' + item.id);
          this.activeSource = detail.source;
        } catch (e) { handleError(e); }
      },
      openSource(sourceId) {
        store.insightSelection.sourceId = sourceId;
        go('extraction');
      },
      copyOriginal() {
        const text = this.activeSource && this.activeSource.text;
        if (!text) return toast('该来源还没有完整原文', 'warn');
        navigator.clipboard && navigator.clipboard.writeText(text);
        toast('原始文案已复制');
      },
      async startAnalysis(sc) {
        try {
          const plan = await api.post('/api/analysis/plans', { source_content_id: sc.id, title: sc.title });
          const costText = store.runtime.demo_mode ? `演示估算 ¥${plan.cost_est}` : '费用以 Provider 实际账单为准';
          if (!confirm(`拆解计划：${plan.model} · ${costText}\n确认后创建任务`)) return;
          const r = await api.post('/api/analysis/jobs', { source_content_id: sc.id, title: sc.title, source_chain: ['提取 #' + sc.id] });
          toast(r.note + ' · 任务 ' + r.job_id);
        } catch (e) { handleError(e); }
      },
      async reanalyzeActive() {
        if (!this.active || !confirm('将基于当前已保存原文创建一个新的拆解版本；旧版本会完整保留。确定继续？')) return;
        try {
          const r = await api.post('/api/analysis/jobs', {
            source_content_id: this.active.source_content_id, title: this.active.source_title,
            source_chain: ['拆解 #' + this.active.id + ' 的人工重做'], force_reanalyze: true,
          });
          toast(r.note + ' · 新任务 ' + r.job_id);
        } catch (e) { handleError(e); }
      },
      openPattern() {
        if (!this.active) return;
        this.patternName = this.active.result.topic || this.active.source_title || '';
        this.patternNote = '来源：拆解 #' + this.active.id;
        this.patternOpen = true;
      },
      async savePattern() {
        if (!this.patternName.trim()) return toast('请填写模式卡名称', 'warn');
        this.patternSaving = true;
        try {
          const r = await api.post('/api/analysis/patterns', { name: this.patternName.trim(), note: this.patternNote.trim() });
          this.patternOpen = false; toast(r.note); await this.load();
        } catch (e) { handleError(e); }
        this.patternSaving = false;
      },
      async toCreation() {
        try {
          const chain = [];
          if (this.active.signal_id) chain.push('雷达 #' + this.active.signal_id);
          chain.push('提取 #' + this.active.source_content_id, '拆解 #' + this.active.id);
          const r = await api.post('/api/creation/projects', {
            title: this.active.result.topic || this.active.source_title,
            source_chain: chain,
            source_content_id: this.active.source_content_id,
            analysis_id: this.active.id,
          });
          toast('已携带拆解 #' + this.active.id + ' 创建创作项目');
          go('creation');
        } catch (e) { handleError(e); }
      },
      openCompare() { if (!this.active) return; this.compareBucket = 'high'; this.compareNote = ''; this.compareOpen = true; },
      async addCompare() {
        if (!this.active) return;
        this.compareSaving = true;
        try {
          const result = await api.post('/api/analysis/comparison-items', { analysis_id: this.active.id, performance_bucket: this.compareBucket, note: this.compareNote.trim() });
          this.compareOpen = false; await this.load(); toast(result.note);
        } catch (e) { handleError(e); }
        this.compareSaving = false;
      },
      bucketLabel(bucket) { return { high: '高表现', medium: '中表现', low: '低表现' }[bucket] || bucket; },
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
          <div class="card-head"><div class="card-title">《{{ active.source_title }}》 <span class="badge" :class="active.result.quality_status==='insufficient_source'?'b-warn':'b-ok'">{{ active.result.quality_status==='insufficient_source' ? '来源不足 · 待重做' : '拆解完成' }}</span></div>
            <span class="mini-note">拆解 #{{ active.id }} · {{ active.model }} · ¥{{ active.cost.toFixed(2) }}</span></div>
          <div class="card-body" style="padding-top:6px">
            <div class="src-chain" style="margin-bottom:12px">来源链：
              <template v-if="active.signal_id"><span class="node" @click="go('radar')">雷达 #{{ active.signal_id }}</span><em>→</em></template>
              <span class="node" @click="openSource(active.source_content_id)">提取 #{{ active.source_content_id }}</span><em>→</em>
              <span class="node">拆解 #{{ active.id }}</span>
            </div>
            <div class="analysis-original" v-if="activeSource&&activeSource.text">
              <div class="analysis-original-head"><span><b>原始文案</b> · {{ activeSource.text_kind }} · {{ activeSource.text_chars }} 字 · 已本地保存</span><div><button class="btn btn-sm" :aria-expanded="originalOpen" @click="originalOpen=!originalOpen">{{ originalOpen ? '收起原文' : '展开原文' }}</button><button class="btn btn-sm" @click="copyOriginal">复制</button><button class="btn btn-sm btn-ghost" @click="openSource(active.source_content_id)">资产证据</button></div></div>
              <pre v-if="originalOpen">{{ activeSource.text }}</pre>
            </div>
            <div class="callout c-warn" v-else><span>原文缺失</span><span>该拆解没有可回看的完整文案。<button class="text-btn" @click="openSource(active.source_content_id)">返回来源资产处理</button></span></div>
            <div class="callout c-warn" v-if="active.result.quality_status==='insufficient_source'"><span>需重做</span><span>{{ active.result.quality_note }}。<button class="text-btn" @click="go('extraction')">返回内容提取并授权 ASR</button></span></div>
            <div class="ana-row"><div class="ana-label">核心选题</div>{{ active.result.topic }}</div>
            <div class="ana-row"><div class="ana-label">目标受众 / 用户问题</div>{{ (active.result.target_audience||[]).join('、') }} · “{{ active.result.user_problem }}”</div>
            <div class="ana-row" v-if="active.result.hook"><div class="ana-label">开头钩子（{{ active.result.hook.cite }}）</div>“{{ active.result.hook.text }}” —— {{ active.result.hook.mechanism }}</div>
            <div class="ana-row"><div class="ana-label">内容结构</div>
              <div class="struct" v-if="active.result.structure&&active.result.structure.length"><div class="seg" v-for="seg in active.result.structure" :key="seg.name" :style="{flex:seg.weight, background:seg.color}"><b>{{ seg.name }}</b><span>{{ seg.span }}</span></div></div><span class="mini-note" v-else>模型未返回可验证的结构段，不补造。</span></div>
            <div class="ana-row" v-if="active.result.key_turns&&active.result.key_turns.length"><div class="ana-label">关键转折</div>“{{ active.result.key_turns[0].text }}” <span class="cite">{{ active.result.key_turns[0].cite }}</span></div>
            <div class="ana-row"><div class="ana-label">论据与案例</div>{{ (active.result.evidence||[]).join('；') }}</div>
            <div class="ana-row"><div class="ana-label">情绪机制</div>{{ (active.result.emotional_mechanisms||[]).join('、') }}</div>
            <div class="ana-row" v-if="active.result.golden_quotes&&active.result.golden_quotes.length"><div class="ana-label">金句与记忆点</div>“{{ active.result.golden_quotes[0].text }}” <span class="cite">{{ active.result.golden_quotes[0].cite }}</span></div>
            <div class="ana-row"><div class="ana-label">结尾动作 / 评论触发点</div>{{ active.result.cta }}</div>
            <div class="ana-row"><div class="ana-label" style="color:var(--ok)">可借鉴部分</div>{{ (active.result.reusable||[]).join(' · ') }}</div>
            <div class="ana-row"><div class="ana-label" style="color:var(--err)">不应照搬部分</div>{{ (active.result.non_reusable||[]).join(' · ') }}</div>
            <div style="display:flex;gap:8px;margin-top:12px;flex-wrap:wrap">
              <button class="btn btn-pri" :disabled="active.result.quality_status==='insufficient_source'" @click="toCreation">转为我的选题</button>
              <button class="btn" @click="reanalyzeActive">重新拆解为新版本</button>
              <button class="btn" :disabled="active.result.quality_status==='insufficient_source'" @click="openPattern">保存为模式卡</button>
              <button class="btn btn-ghost" id="addComparisonBtn" :disabled="active.result.quality_status==='insufficient_source'" @click="openCompare">加入对比组</button>
            </div>
          </div>
        </div>
        <div class="card" v-else><div class="loading">加载中</div></div>

        <div>
          <div class="card section-gap analysis-history-card">
            <div class="card-head"><div class="card-title">历史拆解 <span class="badge b-neu">{{ analyses.length }} 条</span></div></div>
            <div class="card-body analysis-history-list">
              <div class="empty compact" v-if="!analyses.length">暂无历史拆解</div>
              <button type="button" v-for="item in analyses" :key="item.id" class="analysis-history-item" :class="{active:active&&active.id===item.id}" @click="selectAnalysis(item)">
                <b>《{{ item.source_title }}》</b><small>{{ item.original_text_kind }} · {{ item.original_text_chars }} 字 · {{ item.created_at.slice(0,16).replace('T',' ') }}</small>
              </button>
            </div>
          </div>
          <div class="card section-gap">
            <div class="card-head"><div class="card-title">待拆解来源</div></div>
            <div class="card-body" style="padding-top:4px">
              <div class="empty" v-if="!pending.length">暂无待拆解内容</div>
              <div class="kv-row" v-for="sc in pending" :key="sc.id">
                <span>《{{ sc.title }}》</span>
                <button class="btn btn-sm btn-pri" @click="startAnalysis(sc)">拆解</button>
              </div>
              <div class="mini-note" style="margin-top:6px">拆解将调用当前绑定的深度分析模型；确认后才执行，实际用量进入台账。</div>
            </div>
          </div>
          <div class="card section-gap research-tools-card">
            <button type="button" class="collapsible-head" :aria-expanded="researchOpen" @click="researchOpen=!researchOpen">
              <span><b>研究工具</b> <span class="badge b-neu">模式卡 {{ patterns.length }}</span> <span class="badge b-neu">对比样本 {{ comparison.length }}</span><small>把单条拆解沉淀成可复用规律，再用高/中/低表现验证</small></span>
              <span>{{ researchOpen ? '收起' : '展开使用' }} <i>{{ researchOpen ? '▴' : '▾' }}</i></span>
            </button>
            <div class="card-body research-tools-body" v-if="researchOpen">
              <div class="research-explainer"><b>内容模式卡是什么？</b><span>把一条拆解里的结构、钩子或表达规律存成草稿。它不是结论；积累多个真实样本后，才适合作为创作规则。</span><button class="btn btn-sm" :disabled="!active || active.result.quality_status==='insufficient_source'" @click="openPattern">从当前拆解保存</button></div>
              <div class="pattern-card" v-for="p in patterns" :key="p.id">
                <b>{{ p.name }}</b><span class="badge" :class="patternBadge(p)[0]">{{ patternBadge(p)[1] }}</span>
                <div class="mini-note">来源样本 {{ p.samples }} · 置信度 {{ p.confidence }} · {{ p.fit }} · {{ p.note }}</div>
              </div>
              <div class="empty compact" v-if="!patterns.length">还没有模式卡。先从当前拆解保存一个草稿，后续用更多作品验证。</div>
              <div class="research-divider"></div>
              <div class="research-explainer"><b>组合拆解怎么用？</b><span>把同类作品按真实数据分到高/中/低表现组，横向比较共同结构，避免凭感觉认定“爆款公式”。</span><button class="btn btn-sm" :disabled="!active || active.result.quality_status==='insufficient_source'" @click="openCompare">加入当前样本</button></div>
              <div class="kv-row" v-for="item in comparison" :key="item.analysis_id"><span><span class="badge" :class="item.performance_bucket==='high'?'b-ok':item.performance_bucket==='low'?'b-warn':'b-neu'">{{ bucketLabel(item.performance_bucket) }}</span> {{ item.title }}</span><button class="btn btn-sm" @click="selectAnalysis(analyses.find(a=>a.id===item.analysis_id))">查看</button></div>
              <div class="empty compact" v-if="!comparison.length">暂无对比样本。至少加入 1 条高表现和 1 条普通/低表现作品，比较才有意义。</div>
            </div>
          </div>
        </div>
      </div>

      <div class="modal analysis-compare-modal" :class="{open:compareOpen}" role="dialog" aria-modal="true" aria-labelledby="analysisCompareTitle" @click.self="compareOpen=false">
        <div class="modal-card"><div class="modal-head"><div class="modal-title" id="analysisCompareTitle">加入组合拆解</div><button class="modal-x" aria-label="关闭" @click="compareOpen=false">×</button></div><div class="modal-body"><p v-if="active">《{{ active.source_title }}》</p><label class="field"><span>实际表现分组</span><select class="input" v-model="compareBucket"><option value="high">高表现</option><option value="medium">中表现</option><option value="low">低表现</option></select><small>这里记录事实分组，不由模型猜测。</small></label><label class="field"><span>依据或备注（可选）</span><input class="input" v-model="compareNote" placeholder="例如：发布 7 天播放高于账号中位数 2.4 倍"></label></div><div class="modal-foot"><button class="btn" @click="compareOpen=false">取消</button><button class="btn btn-pri" :disabled="compareSaving" @click="addCompare">{{ compareSaving ? '保存中…' : '加入对比组' }}</button></div></div>
      </div>
      <div class="modal analysis-pattern-modal" :class="{open:patternOpen}" role="dialog" aria-modal="true" aria-labelledby="analysisPatternTitle" @click.self="patternOpen=false">
        <div class="modal-card"><div class="modal-head"><div class="modal-title" id="analysisPatternTitle">保存模式卡草稿</div><button class="modal-x" aria-label="关闭" @click="patternOpen=false">×</button></div><div class="modal-body"><label class="field"><span>模式名称</span><input class="input" v-model="patternName" maxlength="120"></label><label class="field"><span>证据说明</span><textarea class="input" rows="3" v-model="patternNote" maxlength="300"></textarea></label><div class="callout c-info"><span>证据</span><span>模式卡先以草稿保存；只有积累真实样本后才可提升置信度。</span></div></div><div class="modal-foot"><button class="btn" @click="patternOpen=false">取消</button><button class="btn btn-pri" :disabled="patternSaving || !patternName.trim()" @click="savePattern">{{ patternSaving ? '保存中…' : '保存草稿' }}</button></div></div>
      </div>
    </section>`,
  };
})();
