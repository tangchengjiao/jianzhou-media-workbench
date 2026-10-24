/* 页面：创作工坊 / 内容计划 */
(function () {
  const go = (p) => { window.__app && window.__app.go(p); };

  /* ================= 创作工坊 ================= */
  window.PageCreation = {
    data() {
      return {
        projects: [], activeId: null, p: null,
        loading: true, loadError: null, creating: false, newTitle: '',
        ideaComposerOpen: false, ideaFile: null,
        ideaForm: { id: '', title: '', body: '', personal_judgement: '', audience: '',
          item_type: 'self_idea', source_url: '', tags_text: '' },
        directions: [], directionsError: null, loadingDirections: false, selDir: -1, draft: '', selectedDraftId: null,
        currentPlatform: '抖音口播', generating: false, generatingMulti: false, restoring: false,
        comparingDrafts: false, styleMode: 'structure',
        predictionGenerating: false, coverGenerating: false,
        assessmentGenerating: false, optimizingDraft: false, confirmingCandidate: false,
        selectedIssueIds: [], qualityInstructions: '',
        visualProfile: null, personUploading: false,
        predForm: { goal: '', expected_bucket: '', confidence: 0.6, strengths: '', risks: '', hypothesis: '', user_judgement: '' },
      };
    },
    async mounted() {
      const params = new URLSearchParams(location.search);
      const target = params.get('project');
      const targetDraft = params.get('draft');
      const targetIdea = params.get('idea');
      await Promise.all([this.loadProjects(false), this.loadVisualProfile()]);
      if (target && this.projects.some((item) => item.id === target)) await this.openProject(target, targetDraft);
      else if (this.projects.length) await this.openProject(this.projects[0].id);
      if (targetIdea) {
        try {
          const idea = await api.get('/api/ideas/' + encodeURIComponent(targetIdea));
          this.ideaForm = { id: idea.id, title: idea.title || '', body: idea.body || '',
            personal_judgement: idea.personal_judgement || '', audience: '',
            item_type: idea.item_type || 'self_idea', source_url: idea.source_url || '',
            tags_text: (idea.tags || []).join('，') };
          this.ideaComposerOpen = true;
        } catch (e) { handleError(e); }
      }
    },
    methods: {
      go,
      async loadVisualProfile() {
        try { this.visualProfile = await api.get('/api/visuals/profile'); }
        catch (e) { /* 封面配置失败不阻断稿件编辑 */ }
      },
      async uploadPerson(event) {
        const file = event.target.files && event.target.files[0];
        if (!file) return;
        this.personUploading = true;
        const fd = new FormData(); fd.append('file', file);
        try { const r = await api.upload('/api/visuals/profile/person-image', fd); toast(r.note); await this.loadVisualProfile(); }
        catch (e) { handleError(e); }
        this.personUploading = false; event.target.value = '';
      },
      async loadProjects(autoOpen = true) {
        this.loading = true; this.loadError = null;
        try {
          this.projects = await api.get('/api/creation/projects');
          if (autoOpen && this.projects.length && !this.activeId) await this.openProject(this.projects[0].id);
          if (!this.projects.length) { this.activeId = null; this.p = null; }
        } catch (e) { this.loadError = e; handleError(e); }
        this.loading = false;
      },
      openIdeaComposer() { this.ideaComposerOpen = true; },
      closeIdeaComposer() { if (!this.creating) this.ideaComposerOpen = false; },
      pickIdeaFile(event) { this.ideaFile = (event.target.files && event.target.files[0]) || null; },
      resetIdeaComposer() {
        this.ideaForm = { id: '', title: '', body: '', personal_judgement: '', audience: '',
          item_type: 'self_idea', source_url: '', tags_text: '' };
        this.ideaFile = null; this.newTitle = '';
      },
      async uploadIdeaFile(ideaId) {
        if (!ideaId || !this.ideaFile) return;
        const fd = new FormData(); fd.append('file', this.ideaFile);
        await api.upload('/api/ideas/' + ideaId + '/attachments', fd);
      },
      async saveIdeaOnly() {
        const text = this.ideaForm.body.trim();
        if (!text) return toast('先写下这次真实的灵感、经历或判断', 'warn');
        this.creating = true;
        try {
          const payload = {
            title: this.ideaForm.title.trim(), body: text,
            personal_judgement: this.ideaForm.personal_judgement.trim(),
            item_type: this.ideaForm.item_type, source_url: this.ideaForm.source_url.trim(),
            tags: this.ideaForm.tags_text.split(/[,，]/).map((item) => item.trim()).filter(Boolean),
          };
          const result = this.ideaForm.id
            ? await api.put('/api/ideas/' + encodeURIComponent(this.ideaForm.id), payload)
            : await api.post('/api/ideas', payload);
          await this.uploadIdeaFile(result.id);
          toast('已存入“灵感与随手记”，不会自动生成稿件');
          this.resetIdeaComposer(); this.ideaComposerOpen = false;
        } catch (e) { handleError(e); }
        this.creating = false;
      },
      async createProject() {
        const ideaBody = this.ideaForm.body.trim();
        const title = this.ideaForm.title.trim() || this.newTitle.trim() || ideaBody.split('\n')[0].slice(0, 80);
        if (!ideaBody && !title) return toast('先写下灵感或创作主题', 'warn');
        this.creating = true;
        try {
          if (this.ideaForm.id) {
            await api.put('/api/ideas/' + encodeURIComponent(this.ideaForm.id), {
              title, body: ideaBody, personal_judgement: this.ideaForm.personal_judgement.trim(),
              item_type: this.ideaForm.item_type, source_url: this.ideaForm.source_url.trim(),
              tags: this.ideaForm.tags_text.split(/[,，]/).map((item) => item.trim()).filter(Boolean),
            });
          }
          const result = await api.post('/api/creation/projects', {
            title, goal: '待确认', audience: this.ideaForm.audience.trim(),
            idea_id: this.ideaForm.id || null, idea_body: this.ideaForm.id ? '' : (ideaBody || title),
            personal_judgement: this.ideaForm.personal_judgement.trim(),
            idea_type: this.ideaForm.item_type, source_url: this.ideaForm.source_url.trim(),
            tags: this.ideaForm.tags_text.split(/[,，]/).map((item) => item.trim()).filter(Boolean),
          });
          await this.uploadIdeaFile(result.idea_id);
          this.resetIdeaComposer(); this.ideaComposerOpen = false;
          await this.loadProjects();
          await this.openProject(result.id);
          await this.loadDirections();
          toast('灵感已存档，创作项目、差异化方向与内容计划已经串联');
        } catch (e) { handleError(e); }
        this.creating = false;
      },
      async openProject(pid, draftId = null, syncRoute = true) {
        try {
          this.activeId = pid;
          this.p = await api.get('/api/creation/projects/' + pid);
          const requestedDraft = draftId ? this.p.drafts.find((item) => item.id === draftId) : null;
          const cur = requestedDraft || this.p.drafts[this.p.drafts.length - 1];
          this.draft = cur ? cur.content : '';
          this.selectedDraftId = cur ? cur.id : null;
          this.currentPlatform = cur ? cur.platform : '抖音口播';
          try {
            this.directions = await api.get('/api/creation/projects/' + pid + '/directions');
            this.directionsError = null;
          } catch (directionError) {
            this.directions = [];
            this.directionsError = directionError;
          }
          if (this.p.direction_decision) {
            this.selDir = this.directions.findIndex((d) => d.run_id === this.p.direction_decision.run_id && String(d.candidate_index) === String(this.p.direction_decision.candidate_key));
          } else if (this.p.diff_strategy) this.selDir = this.directions.findIndex((d) => this.p.diff_strategy.includes(d.type));
          if (this.p.prediction) {
            this.predForm = { goal: this.p.prediction.goal, expected_bucket: this.p.prediction.expected_bucket,
              confidence: this.p.prediction.confidence, strengths: this.p.prediction.strengths,
              risks: this.p.prediction.risks, hypothesis: this.p.prediction.hypothesis,
              user_judgement: this.p.prediction.user_judgement || '' };
          }
          this.selectedIssueIds = [];
          this.qualityInstructions = '';
          if (syncRoute) this.syncCreationRoute(pid, this.selectedDraftId);
        } catch (e) { handleError(e); }
      },
      syncCreationRoute(projectId, draftId = null) {
        const params = new URLSearchParams(location.search);
        params.delete('idea');
        if (projectId) params.set('project', projectId); else params.delete('project');
        if (draftId) params.set('draft', draftId); else params.delete('draft');
        history.replaceState(null, '', '/?' + params.toString() + '#/creation');
      },
      async loadDirections() {
        if (!this.activeId || this.loadingDirections) return;
        this.loadingDirections = true; this.directionsError = null;
        try {
          this.directions = await api.post('/api/creation/projects/' + this.activeId + '/directions/generate');
          if (this.p.diff_strategy) this.selDir = this.directions.findIndex((d) => this.p.diff_strategy.includes(d.type));
        } catch (e) { this.directions = []; this.directionsError = e; }
        this.loadingDirections = false;
      },
      async chooseDir(i) {
        this.selDir = i;
        try {
          const item = this.directions[i];
          const r = await api.post('/api/creation/projects/' + this.activeId + '/direction', {
            direction: item.type + '：' + item.title,
            run_id: item.run_id, candidate_index: item.candidate_index,
            reason: '用户在创作工坊主动选择',
          });
          toast('已切换差异化方向 · 提纲已按新方向生成');
          await this.openProject(this.activeId);
        } catch (e) { handleError(e); }
      },
      async genDraft() {
        this.generating = true;
        try {
          const r = await api.post('/api/creation/projects/' + this.activeId + '/draft', {
            style_mode: this.styleMode, platform: this.currentPlatform,
          });
          this.draft = r.content;
          toast(`初稿已生成并自动保存为 v${r.version} · 锁定不是保存开关`);
          await this.openProject(this.activeId);
        } catch (e) { handleError(e); }
        this.generating = false;
      },
      async genMultiDrafts() {
        this.generatingMulti = true;
        try {
          const r = await api.post('/api/creation/projects/' + this.activeId + '/drafts/generate', {
            platforms: ['抖音口播', '小红书图文', 'B站视频简介', '视频号口播'],
            style_mode: this.styleMode,
          });
          toast(r.note || ('已生成并自动保存 ' + r.drafts.length + ' 个平台版本'));
          await this.openProject(this.activeId);
        } catch (e) { handleError(e); }
        this.generatingMulti = false;
      },
      async compareDrafts() {
        this.comparingDrafts = true;
        try {
          const r = await api.post('/api/creation/projects/' + this.activeId + '/drafts/compare', { platform: this.currentPlatform });
          toast(r.note);
          await this.openProject(this.activeId);
        } catch (e) { handleError(e); }
        this.comparingDrafts = false;
      },
      async saveDraft() {
        try {
          const r = await api.put('/api/creation/projects/' + this.activeId + '/draft', { content: this.draft, note: '手动修改', platform: this.currentPlatform });
          toast('已保存为 v' + r.version + '（稿件版本只增不改）');
          await this.openProject(this.activeId);
        } catch (e) { handleError(e); }
      },
      async generateAssessment() {
        const draft = this.selectedDraft;
        if (!draft) return toast('请先选择一个已保存的稿件版本', 'warn');
        this.assessmentGenerating = true;
        try {
          const r = await api.post('/api/creation/projects/' + this.activeId + '/assessments/generate', { draft_version_id: draft.id });
          toast(r.note || `v${draft.version_no} 体检完成`);
          await this.openProject(this.activeId);
          this.selectedDraftId = draft.id;
          const assessed = this.p.drafts.find((item) => item.id === draft.id);
          if (assessed) this.draft = assessed.content;
        } catch (e) { handleError(e); }
        this.assessmentGenerating = false;
      },
      async optimizeDraft() {
        const assessment = this.selectedAssessment;
        if (!assessment) return toast('请先对当前查看版本运行稿件体检', 'warn');
        if (!this.selectedIssueIds.length && !this.qualityInstructions.trim()) return toast('至少选择一条建议，或填写本轮补充要求', 'warn');
        this.optimizingDraft = true;
        try {
          const r = await api.post('/api/creation/projects/' + this.activeId + '/drafts/optimize', {
            assessment_id: assessment.id,
            selected_issue_ids: this.selectedIssueIds,
            instructions: this.qualityInstructions.trim(),
          });
          toast(r.note || `已生成优化稿 v${r.version_no}`);
          this.selectedIssueIds = [];
          this.qualityInstructions = '';
          await this.openProject(this.activeId, r.id);
        } catch (e) { handleError(e); }
        this.optimizingDraft = false;
      },
      async confirmCandidateDraft() {
        if (!this.latestDraft || !this.latestAssessment) return toast('最新稿必须先完成体检', 'warn');
        this.confirmingCandidate = true;
        try {
          const r = await api.post('/api/creation/projects/' + this.activeId + '/candidate-final', { draft_version_id: this.latestDraft.id });
          toast(r.note || `v${this.latestDraft.version_no} 已确认为候选终稿`);
          await this.openProject(this.activeId);
        } catch (e) { handleError(e); }
        this.confirmingCandidate = false;
      },
      viewVersion(d) {
        this.selectedDraftId = d.id;
        this.currentPlatform = d.platform;
        this.draft = d.content;
        this.selectedIssueIds = [];
        this.syncCreationRoute(this.activeId, d.id);
      },
      restoreRevisionInputs() {
        if (!this.latestRevision || !this.latestRevisionSource) return;
        this.viewVersion(this.latestRevisionSource);
        this.selectedIssueIds = [...(this.latestRevision.selected_issue_ids || [])];
        this.qualityInstructions = this.latestRevision.instructions || '';
        toast('已恢复上次体检问题和补充要求，可重新生成并验证落实证据');
      },
      async restoreVersion(d) {
        if (!confirm(`把 v${d.version_no} 恢复为一个新的当前版本？\n历史版本不会被覆盖。`)) return;
        this.restoring = true;
        try {
          const r = await api.put('/api/creation/projects/' + this.activeId + '/draft', {
            content: d.content, platform: d.platform, note: `恢复自 v${d.version_no}`,
          });
          toast(`已从 v${d.version_no} 恢复并另存为 v${r.version}`);
          await this.openProject(this.activeId);
        } catch (e) { handleError(e); }
        this.restoring = false;
      },
      async savePrediction() {
        try {
          await api.post('/api/creation/projects/' + this.activeId + '/prediction', this.predForm);
          toast('人工复核意见已保存（AI原始预测保留）');
          await this.openProject(this.activeId);
        } catch (e) { handleError(e); }
      },
      async lockPrediction() {
        try {
          if (!this.p.prediction) return toast('请先运行 AI 发布前预测', 'warn');
          await api.post('/api/creation/projects/' + this.activeId + '/prediction', this.predForm);
          const r = await api.post('/api/creation/projects/' + this.activeId + '/prediction/lock');
          toast(r.note);
          await this.openProject(this.activeId);
        } catch (e) { handleError(e); }
      },
      async finalizeProject() {
        try {
          const r = await api.post('/api/creation/projects/' + this.activeId + '/finalize');
          toast(r.note);
          await this.openProject(this.activeId);
        } catch (e) { handleError(e); }
      },
      async generatePrediction() {
        if (!this.p.drafts.length) return toast('请先生成或保存非空稿件', 'warn');
        if (!this.candidateReady) return toast('请先完成最新稿体检，并将其确认为候选终稿', 'warn');
        this.predictionGenerating = true;
        try {
          const r = await api.post('/api/creation/projects/' + this.activeId + '/prediction/generate');
          toast(r.note || `AI 预测 v${r.version_no} 已生成，等待你复核`);
          await this.openProject(this.activeId);
        } catch (e) { handleError(e); }
        this.predictionGenerating = false;
      },
      async generateCover() {
        if (!this.selectedDraftId) return toast('请先选择一个已保存的稿件版本', 'warn');
        this.coverGenerating = true;
        try {
          const r = await api.post('/api/visuals/covers', {
            creation_project_id: this.activeId, draft_version_id: this.selectedDraftId,
            ratio: '3:4', fields: {},
          });
          window.open(r.preview_url, '_blank', 'noopener');
          toast('封面预览已生成并保存，可在新窗口查看');
        } catch (e) { handleError(e); }
        this.coverGenerating = false;
      },
      openVisualFactory() {
        const q = new URLSearchParams({ project: this.activeId });
        location.href = '/?' + q.toString() + '#/visuals';
      },
      openChannelOperations() {
        const campaign = this.p && this.p.campaign_id;
        location.href = '/?' + (campaign ? ('campaign=' + encodeURIComponent(campaign)) : '') + '#/channels';
      },
      checkBadge(c) { return c.status === 'ok' ? 'b-ok' : c.status === 'warn' ? 'b-warn' : 'b-err'; },
      async addToPlan() {
        try {
          const r = await api.post('/api/planning/tasks', { title: this.p.title, platform: '抖音口播', project_id: this.activeId, estimate_h: 2 });
          if (!r.existing) {
            await api.patch('/api/planning/tasks/' + r.id, { status: 'SELECTED' });
            toast('已加入本周计划（候选 → 已选题）');
          } else if (r.status === 'CANDIDATE') {
            await api.patch('/api/planning/tasks/' + r.id, { status: 'SELECTED' });
            toast('该项目已在计划中，已恢复为“已选题”，没有重复创建');
          } else {
            toast(r.note || '该项目已经在内容计划中，没有重复创建');
          }
          go('plan');
        } catch (e) { handleError(e); }
      },
      stepState() {
        if (!this.p) return 0;
        if (this.p.finalization && this.p.finalization.ready) return 8;
        if (this.p.prediction && this.predictionCurrent) return 7;
        if (this.candidateReady) return 6;
        if (this.latestAssessment) return 5;
        if (this.p.drafts.length) return 4;
        if (this.p.outline.length) return 3;
        if (this.p.diff_strategy) return 2;
        return 1;
      },
    },
    computed: {
      locked() { return this.p && this.p.prediction && this.p.prediction.locked === 1; },
      predictionCurrent() { return !!(this.p && this.p.finalization && this.p.finalization.prediction_matches_latest); },
      readyToPublish() { return !!(this.p && this.p.finalization && this.p.finalization.ready); },
      steps() {
        return ['选择来源', '差异化方向', '生成提纲', '生成初稿', '稿件体检', '迭代优化', '正式预测', '确认定稿'];
      },
      curStep() { return this.stepState(); },
      selectedDraft() { return this.p && this.p.drafts.find((d) => d.id === this.selectedDraftId); },
      latestDraft() { return this.p && this.p.drafts.length ? this.p.drafts[this.p.drafts.length - 1] : null; },
      selectedAssessment() {
        if (!this.p || !this.selectedDraftId) return null;
        const items = (this.p.assessments || []).filter((item) => item.draft_version_id === this.selectedDraftId);
        return items.length ? items[0] : null;
      },
      latestAssessment() {
        if (!this.p || !this.latestDraft) return null;
        const items = (this.p.assessments || []).filter((item) => item.draft_version_id === this.latestDraft.id);
        return items.length ? items[0] : null;
      },
      candidateReady() { return !!(this.p && this.p.quality_gate && this.p.quality_gate.ready_for_prediction); },
      latestRevision() {
        if (!this.p || !this.p.revision_runs || !this.p.revision_runs.length) return null;
        return this.p.revision_runs[0];
      },
      latestRevisionSource() {
        if (!this.latestRevision || !this.p) return null;
        return this.p.drafts.find((item) => item.id === this.latestRevision.source_draft_version_id) || null;
      },
      latestInstructionChange() {
        if (!this.latestRevision) return null;
        return (this.latestRevision.change_log || []).find((item) => item && typeof item === 'object' && item.issue_id === 'user_instruction') || null;
      },
    },
    template: `
    <section>
      <div class="page-head">
        <div><div class="page-title">创作工坊</div><div class="page-q">这件事如何用我的身份、观点和语言讲出来？</div></div>
        <div class="creation-head-actions">
          <select class="input" v-if="projects.length" style="width:220px;display:inline-block;margin-right:8px" :value="activeId" @change="openProject($event.target.value)">
            <option v-for="pr in projects" :key="pr.id" :value="pr.id">{{ pr.title }}（{{ pr.id }}）</option>
          </select>
          <button class="btn" @click="openIdeaComposer">＋ 记录灵感</button>
          <button class="btn btn-pri" v-if="p" @click="addToPlan">加入本周计划</button>
        </div>
      </div>

      <div class="card section-gap" v-if="p"><div class="card-body" style="padding:12px 16px 8px">
        <div class="steps">
          <div class="step" v-for="(s,i) in steps" :key="s" :class="{done: i<curStep, cur: i===curStep}"><i>{{ i<curStep?'✓':i+1 }}</i>{{ s }}</div>
        </div>
      </div></div>

      <template v-if="p">
        <div class="ctx-bar"><b>Context Resolver</b> 本次任务显式加载 {{ (p.context.refs||[]).length }} 个引用 · 约 {{ p.context.tokens }} tokens · {{ p.context.isolation }} · Run {{ p.context.context_run_id }}</div>

        <div class="callout c-info"><span>自动保存</span><span><b>每次生成都会立即新增一个不可变版本。</b>“锁定判断”只锁定发布前预测，不控制稿件保存；手动编辑后请点“保存为新版本”。</span></div>

        <div class="card section-gap">
          <div class="card-head"><div class="card-title">差异化方向 <span class="card-sub">按需调用模型，不会打开页面就自动消耗或报错</span></div><button class="btn btn-sm" :disabled="loadingDirections" @click="loadDirections">{{ loadingDirections ? '生成中…' : (directions.length ? '重新生成方向' : '生成方向建议') }}</button></div>
          <div class="card-body"><div class="grid g3">
            <div class="inline-error" role="alert" v-if="directionsError" style="grid-column:1/-1"><b>{{ directionsError.code }}</b><span>差异化方向暂时不可用，但项目和已保存稿件仍可查看。</span><button class="btn btn-sm" @click="loadDirections">重试</button></div>
            <div class="empty" v-else-if="!directions.length" style="grid-column:1/-1"><b>{{ p.diff_strategy ? '当前已采用：' + p.diff_strategy : '尚未生成方向建议' }}</b><span>需要新方向时再主动生成；查看、编辑和恢复历史稿件不依赖这一步。</span></div>
            <div class="diff-card" v-for="(d,i) in directions" :key="d.run_id+'-'+d.candidate_index" :class="{sel: selDir===i}" @click="chooseDir(i)">
              <span class="badge" :class="d.cls">{{ d.type }}<template v-if="selDir===i"> ✓ 已选</template></span>
              <span class="badge b-neu" v-if="d.run_version">方向批次 v{{ d.run_version }}</span>
              <div style="font-weight:600;margin:7px 0 4px">{{ d.title }}</div>
              <div class="mini-note">{{ d.note }}</div>
            </div>
          </div></div>
        </div>

        <div class="grid g31 section-gap creation-quality-layout">
          <div class="creation-main-column">
          <div class="card">
            <div class="card-head"><div class="card-title">稿件工作区 · 《{{ p.title }}》
              <span class="badge b-neu" v-if="p.drafts.length">草稿 v{{ p.drafts[p.drafts.length-1].version_no }}</span></div>
              <div class="src-chain"><span class="node" v-for="n in p.source_chain" :key="n" @click="go('analysis')">{{ n }}</span></div></div>
            <div class="card-body">
              <div class="grid g2" style="margin-bottom:12px">
                <div><div class="ana-label">本条目标</div><div style="font-size:12.5px">{{ p.goal }}</div></div>
                <div><div class="ana-label">IP 匹配</div><div style="font-size:12.5px">{{ p.audience }}</div></div>
              </div>
              <div class="ana-label">提纲</div>
              <div class="iv-flow" style="margin-bottom:10px"><template v-for="(o,i) in p.outline" :key="i"><span>{{ o }}</span><em v-if="i<p.outline.length-1">→</em></template></div>
              <div class="ana-label">正文（当前查看：{{ currentPlatform }}<template v-if="selectedDraft"> · v{{ selectedDraft.version_no }}</template>）</div>
              <textarea class="draft-area" v-model="draft"></textarea>
              <div style="display:flex;gap:8px;margin-top:10px;flex-wrap:wrap">
                <select class="input" style="width:168px" v-model="styleMode" title="口吻只作为可控适配层，不覆盖内容结构"><option value="structure">结构优先（推荐）</option><option value="light">轻量本人风格</option><option value="deep">深度口吻模仿</option></select>
                <button class="btn btn-pri" :disabled="generating" @click="genDraft">{{ generating ? '生成中…' : (p.drafts.length ? '重新生成初稿' : '生成初稿') }}</button>
                <button class="btn" :disabled="comparingDrafts" @click="compareDrafts">{{ comparingDrafts ? '正在生成 A/B…' : '生成结构版 / 轻口吻版 A/B' }}</button>
                <button class="btn" :disabled="generatingMulti" @click="genMultiDrafts">{{ generatingMulti ? '生成多平台版本中…' : '生成抖音 / 小红书 / B站 / 视频号版本' }}</button>
                <button class="btn" @click="saveDraft">保存为新版本</button>
              </div>
              <div class="mini-note" style="margin-top:6px">生成结果自动保存；文本框中的手动修改需要另存新版本。引用与事实来源以 Context Run {{ p.context.context_run_id }} 为准。</div>
            </div>
          </div>

          <div class="card quality-gate-card" v-if="selectedDraft">
            <div class="card-head">
              <div class="card-title">AI 稿件体检 <span class="badge b-neu">v{{ selectedDraft.version_no }}</span><span class="card-sub">可反复运行 · 不产生正式预测</span></div>
              <button class="btn btn-sm" :disabled="assessmentGenerating" @click="generateAssessment">{{ assessmentGenerating ? '体检中…' : (selectedAssessment ? '重新体检本版' : '开始体检') }}</button>
            </div>
            <div class="card-body quality-gate-body">
              <div class="callout c-info quality-boundary"><span>边界</span><span>体检用于找问题和迭代稿件；只有你确认的<b>候选终稿</b>才会进入正式盲预测。体检不会提前写入表现档位，也不会覆盖旧稿。</span></div>
              <template v-if="selectedAssessment">
                <div class="quality-score-row">
                  <div class="quality-score"><strong>{{ Math.round(selectedAssessment.composite_score) }}</strong><span>/ 100</span></div>
                  <div><b>本版诊断摘要</b><p>{{ selectedAssessment.summary || '已完成可解释评分，请结合下方证据选择本轮要修改的问题。' }}</p></div>
                </div>
                <div class="quality-dimensions">
                  <div class="quality-dimension" v-for="(dim,i) in selectedAssessment.dimensions" :key="dim.key||i">
                    <div><span>{{ dim.label || dim.name || ('维度 '+(i+1)) }}</span><b>{{ Math.round(Number(dim.score)||0) }}</b></div>
                    <div class="quality-bar"><i :style="{width:Math.max(0,Math.min(100,Number(dim.score)||0))+'%'}"></i></div>
                    <small>{{ dim.reason || dim.evidence }}</small>
                  </div>
                </div>
                <div class="ana-label quality-section-label">选择本轮要修的问题</div>
                <label class="quality-issue" v-for="issue in selectedAssessment.issues" :key="issue.id" :class="'severity-'+issue.severity">
                  <input type="checkbox" :value="issue.id" v-model="selectedIssueIds">
                  <span class="quality-issue-content">
                    <span class="quality-issue-head"><b>{{ issue.problem }}</b><em>{{ ({high:'高优先',medium:'中优先',low:'低优先'})[issue.severity] || '建议' }}</em></span>
                    <small v-if="issue.quote">证据：“{{ issue.quote }}”</small>
                    <span>建议：{{ issue.action }}</span>
                    <small v-if="issue.side_effect">取舍：{{ issue.side_effect }}</small>
                  </span>
                </label>
                <div class="empty compact" v-if="!selectedAssessment.issues.length"><b>没有发现必须修改的问题</b><span>你仍可补充自己的要求，生成一个新版本进行比较。</span></div>
                <div class="field quality-instructions"><label>本轮补充要求（可选）</label><textarea class="input" rows="2" v-model="qualityInstructions" placeholder="例如：保留当前案例，只把开头缩短到两句话；不要增加未经证实的数据"></textarea></div>
                <button class="btn btn-pri" style="width:100%" :disabled="optimizingDraft" @click="optimizeDraft">{{ optimizingDraft ? '正在生成优化版本…' : '应用所选建议，生成一个新版本' }}</button>
                <div class="mini-note" style="margin-top:7px">只处理你勾选的问题；未勾选项不会被静默改写。新结果会另存版本，并让旧候选资格失效。</div>
              </template>
              <div class="empty compact" v-else><b>当前版本还没有体检记录</b><span>体检会保存评分、证据与建议，之后可回看；不会修改正文。</span></div>

              <div class="revision-summary" v-if="latestRevision && latestDraft && latestRevision.target_draft_version_id===latestDraft.id">
                <div><b>最近一次优化：v{{ latestRevisionSource ? latestRevisionSource.version_no : '?' }} → v{{ latestDraft.version_no }}</b><span>修改幅度 {{ Math.round((1-Number((latestRevision.diff||{}).similarity||1))*100) }}% · {{ (latestRevision.change_log||[]).length }} 条变更说明</span></div>
                <div class="revision-instruction" v-if="latestRevision.instructions">
                  <b>你的补充要求</b><span>{{ latestRevision.instructions }}</span>
                  <small v-if="latestInstructionChange" class="revision-evidence">✓ 已落实证据：{{ latestInstructionChange.evidence || latestInstructionChange.change }}</small>
                  <small v-else class="revision-missing">旧记录没有返回可验证的落实证据，不能据此认定已完成。</small>
                  <button class="btn btn-sm" v-if="!latestInstructionChange" @click="restoreRevisionInputs">带回原稿与要求重新优化</button>
                </div>
                <details><summary>查看变更说明</summary><ul><li v-for="(change,i) in latestRevision.change_log" :key="i">{{ typeof change==='string' ? change : (change.summary||change.change||JSON.stringify(change)) }}</li></ul></details>
                <details v-if="latestRevision.diff && latestRevision.diff.preview && latestRevision.diff.preview.length"><summary>逐行查看正文差异</summary><pre class="revision-diff"><span v-for="(line,i) in latestRevision.diff.preview" :key="i" :class="line.startsWith('+')?'added':(line.startsWith('-')?'removed':'')">{{ line }}</span></pre></details>
              </div>

              <div class="candidate-confirm" v-if="latestDraft && selectedDraft.id===latestDraft.id">
                <template v-if="candidateReady">
                  <div class="callout c-ok"><span>候选终稿</span><span><b>v{{ latestDraft.version_no }} 已确认。</b>现在可以对这个固定版本运行正式发布前预测。</span></div>
                </template>
                <template v-else-if="latestAssessment">
                  <button class="btn" style="width:100%" :disabled="confirmingCandidate" @click="confirmCandidateDraft">{{ confirmingCandidate ? '确认中…' : '确认 v'+latestDraft.version_no+' 为候选终稿' }}</button>
                  <div class="mini-note" style="margin-top:7px">确认只是冻结正式预测的输入版本；如果之后再改稿，系统会自动撤销候选资格并回到体检阶段。</div>
                </template>
              </div>
            </div>
          </div>
          </div>

          <div>
            <div class="card section-gap" v-if="p.checks && p.checks.style_match">
              <div class="card-head"><div class="card-title">检查</div></div>
              <div class="card-body" style="padding-top:4px">
                <template v-for="(c,k) in p.checks" :key="k">
                  <div class="kv-row" v-if="c && c.label">
                    <span>{{ c.label }}</span><span class="badge" :class="checkBadge(c)">{{ c.value }}</span>
                  </div>
                </template>
                <div class="mini-note" style="margin-top:8px">{{ p.checks.note }}</div>
              </div>
            </div>
            <div class="card section-gap draft-history-card">
              <div class="card-head"><div class="card-title">版本历史 <span class="badge b-neu">{{ p.drafts.length }} 版</span><span class="card-sub">生成即保存 · 旧版不覆盖</span></div></div>
              <div class="card-body draft-history-list">
                <div class="draft-history-row" :class="{active:selectedDraftId===d.id}" v-for="d in [...p.drafts].reverse()" :key="d.id">
                  <button type="button" class="draft-history-main" @click="viewVersion(d)"><b class="mono">v{{ d.version_no }} · {{ d.platform }}</b><span>{{ d.note }}</span><small>{{ d.created_at.slice(0,16).replace('T',' ') }} · {{ d.content.length }} 字 · {{ ({structure:'结构优先',light:'轻量口吻',deep:'深度口吻',manual:'人工修改'})[d.style_mode] || '历史模式' }}</small></button>
                  <button class="btn btn-sm btn-ghost" :disabled="restoring" @click="restoreVersion(d)">恢复为新版本</button>
                </div>
                <div class="empty" v-if="!p.drafts.length">尚无稿件版本</div>
              </div>
            </div>
            <div class="lock-panel" :class="{locked:readyToPublish}">
              <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:9px">
                <b style="font-size:13px">AI 发布前预测 <span class="card-sub">相对表现建议，不承诺结果</span></b>
                <span class="badge" :class="readyToPublish?'b-ok':(locked?'b-warn':'b-neu')">{{ readyToPublish ? '已定稿 · 待发布' : (locked ? '旧预测已锁定' : '尚未确认定稿') }}</span>
              </div>
              <template v-if="!locked">
                <div class="callout c-warn" v-if="!candidateReady"><span>尚未开放</span><span>先对最新稿运行体检并确认候选终稿。正式预测只评估一个确定版本，不参与改稿。</span></div>
                <button class="btn btn-pri" style="width:100%;margin-bottom:10px" :disabled="predictionGenerating || !candidateReady" @click="generatePrediction">{{ predictionGenerating ? 'AI 正在盲预测…' : (p.prediction ? '按候选终稿生成新预测版本' : '运行正式发布前预测') }}</button>
                <template v-if="p.prediction">
                  <div class="kv-row"><span>预计相对表现</span><b>{{ p.prediction.expected_bucket }}</b></div>
                  <div class="kv-row"><span>校准置信度</span><b class="mono">{{ p.prediction.confidence }}</b></div>
                  <div class="kv-row"><span>主要优势</span><span>{{ p.prediction.strengths }}</span></div>
                  <div class="kv-row"><span>主要风险</span><span>{{ p.prediction.risks }}</span></div>
                  <div class="field" v-for="d in (p.prediction.details&&p.prediction.details.dimensions)||[]" :key="d.key">
                    <label>{{ d.label }} · {{ d.score }}/100</label><div class="mini-note">{{ d.reason }}<br>依据：{{ d.evidence }}</div>
                  </div>
                  <div class="field"><label>我的复核意见 / 修正理由（可选）</label><textarea class="input" rows="2" v-model="predForm.user_judgement" placeholder="AI给预测，人在锁定前负责复核；可说明同意或修正原因"></textarea></div>
                  <button class="btn btn-pri" style="width:100%" @click="lockPrediction">确认当前稿为最终版并锁定预测 → 进入待发布</button>
                </template>
                <div class="mini-note" v-else>AI 会读取已确认的候选终稿、IP档案和已有复盘样本。没有足够历史样本时会明确降低置信度。</div>
              </template>
              <template v-else>
                <div class="kv-row"><span>预计相对表现</span><b>{{ p.prediction.expected_bucket }}</b></div>
                <div class="kv-row"><span>主要优势</span><span>{{ p.prediction.strengths }}</span></div>
                <div class="kv-row"><span>主要风险</span><span>{{ p.prediction.risks }}</span></div>
                <div class="kv-row"><span>验证假设</span><span>{{ p.prediction.hypothesis }}</span></div>
                <div class="kv-row"><span>系统置信度</span><b class="mono">{{ p.prediction.confidence }}</b></div>
                <div class="kv-row"><span>我的判断</span><span>{{ p.prediction.user_judgement }}</span></div>
                <div class="callout c-warn" v-if="!predictionCurrent"><span>稿件已变化</span><span><b>当前锁定预测对应旧稿，不能把它当成现稿判断。</b>请为最新稿重新运行预测并确认，内容计划会继续保持“写作中”。</span></div>
                <button class="btn btn-pri" v-if="!predictionCurrent" style="width:100%;margin-top:10px" :disabled="predictionGenerating || !candidateReady" @click="generatePrediction">{{ predictionGenerating ? 'AI 正在盲预测最新候选稿…' : (candidateReady ? '为当前候选终稿重新运行预测' : '先重新体检并确认候选终稿') }}</button>
                <button class="btn btn-pri" v-else-if="!readyToPublish" style="width:100%;margin-top:10px" @click="finalizeProject">确认定稿并同步到内容计划</button>
                <div class="callout c-ok" v-else><span>已串联</span><span><b>当前稿件已确认定稿，内容计划已进入“待发布”。</b>发布后请登记真实链接，系统会建立 T+1 / T+3 / T+7 快照。</span></div>
              <button class="btn" v-if="readyToPublish" style="width:100%;margin-top:10px" @click="go('plan')">去内容计划登记发布 →</button>
              <button class="btn" v-if="readyToPublish" style="width:100%;margin-top:8px" @click="openChannelOperations">派生朋友圈 / 公众号 →</button>
                <div class="mini-note" style="text-align:center;margin-top:8px">预测记录只读 · 稿件变化后必须新增预测版本 · 发布后仅追加复盘</div>
              </template>
            </div>
            <div class="card section-gap creation-visual-launch">
              <div class="card-head"><div class="card-title">视觉生产 <span class="card-sub">封面 / 图文卡片 / 长图</span></div></div>
              <div class="card-body"><div class="mini-note" style="margin-bottom:10px">稿件在这里确定内容；图片模型、人物资产、模板、多比例和生成历史统一进入独立视觉工厂管理。</div><button class="btn btn-pri" style="width:100%" @click="openVisualFactory">带当前稿件进入视觉工厂 →</button></div>
            </div>
          </div>
        </div>
      </template>
      <div class="card" v-else-if="loading"><div class="loading">正在读取创作项目…</div></div>
      <div class="card" v-else-if="loadError"><div class="empty"><b>创作项目加载失败</b><span>{{ loadError.message }}</span><button class="btn" @click="loadProjects">重新加载</button></div></div>
      <div class="card" v-else><div class="empty creation-empty"><b>还没有创作项目</b><span>先记录一个真实灵感，或从“内容拆解”携带完整证据链进入。</span><div class="creation-empty-form"><button class="btn btn-pri" @click="openIdeaComposer">记录灵感并开始创作</button><button class="btn" @click="go('analysis')">从拆解结果创建</button></div></div></div>

      <div class="modal idea-composer-modal" :class="{open:ideaComposerOpen}" @click.self="closeIdeaComposer"><div class="modal-card"><div class="modal-head"><div><div class="modal-title">记录真实灵感</div><div class="card-sub">可只存档，也可立刻建立项目并生成差异化方向。AI 只能引用，不会覆盖你的原文。</div></div><button class="modal-x" @click="closeIdeaComposer">×</button></div><div class="modal-body idea-composer-body">
        <div class="grid g2"><label>记录类型<select class="input" v-model="ideaForm.item_type"><option value="self_idea">灵感与观点</option><option value="life_note">生活感悟</option><option value="work_process">工作过程</option><option value="hot_opinion">热点判断</option><option value="feedback">用户反馈</option><option value="trust_evidence">可信证据</option></select></label><label>主题（可选）<input class="input" v-model="ideaForm.title" placeholder="不填则使用正文第一行"></label></div>
        <label>原始记录<textarea v-model="ideaForm.body" placeholder="写下当时真正想到、看到或经历的内容。这里保存的是你的原话。"></textarea></label>
        <label>我为什么在意 / 我的判断（可选）<textarea v-model="ideaForm.personal_judgement" placeholder="例如：我不同意常见说法的原因；这件事最让我有感触的点"></textarea></label>
        <div class="grid g2"><label>想讲给谁（可选）<input class="input" v-model="ideaForm.audience" placeholder="例如：刚开始做个人IP的AI从业者"></label><label>来源链接（可选）<input class="input" v-model="ideaForm.source_url" placeholder="文章、视频或公开资料链接"></label></div>
        <div class="grid g2"><label>标签（逗号分隔）<input class="input" v-model="ideaForm.tags_text" placeholder="工作复盘，知识管理"></label><label class="idea-file-field"><span>真实附件（可选）</span><span class="idea-file-picker"><input class="file-input-hidden" type="file" accept="image/*,audio/*,video/mp4" @change="pickIdeaFile"><span class="idea-file-button">选择真实素材</span><span class="idea-file-name">{{ ideaFile ? ideaFile.name : '未选择文件' }}</span></span><small>支持照片、截图、语音或短视频，最大 20MB</small></label></div>
      </div><div class="modal-foot idea-composer-actions"><button class="btn" :disabled="creating" @click="saveIdeaOnly">{{ creating?'保存中…':'仅存入灵感箱' }}</button><button class="btn btn-pri" :disabled="creating" @click="createProject">{{ creating?'正在串联…':'建立项目并生成方向' }}</button></div></div></div>
    </section>`,
  };

  /* ================= 内容计划 ================= */
  window.PagePlan = {
    data() { return { columns: [], done: [], week: null, dragId: null, showDone: false, startingId: null }; },
    async mounted() { await this.load(); bus.on('board-changed', this.load); },
    methods: {
      go,
      async load() {
        try {
          const [b, w] = await Promise.all([api.get('/api/planning/board'), api.get('/api/planning/week')]);
          this.columns = b.columns; this.done = b.done; this.week = w;
        } catch (e) { handleError(e); }
      },
      onDragStart(card) {
        if (card.delivery_kind && card.delivery_kind !== 'public_content') {
          this.dragId = null;
          return toast('渠道任务由生成、审核、排期和发布事件自动推进', 'warn');
        }
        this.dragId = card.id;
      },
      onDrop(col) {
        if (!this.dragId) return;
        const card = this.columns.flatMap((c) => c.cards).find((c) => c.id === this.dragId);
        this.dragId = null;
        if (!card || card.status === col.state) return;
        this.moveTask(card, col.state);
      },
      async moveTask(card, status) {
        try {
          await api.patch('/api/planning/tasks/' + card.id, { status });
          toast('状态已更新（明确事件驱动）');
          await this.load();
        } catch (e) {
          handleError(e);
          await this.load();
        }
      },
      async publish(card) {
        if (card.delivery_kind && card.delivery_kind !== 'public_content') return this.openChannelTask(card, true);
        bus.emit('open-publish', card);
      },
      openChannelTask(card, performance = false) {
        const params = new URLSearchParams();
        if (card.campaign_id) params.set('campaign', card.campaign_id);
        const official = card.delivery_kind === 'official_article';
        params.set('channel_tab', official ? 'official' : 'moments');
        if (official && card.channel_target_id) params.set('article', card.channel_target_id);
        else if (card.channel_target_id) params.set('moment', card.channel_target_id);
        else if (card.deliverable_id) params.set('deliverable', card.deliverable_id);
        if (performance && card.deliverable_id) params.set('performance', card.deliverable_id);
        location.href = '/?' + params.toString() + '#/channels';
      },
      openVisual(card) {
        if (!card.project_id) return this.startCreationById(card.id);
        location.href = '/?project=' + encodeURIComponent(card.project_id) + '#/visuals';
      },
      openExternalPublication() { bus.emit('open-publish-import'); },
      openRetro(publicationId) {
        const query = publicationId ? ('?publication=' + encodeURIComponent(publicationId)) : '';
        location.href = '/' + query + '#/retro';
      },
      handleCalendarEvent(ev) {
        if (ev.delivery_kind && ev.delivery_kind !== 'public_content') {
          this.openChannelTask(ev, ['PUBLISHED','WAITING_RETRO','RETRO_COMPLETED','ARCHIVED'].includes(ev.status));
          return;
        }
        if (['PUBLISHED','WAITING_RETRO','RETRO_COMPLETED','ARCHIVED'].includes(ev.status)) {
          this.openRetro(ev.publication_id);
          return;
        }
        this.startCreationById(ev.task_id);
      },
      async startCreationById(taskId) {
        const channelCard = this.columns.flatMap((col) => col.cards).find((card) => card.id === taskId);
        if (channelCard && channelCard.delivery_kind && channelCard.delivery_kind !== 'public_content') {
          this.openChannelTask(channelCard);
          return;
        }
        if (this.startingId) return;
        this.startingId = taskId;
        try {
          const result = await api.post('/api/planning/tasks/' + encodeURIComponent(taskId) + '/start-creation');
          toast(result.note);
          location.href = '/?project=' + encodeURIComponent(result.project_id) + '#/creation';
        } catch (e) { handleError(e); }
        this.startingId = null;
      },
    },
    template: `
    <section>
      <div class="page-head">
        <div><div class="page-title">内容计划</div><div class="page-q">这周具体做哪些内容，什么时候做？</div></div>
        <div style="display:flex;gap:8px;flex-wrap:wrap">
          <button class="btn" @click="load();toast('建议由规则引擎计算（非 LLM）：产能 - 已排时长 = 建议数量')">重新计算建议</button>
          <button class="btn btn-pri" @click="openExternalPublication">补录工作台外已发布内容</button>
        </div>
      </div>

      <div class="card section-gap" v-if="week"><div class="card-body">
        <div style="display:flex;gap:26px;flex-wrap:wrap;align-items:center">
          <div style="flex:1;min-width:240px">
            <div style="font-size:11px;color:var(--ink3);margin-bottom:4px">本周产能 · {{ week.hours_total }}h 可投入</div>
            <div style="display:flex;align-items:center;gap:10px">
              <div class="progress" style="flex:1"><i :style="{width:(week.hours_scheduled/week.hours_total*100)+'%'}"></i></div>
              <b>已排 {{ week.hours_scheduled }}h · 剩余 {{ week.hours_left }}h</b></div>
          </div>
          <div style="font-size:12px;color:var(--ink2)">当前周期目标 <b style="color:var(--ink)">{{ week.weekly_target }} 条</b> · 还可安排 <b style="color:var(--ink)">{{ week.suggested_count }} 条</b>（近 8 周 {{ week.completion_samples }} 个样本，完成率 {{ Math.round(week.completion_rate*100) }}%） · 已安排 <b style="color:var(--ink)">{{ week.scheduled_count }} 条</b></div>
        </div>
        <div class="mini-note" style="margin-top:9px">{{ week.note }} · 平台比例：{{ week.ratio }}</div>
      </div></div>

      <div class="callout c-info"><span>从计划到创作</span><span>动态运营顾问确认后的选题会进入“已选题”。点击卡片中的“开始创作”或日历事件，即可建立真实创作项目；再次点击会继续原项目，不会重复创建。</span></div>
      <div class="callout c-info"><span>随手发布也能复盘</span><span>如果内容没有经过本计划和创作工坊，点击右上角“补录工作台外已发布内容”。系统会保存作品与真实快照，但不会事后伪造发布前预测。</span></div>

      <div class="callout c-warn" v-if="week && week.backlog > 3"><span>⚠</span><span><b>库存积压 {{ week.backlog }} 条：</b>建议优先完成已有内容再开新选题。若热点插队，将提示会挤占什么任务——确认后才会调整，系统不会静默改排期。</span></div>

      <div class="card section-gap">
        <div class="card-head"><div class="card-title">内容库存看板 <span class="card-sub">拖动卡片调整状态 · 非法转换会被状态机拒绝</span></div>
          <button class="btn btn-sm btn-ghost" @click="showDone=!showDone">{{ showDone?'隐藏':'已完成/归档' }}（{{ done.length }}）</button></div>
        <div class="card-body"><div class="board">
          <div class="bcol" v-for="col in columns" :key="col.state"
               @dragover.prevent="$event.currentTarget.classList.add('dragover')"
               @dragleave="$event.currentTarget.classList.remove('dragover')"
               @drop.prevent="onDrop(col);$event.currentTarget.classList.remove('dragover')">
            <div class="bcol-h">{{ col.label }} <span>{{ col.count }}</span></div>
            <div class="kcard" v-for="card in col.cards" :key="card.id" draggable="true" @dragstart="onDragStart(card)">
              <div class="kt">{{ card.title }}</div>
              <div class="km">
                <span class="badge" v-for="bd in card.badges" :key="bd.t" :class="bd.c">{{ bd.t }}</span>
                <button class="btn btn-sm" v-if="card.project_id && ['PRODUCING','READY_TO_PUBLISH'].includes(card.status) && card.visual_status!=='READY'" @click.stop="openVisual(card)">制作视觉</button>
                <button class="btn btn-sm" v-if="card.project_id && card.visual_status==='READY'" @click.stop="openVisual(card)">查看视觉</button>
                <span v-if="card.platform">{{ card.platform }}</span>
                <span v-if="card.estimate_h">预计 {{ card.estimate_h }}h</span>
                <button class="btn btn-sm btn-pri" v-if="['CANDIDATE','SELECTED','OUTLINING','DRAFTING','PRODUCING'].includes(card.status)" :disabled="startingId===card.id" @click.stop="startCreationById(card.id)">{{ startingId===card.id ? '正在连接…' : card.creation_action }}</button>
                <button class="btn btn-sm btn-pri" v-if="card.status==='READY_TO_PUBLISH'" @click="publish(card)">发布登记</button>
                <button class="btn btn-sm" v-if="['PUBLISHED','WAITING_RETRO'].includes(card.status)" @click="card.delivery_kind&&card.delivery_kind!=='public_content' ? openChannelTask(card,true) : openRetro(card.publication_id)">{{ card.delivery_kind&&card.delivery_kind!=='public_content' ? '渠道数据 / 复盘' : (card.status==='PUBLISHED' ? '录入快照 / 复盘' : '继续复盘') }}</button>
              </div>
            </div>
            <div class="empty" v-if="!col.count" style="padding:12px">—</div>
          </div>
        </div>
        <div v-if="showDone" style="margin-top:10px">
          <div class="ana-label">已完成 / 已归档</div>
          <div class="chips"><button type="button" class="chip" v-for="d in done" :key="d.id" @click="openRetro(d.publication_id)">{{ d.title }} · 查看复盘</button>
            <span class="mini-note" v-if="!done.length">（暂无）</span></div>
        </div>
        </div>
      </div>

      <div class="card" v-if="week">
        <div class="card-head"><div class="card-title">本周日历 <span class="card-sub">发布登记为手动：填写链接后创建 T+N 快照计划</span></div></div>
        <div class="card-body"><div class="cal">
          <div class="cal-day" v-for="d in week.days" :key="d.date" :class="{today: d.today}">
            <h6>{{ d.name }} {{ d.date.slice(5) }}{{ d.today ? ' · 今天' : '' }}</h6>
            <button type="button" class="cal-ev plan-calendar-event" v-for="(ev,i) in d.events" :key="i" :class="'c-'+ev.c" :disabled="startingId===ev.task_id" @click="handleCalendarEvent(ev)"><span>{{ ev.t }}</span><small>{{ startingId===ev.task_id ? '正在连接…' : ev.action+' →' }}</small></button>
          </div>
        </div></div>
      </div>
    </section>`,
  };
})();
