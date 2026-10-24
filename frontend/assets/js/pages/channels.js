/* 渠道运营：以内容母题串联朋友圈、公众号、草稿箱与复盘。 */
(function () {
  const TYPE_LABELS = { value: '价值观点', process: '过程记录', human: '真人感', trust: '可信依据', interaction: '互动承接' };
  const STATUS_LABELS = { DRAFT: '草稿', READY: '待发布', SCHEDULED: '已排期', PUBLISHED: '已发布', REVIEW_REQUIRED: '待修改', IN_DRAFT_BOX: '公众号草稿箱', PUBLISHING: '发布中', PUBLISH_FAILED: '发布失败', REMOVED: '发布后删除', BLOCKED: '平台封禁' };
  const PLAN_STATUS_LABELS = { CANDIDATE: '待选题', SELECTED: '已选题', OUTLINING: '提纲中', DRAFTING: '写作中', PRODUCING: '制作中', READY_TO_PUBLISH: '待发布', PUBLISHED: '已发布', WAITING_RETRO: '待复盘', RETRO_COMPLETED: '已复盘', ARCHIVED: '已归档', PAUSED: '已暂停' };

  window.PageChannels = {
    data() {
      return {
        tab: 'overview', loading: true, busy: '', overview: null, campaigns: [], moments: [], articles: [],
        selectedCampaignId: '', instruction: '', newCampaign: { title: '', core_topic: '', objective: '', audience: '' },
        momentMode: 'manual', ideas: [], momentGaps: [], momentFile: null,
        manualMoment: { title: '', body: '', personal_judgement: '', content_type: 'human', source_url: '', scheduled_at: '' },
        momentMix: { window_days: 7, profile_key: 'balanced', max_posts: 5,
          ratios: { value: 30, process: 25, human: 25, trust: 10, interaction: 10 }, instruction: '', idea_ids: [] },
        activeArticle: null, articleDraft: { title: '', digest: '', author: '', markdown: '', content_source_url: '' },
        wechat: null, wechatProbe: null, wechatForm: { enabled: false, account_name: '', original_id: '', account_type: 'subscription', verified: false,
          app_id: '', app_secret: '', default_author: '见舟AI', default_thumb_media_id: '', draft_push_enabled: true,
          publish_enabled: false, auto_publish_enabled: false, ip_whitelist_note: '' },
        thumbFile: null, momentEditId: '', momentEditBody: '', manualUrl: '', expandedMoments: {}, momentCoverChoice: {},
        performance: null, performanceOpen: false, performanceBusy: '',
      };
    },
    computed: {
      campaignOptions() { return this.campaigns.filter((c) => c.status === 'ACTIVE'); },
      activeCampaign() { return this.campaigns.find((c) => c.id === this.selectedCampaignId); },
    },
    async mounted() {
      await this.load();
      const params = new URLSearchParams(location.search);
      const requested = params.get('campaign');
      if (requested && this.campaigns.some((item) => item.id === requested)) this.selectedCampaignId = requested;
      const requestedTab = params.get('channel_tab');
      if (['overview', 'moments', 'official', 'connector'].includes(requestedTab)) this.tab = requestedTab;
      const requestedArticle = params.get('article');
      const requestedMoment = params.get('moment');
      const requestedDeliverable = params.get('deliverable');
      const requestedPerformance = params.get('performance');
      if (requestedArticle && this.articles.some((item) => item.id === requestedArticle)) {
        this.tab = 'official'; await this.openArticle(requestedArticle);
      }
      if (requestedMoment && this.moments.some((item) => item.id === requestedMoment)) {
        this.tab = 'moments';
        this.$nextTick(() => document.getElementById('moment-' + requestedMoment)?.scrollIntoView({ block: 'center' }));
      }
      if (requestedDeliverable) {
        const article = this.articles.find((item) => item.deliverable_id === requestedDeliverable);
        const moment = this.moments.find((item) => item.deliverable_id === requestedDeliverable);
        if (article) { this.tab = 'official'; await this.openArticle(article.id); }
        else if (moment) {
          this.tab = 'moments';
          this.$nextTick(() => document.getElementById('moment-' + moment.id)?.scrollIntoView({ block: 'center' }));
        }
      }
      if (requestedPerformance) await this.openPerformance(requestedPerformance);
    },
    methods: {
      typeLabel(v) { return TYPE_LABELS[v] || v || '内容'; },
      statusLabel(v) { return STATUS_LABELS[v] || v || '未知'; },
      planStatusLabel(v) { return PLAN_STATUS_LABELS[v] || v || '未开始'; },
      statusClass(v) { return ['PUBLISHED', 'READY', 'IN_DRAFT_BOX'].includes(v) ? 'b-ok' : (['REVIEW_REQUIRED'].includes(v) ? 'b-warn' : 'b-neu'); },
      async load() {
        this.loading = true;
        try {
          const [overview, campaigns, moments, articles, settings, wechat, ideas, momentPrefs] = await Promise.all([
            api.get('/api/channels/overview'), api.get('/api/channels/campaigns?status=all'),
            api.get('/api/channels/moments'), api.get('/api/channels/official'),
            api.get('/api/settings'), api.get('/api/channels/wechat/status'),
            api.get('/api/ideas?limit=100'), api.get('/api/channels/moments/preferences'),
          ]);
          this.overview = overview; this.campaigns = campaigns; this.moments = moments; this.articles = articles; this.wechat = wechat;
          this.wechatProbe = wechat.last_probe || null;
          this.ideas = ideas;
          this.momentMix = { ...this.momentMix, ...momentPrefs, ratios: { ...this.momentMix.ratios, ...(momentPrefs.ratios || {}) } };
          if (!this.selectedCampaignId && campaigns.length) this.selectedCampaignId = campaigns[0].id;
          const saved = settings.wechat_official || {};
          this.wechatForm = { ...this.wechatForm, ...saved, app_secret: '' };
        } catch (e) { handleError(e); } finally { this.loading = false; }
      },
      async createCampaign() {
        if (!this.newCampaign.title.trim()) return toast('先填写内容母题名称', 'err');
        this.busy = 'campaign';
        try {
          const r = await api.post('/api/channels/campaigns', this.newCampaign);
          toast(r.note); this.newCampaign = { title: '', core_topic: '', objective: '', audience: '' };
          await this.load(); this.selectedCampaignId = r.id;
        } catch (e) { handleError(e); } finally { this.busy = ''; }
      },
      async startPublicCreation(c) {
        this.busy = 'public:' + c.id;
        try {
          const r = await api.post('/api/creation/projects', {
            title: c.title, goal: c.objective || c.core_topic || '待确认',
            source_chain: c.source_chain || [], campaign_id: c.id,
          });
          toast(r.note);
          location.href = '/?project=' + encodeURIComponent(r.id) + '#/creation';
        } catch (e) { handleError(e); } finally { this.busy = ''; }
      },
      async generateMoments() {
        if (!this.selectedCampaignId) return toast('先选择一个内容母题', 'err');
        this.busy = 'moments';
        try {
          const r = await api.post('/api/channels/moments/generate', { campaign_id: this.selectedCampaignId, instruction: this.instruction });
          toast(r.note); this.instruction = ''; await this.load(); this.tab = 'moments';
        } catch (e) { handleError(e); } finally { this.busy = ''; }
      },
      pickMomentFile(event) { this.momentFile = (event.target.files && event.target.files[0]) || null; },
      async createManualMoment() {
        if (!this.manualMoment.body.trim()) return toast('先写下你自己的真实感悟或记录', 'warn');
        this.busy = 'manualMoment';
        try {
          const result = await api.post('/api/channels/moments/manual', this.manualMoment);
          if (this.momentFile && result.idea_id) {
            const fd = new FormData(); fd.append('file', this.momentFile);
            await api.upload('/api/ideas/' + result.idea_id + '/attachments', fd);
          }
          this.manualMoment = { title: '', body: '', personal_judgement: '', content_type: 'human', source_url: '', scheduled_at: '' };
          this.momentFile = null; toast(result.note); await this.load(); this.tab = 'moments';
        } catch (e) { handleError(e); } finally { this.busy = ''; }
      },
      ratioLabel(key) { return ({ value: '专业观点', process: '真实过程', human: '生活/真人感', trust: '信任证据', interaction: '互动承接' })[key] || key; },
      ideaTypeLabel(key) { return ({ self_idea: '灵感观点', life_note: '生活感悟', work_process: '工作过程', hot_opinion: '热点判断', feedback: '用户反馈', trust_evidence: '可信证据' })[key] || key; },
      toggleMixIdea(id) {
        const values = this.momentMix.idea_ids || [];
        this.momentMix.idea_ids = values.includes(id) ? values.filter((item) => item !== id) : [...values, id];
      },
      async composeMomentMix() {
        this.busy = 'momentMix'; this.momentGaps = [];
        try {
          const result = await api.post('/api/channels/moments/compose', this.momentMix);
          this.momentGaps = result.gaps || []; toast(result.note); await this.load(); this.tab = 'moments';
        } catch (e) { handleError(e); } finally { this.busy = ''; }
      },
      originLabel(value) { return ({ manual: '本人手写', ai_campaign: '单母题派生', ai_mix: '滚动编排' })[value] || '历史内容'; },
      editMoment(row) { this.momentEditId = row.id; this.momentEditBody = row.body; },
      async saveMoment(row) {
        try { await api.put('/api/channels/moments/' + row.id, { body: this.momentEditBody }); toast('朋友圈草稿已保存'); this.momentEditId = ''; await this.load(); }
        catch (e) { handleError(e); }
      },
      async scheduleMoment(row) {
        const value = prompt('填写排期时间，例如 2026-08-11T09:30', row.scheduled_at || '');
        if (value === null) return;
        try { await api.put('/api/channels/moments/' + row.id, { scheduled_at: value, status: value ? 'SCHEDULED' : 'READY' }); toast('排期已保存'); await this.load(); }
        catch (e) { handleError(e); }
      },
      copyMoment(row) {
        navigator.clipboard.writeText(row.body).then(() => toast('朋友圈正文已复制，请到微信人工发布'));
      },
      momentExpanded(row) { return !!this.expandedMoments[row.id]; },
      toggleMoment(row) { this.expandedMoments[row.id] = !this.expandedMoments[row.id]; },
      visualFormatLabel(value) {
        return ({ real_photo: '真实照片', screenshot: '过程截图', knowledge_card: '知识卡', minimal_card: '互动卡', process_collage: '过程拼图', no_image: '建议纯文字' })[value] || '配图建议';
      },
      async bindMomentVisual(row) {
        const coverId = this.momentCoverChoice[row.id] || ((row.visual_options || []).find((item) => !item.bound) || {}).id;
        if (!coverId) return toast('当前母题还没有可绑定的视觉成品', 'warn');
        this.busy = 'momentCover:' + row.id;
        try {
          const r = await api.post('/api/channels/moments/' + row.id + '/visual-cover/' + coverId);
          toast(r.note); await this.load();
        } catch (e) { handleError(e); } finally { this.busy = ''; }
      },
      async unbindMomentVisual(row, cover) {
        this.busy = 'momentCover:' + row.id;
        try {
          const r = await api.delete('/api/channels/moments/' + row.id + '/visual-cover/' + cover.id);
          toast(r.note); await this.load();
        } catch (e) { handleError(e); } finally { this.busy = ''; }
      },
      openMomentVisualFactory(row) {
        const query = row.project_id ? ('?project=' + encodeURIComponent(row.project_id)) : '';
        location.href = '/' + query + '#/visuals';
      },
      async markMomentPublished(row) {
        const note = prompt('可填写发布备注（可留空）', '') ; if (note === null) return;
        try { const r = await api.post('/api/channels/moments/' + row.id + '/publish-manual', { note }); toast(r.note); await this.load(); }
        catch (e) { handleError(e); }
      },
      async deleteMoment(row) {
        if (!confirm('移除这条朋友圈草稿？已留存的母题与其他渠道内容不会受影响。')) return;
        try { await api.delete('/api/channels/moments/' + row.id); toast('已移入回收状态', 'warn'); await this.load(); }
        catch (e) { handleError(e); }
      },
      metricText(metrics) { return Object.entries(metrics || {}).map(([key, value]) => key + ' ' + value).join(' · '); },
      async openPerformance(deliverableId) {
        this.performanceOpen = true; this.performanceBusy = 'load';
        try { this.performance = await api.get('/api/channels/deliverables/' + deliverableId + '/performance'); }
        catch (e) { this.performanceOpen = false; handleError(e); }
        finally { this.performanceBusy = ''; }
      },
      closePerformance() { this.performanceOpen = false; this.performance = null; },
      async generateRetro() {
        if (!this.performance || !this.performance.deliverable) return;
        this.performanceBusy = 'retro';
        try {
          const id = this.performance.deliverable.id;
          const r = await api.post('/api/channels/deliverables/' + id + '/retro');
          toast(r.note); await this.openPerformance(id);
        } catch (e) { handleError(e); } finally { this.performanceBusy = ''; }
      },
      async recordMetrics(deliverableId, channel, subjectType = 'deliverable', subjectId = '') {
        const labels = channel === 'moments' ? '点赞,评论,转发,私聊咨询' : '阅读,点赞,在看,转发';
        const raw = prompt('按顺序填写真实数据（' + labels + '），例如：120,8,2,3', '');
        if (raw === null) return;
        const tn = prompt('填写数据时间点，例如 T+1、T+3、T+7', 'T+1');
        if (tn === null) return;
        const names = labels.split(','); const values = raw.split(',').map((v) => Number(v.trim() || 0));
        const metrics = {}; names.forEach((name, index) => { metrics[name] = Number.isFinite(values[index]) ? values[index] : 0; });
        try { const r = await api.post('/api/channels/deliverables/' + deliverableId + '/metrics', { tn: tn || 'manual', metrics, subject_type: subjectType, subject_id: subjectId }); toast(r.note); await this.load(); await this.openPerformance(deliverableId); }
        catch (e) { handleError(e); }
      },
      async generateOfficial() {
        if (!this.selectedCampaignId) return toast('先选择一个内容母题', 'err');
        this.busy = 'official';
        try {
          const r = await api.post('/api/channels/official/generate', { campaign_id: this.selectedCampaignId, instruction: this.instruction });
          toast(r.note); this.instruction = ''; await this.load(); this.tab = 'official'; await this.openArticle(r.id);
        } catch (e) { handleError(e); } finally { this.busy = ''; }
      },
      async openArticle(id) {
        try {
          const a = await api.get('/api/channels/official/' + id); this.activeArticle = a;
          this.articleDraft = { title: a.title, digest: a.digest || '', author: a.author || '',
            markdown: (a.version && a.version.markdown_content) || '', content_source_url: a.content_source_url || '' };
        } catch (e) { handleError(e); }
      },
      closeArticle() { this.activeArticle = null; },
      async bindVisualCover(cover) {
        if (!this.activeArticle || !cover) return;
        this.busy = 'cover:' + cover.id;
        try {
          const r = await api.post('/api/channels/official/' + this.activeArticle.id + '/visual-cover/' + cover.id);
          toast(r.note); await this.openArticle(this.activeArticle.id); await this.load();
        } catch (e) { handleError(e); } finally { this.busy = ''; }
      },
      async unbindVisualCover() {
        if (!this.activeArticle || !this.activeArticle.cover_project_id) return;
        if (!confirm('解除本篇封面绑定？视觉工厂中的历史成品不会被删除。')) return;
        this.busy = 'cover:unbind';
        try {
          const r = await api.delete('/api/channels/official/' + this.activeArticle.id + '/visual-cover');
          toast(r.note); await this.openArticle(this.activeArticle.id); await this.load();
        } catch (e) { handleError(e); } finally { this.busy = ''; }
      },
      async saveArticle() {
        if (!this.activeArticle) return;
        this.busy = 'saveArticle';
        try {
          const r = await api.put('/api/channels/official/' + this.activeArticle.id, { ...this.articleDraft, change_note: '人工编辑保存' });
          toast(r.note); await this.openArticle(this.activeArticle.id); await this.load();
        } catch (e) { handleError(e); } finally { this.busy = ''; }
      },
      async reviewArticle() {
        if (!this.activeArticle) return;
        this.busy = 'review';
        try { const r = await api.post('/api/channels/official/' + this.activeArticle.id + '/review'); toast(r.status === 'PASS' ? '发布前审核通过' : '审核完成，请处理问题', r.status === 'PASS' ? 'ok' : 'warn'); await this.openArticle(this.activeArticle.id); await this.load(); }
        catch (e) { handleError(e); } finally { this.busy = ''; }
      },
      previewArticle() { if (this.activeArticle) window.open('/api/channels/official/' + this.activeArticle.id + '/preview', '_blank', 'noopener'); },
      async copyArticleHtml() {
        if (!this.activeArticle) return;
        try { const r = await api.get('/api/channels/official/' + this.activeArticle.id + '/export'); await navigator.clipboard.writeText(r.html); toast('公众号 HTML 已复制，可粘贴到公众号编辑器'); }
        catch (e) { handleError(e); }
      },
      async pushDraft() {
        if (!this.activeArticle) return;
        if (!confirm('确认把当前已审核版本上传到公众号草稿箱？这不会自动正式发布。')) return;
        this.busy = 'push';
        try { const r = await api.post('/api/channels/official/' + this.activeArticle.id + '/push-draft', { thumb_media_id: '' }); toast(r.note); await this.openArticle(this.activeArticle.id); await this.load(); }
        catch (e) { handleError(e); } finally { this.busy = ''; }
      },
      async publishOfficial() {
        if (!this.activeArticle || this.activeArticle.status !== 'IN_DRAFT_BOX') return;
        if (!confirm('确认调用公众号正式发布接口？请先在公众号后台完成预览。此动作不会自动撤回。')) return;
        this.busy = 'publishOfficial';
        try { const r = await api.post('/api/channels/official/' + this.activeArticle.id + '/publish', { confirm: true }); toast(r.note); await this.openArticle(this.activeArticle.id); await this.load(); }
        catch (e) { handleError(e); } finally { this.busy = ''; }
      },
      async syncOfficial() {
        if (!this.activeArticle) return;
        this.busy = 'syncOfficial';
        try { const r = await api.post('/api/channels/official/' + this.activeArticle.id + '/sync-status'); toast(r.note, r.status === 'PUBLISHED' ? 'ok' : 'warn'); await this.openArticle(this.activeArticle.id); await this.load(); }
        catch (e) { handleError(e); } finally { this.busy = ''; }
      },
      async markArticlePublished() {
        if (!this.activeArticle) return;
        const url = prompt('粘贴真实公众号文章链接'); if (!url) return;
        try { const r = await api.post('/api/channels/official/' + this.activeArticle.id + '/publish-manual', { url }); toast(r.note); await this.openArticle(this.activeArticle.id); await this.load(); }
        catch (e) { handleError(e); }
      },
      async deleteArticle(row) {
        if (!confirm('移除这篇公众号文档？其他渠道成品不会被删除。')) return;
        try { await api.delete('/api/channels/official/' + row.id); toast('文档已移入回收状态', 'warn'); if (this.activeArticle && this.activeArticle.id === row.id) this.closeArticle(); await this.load(); }
        catch (e) { handleError(e); }
      },
      async saveWechat() {
        this.busy = 'wechatSave';
        try {
          const values = { ...this.wechatForm };
          delete values.app_secret_configured;
          if (!values.app_secret) delete values.app_secret;
          const r = await api.put('/api/settings', { section: 'wechat_official', values }); toast(r.note); await this.load();
        } catch (e) { handleError(e); } finally { this.busy = ''; }
      },
      async testWechat() {
        this.busy = 'wechatTest';
        try {
          const r = await api.post('/api/channels/wechat/test'); this.wechatProbe = { ...r, checked_at: new Date().toISOString() };
          toast(r.capabilities.draft ? '草稿箱权限已真实验证；正式发布仍以实际回执为准' : '连接尚未完整通过，请按探测结果处理', r.capabilities.draft ? 'ok' : 'warn');
        }
        catch (e) { handleError(e); } finally { this.busy = ''; }
      },
      probeState(scope) {
        const capabilities = this.wechatProbe && this.wechatProbe.capabilities;
        const states = (capabilities && capabilities.states) || (this.wechatProbe && this.wechatProbe.capability_states) || {};
        return states[scope] || { status: 'PENDING', verified: false, reason: '尚未测试' };
      },
      probeLabel(scope) {
        const status = this.probeState(scope).status;
        return ({ VERIFIED: '已验证', BLOCKED: '被阻断', UNAVAILABLE: '不可用', DISABLED: '未启用', NOT_VERIFIED: '未验证', PENDING: '待测试' })[status] || status;
      },
      pickThumb(e) { this.thumbFile = e.target.files[0] || null; },
      async uploadThumb() {
        if (!this.thumbFile) return toast('先选择 JPG 或 PNG 封面', 'err');
        const fd = new FormData(); fd.append('file', this.thumbFile); this.busy = 'thumb';
        try { const r = await api.upload('/api/channels/wechat/upload-thumb', fd); this.wechatForm.default_thumb_media_id = r.media_id; toast(r.note); }
        catch (e) { handleError(e); } finally { this.busy = ''; }
      },
    },
    template: `
      <div class="channels-page">
        <div class="page-head channels-head"><div><h1>渠道运营</h1><div class="page-answer"><b>本页回答：</b>同一个内容母题，如何变成公域内容、朋友圈和公众号，并留下真实发布与复盘证据？</div></div><button class="btn" @click="go('plan')">打开内容计划</button></div>
        <div class="channel-truth"><span>内容母题 {{ overview&&overview.campaigns||0 }}</span><span>朋友圈 {{ overview&&overview.counts&&overview.counts.moments||0 }}</span><span>公众号 {{ overview&&overview.counts&&overview.counts.wechat_official||0 }}</span><span>个人朋友圈不伪装自动发布</span></div>
        <div class="tabs channel-tabs"><button v-for="item in [['overview','运营总览'],['moments','朋友圈'],['official','公众号'],['connector','公众号连接']]" :key="item[0]" :class="{active:tab===item[0]}" @click="tab=item[0]">{{ item[1] }}</button></div>
        <div class="loading-panel" v-if="loading"><span class="spinner"></span><span>正在加载渠道资产…</span></div>

        <template v-else-if="tab==='overview'">
          <div class="channel-grid channel-overview-grid">
            <section class="card channel-create-card"><div class="card-head"><div><div class="card-title">建立内容母题</div><div class="card-sub">临时灵感也可以先建母题，再分别派生渠道成品。</div></div></div><div class="card-body channel-form">
              <label>母题名称<input v-model="newCampaign.title" placeholder="例如：个人知识库到底要不要放本地"></label>
              <div class="grid g2"><label>核心判断<input v-model="newCampaign.core_topic" placeholder="一句话结论"></label><label>目标人群<input v-model="newCampaign.audience" placeholder="这篇内容给谁看"></label></div>
              <label>运营目标<textarea v-model="newCampaign.objective" placeholder="本轮想验证什么，不填虚假指标"></textarea></label>
              <button class="btn btn-pri" :disabled="busy==='campaign'" @click="createCampaign">{{ busy==='campaign'?'正在建立…':'建立母题' }}</button>
            </div></section>
            <section class="card"><div class="card-head"><div><div class="card-title">母题与渠道进度</div><div class="card-sub">短视频、朋友圈、公众号分别推进，不互相覆盖。</div></div></div><div class="card-body campaign-list">
            <div class="campaign-row" v-for="c in campaigns" :key="c.id"><div class="campaign-copy"><b>{{ c.title }}</b><span>{{ c.core_topic||c.objective||'尚未补充核心判断' }}</span><div class="campaign-progress"><em :class="['badge',c.progress&&c.progress.plan_task_id?'b-info':'b-neu']">公域 · {{ planStatusLabel(c.progress&&c.progress.plan_task_status) }}</em><em class="badge b-neu">视觉 {{ (c.progress&&c.progress.visual_count)||0 }}</em><em class="badge b-neu">渠道 {{ (c.progress&&c.progress.channel_count)||0 }}</em><em :class="['badge',c.progress&&c.progress.retro_count?'b-ok':'b-neu']">复盘 {{ (c.progress&&c.progress.retro_count)||0 }}</em></div></div><div class="campaign-actions"><button class="btn btn-sm" :disabled="busy==='public:'+c.id" @click="startPublicCreation(c)">{{ busy==='public:'+c.id?'正在串联…':(c.progress&&c.progress.project_id?'继续公域创作':'开始公域创作') }}</button><button class="btn btn-sm" @click="selectedCampaignId=c.id;tab='moments'">派生渠道</button></div></div>
              <div class="empty" v-if="!campaigns.length">尚无内容母题。创作工坊新建项目后会自动建立，也可以在左侧手动创建。</div>
            </div></section>
          </div>
          <section class="card section-gap channel-pending-section"><div class="card-head"><div><div class="card-title">待处理渠道成品</div><div class="card-sub">这里只展示需要下一步动作的成品，历史内容进入各自列表。</div></div></div><div class="card-body channel-pending-grid">
            <div class="channel-pending" v-for="d in (overview.pending||[])" :key="d.id"><span class="badge b-neu">{{ d.channel==='moments'?'朋友圈':'公众号' }}</span><b>{{ d.title }}</b><small>{{ d.campaign_title }} · {{ statusLabel(d.status) }}</small></div>
            <div class="empty" v-if="!overview.pending||!overview.pending.length">目前没有待处理渠道成品。</div>
          </div></section>
        </template>

        <template v-else-if="tab==='moments'">
          <section class="card moment-workbench"><div class="card-head"><div><div class="card-title">朋友圈工作台</div><div class="card-sub">你可以直接写自己的感悟，也可以从单个母题派生，或按真实素材做一周滚动编排。</div></div></div><div class="card-body">
            <div class="moment-mode-tabs"><button v-for="item in [['manual','自己写感悟'],['campaign','单母题派生'],['mix','本周智能编排']]" :key="item[0]" :class="{active:momentMode===item[0]}" @click="momentMode=item[0]">{{ item[1] }}</button></div>
            <div class="moment-manual-composer" v-if="momentMode==='manual'"><div class="moment-form-main"><div class="grid g2"><select v-model="manualMoment.content_type"><option value="human">生活 / 真人感</option><option value="value">个人观点</option><option value="process">工作过程</option><option value="trust">可信证据</option><option value="interaction">互动问题</option></select><input v-model="manualMoment.title" placeholder="标题（可选，不会强行显示在正文）"></div><textarea v-model="manualMoment.body" placeholder="直接写下你自己的感悟、生活片段、工作记录或真实判断。系统会原样保存，不调用AI改写。"></textarea><textarea class="compact" v-model="manualMoment.personal_judgement" placeholder="补充：我为什么在意这件事 / 我的判断（可选）"></textarea></div><aside class="moment-form-side"><input v-model="manualMoment.source_url" placeholder="来源链接（可选）"><input type="datetime-local" v-model="manualMoment.scheduled_at"><label class="moment-file"><span>真实照片 / 截图 / 语音（可选）</span><span class="moment-file-picker"><input class="file-input-hidden" type="file" accept="image/*,audio/*,video/mp4" @change="pickMomentFile"><span class="moment-file-button">选择素材</span><span class="moment-file-name">{{ momentFile?momentFile.name:'未选择文件' }}</span></span><small>支持图片、语音与 MP4，最大 20MB</small></label><button class="btn btn-pri" :disabled="busy==='manualMoment'" @click="createManualMoment">{{ busy==='manualMoment'?'保存中…':'保存为朋友圈草稿' }}</button></aside></div>
            <div class="channel-generator-body" v-else-if="momentMode==='campaign'"><select v-model="selectedCampaignId"><option value="">选择内容母题</option><option v-for="c in campaignOptions" :value="c.id" :key="c.id">{{ c.title }}</option></select><textarea v-model="instruction" placeholder="本轮补充要求（可选）。单个母题只派生1—2条，不会伪造生活和工作过程。"></textarea><button class="btn btn-pri" :disabled="busy==='moments'" @click="generateMoments">{{ busy==='moments'?'生成并存档中…':'生成1—2条' }}</button></div>
            <div class="moment-mix-composer" v-else><div class="moment-mix-settings"><div class="grid g2"><label>回看范围<select v-model.number="momentMix.window_days"><option :value="7">近7天</option><option :value="14">近14天</option><option :value="30">近30天</option></select></label><label>本轮条数<input type="number" min="1" max="7" v-model.number="momentMix.max_posts"></label></div><div class="moment-ratio-grid"><label v-for="(value,key) in momentMix.ratios" :key="key"><span>{{ ratioLabel(key) }}</span><input type="number" min="0" max="100" v-model.number="momentMix.ratios[key]"><em>%</em></label></div><textarea v-model="momentMix.instruction" placeholder="本周补充要求（可选），例如：减少专业输出，多保留真实生活片段"></textarea><button class="btn btn-pri" :disabled="busy==='momentMix'" @click="composeMomentMix">{{ busy==='momentMix'?'正在检查真实来源…':'生成本周朋友圈草稿' }}</button><div class="moment-gap-list" v-if="momentGaps.length"><b>真实素材缺口</b><span v-for="gap in momentGaps" :key="gap">{{ gap }}</span></div></div><aside class="moment-idea-picker"><div><b>本轮可用的一手记录</b><small>不选择时自动使用回看范围内的记录</small></div><button v-for="idea in ideas" :key="idea.id" :class="{active:momentMix.idea_ids.includes(idea.id)}" @click="toggleMixIdea(idea.id)"><span>{{ ideaTypeLabel(idea.item_type) }}</span><b>{{ idea.title||idea.body.slice(0,32) }}</b><small>{{ idea.body.slice(0,72) }}</small></button><div class="empty compact" v-if="!ideas.length">还没有本人记录。请先切到“自己写感悟”保存一条。</div></aside></div>
          </div></section>
          <div class="moment-grid">
            <article class="card moment-card" v-for="m in moments" :key="m.id" :id="'moment-'+m.id">
              <div class="card-head moment-card-head"><div><span class="badge b-pri">{{ typeLabel(m.content_type) }}</span><span class="badge b-neu">{{ originLabel(m.origin_mode) }}</span><span class="badge" :class="statusClass(m.status)">{{ statusLabel(m.status) }}</span></div><button class="btn btn-sm btn-danger" @click="deleteMoment(m)">删除</button></div>
              <div class="card-body moment-card-body">
                <textarea class="moment-editor" v-if="momentEditId===m.id" v-model="momentEditBody"></textarea>
                <template v-else><div class="moment-copy" :class="{expanded:momentExpanded(m)}">{{ m.body }}</div><button class="moment-copy-toggle" type="button" v-if="m.body&&m.body.length>180" @click="toggleMoment(m)">{{ momentExpanded(m)?'收起全文':'展开全文' }}</button></template>
                <section class="moment-visual" v-if="m.visual_suggestion">
                  <div class="moment-visual-head"><div><span class="badge b-info">{{ visualFormatLabel(m.visual_suggestion.format) }}</span><b>配图建议</b></div><span>{{ m.visual_suggestion.ratio }}</span></div>
                  <p>{{ m.visual_suggestion.brief }}</p><small>{{ m.visual_suggestion.reason }} · {{ m.visual_suggestion.source_hint }}</small>
                  <div class="moment-bound-visuals" v-if="m.bound_visuals&&m.bound_visuals.length"><div v-for="cover in m.bound_visuals" :key="cover.id"><img :src="cover.preview_url" :alt="cover.title"><span><b>{{ cover.title }}</b><small>{{ cover.ratio }}</small></span><button type="button" :disabled="busy==='momentCover:'+m.id" @click="unbindMomentVisual(m,cover)">解除</button></div></div>
                  <div class="moment-visual-actions"><template v-if="m.visual_options&&m.visual_options.length"><select v-model="momentCoverChoice[m.id]"><option value="">选择同母题视觉成品</option><option v-for="cover in m.visual_options" :key="cover.id" :value="cover.id" :disabled="cover.bound">{{ cover.title }} · {{ cover.ratio }}{{ cover.bound?' · 已绑定':'' }}</option></select><button class="btn btn-sm" :disabled="busy==='momentCover:'+m.id" @click="bindMomentVisual(m)">绑定配图</button></template><button class="btn btn-sm" @click="openMomentVisualFactory(m)">去视觉工厂</button></div>
                </section>
                <div class="moment-source-note" v-if="m.idea"><b>本人原始记录</b><span>{{ ideaTypeLabel(m.idea.item_type) }} · {{ m.idea.title||m.idea.body.slice(0,40) }}</span><div class="moment-source-media" v-if="m.idea.media&&m.idea.media.length"><template v-for="media in m.idea.media" :key="media.id"><img v-if="media.content_type&&media.content_type.startsWith('image/')" :src="media.url" :alt="media.name"><a v-else :href="media.url" target="_blank" rel="noreferrer">{{ media.name }}</a></template></div></div><div class="moment-meta">{{ m.campaign_title }}<span v-if="m.scheduled_at"> · {{ m.scheduled_at }}</span></div>
                <div class="moment-actions" v-if="momentEditId===m.id"><button class="btn btn-pri btn-sm" @click="saveMoment(m)">保存新版本</button><button class="btn btn-sm" @click="momentEditId=''">取消</button></div>
                <div class="moment-actions" v-else><button class="btn btn-sm" @click="editMoment(m)">编辑</button><button class="btn btn-sm" @click="scheduleMoment(m)">排期</button><button class="btn btn-sm" @click="copyMoment(m)">复制</button><button class="btn btn-pri btn-sm" v-if="m.status!=='PUBLISHED'" @click="markMomentPublished(m)">登记已发布</button><template v-else><button class="btn btn-sm" @click="recordMetrics(m.deliverable_id,'moments','moment_post',m.id)">录入真实数据</button><button class="btn btn-sm" @click="openPerformance(m.deliverable_id)">数据与复盘</button></template></div>
              </div>
            </article><div class="empty" v-if="!moments.length">还没有朋友圈草稿。</div>
          </div>
        </template>

        <template v-else-if="tab==='official'">
          <section class="card channel-generator"><div class="card-head"><div><div class="card-title">公众号深度长文</div><div class="card-sub">不是拉长短稿：模型会重组结论、依据、方法和边界；保存、审核、排版与草稿箱上传分步完成。</div></div></div><div class="card-body channel-generator-body"><select v-model="selectedCampaignId"><option value="">选择内容母题</option><option v-for="c in campaignOptions" :value="c.id" :key="c.id">{{ c.title }}</option></select><textarea v-model="instruction" placeholder="本轮长文要求（可选），例如：结合热点来源，但只使用已有证据，不要虚构数据"></textarea><button class="btn btn-pri" :disabled="busy==='official'" @click="generateOfficial">{{ busy==='official'?'生成并存档中…':'生成公众号初稿' }}</button></div></section>
          <div class="official-layout section-gap"><aside class="card official-list"><div class="card-head"><div class="card-title">文章档案</div></div><div class="card-body"><button class="official-list-row" :class="{active:activeArticle&&activeArticle.id===a.id}" v-for="a in articles" :key="a.id" @click="openArticle(a.id)"><b>{{ a.title }}</b><span>{{ a.campaign_title }} · {{ statusLabel(a.status) }}</span><em @click.stop="deleteArticle(a)">删除</em></button><div class="empty" v-if="!articles.length">还没有公众号文档。</div></div></aside>
            <section class="card official-editor" v-if="activeArticle"><div class="card-head official-editor-head"><div><div class="card-title">公众号编辑与审核</div><div class="card-sub">v{{ activeArticle.version.version_no }} · {{ statusLabel(activeArticle.status) }}</div></div><div class="official-actions"><button class="btn btn-sm" @click="previewArticle">排版预览</button><button class="btn btn-sm" @click="copyArticleHtml">复制HTML</button><button class="btn btn-sm" :disabled="busy==='review'" @click="reviewArticle">发布前审核</button><button class="btn btn-pri btn-sm" :disabled="busy==='saveArticle'" @click="saveArticle">保存新版本</button></div></div><div class="card-body official-edit-form"><input class="official-title-input" v-model="articleDraft.title"><div class="grid g2"><input v-model="articleDraft.author" placeholder="作者"><input v-model="articleDraft.content_source_url" placeholder="原文链接（可选）"></div><textarea class="official-digest" v-model="articleDraft.digest" placeholder="摘要"></textarea><textarea class="official-markdown" v-model="articleDraft.markdown"></textarea>
              <div class="official-review" v-if="activeArticle.review"><div><b>最近审核：{{ activeArticle.review.status==='PASS'?'通过':'需要修改' }}</b><span>得分 {{ activeArticle.review.score }}</span></div><ul v-if="activeArticle.review.issues&&activeArticle.review.issues.length"><li v-for="(issue,i) in activeArticle.review.issues" :key="i"><b>{{ issue.severity }}</b> {{ issue.message }}<small>{{ issue.action }}</small></li></ul><div class="callout c-ok" v-else>结构、事实边界、排版与可读性检查通过。</div></div>
              <div class="official-publish-bar"><div><b>发布边界</b><span>默认上传草稿箱，不会自动正式发布；无接口权限时复制 HTML 人工粘贴。</span></div><button class="btn" v-if="activeArticle.status!=='PUBLISHED'" @click="markArticlePublished">登记手动发布</button><template v-else><button class="btn" @click="recordMetrics(activeArticle.deliverable_id,'official','official_article',activeArticle.id)">录入真实数据</button><button class="btn" @click="openPerformance(activeArticle.deliverable_id)">数据与复盘</button></template><button class="btn" v-if="activeArticle.status==='IN_DRAFT_BOX'&&wechatForm.publish_enabled" :disabled="busy==='publishOfficial'" @click="publishOfficial">显式正式发布</button><button class="btn" v-if="activeArticle.status==='PUBLISHING'" :disabled="busy==='syncOfficial'" @click="syncOfficial">同步发布结果</button><button class="btn btn-pri" :disabled="busy==='push'||activeArticle.review_status!=='PASS'||activeArticle.status==='PUBLISHED'" @click="pushDraft">{{ busy==='push'?'正在上传…':'上传公众号草稿箱' }}</button></div>
              <section class="official-cover-picker"><div class="official-cover-head"><div><b>本篇公众号封面</b><span>只显示同一内容母题的封面成品；建议使用 16:9。选择时不调用微信，上传草稿箱时才转为永久素材。</span></div><div><button class="btn btn-sm" @click="go('visuals')">打开视觉工厂</button><button class="btn btn-sm" v-if="activeArticle.cover_project_id" :disabled="busy==='cover:unbind'" @click="unbindVisualCover">解除绑定</button></div></div><div class="official-cover-list" v-if="activeArticle.visual_covers&&activeArticle.visual_covers.length"><button type="button" class="official-cover-option" :class="{active:cover.bound}" v-for="cover in activeArticle.visual_covers" :key="cover.id" :disabled="busy==='cover:'+cover.id" @click="bindVisualCover(cover)"><img :src="cover.preview_url" :alt="cover.title"><span><b>{{ cover.title }}</b><small>{{ cover.ratio }} · {{ cover.generation_mode==='ai_full'?'AI 完整成图':'模板排版' }}</small><em>{{ cover.bound?'当前文章封面':(cover.selected?'已在视觉工厂采用':'选择此封面') }}</em></span></button></div><div class="official-cover-empty" v-else><span>这个母题还没有可用封面。先去视觉工厂生成封面，再返回本篇文章选择。</span><button class="btn btn-sm" @click="go('visuals')">去生成封面</button></div></section>
            </div></section><section class="card official-placeholder" v-else><div class="empty">从左侧选择文章，或先生成一篇公众号初稿。</div></section></div>
        </template>

        <template v-else-if="tab==='connector'">
          <div class="connector-layout"><section class="card connector-config-card"><div class="card-head connector-config-head"><div><div class="card-title">公众号开发配置</div><div class="card-sub">AppSecret 只写入当前操作系统安全凭据库，不通过 API 回传。</div></div><label class="check-row connector-enable-row"><input type="checkbox" v-model="wechatForm.enabled"><span>启用公众号连接</span></label></div><div class="card-body channel-form"><div class="grid g2"><label>公众号名称<input v-model="wechatForm.account_name"></label><label>公众号原始 ID<input v-model="wechatForm.original_id" placeholder="例如 gh_xxxxxxxxxxxx"></label></div><div class="grid g2"><label>账号类型<select v-model="wechatForm.account_type"><option value="subscription">订阅号</option><option value="service">服务号</option></select></label><label>AppID<input v-model="wechatForm.app_id" autocomplete="off"></label></div><label>AppSecret<input type="password" v-model="wechatForm.app_secret" placeholder="留空表示不修改已保存密钥" autocomplete="new-password"></label><div class="grid g2"><label>默认作者<input v-model="wechatForm.default_author"></label><label>默认封面 media_id<input v-model="wechatForm.default_thumb_media_id"></label></div><label>IP 白名单备注<input v-model="wechatForm.ip_whitelist_note" placeholder="例如：已加入当前服务器公网出口IP"></label><div class="connector-checks"><label class="connector-check"><input type="checkbox" v-model="wechatForm.verified"><span>已认证</span></label><label class="connector-check"><input type="checkbox" v-model="wechatForm.draft_push_enabled"><span>允许上传草稿箱</span></label><label class="connector-check"><input type="checkbox" v-model="wechatForm.publish_enabled"><span>允许显式正式发布</span></label></div><div class="connector-actions"><button class="btn btn-pri" :disabled="busy==='wechatSave'" @click="saveWechat">保存配置</button><button class="btn" :disabled="busy==='wechatTest'" @click="testWechat">保存后测试权限</button></div></div></section>
            <section class="card"><div class="card-head"><div><div class="card-title">真实权限状态</div><div class="card-sub">本地开关不等于微信权限；三项能力分别验证。</div></div></div><div class="card-body requirement-list"><div class="wechat-probe-grid"><div v-for="scope in ['token','draft','publish']" :key="scope" :class="['wechat-probe-item','state-'+probeState(scope).status.toLowerCase()]" ><span><b>{{ scope==='token'?'基础连接':(scope==='draft'?'草稿箱':'正式发布') }}</b><em>{{ probeLabel(scope) }}</em></span><small>{{ probeState(scope).reason }}</small></div></div><div class="wechat-probe-action" v-if="wechatProbe"><b>最近探测 {{ wechatProbe.checked_at||'刚刚' }}</b><span v-if="wechatProbe.egress_ips&&wechatProbe.egress_ips.length">当前出口 IP：<code>{{ wechatProbe.egress_ips.join(', ') }}</code></span><span v-for="(action,i) in (wechatProbe.next_actions||[])" :key="i">{{ i+1 }}. {{ action }}</span></div><div v-else class="callout c-info">保存配置后点击“测试真实权限”，系统只调用 Token 与草稿箱只读探测，不会创建草稿或发布文章。</div><details class="wechat-requirements"><summary>查看配置字段与地址</summary><div v-for="r in wechat.requirements" :key="r.field"><b>{{ r.field }}</b><span>{{ r.where }}</span></div></details><div class="callout c-info">{{ wechat.boundary }}</div><div class="thumb-upload"><div class="thumb-upload-copy"><b>上传公众号封面</b><span>JPG / PNG，用于换取永久素材 media_id</span></div><label class="themed-file-picker"><input class="file-input-hidden" type="file" accept="image/png,image/jpeg" @change="pickThumb"><span class="themed-file-button">选择封面文件</span><span class="themed-file-name">{{ thumbFile?thumbFile.name:'未选择文件' }}</span></label><button class="btn btn-sm" :disabled="busy==='thumb'||!thumbFile" @click="uploadThumb">{{ busy==='thumb'?'上传中…':'上传永久封面素材' }}</button></div></div></section></div>
        </template>
        <div class="modal channel-performance-modal" :class="{open:performanceOpen}" @click.self="closePerformance"><div class="modal-card"><div class="modal-head"><div class="modal-title">渠道数据与复盘</div><button class="modal-x" @click="closePerformance">×</button></div><div class="modal-body" v-if="performance"><div class="performance-heading"><div><b>{{ performance.deliverable.title }}</b><span>{{ performance.deliverable.channel==='moments'?'朋友圈':'公众号' }} · {{ statusLabel(performance.deliverable.status) }}</span></div><button class="btn btn-pri btn-sm" :disabled="performanceBusy==='retro'||!performance.snapshots.length" @click="generateRetro">{{ performanceBusy==='retro'?'复盘生成中…':'生成 / 刷新复盘' }}</button></div><section class="performance-section"><h4>真实数据快照</h4><div class="performance-list"><div v-for="s in performance.snapshots" :key="s.id"><b>{{ s.tn }} · {{ s.subject_type==='deliverable'?'整个成品':'具体内容' }}</b><span>{{ metricText(s.metrics) }}</span><small>{{ s.taken_at }} · {{ s.source }}</small></div><div class="empty compact" v-if="!performance.snapshots.length">尚未录入真实数据。</div></div></section><section class="performance-section"><h4>发布证据</h4><div class="performance-list"><div v-for="r in performance.publish_records" :key="r.id"><b>{{ r.mode }} · {{ r.status }}</b><span>{{ r.url||r.remote_id||'已登记发布动作' }}</span><small>{{ r.published_at||r.created_at }}</small></div><div class="empty compact" v-if="!performance.publish_records.length">尚未登记发布。</div></div></section><section class="performance-section"><h4>版本化复盘</h4><article class="retro-card" v-for="retro in performance.retro_reports" :key="retro.id"><div><b>复盘 v{{ retro.version_no }}</b><small>{{ retro.created_at }} · {{ retro.report.confidence||'未标注置信度' }}</small></div><p>{{ retro.report.summary }}</p><h5 v-if="retro.report.verified_findings&&retro.report.verified_findings.length">数据可验证的发现</h5><ul><li v-for="(finding,i) in retro.report.verified_findings" :key="'f'+i">{{ finding.finding }} <small>{{ finding.evidence }}</small></li></ul><h5 v-if="retro.report.unverified_hypotheses&&retro.report.unverified_hypotheses.length">待验证假设</h5><ul><li v-for="(item,i) in retro.report.unverified_hypotheses" :key="'h'+i">{{ item }}</li></ul><h5 v-if="retro.report.next_actions&&retro.report.next_actions.length">下一轮动作</h5><ul><li v-for="(item,i) in retro.report.next_actions" :key="'a'+i">{{ item }}</li></ul></article><div class="empty compact" v-if="!performance.retro_reports.length">数据录入后，可生成第一版复盘；同一组快照不会重复计费。</div></section></div><div class="modal-body loading-panel" v-else><span class="spinner"></span><span>加载发布证据与快照…</span></div></div></div>
      </div>`,
  };
})();
