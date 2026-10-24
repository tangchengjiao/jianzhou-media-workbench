/* 页面：资料中心 / 系统设置 */
(function () {
  const go = (p) => { window.__app && window.__app.go(p); };

  /* ================= 统一资产中心 ================= */
  window.PagePackages = {
    data() {
      return {
        tab: 'all', loading: true, syncing: false, analysisKind: 'all', creationKind: 'all',
        summary: { total: 0, sources: 0, reusable: 0, unlinked_works: 0, counts: {} },
        assets: [], total: 0, facets: { types: [], platforms: [], sources: [] },
        query: { q: '', platform: 'all', source_kind: 'all', sort: 'updated', favorite: false },
        selected: null, detailOpen: false, collections: [], collectionDraft: '', collectionChoice: '',
        packages: [], imports: { flow: [], batches: [] }, tracking: [], users: [],
        uploading: false, lastPlan: null,
      };
    },
    async mounted() {
      const params = new URLSearchParams(location.search);
      const requestedTab = params.get('asset_tab');
      const requestedKind = params.get('asset_kind');
      if (['all','ideas','sources','text','analysis','drafts','channels','visual','published','packages'].includes(requestedTab)) this.tab = requestedTab;
      if (this.tab === 'drafts' && ['all','project','decision','draft'].includes(requestedKind)) this.creationKind = requestedKind;
      if (this.tab === 'analysis' && ['all','analysis','pattern'].includes(requestedKind)) this.analysisKind = requestedKind;
      await this.load();
    },
    computed: {
      activeType() {
        if (this.tab === 'analysis') return this.analysisKind === 'all' ? 'analysis,pattern' : this.analysisKind;
        if (this.tab === 'drafts') return this.creationKind === 'all' ? 'project,decision,draft' : this.creationKind;
        return ({ all: 'all', ideas: 'idea', sources: 'source', text: 'transcript,clean_text',
          channels: 'campaign,channel_deliverable,moment,official_article,channel_retro',
          visual: 'visual_batch,visual,visual_page,visual_deliverable', published: 'publication,retro' })[this.tab] || 'all';
      },
    },
    methods: {
      go,
      async load() {
        this.loading = true;
        try {
          const [summary, facets, p, i, u, collections] = await Promise.all([
            api.get('/api/assets/summary'), api.get('/api/assets/facets'),
            api.get('/api/packages'), api.get('/api/imports'), api.get('/api/users'),
            api.get('/api/assets/collections/list'),
          ]);
          this.summary = summary; this.facets = facets; this.packages = p; this.imports = i;
          this.users = u; this.collections = collections;
          this.tracking = await api.get('/api/packages/sources/tracking');
          await this.loadAssets();
        } catch (e) { handleError(e); }
        this.loading = false;
      },
      async loadAssets() {
        const params = new URLSearchParams({ asset_type: this.activeType, platform: this.query.platform,
          source_kind: this.query.source_kind, sort: this.query.sort, limit: '120' });
        if (this.query.q.trim()) params.set('q', this.query.q.trim());
        if (this.query.favorite) params.set('favorite', 'true');
        const result = await api.get('/api/assets?' + params.toString());
        this.assets = result.items; this.total = result.total;
      },
      async switchTab(tab) {
        this.tab = tab;
        if (tab !== 'packages') await this.loadAssets();
      },
      async switchAssetKind(kind, value) {
        this[kind] = value;
        await this.loadAssets();
      },
      async syncAssets() {
        this.syncing = true;
        try { const result = await api.post('/api/assets/sync'); toast(result.note); await this.load(); }
        catch (e) { handleError(e); }
        this.syncing = false;
      },
      async openAsset(asset) {
        try {
          this.selected = await api.get('/api/assets/' + encodeURIComponent(asset.id));
          this.collectionChoice = ''; this.detailOpen = true;
        } catch (e) { handleError(e); }
      },
      async favorite(asset) {
        try { const result = await api.post('/api/assets/' + asset.id + '/favorite'); asset.favorite = result.favorite ? 1 : 0; }
        catch (e) { handleError(e); }
      },
      async createCollection() {
        if (!this.collectionDraft.trim()) return;
        try {
          await api.post('/api/assets/collections', { name: this.collectionDraft.trim(), description: '' });
          this.collectionDraft = ''; this.collections = await api.get('/api/assets/collections/list');
          toast('资产合集已创建');
        } catch (e) { handleError(e); }
      },
      async addToCollection() {
        if (!this.collectionChoice || !this.selected) return;
        try {
          await api.post('/api/assets/collections/' + this.collectionChoice + '/items', { asset_id: this.selected.asset.id });
          toast('已加入资产合集'); this.selected = await api.get('/api/assets/' + this.selected.asset.id);
        } catch (e) { handleError(e); }
      },
      assetAction(asset) {
        const type = asset.asset_type;
        if (['visual','visual_page'].includes(type) && asset.metadata.preview_url) return window.open(asset.metadata.preview_url, '_blank', 'noopener');
        if (type === 'own_work') return go('account');
        if (type === 'source' || type === 'transcript' || type === 'clean_text') return go('extraction');
        if (type === 'analysis' || type === 'pattern') return go('analysis');
        if (type === 'idea') {
          location.href = '/?idea=' + encodeURIComponent(asset.entity_id) + '#/creation';
          return;
        }
        if (['campaign','channel_deliverable','moment','official_article','channel_retro'].includes(type)) {
          const params = new URLSearchParams();
          const campaignId = asset.metadata.campaign_id || (type === 'campaign' ? asset.entity_id : '');
          if (campaignId) params.set('campaign', campaignId);
          if (type === 'official_article') {
            params.set('channel_tab', 'official'); params.set('article', asset.entity_id);
          } else if (type === 'moment') {
            params.set('channel_tab', 'moments'); params.set('moment', asset.entity_id);
          } else if (type === 'channel_retro') {
            params.set('channel_tab', asset.metadata.channel === 'wechat_official' ? 'official' : 'moments');
            if (asset.metadata.deliverable_id) params.set('performance', asset.metadata.deliverable_id);
          } else if (type === 'channel_deliverable') {
            params.set('channel_tab', (asset.platform === 'wechat_official' || asset.platform === '公众号' || asset.source_kind === 'official_article') ? 'official' : 'moments');
            if (asset.entity_id) params.set('deliverable', asset.entity_id);
          }
          location.href = '/?' + params.toString() + '#/channels';
          return;
        }
        if (type === 'project' || type === 'decision' || type === 'draft') {
          const projectId = asset.metadata.project_id || (type === 'project' ? asset.entity_id : '');
          const params = new URLSearchParams();
          if (projectId) params.set('project', projectId);
          if (type === 'draft') params.set('draft', asset.entity_id);
          location.href = '/?' + params.toString() + '#/creation';
          return;
        }
        if (['visual_batch','visual_deliverable'].includes(type)) {
          const params = new URLSearchParams();
          const projectId = asset.metadata.creation_project_id || asset.metadata.project_id || '';
          if (projectId) params.set('project', projectId);
          params.set('visual_tab', 'history');
          location.href = '/?' + params.toString() + '#/visuals';
          return;
        }
        if (type === 'publication' || type === 'retro') {
          const publicationId = type === 'publication' ? asset.entity_id : asset.metadata.publication_id;
          location.href = '/?' + (publicationId ? ('publication=' + encodeURIComponent(publicationId)) : '') + '#/retro';
          return;
        }
      },
      assetTypeLabel(type) { return (this.facets.types.find((item) => item.id === type) || {}).label || type; },
      assetHint(asset) {
        if (asset.asset_type === 'moment') return '编辑朋友圈成品';
        if (asset.asset_type === 'official_article') return '编辑公众号文章';
        if (asset.asset_type === 'campaign' || asset.asset_type === 'channel_deliverable') return '打开渠道运营';
        if (asset.asset_type === 'channel_retro') return '查看渠道复盘';
        return ({ idea: '带着这条灵感去创作', own_work: '查看账号作品', signal: '查看雷达来源', source: '继续提取或拆解', transcript: '继续拆解',
          clean_text: '继续拆解', analysis: '查看拆解版本', pattern: '复用模式卡', project: '打开项目', decision: '回到所属项目', draft: '打开此稿件版本',
          visual_batch: '打开视觉工厂', visual: '查看成品', visual_page: '查看图文页面', visual_deliverable: '打开视觉交付物',
          publication: '查看发布复盘', retro: '查看复盘' })[asset.asset_type] || '查看详情';
      },
      assetContext(asset) {
        if (!asset || !asset.metadata) return '';
        if (asset.asset_type === 'project') return `${asset.metadata.draft_count || 0} 个稿件版本`;
        if (asset.asset_type === 'decision') return `所属项目：${asset.metadata.project_title || '未命名项目'}`;
        if (asset.asset_type === 'draft') return `所属项目：${asset.metadata.project_title || '未命名项目'} · v${asset.metadata.version_no || 1}`;
        return '';
      },
      contentText(detail) {
        if (!detail) return '';
        if (detail.content && detail.content.text) return detail.content.text;
        return JSON.stringify((detail.content || {}).structured || {}, null, 2);
      },
      versionLabel(version) {
        return `${version.kind || '版本'} · v${version.version_no || 1} · ${(version.created_at || '').replace('T',' ').slice(0,16)}`;
      },
      switchUser(u) { if (!u.active) bus.emit('open-switch', u.id); },
      async impact(p) {
        try {
          const r = await api.get('/api/packages/' + p.id + '/impact');
          bus.emit('open-trace', { title: '删除影响 · ' + r.package, src: r.sources + ' 个来源', quote: `${r.note}\n引用：${(r.referenced_by || []).join('、') || '无'}` });
        } catch (e) { handleError(e); }
      },
      async uploadFile(e) {
        const file = e.target.files[0];
        if (!file) return;
        this.uploading = true;
        const fd = new FormData();
        fd.append('file', file);
        try {
          const r = await api.upload('/api/imports/upload?target_package=' + encodeURIComponent('内容语料包'), fd);
          this.lastPlan = r;
          toast(r.note, r.failed ? 'warn' : 'ok');
          await this.load();
        } catch (e) { handleError(e); }
        this.uploading = false;
        e.target.value = '';
      },
      async commit(bid) {
        try { const r = await api.post('/api/imports/' + bid + '/commit'); toast(r.note, 'warn'); this.lastPlan = null; await this.load(); }
        catch (e) { handleError(e); }
      },
      async rollback(bid) {
        if (!confirm('二次确认：将回滚整个批次 ' + bid + '，已写入数据将被移除。确定继续？')) return;
        try { const r = await api.post('/api/imports/' + bid + '/rollback'); toast(r.note, 'warn'); await this.load(); }
        catch (e) { handleError(e); }
      },
      batchBadge(b) {
        return {
          parsed: ['b-info', '待确认'], partial: ['b-warn', '部分成功 · 待确认'],
          committed: ['b-ok', '已写入'], partial_committed: ['b-warn', '部分成功'],
          rolled_back: ['b-neu', '已回滚'],
        }[b.status] || ['b-neu', b.status];
      },
    },
    template: `
    <section>
      <div class="page-head">
        <div><div class="page-title">资产中心</div><div class="page-q">所有原始内容、文案、拆解、稿件、视觉和复盘，都能在这里找到并继续使用。</div></div>
        <button class="btn btn-pri" :disabled="syncing" @click="syncAssets">{{ syncing ? '正在刷新关系…' : '刷新资产索引' }}</button>
      </div>
      <div class="asset-summary-grid section-gap">
        <div class="asset-stat"><span>全部资产</span><b>{{ summary.total }}</b><small>统一索引，不复制原始文件</small></div>
        <div class="asset-stat"><span>来源资产</span><b>{{ summary.sources }}</b><small>自有作品、雷达与原始内容</small></div>
        <div class="asset-stat"><span>可复用成果</span><b>{{ summary.reusable }}</b><small>文案、拆解、稿件与视觉</small></div>
        <div class="asset-stat" :class="{'has-gap':summary.unlinked_works}"><span>仅元数据作品</span><b>{{ summary.unlinked_works }}</b><small>提取后才能参与语义运营分析</small></div>
      </div>
      <div class="asset-tabs" role="tablist">
        <button v-for="item in [['all','全部'],['ideas','灵感与随手记'],['sources','原始内容'],['text','提取文案'],['analysis','拆解与模式'],['drafts','创作稿件'],['channels','渠道成品'],['visual','视觉资产'],['published','发布与复盘'],['packages','数据包与导入']]" :key="item[0]" class="asset-tab" :class="{active:tab===item[0]}" @click="switchTab(item[0])">{{ item[1] }}</button>
      </div>
      <template v-if="tab!=='packages'">
        <div class="asset-kind-filter" v-if="tab==='analysis'">
          <button class="chip" :class="{active:analysisKind==='all'}" @click="switchAssetKind('analysisKind','all')">全部成果</button><button class="chip" :class="{active:analysisKind==='analysis'}" @click="switchAssetKind('analysisKind','analysis')">内容拆解</button><button class="chip" :class="{active:analysisKind==='pattern'}" @click="switchAssetKind('analysisKind','pattern')">模式卡</button>
        </div>
        <div class="asset-kind-filter" v-if="tab==='drafts'">
          <button class="chip" :class="{active:creationKind==='all'}" @click="switchAssetKind('creationKind','all')">全部创作资产</button><button class="chip" :class="{active:creationKind==='project'}" @click="switchAssetKind('creationKind','project')">创作项目</button><button class="chip" :class="{active:creationKind==='decision'}" @click="switchAssetKind('creationKind','decision')">差异化方向</button><button class="chip" :class="{active:creationKind==='draft'}" @click="switchAssetKind('creationKind','draft')">稿件版本</button>
        </div>
        <div class="creation-asset-guide" v-if="tab==='drafts'">
          <div><b>创作项目</b><span>一个选题的完整主线，承载方向、稿件、视觉与发布。</span></div>
          <div><b>差异化方向</b><span>该项目为什么这样写的决策记录，不是另一篇稿件。</span></div>
          <div><b>稿件版本</b><span>每次生成或人工保存的正文快照；点击会打开准确项目和准确版本。</span></div>
        </div>
        <div class="asset-toolbar section-gap"><label class="asset-search"><span>搜索</span><input class="input" v-model="query.q" placeholder="标题、正文、作者或标签" @keyup.enter="loadAssets"></label><label><span>平台</span><select class="input input-sm" v-model="query.platform" @change="loadAssets"><option value="all">全部平台</option><option v-for="item in facets.platforms" :key="item">{{ item }}</option></select></label><label><span>来源</span><select class="input input-sm" v-model="query.source_kind" @change="loadAssets"><option value="all">全部来源</option><option v-for="item in facets.sources" :key="item">{{ item }}</option></select></label><label><span>排序</span><select class="input input-sm" v-model="query.sort" @change="loadAssets"><option value="updated">最近更新</option><option value="created">最近创建</option><option value="favorite">收藏优先</option><option value="title">按标题</option></select></label><button class="chip" :class="{active:query.favorite}" @click="query.favorite=!query.favorite;loadAssets()">只看收藏</button><button class="btn btn-pri" @click="loadAssets">搜索</button></div>
        <div class="asset-result-head"><div><b>{{ total }}</b> 项结果</div><span>点击资产可查看完整内容与上下游来源</span></div>
        <div class="asset-grid" v-if="assets.length"><article class="asset-card" v-for="asset in assets" :key="asset.id" @click="openAsset(asset)"><div class="asset-card-top"><span class="asset-type">{{ asset.type_label }}</span><span class="badge b-neu" v-if="asset.metadata.version_count">{{ asset.metadata.version_count }} 个版本</span><button class="asset-favorite" :class="{active:asset.favorite}" @click.stop="favorite(asset)" :aria-label="asset.favorite?'取消收藏':'收藏'">★</button></div><b class="asset-title">{{ asset.title }}</b><small class="asset-context" v-if="assetContext(asset)">{{ assetContext(asset) }}</small><p>{{ asset.summary || '暂无摘要，打开查看来源详情。' }}</p><div class="asset-meta"><span v-if="asset.platform">{{ asset.platform }}</span><span>{{ asset.source_kind || '本地' }}</span><span>{{ (asset.updated_at||'').slice(0,10) }}</span></div><button class="text-btn" @click.stop="assetAction(asset)">{{ assetHint(asset) }} →</button></article></div>
        <div class="empty card" v-else><b>当前筛选下没有资产</b><span>修改筛选条件，或先从雷达、账号作品和外部导入沉淀内容。</span></div>
      </template>
      <template v-else>
        <div class="callout c-info section-gap"><span>◈</span><span><b>数据包与导入：</b>这里管理可替换的数据包和外部文件；全部内部成果请回到上方资产分类查看。</span></div>
        <div class="grid g4 section-gap"><div class="card" v-for="p in packages" :key="p.id"><div class="card-body"><div class="asset-pack-head"><b>{{ p.name }}</b><span class="badge" :class="p.status==='active'?'b-ok':'b-neu'">{{ p.status==='active'?'激活中':p.status }}</span></div><div class="mini-note">{{ p.version }} · 来源 {{ p.source_count }}<br>{{ p.summary }}</div><button class="btn btn-sm" @click="impact(p)">查看引用影响</button></div></div></div>
        <div class="grid g2 section-gap"><div class="card"><div class="card-head"><div class="card-title">外部导入</div><label class="btn btn-pri">选择文件<input type="file" hidden @change="uploadFile" accept=".csv,.xlsx,.json,.md,.txt,.zip"></label></div><div class="card-body"><div class="iv-flow"><span v-for="(f,i) in imports.flow" :key="f"><em v-if="i">→</em><span>{{ f }}</span></span></div><div class="callout section-gap" v-if="lastPlan" :class="lastPlan.failed?'c-warn':'c-info'"><span>导入计划</span><span>成功 {{ lastPlan.success }} · 异常 {{ lastPlan.failed }}<br><button class="btn btn-pri btn-sm" @click="commit(lastPlan.batch_id)">确认写入</button></span></div><div class="empty" v-else>导入默认只保存；确认后才写入，不会自动产生模型费用。</div></div></div><div class="card"><div class="card-head"><div class="card-title">导入批次</div></div><div class="card-body"><div class="kv-row" v-for="b in imports.batches" :key="b.id"><span>{{ b.filename }}<small>成功 {{ b.success }} · 失败 {{ b.failed }}</small></span><span class="badge" :class="batchBadge(b)[0]">{{ batchBadge(b)[1] }}</span></div><div class="empty" v-if="!imports.batches.length">暂无导入批次</div></div></div></div>
      </template>
      <div class="modal asset-detail-modal" :class="{open:detailOpen}" role="dialog" aria-modal="true" @click.self="detailOpen=false"><div class="modal-card"><div class="modal-head"><div><div class="modal-title">{{ selected&&selected.asset.title }}</div><div class="mini-note" v-if="selected">{{ selected.asset.type_label }} · {{ selected.asset.platform || selected.asset.source_kind }}</div></div><button class="modal-x" @click="detailOpen=false">×</button></div><div class="modal-body" v-if="selected"><div class="asset-detail-actions"><button class="btn btn-pri" @click="assetAction(selected.asset)">{{ assetHint(selected.asset) }}</button><select class="input input-sm" v-model="collectionChoice"><option value="">选择资产合集</option><option v-for="item in collections" :value="item.id" :key="item.id">{{ item.name }}（{{ item.item_count }}）</option></select><button class="btn" :disabled="!collectionChoice" @click="addToCollection">加入合集</button></div><section class="asset-detail-section"><h4>完整内容</h4><pre v-if="contentText(selected)">{{ contentText(selected) }}</pre><div class="empty" v-else>该资产当前只有元数据；提取正文后会在这里显示完整内容。</div></section><section class="asset-detail-section" v-if="selected.content&&selected.content.versions&&selected.content.versions.length"><h4>版本记录</h4><div class="asset-version-list"><details v-for="version in selected.content.versions" :key="version.id"><summary><b>{{ versionLabel(version) }}</b><span>{{ version.provider || version.status }}</span></summary><pre>{{ JSON.stringify(version.content||{},null,2) }}</pre></details></div></section><section class="asset-detail-section"><h4>来源与下游</h4><div class="asset-lineage" v-if="selected.relations.length"><button v-for="rel in selected.relations" :key="rel.id+rel.relation_type" @click="openAsset(rel)"><span>{{ rel.relation_type }}</span><b>{{ rel.title }}</b><small>{{ assetTypeLabel(rel.asset_type) }} · {{ rel.platform }}</small></button></div><div class="empty" v-else>尚无上下游关系。</div></section><section class="asset-detail-section"><h4>新建资产合集</h4><div class="asset-new-collection"><input class="input" v-model="collectionDraft" placeholder="例如：本周AI选题素材"><button class="btn" @click="createCollection">创建</button></div></section></div></div></div>
    </section>`,
  };

  /* ================= 系统设置 ================= */
  window.PageSettings = {
    data() {
      return {
        tab: 'execution', settings: {}, diag: null, errors: [], usage: null,
        ledger: [], traceQ: '', traceResult: null, saving: false,
        testingSource: false, testingLlm: false, testingAsr: false, testingImage: false, loadingModels: false,
        sourceTest: { platform: 'douyin', link: '' },
        sourceResult: null, llmResult: null, asrResult: null, imageResult: null, asrFile: null, availableModels: [],
        saveResult: null,
        secretDrafts: { source: '', llm: '', voice: '', image: '' },
        tabs: [
          ['execution', '运行模式'], ['source', '数据来源'], ['llm', '大模型'], ['image', '图片模型'], ['voice', '语音模型'],
          ['cost', '成本控制'], ['local', '本地数据'], ['diag', '系统诊断'],
        ],
      };
    },
    async mounted() {
      await this.load();
      const requested = new URLSearchParams(location.search).get('settings');
      if (this.tabs.some((item) => item[0] === requested)) await this.switchTab(requested);
    },
    methods: {
      async load() {
        try {
          this.settings = await api.get('/api/settings');
          this.usage = await api.get('/api/usage/summary');
        } catch (e) { handleError(e); }
      },
      async loadDiag() {
        try {
          this.diag = await api.get('/api/diagnostics');
          this.errors = await api.get('/api/diagnostics/errors');
          this.ledger = await api.get('/api/usage/ledger');
        } catch (e) { handleError(e); }
      },
      async switchTab(t) {
        this.tab = t;
        if (t === 'diag') await this.loadDiag();
      },
      async save(section, showToast = true) {
        this.saving = true;
        try {
          const values = { ...this.settings[section] };
          delete values.tikhub_api_key_configured;
          delete values.api_key_configured;
          if (section === 'source' && this.secretDrafts.source) values.tikhub_api_key = this.secretDrafts.source;
          if (section === 'llm' && this.secretDrafts.llm) values.api_key = this.secretDrafts.llm;
          if (section === 'voice' && this.secretDrafts.voice) values.api_key = this.secretDrafts.voice;
          if (section === 'image' && this.secretDrafts.image) values.api_key = this.secretDrafts.image;
          const r = await api.put('/api/settings', { section, values });
          if (section === 'source' && this.settings.datasource) {
            await api.put('/api/settings', { section: 'datasource', values: this.settings.datasource });
          }
          this.secretDrafts[section] = '';
          await this.load();
          if (section === 'execution') store.runtime.demo_mode = r.demo_mode;
          const summaries = {
            source: `TikHub · ${this.settings.datasource.providers.tikhub.baseUrl} · Key ${this.settings.source.tikhub_api_key_configured ? '已配置' : '未配置'}`,
            llm: `${this.settings.llm.api_base} · ${this.settings.llm.fast_model}`,
            voice: `${this.settings.voice.api_base} · ${this.settings.voice.asr_model}`,
            image: `${this.settings.image.api_base || '未配置地址'} · ${this.settings.image.model || '未配置模型'}`,
            execution: this.settings.execution.mode === 'real' ? '真实 Provider 模式' : '演示模式',
          };
          this.saveResult = { section, note: r.note, summary: summaries[section] || '本分区配置已写入本机数据库', at: new Date().toLocaleTimeString() };
          if (section === 'execution') await refreshTopbar();
          if (showToast) toast(r.note);
          this.saving = false;
          return true;
        } catch (e) { handleError(e); }
        this.saving = false;
        return false;
      },
      async setMode(mode) {
        this.settings.execution.mode = mode;
        if (await this.save('execution', false)) {
          toast(mode === 'real' ? '已切换到独立真实工作空间；这里只显示你真实录入和同步的数据。' : '已切换到独立演示工作空间。');
        }
      },
      async testSource() {
        if (!this.sourceTest.link.trim()) return toast('请先填写一个公开账号主页或账号 ID', 'warn');
        if (!(await this.save('source', false))) return;
        this.testingSource = true; this.sourceResult = null;
        try {
          this.sourceResult = await api.post('/api/datasource/check', {
            platform: this.sourceTest.platform, link: this.sourceTest.link.trim(),
          });
          toast(this.sourceResult.note);
        } catch (e) { handleError(e); }
        this.testingSource = false;
      },
      openAccountFromSource() {
        const q = new URLSearchParams({ account: 'add', platform: this.sourceTest.platform, link: this.sourceTest.link.trim() });
        location.href = '/?' + q.toString() + '#/account';
      },
      openBenchmarkFromSource() {
        const q = new URLSearchParams({ radar: 'add', platform: this.sourceTest.platform, link: this.sourceTest.link.trim() });
        location.href = '/?' + q.toString() + '#/radar';
      },
      async loadModels() {
        if (!(await this.save('llm', false))) return;
        this.loadingModels = true; this.llmResult = null;
        try {
          const result = await api.get('/api/llm/models');
          this.availableModels = result.models || [];
          this.llmResult = { ok: true, note: result.note };
          toast(result.note);
        } catch (e) { handleError(e); }
        this.loadingModels = false;
      },
      async testLlm() {
        if (!(await this.save('llm', false))) return;
        this.testingLlm = true; this.llmResult = null;
        try {
          const result = await api.post('/api/llm/check', { model: this.settings.llm.fast_model });
          this.llmResult = { ok: true, note: result.note, model: result.model };
          toast(result.note);
        } catch (e) { handleError(e); }
        this.testingLlm = false;
      },
      async testImage() {
        if (!(await this.save('image', false))) return;
        this.testingImage = true; this.imageResult = null;
        try {
          this.imageResult = await api.post('/api/image/check', {});
          toast(this.imageResult.note);
        } catch (e) { handleError(e); }
        this.testingImage = false;
      },
      pickAsrFile(event) { this.asrFile = event.target.files && event.target.files[0]; },
      async testAsr() {
        if (!this.asrFile) return toast('请先选择一个 10 MB 以内的音频文件', 'warn');
        if (!(await this.save('voice', false))) return;
        this.testingAsr = true; this.asrResult = null;
        try {
          const form = new FormData(); form.append('file', this.asrFile);
          this.asrResult = await api.upload('/api/stt/check', form);
          toast(this.asrResult.note);
        } catch (e) { handleError(e); }
        this.testingAsr = false;
      },
      async queryTrace() {
        if (!this.traceQ.trim()) return;
        try {
          this.traceResult = await api.get('/api/diagnostics/trace/' + this.traceQ.trim());
          if (!this.traceResult.errors.length && !this.traceResult.jobs.length) toast('未找到该 Trace ID 记录', 'warn');
        } catch (e) { handleError(e); }
      },
      checkBadge(s) { return s === 'ok' ? 'b-ok' : s === 'warn' ? 'b-warn' : 'b-err'; },
      fieldItems(section) {
        const v = this.settings[section];
        if (!v) return [];
        const labels = {
          tikhub_api_key: 'TikHub API Key（仅存本机 · 脱敏显示）', sync_frequency: '同步频率',
          daily_request_limit: '每日请求限额', cache_policy: '缓存策略',
          stt_provider: 'STT 提供商', tts_provider: 'TTS 提供商', interview_mode: '默认访谈模式',
          language: '转写语言', hot_words: '热词词典',
          fast_model: '快速分类模型', analysis_model: '深度分析模型', writing_model: '文案生成模型',
          structured_model: '结构化抽取模型', embedding_model: 'Embedding 模型',
          api_base: 'API 地址', api_key: 'API Key（仅存本机）',
          daily_budget: '每日预算（¥）', monthly_budget: '每月预算（¥）',
          confirm_threshold: '单次任务确认阈值（¥）', max_media_minutes: '媒体最长（分钟）',
          max_comments: '评论最多（条）', cache_days: '缓存有效期（天）',
          weekly_capacity_hours: '每周内容产能（小时）',
          auto_backup: '自动备份', theme: '主题（light / dark）',
        };
        return Object.keys(v).filter((k) => !k.endsWith('_configured'))
          .map((k) => ({ key: k, label: labels[k] || k, value: v[k] }));
      },
    },
    template: `
    <section>
      <div class="page-head">
        <div><div class="page-title">系统设置</div><div class="page-q">配置数据源、模型、成本、安全和诊断。</div></div>
      </div>

      <div class="tabs">
        <button class="tab" v-for="t in tabs" :key="t[0]" :class="{active:tab===t[0]}" :aria-pressed="tab===t[0]" @click="switchTab(t[0])">{{ t[1] }}
          <span class="badge b-err" v-if="t[0]==='diag' && errors.length" style="margin-left:2px">{{ errors.length }}</span></button>
      </div>

      <div class="callout c-ok section-gap" role="status" aria-live="polite" v-if="saveResult && saveResult.section===tab">
        <span>✓</span><span><b>已保存并生效</b> · {{ saveResult.at }}<br>{{ saveResult.summary }}<small style="display:block">{{ saveResult.note }}</small></span>
      </div>

      <div class="settings-mode-grid section-gap" v-if="tab==='execution' && settings.execution">
        <button type="button" class="settings-mode-card" :class="{active:settings.execution.mode==='demo'}" :aria-pressed="settings.execution.mode==='demo'" @click="setMode('demo')">
          <span class="settings-mode-check">{{ settings.execution.mode==='demo' ? '✓' : '' }}</span><b>演示模式</b><small>使用明确标记的内置数据，不产生外部调用费用。</small>
        </button>
        <button type="button" class="settings-mode-card" :class="{active:settings.execution.mode==='real'}" :aria-pressed="settings.execution.mode==='real'" @click="setMode('real')">
          <span class="settings-mode-check">{{ settings.execution.mode==='real' ? '✓' : '' }}</span><b>真实模式</b><small>立即使用你保存的 TikHub 与模型 Provider；配置缺失时显式失败。</small>
        </button>
        <div class="card settings-mode-summary"><div class="card-body">
          <div class="settings-section-title">当前生效状态 <span class="badge" :class="store.runtime.demo_mode?'b-vio':'b-ok'">{{ store.runtime.demo_mode ? '演示' : '真实' }}</span></div>
          <p>两个模式使用完全隔离的数据空间：切换不会删除任何历史数据；真实空间只展示你录入、导入或真实同步的内容，演示记录不会混入。</p>
          <div class="settings-readiness"><span>数据源 Key <b>{{ settings.source && settings.source.tikhub_api_key_configured ? '已保存' : '未保存' }}</b></span><span>模型 Key <b>{{ settings.llm && (settings.llm.auth_mode==='none'||settings.llm.api_key_configured) ? '已就绪' : '未保存' }}</b></span><span>图片模型 <b>{{ settings.image && settings.image.api_base && (settings.image.auth_mode==='none'||settings.image.api_key_configured) ? '已就绪' : '未配置' }}</b></span><span>模型地址 <b>{{ settings.llm && settings.llm.api_base ? '已填写' : '未填写' }}</b></span></div>
        </div></div>
      </div>

      <div class="card section-gap settings-provider-card" v-if="tab==='source' && settings.source && settings.datasource">
        <div class="card-body">
          <div class="settings-section-head"><div><div class="settings-section-title">TikHub 数据源</div><p>保存后使用公开账号做一次真实连通测试；测试可能产生接口费用。</p></div><span class="badge" :class="settings.source.tikhub_api_key_configured?'b-ok':'b-warn'">{{ settings.source.tikhub_api_key_configured ? 'Key 已保存' : '待配置' }}</span></div>
          <div class="grid g2">
            <label class="field"><span>API Base URL</span><input class="input mono" v-model="settings.datasource.providers.tikhub.baseUrl" placeholder="https://api.tikhub.io"><small>大陆网络可使用 https://api.tikhub.dev</small></label>
            <label class="field"><span>TikHub API Key（系统安全凭据库）</span><input class="input mono" type="password" v-model="secretDrafts.source" :placeholder="settings.source.tikhub_api_key_configured ? '已配置 · 留空表示不修改' : '粘贴 API Key'"><small>Windows 使用 DPAPI，macOS 使用登录钥匙串；密钥不会通过设置接口回传。</small></label>
            <label class="field"><span>测试平台</span><select class="input" v-model="sourceTest.platform"><option value="douyin">抖音</option><option value="xhs">小红书</option><option value="bilibili">B站</option><option value="kuaishou">快手</option><option value="weibo">微博</option><option value="channels">视频号</option><option value="tiktok">TikTok</option></select></label>
            <label class="field"><span>公开账号主页 / 账号 ID</span><input class="input" v-model="sourceTest.link" placeholder="用于真实解析和账号资料测试"><small>不会再使用无效的 check_probe。</small></label>
          </div>
          <div class="settings-actions"><button class="btn btn-pri" :disabled="saving" @click="save('source')">保存数据源</button><button class="btn" :disabled="testingSource||saving" @click="testSource">{{ testingSource ? '正在测试…' : '保存并真实测试' }}</button></div>
          <div class="callout c-ok" role="status" aria-live="polite" v-if="sourceResult"><span>✓</span><span style="flex:1"><b>{{ sourceResult.note }}</b><br>
            已解析：{{ sourceResult.account_name || sourceResult.uid }} · {{ sourceResult.platform }} · {{ Number(sourceResult.fans||0).toLocaleString() }} 粉丝 · {{ Number(sourceResult.video_count||0).toLocaleString() }} 作品
            <small style="display:block" v-if="sourceResult.bio">{{ sourceResult.bio }}</small>
            <span class="settings-actions" style="margin-top:8px"><button class="btn btn-sm btn-pri" @click="openAccountFromSource">带入“账号运营”添加</button><button class="btn btn-sm" @click="openBenchmarkFromSource">带入“对标账号”添加</button></span>
          </span></div>
        </div>
      </div>

      <div class="card section-gap settings-provider-card" v-if="tab==='llm' && settings.llm">
        <div class="card-body">
          <div class="settings-section-head"><div><div class="settings-section-title">OpenAI Compatible 模型服务</div><p>支持 OpenAI、兼容中转服务和显式授权的本地服务。模型列表不可用时仍可手填名称。</p></div><span class="badge" :class="settings.llm.api_key_configured||settings.llm.auth_mode==='none'?'b-ok':'b-warn'">{{ settings.llm.api_key_configured||settings.llm.auth_mode==='none' ? '鉴权已就绪' : '待配置 Key' }}</span></div>
          <div class="callout c-info"><span>ⓘ</span><span>保存配置本身不调用模型；“读取模型列表”和“最小生成测试”会发起真实请求，后者可能计费。</span></div>
          <div class="grid g2 settings-config-grid">
            <label class="field"><span>协议</span><select class="input" v-model="settings.llm.protocol"><option value="openai_compatible">OpenAI Compatible · Chat Completions</option></select></label>
            <label class="field"><span>鉴权方式</span><select class="input" v-model="settings.llm.auth_mode"><option value="bearer">Bearer API Key</option><option value="none">无需鉴权（本地服务）</option></select></label>
            <label class="field"><span>API Base URL</span><input class="input mono" v-model="settings.llm.api_base" placeholder="https://api.openai.com/v1"><small>填写到 /v1；只填域名时系统会自动补齐。</small></label>
            <label class="field"><span>API Key（系统安全凭据库）</span><input class="input mono" type="password" :disabled="settings.llm.auth_mode==='none'" v-model="secretDrafts.llm" :placeholder="settings.llm.api_key_configured ? '已配置 · 留空表示不修改' : '粘贴模型 API Key'"><small>{{ settings.llm.auth_mode==='none' ? '当前服务不发送 Authorization 请求头。' : '仅由当前操作系统用户解密。' }}</small></label>
          </div>
          <label class="settings-private-toggle"><input type="checkbox" v-model="settings.llm.allow_private_network"><span><b>允许访问本机/私有网络模型服务</b><small>仅在使用 Ollama、LM Studio 等可信本地服务时开启；开启后允许 HTTP。</small></span></label>
          <div class="settings-model-heading"><div><b>任务模型绑定</b><span>每个任务可以使用不同的模型名称</span></div><span v-if="availableModels.length">已读取 {{ availableModels.length }} 个模型</span></div>
          <datalist id="llmModelOptions"><option v-for="model in availableModels" :value="model"></option></datalist>
          <div class="settings-model-grid">
            <label class="field"><span>快速分类 / 指令路由</span><input class="input mono" list="llmModelOptions" v-model="settings.llm.fast_model"><small>工作总台指令解析</small></label>
            <label class="field"><span>深度分析模型</span><input class="input mono" list="llmModelOptions" v-model="settings.llm.analysis_model"><small>内容拆解、对标作者分析</small></label>
            <label class="field"><span>写作模型</span><input class="input mono" list="llmModelOptions" v-model="settings.llm.writing_model"><small>方向、提纲、初稿</small></label>
            <label class="field"><span>结构化抽取模型</span><input class="input mono" list="llmModelOptions" v-model="settings.llm.structured_model"><small>访谈字段与 JSON 输出</small></label>
            <label class="field settings-field-pending"><span>Embedding 模型 <em>尚未接入</em></span><input class="input mono" v-model="settings.llm.embedding_model"><small>当前仅保存名称，不会伪装成已调用。</small></label>
          </div>
          <div class="settings-actions"><button class="btn btn-pri" :disabled="saving" @click="save('llm')">保存模型配置</button><button class="btn" :disabled="loadingModels||saving" @click="loadModels">{{ loadingModels ? '正在读取…' : '保存并读取模型列表' }}</button><button class="btn" :disabled="testingLlm||saving" @click="testLlm">{{ testingLlm ? '正在生成…' : '测试快速模型' }}</button></div>
          <div class="callout c-ok" role="status" aria-live="polite" v-if="llmResult && llmResult.ok"><span>✓</span><span>{{ llmResult.note }}</span></div>
        </div>
      </div>

      <div class="card section-gap settings-provider-card" v-if="tab==='image' && settings.image">
        <div class="card-body">
          <div class="settings-section-head"><div><div class="settings-section-title">Image2 / OpenAI Compatible 图片模型</div><p>视觉工厂只在这里配置并测试真实图片 Provider。未配置时可继续使用明确标记的本地模板，不会伪装成 AI 出图。</p></div><span class="badge" :class="settings.image.api_key_configured||settings.image.auth_mode==='none'?'b-ok':'b-warn'">{{ settings.image.api_key_configured||settings.image.auth_mode==='none' ? '鉴权已就绪' : '待配置 Key' }}</span></div>
          <div class="callout c-info"><span>ⓘ</span><span>“测试图片模型”会真实生成一张最小测试图并可能计费。API Key 只保存在当前操作系统用户的安全凭据库。</span></div>
          <div class="grid g2 settings-config-grid">
            <label class="field"><span>协议</span><select class="input" v-model="settings.image.protocol"><option value="openai_compatible_images">OpenAI Compatible · Images</option></select></label>
            <label class="field"><span>鉴权方式</span><select class="input" v-model="settings.image.auth_mode"><option value="bearer">Bearer API Key</option><option value="none">无需鉴权（本地服务）</option></select></label>
            <label class="field"><span>API Base URL 或完整生成端点</span><input class="input mono" v-model="settings.image.api_base" placeholder="https://provider.example/v1 或 …/v1/images/generations"><small>两种格式都支持；保存时会自动拆分 Base URL 与生成路径，避免重复拼接。</small></label>
            <label class="field"><span>Image2 API Key（系统安全凭据库）</span><input class="input mono" type="password" :disabled="settings.image.auth_mode==='none'" v-model="secretDrafts.image" :placeholder="settings.image.api_key_configured ? '已配置 · 留空表示不修改' : '粘贴图片模型 API Key'"></label>
            <label class="field"><span>图片模型名称</span><input class="input mono" v-model="settings.image.model" placeholder="例如 gpt-image-1 / image-2"><small>按供应商文档填写，不再写死在底层。</small></label>
            <label class="field"><span>生成端点</span><input class="input mono" v-model="settings.image.generation_path" placeholder="images/generations"></label>
            <label class="field"><span>默认比例</span><select class="input" v-model="settings.image.default_ratio"><option>3:4</option><option>9:16</option><option>16:9</option><option>1:1</option></select></label>
            <label class="field"><span>默认候选数</span><input class="input" type="number" min="1" max="4" v-model.number="settings.image.default_variants"></label>
            <label class="field"><span>正式出图质量</span><select class="input" v-model="settings.image.quality"><option value="high">高质量（APIMart 2K）</option><option value="low">快速预览（APIMart 1K）</option></select><small>“测试图片模型”固定使用最低规格，避免连通测试产生不必要费用。</small></label>
          </div>
          <label class="settings-private-toggle"><input type="checkbox" v-model="settings.image.allow_private_network"><span><b>允许可信本机/私有网络图片服务</b><small>只在明确使用本地图片模型时开启。</small></span></label>
          <div class="settings-actions"><button class="btn btn-pri" :disabled="saving" @click="save('image')">保存图片模型</button><button class="btn" :disabled="saving||testingImage" @click="testImage">{{ testingImage ? '正在真实生成…' : '保存并测试图片模型' }}</button></div>
          <div class="callout c-ok" role="status" aria-live="polite" v-if="imageResult"><span>✓</span><span>{{ imageResult.note }} · 返回 {{ imageResult.image_bytes }} bytes</span></div>
        </div>
      </div>

      <div class="card section-gap settings-provider-card" v-if="tab==='voice' && settings.voice"><div class="card-body">
        <div class="settings-section-head"><div><div class="settings-section-title">阿里云百炼语音识别</div><p>已接通 qwen3-asr-flash 的真实音频转写；测试会发起一次可能计费的请求。TTS 仍保持明确未实现。</p></div><span class="badge" :class="settings.voice.api_key_configured?'b-ok':'b-warn'">{{ settings.voice.api_key_configured ? 'ASR Key 已保存' : '待配置 Key' }}</span></div>
        <div class="grid g2 settings-config-grid">
          <label class="field"><span>STT Provider</span><select class="input" v-model="settings.voice.stt_provider"><option value="aliyun_bailian">阿里云百炼</option></select></label>
          <label class="field"><span>协议</span><select class="input" v-model="settings.voice.protocol"><option value="openai_compatible_audio">OpenAI Compatible Audio</option></select></label>
          <label class="field"><span>API Base URL</span><input class="input mono" v-model="settings.voice.api_base"></label>
          <label class="field"><span>API Key（系统安全凭据库）</span><input class="input mono" type="password" v-model="secretDrafts.voice" :placeholder="settings.voice.api_key_configured ? '已配置 · 留空表示不修改' : '粘贴百炼 API Key'"></label>
          <label class="field"><span>ASR 模型</span><input class="input mono" v-model="settings.voice.asr_model"></label>
          <label class="field"><span>转写语言</span><select class="input" v-model="settings.voice.language"><option value="zh">中文</option><option value="en">英语</option><option value="yue">粤语</option></select></label>
          <label class="field"><span>热词词典</span><input class="input" v-model="settings.voice.hot_words"></label>
          <label class="field"><span>TTS Provider · 尚未接入</span><input class="input" disabled v-model="settings.voice.tts_provider"><small>不会显示成已生效。</small></label>
          <label class="field settings-file-field"><span>真实测试音频（≤10 MB）</span><span class="settings-file-picker" :class="{selected:asrFile}"><input class="file-input-hidden" type="file" accept="audio/*,video/mp4" @change="pickAsrFile"><span class="settings-file-button">选择测试音频</span><span class="settings-file-name">{{ asrFile ? asrFile.name : '未选择音频文件' }}</span></span><small>音频仅用于本次真实转写测试。</small></label>
        </div>
        <div class="settings-actions"><button class="btn btn-pri" :disabled="saving" @click="save('voice')">保存 ASR 配置</button><button class="btn" :disabled="saving||testingAsr" @click="testAsr">{{ testingAsr ? '正在转写…' : '保存并测试音频' }}</button></div>
        <div class="callout c-ok" role="status" aria-live="polite" v-if="asrResult"><span>✓</span><span>{{ asrResult.note }} · {{ asrResult.model }}<br>{{ asrResult.text }}</span></div>
      </div></div>

      <div class="card section-gap" v-if="['cost','local'].includes(tab) && settings[tab]"><div class="card-body">
        <div class="grid g2"><div class="field" v-for="f in fieldItems(tab)" :key="f.key"><label>{{ f.label }}</label><input class="input" v-model="settings[tab][f.key]"></div></div>
        <div class="settings-actions"><button class="btn btn-pri" :disabled="saving" @click="save(tab)">保存本分区</button><span class="mini-note" v-if="tab==='cost' && usage">今日已用 ¥{{ usage.today.toFixed(2) }} / ¥{{ usage.daily_budget }} · 本月 ¥{{ usage.month.toFixed(2) }} / ¥{{ usage.monthly_budget }}</span></div>
      </div></div>

      <template v-if="tab==='diag'">
        <div class="card section-gap" v-if="diag && diag.runtime"><div class="card-head"><div><div class="card-title">当前版本与数据源</div><div class="mini-note">先确认这里，再判断页面能力和历史数据是否属于同一版本。</div></div><span class="badge b-ok">Schema {{ diag.runtime.schema_version }}</span></div><div class="card-body" style="padding-top:8px">
          <div class="kv-row"><span>运行类型</span><b>{{ diag.runtime.build_profile }} · {{ diag.runtime.build_id }}</b></div>
          <div class="kv-row"><span>数据目录</span><b class="mono">{{ diag.runtime.data_dir }}</b></div>
          <div class="kv-row"><span>源码/资源目录</span><b class="mono">{{ diag.runtime.resource_dir }}</b></div>
          <div class="settings-readiness"><span>视觉工厂 <b>{{ diag.runtime.capabilities.visual_factory?'已包含':'缺失' }}</b></span><span>统一资料中心 <b>{{ diag.runtime.capabilities.unified_assets?'已包含':'缺失' }}</b></span><span>创作生命周期 <b>{{ diag.runtime.capabilities.content_lifecycle?'已包含':'缺失' }}</b></span><span>运营顾问 <b>{{ diag.runtime.capabilities.advisor?'已包含':'缺失' }}</b></span></div>
        </div>
        <div class="card section-gap"><div class="card-body" style="padding-top:14px">
          <div class="kv-row" v-for="c in (diag ? diag.checks : [])" :key="c.name">
            <span>{{ c.name }}</span>
            <span><span class="badge" :class="checkBadge(c.status)">{{ c.detail }}</span></span>
          </div>
          <div class="loading" v-if="!diag">探测中</div>
        </div></div>
        <div class="card section-gap">
          <div class="card-head"><div class="card-title">最近错误与任务日志</div>
            <div style="display:flex;gap:6px">
              <input class="input mono" style="width:220px;height:28px" placeholder="输入 Trace ID 查询" v-model="traceQ" @keydown.enter="queryTrace">
              <button class="btn btn-sm" @click="queryTrace">查询</button>
            </div></div>
          <div class="card-body" style="padding-top:4px">
            <div v-if="traceResult" class="callout c-info">
              <span>→</span><span>Trace <b class="mono">{{ traceQ }}</b>：错误 {{ traceResult.errors.length }} 条 · 关联任务 {{ traceResult.jobs.length }} 个
                <template v-if="traceResult.jobs.length">（{{ traceResult.jobs.map(j=>j.id+'/'+j.status).join('、') }}）</template></span>
            </div>
            <table class="tbl">
              <tr><th>时间</th><th>错误码</th><th>阶段</th><th>Provider</th><th>Trace ID</th></tr>
              <tr v-for="e in errors" :key="e.id">
                <td class="mono">{{ e.ts.slice(5,16).replace('T',' ') }}</td>
                <td class="mono" style="color:var(--err)">{{ e.code }}</td>
                <td>{{ e.stage }}</td><td>{{ e.provider }}</td>
                <td><span class="trace mono" @click="bus.emit('open-error',{code:e.code,message:e.message,stage:e.stage,provider:e.provider,retryable:false,trace_id:e.trace_id,details:{}})">{{ e.trace_id.slice(0,14) }}…</span></td>
              </tr>
              <tr v-if="!errors.length"><td colspan="5" class="empty">暂无错误记录</td></tr>
            </table>
          </div>
        </div>
        <div class="card">
          <div class="card-head"><div class="card-title">Usage Ledger <span class="card-sub">每次外部调用均记录（含失败是否收费）</span></div></div>
          <div class="card-body" style="padding-top:4px"><table class="tbl">
            <tr><th>时间</th><th>任务</th><th>Provider</th><th>接口</th><th>费用</th></tr>
            <tr v-for="u in ledger" :key="u.id">
              <td class="mono">{{ u.ts.slice(5,16).replace('T',' ') }}</td><td>{{ u.task }}</td>
              <td class="mono">{{ u.provider }}</td><td>{{ u.endpoint }}</td>
              <td class="mono">¥{{ u.cost.toFixed(2) }}</td>
            </tr>
          </table></div>
        </div>
      </template>
    </section>`,
  };
})();
