/* 视觉工厂 v3：封面与多页图文分域，提示词、来源、版本和交付物全程留档。 */
(function () {
  const MODE_LABELS = {
    ai_full: 'AI 完整成图', ai_hybrid: 'AI 视觉 + 可控中文排版',
    local_template: '本地模板排版', ai_background: 'AI 视觉 + 可控中文排版',
  };
  const ROLE_LABELS = {
    cover: '封面', problem: '问题', insight: '核心洞察', method: '方法', steps: '步骤',
    case: '案例', comparison: '对比', checklist: '清单', quote: '金句', summary: '总结', cta: '行动建议',
  };
  const LAYOUT_LABELS = {
    hero: '主视觉', editorial: '编辑排版', steps: '步骤流程', comparison: '对比',
    checklist: '清单', quote: '金句', summary: '总结',
  };

  window.PageVisualFactory = {
    data() {
      return {
        loading: true, busy: '', tab: 'studio', studio: 'cover', historyKind: 'cover', promptFilter: 'all',
        projects: [], project: null, projectId: '', draftId: '', settings: {}, profile: {},
        templates: [], assets: [], promptPresets: [], batches: [], documents: [], currentDocument: null,
        documentEditing: false, editingPageId: '',
        roleLabels: ROLE_LABELS, layoutLabels: LAYOUT_LABELS,
        prompt: { mode: 'smart', preset_id: '', raw: '', result: null },
        presetDraft: { name: '', scope: 'cover', template_text: '', negative_prompt: '' },
        cover: {
          ratio: '3:4', generation_mode: 'ai_hybrid', quantity: 3, template_id: '',
          use_person: false, use_logo: true, reference_asset_ids: [],
          fields: { main_topic: '', title: '', subtitle: '', category: 'AI知识IP', features: '', keywords: '' },
        },
        graphic: {
          ratio: '3:4', generation_mode: 'ai_hybrid', template_id: '', platform: '小红书图文',
          page_count: 6, narrative_mode: 'auto', title: '', pages: [], use_logo: true, reference_asset_ids: [],
        },
      };
    },
    computed: {
      activeDrafts() { return this.project ? [...(this.project.drafts || [])].reverse() : []; },
      imageReady() {
        const item = this.settings.image || {};
        return !!(item.api_base && item.model && (item.auth_mode === 'none' || item.api_key_configured));
      },
      activeTemplates() { return this.templates.filter((item) => item.template_type === this.studio); },
      currentTemplateId() { return this.studio === 'cover' ? this.cover.template_id : this.graphic.template_id; },
      currentRatio() { return this.studio === 'cover' ? this.cover.ratio : this.graphic.ratio; },
      currentMode() { return this.studio === 'cover' ? this.cover.generation_mode : this.graphic.generation_mode; },
      canOptimize() { return !!(this.projectId && this.draftId && this.prompt.preset_id); },
      canGenerateCover() { return !!(this.projectId && this.draftId && this.cover.template_id &&
        (this.cover.generation_mode === 'local_template' || this.imageReady)); },
      canGenerateGraphic() { return !!(this.projectId && this.draftId && this.graphic.template_id && this.graphic.pages.length >= 3 &&
        (this.graphic.generation_mode === 'local_template' || this.imageReady)); },
      missingChecks() {
        const checks = [
          { key: 'source', ok: !!(this.projectId && this.draftId), label: '选择已保存稿件版本' },
          { key: 'prompt', ok: !!this.prompt.result, label: '优化并保存本次提示词' },
          { key: 'template', ok: !!this.currentTemplateId, label: '选择当前工作室模板' },
          { key: 'provider', ok: this.currentMode === 'local_template' || this.imageReady, label: '配置图片生成 Provider' },
        ];
        if (this.studio === 'graphic') checks.push({ key: 'pages', ok: this.graphic.pages.length >= 3, label: '确认至少 3 页图文内容' });
        return checks;
      },
      filteredPromptPresets() {
        if (this.promptFilter === 'all') return this.promptPresets;
        return this.promptPresets.filter((item) => item.scope === this.promptFilter);
      },
      editingPage() {
        return (this.currentDocument?.pages || []).find((page) => page.id === this.editingPageId) || null;
      },
      coverVisibleSummary() {
        const fields = this.coverFields();
        return [fields.title, fields.subtitle, ...(fields.features || [])].filter(Boolean);
      },
    },
    async mounted() {
      const params = new URLSearchParams(location.search);
      const requested = params.get('project') || '';
      const requestedTab = params.get('visual_tab') || '';
      const requestedHistoryKind = params.get('history_kind') || '';
      const requestedDocument = params.get('document') || '';
      await this.loadAll();
      this.projectId = (requested && this.projects.some((item) => item.id === requested)) ? requested : (this.projects[0]?.id || '');
      if (this.projectId) await this.openProject();
      if (['studio','brand','history'].includes(requestedTab)) this.tab = requestedTab;
      if (['cover','graphic'].includes(requestedHistoryKind)) this.historyKind = requestedHistoryKind;
      if (this.tab === 'history' && this.historyKind === 'graphic' && this.documents.length) {
        const document = this.documents.find((item) => item.id === requestedDocument) || this.documents[0];
        await this.openDocument(document);
      }
      this.loading = false;
    },
    methods: {
      modeLabel(value) { return MODE_LABELS[value] || value; },
      roleLabel(value) { return ROLE_LABELS[value] || value; },
      formatDate(value) { return String(value || '').slice(0, 16).replace('T', ' '); },
      goCreation() { window.__app.go('creation'); },
      async loadAll() {
        try {
          const [projects, profile, templates, assets, settings, batches, documents, presets] = await Promise.all([
            api.get('/api/creation/projects'), api.get('/api/visuals/profile'), api.get('/api/visuals/templates'),
            api.get('/api/visuals/assets'), api.get('/api/settings'), api.get('/api/visuals/batches'),
            api.get('/api/visuals/graphic-documents'), api.get('/api/visuals/prompt-presets'),
          ]);
          this.projects = projects; this.profile = profile; this.templates = templates; this.assets = assets;
          this.settings = settings; this.batches = batches; this.documents = documents; this.promptPresets = presets;
          this.applyDefaults();
        } catch (error) { handleError(error); }
      },
      applyDefaults() {
        const coverTemplate = this.templates.find((x) => x.template_type === 'cover' && x.is_default) || this.templates.find((x) => x.template_type === 'cover');
        const graphicTemplate = this.templates.find((x) => x.template_type === 'graphic');
        this.cover.template_id ||= coverTemplate?.id || '';
        this.graphic.template_id ||= graphicTemplate?.id || '';
        this.selectPromptPreset();
      },
      selectPromptPreset() {
        const scope = this.studio === 'cover' ? 'cover' : 'graphic_note';
        if (!this.promptPresets.some((x) => x.id === this.prompt.preset_id && x.scope === scope)) {
          this.prompt.preset_id = this.promptPresets.find((x) => x.scope === scope)?.id || '';
          this.prompt.result = null;
        }
      },
      async openProject() {
        if (!this.projectId) { this.project = null; this.draftId = ''; return; }
        try {
          this.project = await api.get('/api/creation/projects/' + this.projectId);
          const drafts = this.project.drafts || [];
          this.draftId = drafts.length ? drafts[drafts.length - 1].id : '';
          this.cover.fields.main_topic = this.project.title || '';
          this.cover.fields.title = this.project.title || '';
          this.cover.fields.subtitle = '';
          this.cover.fields.features = '';
          this.cover.fields.keywords = '';
          this.graphic.title = this.project.title || '';
          this.graphic.pages = []; this.prompt.result = null;
        } catch (error) { handleError(error); }
      },
      chooseStudio(kind) {
        this.studio = kind; this.selectPromptPreset();
        const wanted = this.templates.find((x) => x.template_type === kind);
        if (kind === 'cover' && !this.cover.template_id) this.cover.template_id = wanted?.id || '';
        if (kind === 'graphic' && !this.graphic.template_id) this.graphic.template_id = wanted?.id || '';
      },
      splitValues(value) { return String(value || '').split(/[\n,，、]+/).map((x) => x.trim()).filter(Boolean); },
      coverKey(value) { return String(value || '').toLowerCase().replace(/[^\p{L}\p{N}]+/gu, ''); },
      cleanCoverText(value, limit) {
        return String(value || '').replace(/\s+/g, ' ').trim().replace(/^[,，、;；|｜]+|[,，、;；|｜]+$/g, '').slice(0, limit);
      },
      isInternalCoverText(value) {
        const text = this.cleanCoverText(value, 200);
        const labels = ['观点型','教程型','案例型','清单型','市场热点','行业热点','平台热点','对标高表现','账号均值','历史均值','预测档位','内容类型','写作中'];
        return labels.includes(text) || /\b(?:SG|CTX|SC|VPR|VB|CV|A)-[A-Za-z0-9-]{4,}\b/i.test(text) ||
          /(?:收藏|点赞|评论|分享|完播|互动|转化)率?\s*(?:>|<|=|≥|≤|大于|小于|高于|低于)\s*(?:账号|历史)?均值/.test(text);
      },
      cleanCoverList(value, limit, itemLimit, against) {
        const seen = new Set((against || []).map((item) => this.coverKey(item)).filter(Boolean));
        const result = [];
        for (const item of this.splitValues(value)) {
          const text = this.cleanCoverText(item, itemLimit); const key = this.coverKey(text);
          if (!text || !key || this.isInternalCoverText(text)) continue;
          if (seen.has(key) || [...seen].some((other) => other.length >= 4 && key.length >= 4 && (other.includes(key) || key.includes(other)))) continue;
          seen.add(key); result.push(text);
          if (result.length >= limit) break;
        }
        return result;
      },
      coverFields() {
        const fallback = this.cleanCoverText(this.project?.title || '', 36);
        let mainTopic = this.cleanCoverText(this.cover.fields.main_topic || fallback, 36);
        if (this.isInternalCoverText(mainTopic)) mainTopic = fallback;
        let title = this.cleanCoverText(this.cover.fields.title || mainTopic || fallback, 32);
        if (this.isInternalCoverText(title)) title = mainTopic || fallback;
        let subtitle = this.cleanCoverText(this.cover.fields.subtitle, 28);
        if (this.isInternalCoverText(subtitle) || this.coverKey(subtitle) === this.coverKey(title)) subtitle = '';
        const features = this.cleanCoverList(this.cover.fields.features, 2, 10, [title, subtitle]);
        const keywords = this.cleanCoverList(this.cover.fields.keywords, 5, 16, [mainTopic, title, subtitle, ...features]);
        return { main_topic: mainTopic || title, title: title || mainTopic || '未命名主题', subtitle,
          category: this.cleanCoverText(this.cover.fields.category || '知识IP', 16), features, keywords };
      },
      async optimizePrompt() {
        if (!this.canOptimize) return toast('请先选择已保存稿件和提示词预设。', 'warn');
        this.busy = 'prompt';
        try {
          const outputKind = this.studio === 'cover' ? 'cover' : 'graphic_note';
          const result = await api.post('/api/visuals/prompts/optimize', {
            creation_project_id: this.projectId, draft_version_id: this.draftId,
            output_kind: outputKind, prompt_mode: this.prompt.mode, preset_id: this.prompt.preset_id,
            ratio: this.currentRatio, raw_prompt: this.prompt.raw,
            fields: this.studio === 'cover' ? this.coverFields() : {},
          });
          this.prompt.result = result;
          if (this.studio === 'cover' && result.fields) {
            const f = result.fields;
            this.cover.fields = { main_topic: f.main_topic || '', title: f.title || '', subtitle: f.subtitle || '',
              category: f.category || 'AI知识IP', features: (f.features || []).join('\n'), keywords: (f.keywords || []).join('、') };
          }
          toast('提示词已结合当前稿件与IP档案完成优化。');
        } catch (error) { handleError(error); }
        this.busy = '';
      },
      async planGraphic() {
        if (!this.projectId || !this.draftId) return toast('请先选择已保存稿件。', 'warn');
        this.busy = 'plan';
        try {
          const result = await api.post('/api/visuals/graphic-outline', {
            creation_project_id: this.projectId, draft_version_id: this.draftId, page_count: Number(this.graphic.page_count),
            narrative_mode: this.graphic.narrative_mode,
          });
          this.graphic.title = result.title || this.graphic.title;
          this.graphic.pages = (result.pages || []).map((page) => ({ ...page, bullets_text: (page.bullets || []).join('\n') }));
          this.graphic.narrative_mode = result.narrative_mode || this.graphic.narrative_mode;
          toast(result.note || '图文结构已生成。');
        } catch (error) { handleError(error); }
        this.busy = '';
      },
      addPage() {
        if (this.graphic.pages.length >= 12) return toast('单个图文最多 12 页。', 'warn');
        const index = this.graphic.pages.length + 1;
        this.graphic.pages.push({ index, page_index: index, role: 'insight', title: '新页面', body: '', bullets_text: '', layout_key: 'editorial', visual_prompt: '', locked: false });
      },
      removePage(index) {
        if (this.graphic.pages.length <= 3) return toast('图文至少保留 3 页。', 'warn');
        this.graphic.pages.splice(index, 1); this.normalizePages();
      },
      movePage(index, offset) {
        const target = index + offset; if (target < 0 || target >= this.graphic.pages.length) return;
        const pages = [...this.graphic.pages]; [pages[index], pages[target]] = [pages[target], pages[index]];
        this.graphic.pages = pages; this.normalizePages();
      },
      normalizePages() { this.graphic.pages.forEach((page, index) => { page.index = index + 1; page.page_index = index + 1; }); },
      async generateCover() {
        if (!this.canGenerateCover) return toast('请完成来源、模板和Provider检查。', 'warn');
        this.busy = 'cover';
        try {
          const result = await api.post('/api/visuals/covers', {
            creation_project_id: this.projectId, draft_version_id: this.draftId,
            template_id: this.cover.template_id, ratio: this.cover.ratio, content_type: 'cover',
            generation_mode: this.cover.generation_mode, quantity: Number(this.cover.quantity),
            use_person: this.cover.use_person, use_logo: this.cover.use_logo,
            reference_asset_ids: this.cover.reference_asset_ids,
            prompt_run_id: this.prompt.result?.id || null, fields: this.coverFields(),
          });
          this.batches = await api.get('/api/visuals/batches'); this.historyKind = 'cover'; this.tab = 'history';
          toast(`已生成 ${result.items.length} 个封面候选并完整存档。`);
        } catch (error) { handleError(error); }
        this.busy = '';
      },
      async generateGraphic() {
        if (!this.canGenerateGraphic) return toast('请先完成逐页规划、模板和Provider检查。', 'warn');
        this.busy = 'graphic';
        try {
          const pages = this.graphic.pages.map((page) => ({ ...page, bullets: this.splitValues(page.bullets_text) }));
          const result = await api.post('/api/visuals/graphic-documents', {
            creation_project_id: this.projectId, draft_version_id: this.draftId, title: this.graphic.title,
            platform: this.graphic.platform, ratio: this.graphic.ratio, generation_mode: this.graphic.generation_mode,
            template_id: this.graphic.template_id, prompt_run_id: this.prompt.result?.id || null,
            use_logo: this.graphic.use_logo, reference_asset_ids: this.graphic.reference_asset_ids, pages,
          });
          this.documents = await api.get('/api/visuals/graphic-documents');
          result.pages = (result.pages || []).map((page) => ({ ...page, bullets_text: (page.bullets || []).join('\n') }));
          this.currentDocument = result; this.editingPageId = result.pages[0]?.id || ''; this.documentEditing = false;
          this.historyKind = 'graphic'; this.tab = 'history';
          toast(`已生成并保存 ${result.pages.length} 页图文文档。`);
        } catch (error) { handleError(error); }
        this.busy = '';
      },
      async openDocument(document) {
        try {
          const result = await api.get('/api/visuals/graphic-documents/' + document.id);
          result.pages = (result.pages || []).map((page) => ({ ...page, bullets_text: (page.bullets || []).join('\n') }));
          this.currentDocument = result;
          this.editingPageId = result.pages[0]?.id || '';
          this.documentEditing = false;
        }
        catch (error) { handleError(error); }
      },
      async saveDocumentTitle() {
        if (!this.currentDocument?.title?.trim()) return toast('请填写文档标题。', 'warn');
        try {
          const result = await api.put('/api/visuals/graphic-documents/' + this.currentDocument.id, { title: this.currentDocument.title });
          const listItem = this.documents.find((item) => item.id === this.currentDocument.id);
          if (listItem) { listItem.title = result.title; if (result.detached) listItem.status = 'DRAFT'; }
          if (result.detached) this.currentDocument.status = 'DRAFT';
          toast(result.note);
        } catch (error) { handleError(error); }
      },
      async finalizeDocument(document) {
        try {
          const result = await api.post('/api/visuals/graphic-documents/' + document.id + '/finalize', {});
          document.status = 'FINALIZED';
          if (this.currentDocument?.id === document.id) this.currentDocument.status = 'FINALIZED';
          toast(result.note);
        } catch (error) { handleError(error); }
      },
      async saveDocumentPage(page) {
        try {
          const result = await api.put('/api/visuals/graphic-pages/' + page.id, {
            title: page.title, body: page.body, bullets: this.splitValues(page.bullets_text), role: page.role,
            layout_key: page.layout_key, visual_prompt: page.visual_prompt || '', locked: !!page.locked,
          });
          page.bullets = this.splitValues(page.bullets_text);
          if (result.detached) {
            this.currentDocument.status = 'DRAFT';
            const listItem = this.documents.find((item) => item.id === this.currentDocument.id);
            if (listItem) listItem.status = 'DRAFT';
          }
          toast(result.note);
          return true;
        } catch (error) { handleError(error); return false; }
      },
      async saveAndRegeneratePage(page) {
        const saved = await this.saveDocumentPage(page);
        if (!saved) return;
        await this.regeneratePage(page);
      },
      async toggleDocumentPageLock(page) {
        try {
          page.locked = !page.locked;
          await api.put('/api/visuals/graphic-pages/' + page.id, { locked: page.locked });
          toast(page.locked ? '页面已锁定。' : '页面已解锁。');
        } catch (error) { page.locked = !page.locked; handleError(error); }
      },
      async moveSavedPage(index, offset) {
        const target = index + offset; const pages = this.currentDocument?.pages || [];
        if (target < 0 || target >= pages.length) return;
        [pages[index], pages[target]] = [pages[target], pages[index]];
        pages.forEach((page, i) => { page.page_index = i + 1; });
        try { await api.post('/api/visuals/graphic-documents/' + this.currentDocument.id + '/reorder', { page_ids: pages.map((x) => x.id) }); }
        catch (error) { handleError(error); await this.openDocument(this.currentDocument); }
      },
      async regeneratePage(page) {
        try {
          const result = await api.post('/api/visuals/graphic-pages/' + page.id + '/regenerate', {});
          page.preview_url = result.preview_url + '?v=' + result.revision_no; toast(result.note);
        } catch (error) { handleError(error); }
      },
      async selectCover(batch, item) {
        try {
          const result = await api.post('/api/visuals/covers/' + item.id + '/select', {});
          (batch.items || []).forEach((x) => { x.selected = x.id === item.id; }); toast(result.note);
        } catch (error) { handleError(error); }
      },
      async deleteBatch(batch) {
        const adopted = (batch.items || []).some((item) => item.selected);
        const warning = adopted ? '\n该合集包含已采用封面，删除后会同时解除创作项目和内容计划中的视觉关联。' : '';
        if (!confirm(`删除封面合集“${batch.title}”？${warning}\n该操作会让合集立即从当前历史中消失。`)) return;
        try { const result = await api.delete('/api/visuals/batches/' + batch.id + '?force=true'); this.batches = this.batches.filter((x) => x.id !== batch.id); toast(result.note); }
        catch (error) { handleError(error); }
      },
      async deleteDocument(document) {
        const warning = document.status === 'FINALIZED' ? '\n该文档已定稿，删除后会同时解除创作项目和内容计划中的视觉关联。' : '';
        if (!confirm(`删除图文文档“${document.title}”？${warning}\n删除后会立即从当前历史中消失。`)) return;
        try { const result = await api.delete('/api/visuals/graphic-documents/' + document.id + '?force=true'); this.documents = this.documents.filter((x) => x.id !== document.id); if (this.currentDocument?.id === document.id) this.currentDocument = null; toast(result.note); }
        catch (error) { handleError(error); }
      },
      async deleteCover(batch, item) {
        const warning = item.selected ? '\n该候选已采用，删除后会同时解除创作项目和内容计划中的封面关联。' : '';
        if (!confirm(`删除候选 ${item.item_index} · v${item.revision_no || 1}？${warning}\n删除后会立即从当前历史中消失。`)) return;
        try {
          const result = await api.delete('/api/visuals/covers/' + item.id + '?force=true');
          batch.items = (batch.items || []).filter((candidate) => candidate.id !== item.id);
          if (result.batch_deleted || !batch.items.length) this.batches = this.batches.filter((candidate) => candidate.id !== batch.id);
          toast(result.note);
        } catch (error) { handleError(error); }
      },
      async deleteAsset(asset) {
        if (!confirm(`归档视觉资产“${asset.name}”？已生成成品仍保留来源记录。`)) return;
        try {
          const result = await api.delete('/api/visuals/assets/' + asset.id);
          this.assets = this.assets.filter((item) => item.id !== asset.id);
          this.cover.reference_asset_ids = this.cover.reference_asset_ids.filter((id) => id !== asset.id);
          this.graphic.reference_asset_ids = this.graphic.reference_asset_ids.filter((id) => id !== asset.id);
          toast(result.note);
        } catch (error) { handleError(error); }
      },
      async deletePromptPreset(preset) {
        if (preset.builtin) return toast('内置预设不能删除，可以复制后建立自己的版本。', 'warn');
        if (!confirm(`归档提示词预设“${preset.name}”？`)) return;
        try {
          const result = await api.delete('/api/visuals/prompt-presets/' + preset.id);
          this.promptPresets = this.promptPresets.filter((item) => item.id !== preset.id);
          this.selectPromptPreset();
          toast(result.note);
        } catch (error) { handleError(error); }
      },
      copyPromptPreset(preset) {
        this.presetDraft = {
          name: `${preset.name} · 我的版本`, scope: preset.scope,
          template_text: preset.template_text || '', negative_prompt: preset.negative_prompt || '',
        };
        this.promptFilter = preset.scope; toast('已复制到右侧编辑器，可调整后保存。');
      },
      async upload(event, kind) {
        const file = event.target.files?.[0]; if (!file) return;
        this.busy = 'upload-' + kind; const form = new FormData(); form.append('file', file);
        try {
          const endpoint = kind === 'person' ? '/api/visuals/profile/person-image' : '/api/visuals/profile/logo';
          const result = await api.upload(endpoint, form); toast(result.note);
          [this.profile, this.assets] = await Promise.all([api.get('/api/visuals/profile'), api.get('/api/visuals/assets')]);
        } catch (error) { handleError(error); }
        this.busy = ''; event.target.value = '';
      },
      async savePromptPreset() {
        if (!this.presetDraft.name.trim() || this.presetDraft.template_text.trim().length < 20) {
          return toast('请填写预设名称和完整提示词。', 'warn');
        }
        this.busy = 'preset';
        try {
          const result = await api.post('/api/visuals/prompt-presets', this.presetDraft);
          this.promptPresets = await api.get('/api/visuals/prompt-presets');
          this.prompt.preset_id = result.id;
          this.presetDraft = { name: '', scope: this.studio === 'cover' ? 'cover' : 'graphic_note', template_text: '', negative_prompt: '' };
          toast(result.note);
        } catch (error) { handleError(error); }
        this.busy = '';
      },
      toggleReference(assetId, target) {
        const values = target.reference_asset_ids;
        const index = values.indexOf(assetId); if (index >= 0) values.splice(index, 1); else values.push(assetId);
      },
    },
    template: `
    <section class="visual-v3">
      <div class="page-head visual-v3-head">
        <div><div class="page-title">视觉工厂</div><div class="page-q">封面与多页图文独立生产；所有来源、提示词、版本和采用结果都可追溯。</div></div>
        <button class="btn" @click="goCreation">返回创作工坊</button>
      </div>
      <div class="visual-v3-status">
        <span><i class="status-dot ok"></i>真实稿件驱动</span>
        <span><i class="status-dot" :class="imageReady?'ok':'warn'"></i>{{ imageReady?'图片 Provider 已就绪':'图片 Provider 待配置' }}</span>
        <span>{{ templates.length }} 套分域模板</span><span>{{ batches.length }} 个封面合集 · {{ documents.length }} 个图文文档</span>
      </div>
      <div class="tabs visual-v3-tabs">
        <button class="tab" :class="{active:tab==='studio'}" @click="tab='studio'">生成工作台</button>
        <button class="tab" :class="{active:tab==='brand'}" @click="tab='brand'">IP资产与提示词</button>
        <button class="tab" :class="{active:tab==='history'}" @click="tab='history'">成品历史</button>
      </div>
      <div class="card" v-if="loading"><div class="loading">正在读取视觉工作区…</div></div>

      <template v-else-if="tab==='studio'">
        <div class="visual-domain-switch">
          <button :class="{active:studio==='cover'}" @click="chooseStudio('cover')"><b>封面工作室</b><small>单张主视觉与候选版本</small></button>
          <button :class="{active:studio==='graphic'}" @click="chooseStudio('graphic')"><b>图文笔记工作室</b><small>逐页规划、编辑、出图与定稿</small></button>
        </div>
        <div class="visual-v3-grid">
          <main class="visual-v3-main">
            <section class="card visual-v3-section">
              <div class="visual-v3-section-head"><span>1</span><div><b>绑定真实内容来源</b><small>视觉成品必须关联已保存稿件，后续才能进入计划、发布与复盘。</small></div></div>
              <div class="grid g2">
                <label class="field"><span>创作项目</span><select class="input" v-model="projectId" @change="openProject"><option value="">选择项目</option><option v-for="item in projects" :value="item.id" :key="item.id">{{ item.title }}</option></select></label>
                <label class="field"><span>稿件版本</span><select class="input" v-model="draftId"><option value="">选择已保存版本</option><option v-for="item in activeDrafts" :value="item.id" :key="item.id">v{{item.version_no}} · {{item.platform||'未标注平台'}}</option></select></label>
              </div>
            </section>

            <section class="card visual-v3-section">
              <div class="visual-v3-section-head"><span>2</span><div><b>提示词编译器</b><small>智能默认适合不会写提示词的用户；自定义模式也能一键优化并保存原始版本。</small></div></div>
              <div class="visual-prompt-mode">
                <button :class="{active:prompt.mode==='smart'}" @click="prompt.mode='smart'"><b>智能默认</b><small>从稿件自动提炼标题、卖点与视觉要求</small></button>
                <button :class="{active:prompt.mode==='custom'}" @click="prompt.mode='custom'"><b>自定义提示词</b><small>保留你的要求，再由AI补齐专业约束</small></button>
              </div>
              <label class="field"><span>提示词预设</span><select class="input" v-model="prompt.preset_id"><option v-for="item in promptPresets.filter(x=>x.scope===(studio==='cover'?'cover':'graphic_note'))" :key="item.id" :value="item.id">{{item.name}}</option></select></label>
              <label class="field"><span>{{prompt.mode==='smart'?'补充要求（可选）':'你的提示词'}}</span><textarea class="input" rows="4" v-model="prompt.raw" :placeholder="prompt.mode==='smart'?'例如：更像产品发布会，保留人物原图，标题更克制':'用自然语言写你的想法，AI会补齐比例、安全区、品牌和文字规则'"></textarea></label>
              <div class="visual-prompt-actions"><button class="btn btn-pri" :disabled="busy==='prompt'||!canOptimize" @click="optimizePrompt">{{busy==='prompt'?'正在优化…':'AI 一键优化提示词'}}</button><span v-if="prompt.result">已保存提示词版本 {{prompt.result.id}}</span></div>
              <div class="visual-prompt-result" v-if="prompt.result">
                <div><b>原始输入</b><p>{{prompt.result.raw_prompt||'使用智能默认，无额外输入'}}</p></div>
                <div><b>AI优化要求</b><p>{{prompt.result.optimized_prompt}}</p></div>
                <div class="full"><b>最终编译提示词</b><textarea class="input mono" rows="7" v-model="prompt.result.compiled_prompt"></textarea></div>
              </div>
            </section>

            <section class="card visual-v3-section" v-if="studio==='cover'">
              <div class="visual-v3-section-head"><span>3</span><div><b>封面内容与规格</b><small>封面只负责第一眼识别，不与图文正文共用版式。</small></div></div>
              <div class="grid g2"><label class="field"><span>核心主题（仅供 AI 理解）</span><input class="input" v-model="cover.fields.main_topic"><small>不会作为第二个标题重复排版</small></label><label class="field"><span>主标题（画面唯一大字）</span><input class="input" v-model="cover.fields.title" maxlength="32"><small>建议 8–18 字，最多 32 字</small></label><label class="field"><span>副标题（选填，画面可见）</span><input class="input" v-model="cover.fields.subtitle" maxlength="28"><small>运营目标、账号指标和来源标签不会上画面</small></label><label class="field"><span>领域（仅供 AI 理解）</span><input class="input" v-model="cover.fields.category"></label></div>
              <div class="grid g2"><label class="field"><span>辅助短标签（选填，最多 2 项）</span><textarea class="input" rows="3" v-model="cover.fields.features" placeholder="每行一个，每项不超过 10 字"></textarea></label><label class="field"><span>视觉关键词（仅供 AI 理解）</span><textarea class="input" rows="3" v-model="cover.fields.keywords" placeholder="用于风格与语义，不会生成关键词栏"></textarea></label></div>
              <div class="cover-content-contract"><b>本次允许上画面的文字</b><div><span v-for="item in coverVisibleSummary" :key="item">{{item}}</span><em v-if="!coverVisibleSummary.length">请填写主标题</em></div><small>其他项目目标、热点标签、预测指标、证据 ID 和模板名称一律只留在系统内部。</small></div>
              <div class="visual-spec-row">
                <div class="visual-spec-group"><span>成品比例</span><div class="visual-ratio-picker"><button v-for="ratio in ['3:4','9:16','1:1','16:9']" :class="{active:cover.ratio===ratio}" @click="cover.ratio=ratio">{{ratio}}</button></div></div>
                <label class="field visual-quantity-field"><span>候选数量</span><input class="input" type="number" min="1" max="4" v-model.number="cover.quantity"><small>一次请求形成一个候选合集</small></label>
                <label class="settings-private-toggle visual-person-toggle"><input type="checkbox" v-model="cover.use_person" :disabled="!profile.has_person_image"><span><b>使用默认人物参考图</b><small>{{profile.has_person_image?'保持IP人物一致性':'请先到IP资产页上传人物图'}}</small></span></label>
              </div>
            </section>

            <section class="card visual-v3-section" v-else>
              <div class="visual-v3-section-head"><span>3</span><div><b>多页图文结构</b><small>每页承担一个信息任务，不再把同一套封面模板复制多次。</small></div><button class="btn btn-pri btn-sm" :disabled="busy==='plan'" @click="planGraphic">{{busy==='plan'?'正在规划…':'从稿件规划图文'}}</button></div>
              <div class="graphic-v3-toolbar">
                <label>页数 <input class="input input-sm" type="number" min="3" max="12" v-model.number="graphic.page_count"></label>
                <label>平台 <select class="input input-sm" v-model="graphic.platform"><option>小红书图文</option><option>公众号长图</option><option>视频号图文</option></select></label>
                <label class="graphic-narrative-field">叙事策略 <select class="input input-sm" v-model="graphic.narrative_mode"><option value="auto">智能自适应</option><option value="tutorial">教程步骤</option><option value="insight">观点洞察</option><option value="case_study">案例复盘</option><option value="comparison">对比决策</option><option value="checklist">清单指南</option><option value="story">故事论证</option></select></label>
                <div class="visual-ratio-picker compact"><button v-for="ratio in ['3:4','9:16','1:1','16:9']" :class="{active:graphic.ratio===ratio}" @click="graphic.ratio=ratio">{{ratio}}</button></div><button class="btn btn-sm" @click="addPage">新增一页</button>
              </div>
              <div class="graphic-narrative-note"><b>{{graphic.narrative_mode==='auto'?'系统会根据稿件判断最合适的结构':'当前会按所选策略规划，但不会机械复制固定页序'}}</b><span>生成后仍可逐页修改角色、版式、标题、正文和要点。</span></div>
              <div class="graphic-v3-pages" v-if="graphic.pages.length">
                <article v-for="(page,index) in graphic.pages" :key="index">
                  <div class="graphic-v3-page-head"><span>{{index+1}}</span><select class="input input-sm" v-model="page.role" :disabled="page.locked"><option v-for="(label,key) in roleLabels" :value="key">{{label}}</option></select><select class="input input-sm" v-model="page.layout_key" :disabled="page.locked"><option v-for="(label,key) in layoutLabels" :value="key">{{label}}</option></select></div>
                  <input class="input" v-model="page.title" :disabled="page.locked" placeholder="本页标题"><textarea class="input" rows="3" v-model="page.body" :disabled="page.locked" placeholder="本页正文"></textarea><textarea class="input" rows="2" v-model="page.bullets_text" :disabled="page.locked" placeholder="要点，每行一条"></textarea>
                  <div class="graphic-v3-page-actions"><button class="text-btn" :disabled="index===0" @click="movePage(index,-1)">上移</button><button class="text-btn" :disabled="index===graphic.pages.length-1" @click="movePage(index,1)">下移</button><button class="text-btn" @click="page.locked=!page.locked">{{page.locked?'解锁':'锁定'}}</button><button class="text-btn danger" @click="removePage(index)">删除</button></div>
                </article>
              </div><div class="empty compact" v-else><b>还没有图文结构</b><span>先从稿件规划 3–12 页，再逐页确认内容。</span></div>
            </section>

            <section class="card visual-v3-section">
              <div class="visual-v3-section-head"><span>4</span><div><b>生成方式与参考资产</b><small>直接AI、混合排版和本地模板三种方式均保留，不强制单一路径。</small></div></div>
              <div class="visual-generation-modes">
                <label v-for="mode in ['ai_full','ai_hybrid','local_template']" :class="{active:currentMode===mode}"><input type="radio" :value="mode" :checked="currentMode===mode" @change="studio==='cover'?(cover.generation_mode=mode):(graphic.generation_mode=mode)"><span><b>{{modeLabel(mode)}}</b><small>{{mode==='ai_full'?'图片模型直接完成视觉、文字和版式；生成后需核对中文':mode==='ai_hybrid'?'图片模型生成视觉，中文由稳定排版引擎合成':'不调用图片API，适合快速稳定出图'}}</small></span></label>
              </div>
              <div class="visual-template-row"><button v-for="item in activeTemplates" :key="item.id" :class="{active:currentTemplateId===item.id}" @click="studio==='cover'?(cover.template_id=item.id):(graphic.template_id=item.id)"><img :src="item.preview_url" :alt="item.name"><span><b>{{item.name}}</b><small>{{item.ratio}}</small></span></button></div>
              <div class="visual-assets-select" v-if="assets.length"><b>可选参考资产</b><button v-for="asset in assets" :key="asset.id" :class="{active:(studio==='cover'?cover:graphic).reference_asset_ids.includes(asset.id)}" @click="toggleReference(asset.id,studio==='cover'?cover:graphic)"><img :src="asset.file_url"><span>{{asset.name}}</span></button></div>
            </section>
          </main>

          <aside class="card visual-v3-summary">
            <div class="card-head"><div><div class="card-title">生成准备</div><p>{{studio==='cover'?'封面候选':'多页图文文档'}} · {{currentRatio}} · {{modeLabel(currentMode)}}</p></div></div>
            <div class="visual-ready-count" :class="{ready:missingChecks.every(item=>item.ok)}"><b>{{missingChecks.filter(item=>item.ok).length}} / {{missingChecks.length}}</b><span>{{missingChecks.every(item=>item.ok)?'已经可以生成':'完成下列必填项后生成'}}</span></div>
            <div class="visual-checks"><span v-for="item in missingChecks" :key="item.key" :class="{ok:item.ok}">{{item.ok?'已完成：':'待完成：'}}{{item.label}}</span></div>
            <button class="btn btn-pri visual-v3-generate" v-if="studio==='cover'" :disabled="busy==='cover'||!canGenerateCover" @click="generateCover">{{busy==='cover'?'正在生成并存档…':'生成封面候选'}}</button>
            <button class="btn btn-pri visual-v3-generate" v-else :disabled="busy==='graphic'||!canGenerateGraphic" @click="generateGraphic">{{busy==='graphic'?'正在逐页生成…':'生成整组图文'}}</button>
            <p class="mini-note">禁用时请按“待完成”提示补齐；真实 Provider 失败会显示原因，不会返回假成品。</p>
          </aside>
        </div>
      </template>

      <template v-else-if="tab==='brand'">
        <div class="visual-brand-grid">
          <section class="card visual-brand-assets"><div class="card-head"><div><div class="card-title">IP视觉资产库</div><p>人物、Logo和风格参考图长期复用；删除只会归档资产，不影响已经生成的成品。</p></div></div><div class="card-body"><div class="visual-upload-grid"><label class="visual-upload-card"><input class="file-input-hidden" type="file" accept="image/*" @change="upload($event,'person')"><span class="visual-upload-icon">人物</span><span class="visual-upload-copy"><b>{{busy==='upload-person'?'正在上传…':'上传人物参考图'}}</b><small>建议干净半身照或透明 PNG，用于保持身份一致</small></span><em>选择文件</em></label><label class="visual-upload-card"><input class="file-input-hidden" type="file" accept="image/*" @change="upload($event,'logo')"><span class="visual-upload-icon logo">Logo</span><span class="visual-upload-copy"><b>{{busy==='upload-logo'?'正在上传…':'上传品牌 Logo'}}</b><small>原文件会存档，历史成品始终保留来源</small></span><em>选择文件</em></label></div><div class="visual-assets-grid"><article class="visual-asset" v-for="asset in assets" :key="asset.id"><img :src="asset.file_url"><button class="visual-asset-delete" @click="deleteAsset(asset)">归档</button><div class="visual-asset-body"><b :title="asset.name">{{asset.name}}</b><small>{{asset.kind==='person'?'人物参考图':'品牌资产'}}</small></div></article><div class="empty" v-if="!assets.length"><b>还没有视觉资产</b><span>先上传人物或 Logo，之后可以按任务引用。</span></div></div></div></section>
          <section class="card visual-prompt-library"><div class="card-head"><div><div class="card-title">提示词预设库</div><p>封面与图文独立管理；内置预设可复制，自定义预设可归档。</p></div></div><div class="card-body"><div class="visual-prompt-filter"><button :class="{active:promptFilter==='all'}" @click="promptFilter='all'">全部</button><button :class="{active:promptFilter==='cover'}" @click="promptFilter='cover'">封面</button><button :class="{active:promptFilter==='graphic_note'}" @click="promptFilter='graphic_note'">图文</button></div><div class="visual-prompt-list"><article class="prompt-preset-card" v-for="item in filteredPromptPresets" :key="item.id"><div><div><b>{{item.name}}</b><span>{{item.scope==='cover'?'封面':'多页图文'}} · {{item.builtin?'内置':'自定义'}}</span></div><div class="prompt-preset-actions"><button class="text-btn" @click="copyPromptPreset(item)">复制编辑</button><button class="text-btn danger" v-if="!item.builtin" @click="deletePromptPreset(item)">归档</button></div></div><p>{{item.template_text}}</p></article></div><div class="visual-preset-editor"><div class="visual-preset-title"><b>保存我的提示词预设</b><span>长期规则与本次任务要求分开留档</span></div><div class="grid g2"><label class="field"><span>预设名称</span><input class="input" v-model="presetDraft.name" placeholder="例如：见舟AI科技封面"></label><label class="field"><span>适用范围</span><select class="input" v-model="presetDraft.scope"><option value="cover">封面</option><option value="graphic_note">多页图文</option></select></label></div><label class="field"><span>提示词模板</span><textarea class="input mono" rows="6" v-model="presetDraft.template_text" placeholder="可使用 {{RATIO}}、{{TITLE}}、{{SUBTITLE}} 等字段"></textarea></label><label class="field"><span>负向要求</span><textarea class="input" rows="3" v-model="presetDraft.negative_prompt" placeholder="例如：错别字、文字溢出、身份变化"></textarea></label><button class="btn btn-pri" :disabled="busy==='preset'" @click="savePromptPreset">{{busy==='preset'?'正在保存…':'保存为我的预设'}}</button></div></div></section>
        </div>
      </template>

      <template v-else>
        <div class="visual-history-kind"><button :class="{active:historyKind==='cover'}" @click="historyKind='cover'">封面合集 {{batches.length}}</button><button :class="{active:historyKind==='graphic'}" @click="historyKind='graphic'">图文文档 {{documents.length}}</button></div>
        <div class="visual-batch-list" v-if="historyKind==='cover'"><article class="visual-batch-card" v-for="batch in batches" :key="batch.id"><div class="visual-batch-head"><div><b :title="batch.title">{{batch.title}}</b><small>{{formatDate(batch.created_at)}} · {{batch.ratio}} · {{modeLabel(batch.generation_mode)}} · {{batch.items.length}} 个候选</small></div><button class="btn btn-sm btn-danger" @click="deleteBatch(batch)">删除整个合集</button></div><div class="visual-batch-items"><article v-for="item in batch.items" :class="{selected:item.selected}" :key="item.id"><a class="visual-batch-image" :href="item.preview_url" target="_blank"><img :src="item.preview_url"></a><div class="visual-batch-item-meta"><span>候选 {{item.item_index}} · v{{item.revision_no||1}}</span><b v-if="item.selected">已采用</b></div><div class="visual-batch-item-actions"><a class="text-btn" :href="item.preview_url" target="_blank">查看</a><a class="text-btn" :href="item.preview_url" download>下载</a><button class="text-btn" @click="selectCover(batch,item)">{{item.selected?'已同步':'选定'}}</button><button class="text-btn danger" @click="deleteCover(batch,item)">删除</button></div></article></div></article><div class="empty" v-if="!batches.length"><b>还没有封面合集</b><span>每次请求会作为一个合集保存，候选不会散落。</span></div></div>
        <div class="graphic-history-layout" v-else>
          <div class="graphic-document-list"><article v-for="document in documents" :key="document.id" :class="{active:currentDocument&&currentDocument.id===document.id}"><button @click="openDocument(document)"><b>{{document.title}}</b><span>{{formatDate(document.updated_at)}} · {{document.ratio}} · {{modeLabel(document.generation_mode)}}</span><em>{{document.status==='FINALIZED'?'已定稿':'可编辑'}}</em></button><div><button class="text-btn" @click="finalizeDocument(document)" v-if="document.status!=='FINALIZED'">定稿并同步</button><button class="text-btn danger" @click="deleteDocument(document)">删除</button></div></article><div class="empty" v-if="!documents.length"><b>还没有图文文档</b><span>生成后会按文档管理，可继续编辑、重生成或删除。</span></div></div>
          <section class="card graphic-document-detail" v-if="currentDocument">
            <div class="card-head graphic-document-head"><div><input class="input graphic-document-title-input" v-if="documentEditing" v-model="currentDocument.title"><div class="card-title" v-else>{{currentDocument.title}}</div><p>{{currentDocument.pages.length}} 页 · {{currentDocument.status==='FINALIZED'?'已定稿':'可编辑文档'}}</p></div><div class="graphic-document-head-actions"><button class="btn btn-sm" @click="documentEditing=!documentEditing">{{documentEditing?'退出编辑':'编辑文档'}}</button><button class="btn btn-sm" v-if="documentEditing" @click="saveDocumentTitle">保存标题</button><button class="btn btn-pri btn-sm" @click="finalizeDocument(currentDocument)" v-if="currentDocument.status!=='FINALIZED'">定稿并同步计划</button><button class="btn btn-sm btn-danger" @click="deleteDocument(currentDocument)">删除文档</button></div></div>
            <div class="graphic-document-pages"><article v-for="page in currentDocument.pages" :key="page.id" :class="{active:page.id===editingPageId}"><a class="graphic-document-preview" :style="{aspectRatio:String(currentDocument.ratio||'3:4').replace(':',' / ')}" :href="page.preview_url" target="_blank"><img :src="page.preview_url"></a><div><span>第 {{page.page_index}} 页 · {{roleLabel(page.role)}}</span><b>{{page.title}}</b><p>{{page.body}}</p><div class="graphic-page-card-actions"><button class="text-btn" @click="editingPageId=page.id;documentEditing=true">编辑此页</button><button class="text-btn" @click="regeneratePage(page)">追加新版</button></div></div></article></div>
            <div class="graphic-saved-page-editor" v-if="documentEditing&&editingPage">
              <div class="visual-v3-section-head"><span>{{editingPage.page_index}}</span><div><b>编辑第 {{editingPage.page_index}} 页</b><small>修改会先保存正文，再按当前文档的模板与生成方式追加新图；旧版本不会被覆盖。</small></div></div>
              <div class="grid g2"><label class="field"><span>页面角色</span><select class="input" v-model="editingPage.role" :disabled="editingPage.locked"><option v-for="(label,key) in roleLabels" :value="key">{{label}}</option></select></label><label class="field"><span>内容版式</span><select class="input" v-model="editingPage.layout_key" :disabled="editingPage.locked"><option v-for="(label,key) in layoutLabels" :value="key">{{label}}</option></select></label></div>
              <label class="field"><span>本页标题</span><input class="input" v-model="editingPage.title" :disabled="editingPage.locked"></label>
              <label class="field"><span>正文</span><textarea class="input" rows="5" v-model="editingPage.body" :disabled="editingPage.locked"></textarea></label>
              <label class="field"><span>要点（每行一项）</span><textarea class="input" rows="4" v-model="editingPage.bullets_text" :disabled="editingPage.locked"></textarea></label>
              <label class="field"><span>本页视觉补充要求</span><textarea class="input" rows="3" v-model="editingPage.visual_prompt" :disabled="editingPage.locked" placeholder="例如：单一蓝色路径图形，背景极简，不要文字"></textarea></label>
              <div class="graphic-saved-editor-actions"><button class="btn btn-sm" @click="toggleDocumentPageLock(editingPage)">{{editingPage.locked?'解锁页面':'锁定页面'}}</button><button class="btn btn-sm" :disabled="editingPage.locked" @click="saveDocumentPage(editingPage)">仅保存内容</button><button class="btn btn-pri btn-sm" :disabled="editingPage.locked" @click="saveAndRegeneratePage(editingPage)">保存并追加新版</button></div>
            </div>
          </section>
        </div>
      </template>
    </section>`,
  };
})();
