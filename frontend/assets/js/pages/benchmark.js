/* 页面：对标监控（账号卡片墙 → 监控墙 → 作者分析）
   完整闭环：粘贴链接 → 解析 → 拉资料+作品 → 爆款判定 → 信号 → 雷达 */
(function () {
  const go = (p) => { window.__app && window.__app.go(p); };

  window.PageBenchmark = {
    data() {
      return {
        view: 'list',           // list | detail
        list: [], platFilter: 'all',
        plats: [['all', '全部'], ['douyin', '抖音'], ['xhs', '小红书'], ['channels', '视频号'], ['bilibili', 'B站']],
        showAdd: false,
        addForm: { platform: 'douyin', link: '', scan_range: '30', note: '' },
        adding: false, scanningId: null,
        detail: null, analyzing: false,
      };
    },
    async mounted() { await this.load(); },
    computed: {
      filtered() {
        return this.platFilter === 'all' ? this.list
          : this.list.filter((b) => b.platform === this.platFilter);
      },
      platN() {
        return (p) => this.list.filter((b) => p === 'all' || b.platform === p).length;
      },
    },
    methods: {
      go,
      async load() {
        try { this.list = await api.get('/api/benchmarks'); }
        catch (e) { handleError(e); }
      },
      async openDetail(b) {
        try {
          this.detail = await api.get('/api/benchmarks/' + b.id);
          this.view = 'detail';
          await api.post('/api/benchmarks/' + b.id + '/read');
        } catch (e) { handleError(e); }
      },
      async backToList() { this.view = 'list'; this.detail = null; await this.load(); },
      async doAdd() {
        if (!this.addForm.link.trim()) { toast('先粘贴对标账号主页链接 / ID', 'warn'); return; }
        this.adding = true;
        try {
          const r = await api.post('/api/benchmarks', this.addForm);
          toast(r.note);
          this.showAdd = false;
          this.addForm = { platform: 'douyin', link: '', scan_range: '30', note: '' };
          await this.load();
        } catch (e) { handleError(e); }
        this.adding = false;
      },
      async scan(b) {
        this.scanningId = b.id;
        try {
          const r = await api.post('/api/benchmarks/' + b.id + '/scan');
          toast(r.note);
          if (this.view === 'detail') await this.openDetail(b); else await this.load();
        } catch (e) { handleError(e); }
        this.scanningId = null;
      },
      async scanAll() {
        try {
          const r = await api.post('/api/benchmarks/scan-all');
          toast(r.note);
          setTimeout(() => this.load(), 4000);
        } catch (e) { handleError(e); }
      },
      async del(b) {
        if (!confirm(`删除对标账号「${b.name || b.uid}」？其作品缓存将一并删除（已生成的信号与资产保留）。`)) return;
        try { const r = await api.delete('/api/benchmarks/' + b.id); toast(r.note, 'warn'); await this.backToList(); }
        catch (e) { handleError(e); }
      },
      async analyze() {
        if (!this.detail) return;
        this.analyzing = true;
        try {
          const r = await api.post('/api/benchmarks/' + this.detail.id + '/analyze');
          toast('作者分析完成 · 引擎：' + r.engine);
          await this.openDetail(this.detail);
        } catch (e) { handleError(e); }
        this.analyzing = false;
      },
      async toExtraction(w) {
        // 找该作品对应的信号，走提取计划
        try {
          const signals = await api.get('/api/radar/signals');
          const suffix = w.video_id.slice(-6);
          const sig = signals.find((s) => s.id.endsWith(suffix));
          if (sig) {
            const plan = await api.post('/api/extraction/plans', { signal_id: sig.id });
            bus.emit('open-plan', plan);
          } else {
            toast('该作品尚未生成信号，先扫描账号或在雷达中查找', 'warn');
          }
        } catch (e) { handleError(e); }
      },
      num(n) {
        if (n >= 10000) return (n / 10000).toFixed(1) + '万';
        return String(n || 0);
      },
      timeAgo(t) {
        if (!t) return '—';
        const d = new Date(t * 1000);
        return d.toISOString().slice(0, 10);
      },
    },
    template: `
    <section>
      <!-- ============ 列表视图 ============ -->
      <template v-if="view==='list'">
        <div class="page-head">
          <div><div class="page-title">对标监控</div><div class="page-q">同行的账号、作品和打法是什么样的？</div></div>
          <div style="display:flex;gap:8px">
            <button class="btn" v-if="list.length" @click="scanAll">刷新全部</button>
            <button class="btn btn-pri" @click="showAdd=true">+ 添加对标账号</button>
          </div>
        </div>
        <div class="callout c-info"><span>◈</span><span>粘贴同行主页链接即可把他的资料与最近作品搬成监控墙；系统每天按调度自动巡检，发现新作品会进入内容雷达与消息提醒。演示模式下数据来自 demo_ds。</span></div>
        <div class="chips" style="margin-bottom:14px">
          <button class="chip" v-for="p in plats" :key="p[0]" :class="{active:platFilter===p[0]}" @click="platFilter=p[0]">{{ p[1] }}（{{ platN(p[0]) }}）</button>
        </div>

        <div class="empty card" v-if="!filtered.length" style="padding:40px">
          <div style="font-size:14px;font-weight:600;margin-bottom:8px">还没盯上任何同行</div>
          <div class="mini-note" style="margin-bottom:14px">点右上角「添加对标账号」，粘贴主页链接即可把他的资料与最近 30 条作品搬成一面监控墙。</div>
          <button class="btn btn-pri" @click="showAdd=true">+ 添加对标账号</button>
        </div>

        <div class="grid g3">
          <div class="card" v-for="b in filtered" :key="b.id" :style="b.has_update?'border-color:var(--pri-line)':''">
            <div class="card-body">
              <div style="display:flex;gap:12px;align-items:center">
                <div class="avatar" style="width:42px;height:42px;font-size:16px">{{ (b.name||b.platform_label||'?')[0] }}</div>
                <div style="flex:1;min-width:0">
                  <div style="font-weight:700;font-size:13.5px">{{ b.name || b.uid }}
                    <span class="badge b-pri" v-if="b.has_update">有新片</span></div>
                  <div class="mini-note">{{ b.platform_label }} · {{ b.uid }}</div>
                </div>
              </div>
              <div class="mini-note" style="margin:8px 0;min-height:30px">{{ b.bio || '该账号没有填写简介' }}</div>
              <div style="display:flex;gap:14px;font-size:12px;color:var(--ink2);flex-wrap:wrap">
                <span><b>{{ num(b.fans) }}</b> 粉丝</span>
                <span><b>{{ b.works_count }}</b> 已监控</span>
                <span><b style="color:var(--warn)">{{ b.hot_count }}</b> 爆款</span>
                <span>间隔 {{ b.post_gap_days || '—' }} 天</span>
              </div>
              <div class="mini-note" style="margin-top:6px" v-if="b.last_scan_at">最近扫描 {{ b.last_scan_at.slice(5,16).replace('T',' ') }} · 中位分 {{ b.median_score }}</div>
              <div style="display:flex;gap:6px;margin-top:10px">
                <button class="btn btn-sm btn-pri" @click="openDetail(b)">打开监控墙</button>
                <button class="btn btn-sm" :disabled="scanningId===b.id" @click="scan(b)">{{ scanningId===b.id?'扫描中…':'刷新' }}</button>
                <button class="btn btn-sm btn-ghost btn-danger" @click="del(b)">删除</button>
              </div>
            </div>
          </div>
        </div>
      </template>

      <!-- ============ 监控墙详情 ============ -->
      <template v-else-if="detail">
        <div class="page-head">
          <div>
            <div class="page-title">
              <button class="btn btn-sm btn-ghost" @click="backToList">← 返回</button>
              {{ detail.name || detail.uid }} <span class="badge b-info">{{ detail.platform_label }}</span>
            </div>
            <div class="page-q">他的内容为什么有效，我能学什么？</div>
          </div>
          <div style="display:flex;gap:8px">
            <button class="btn" :disabled="analyzing" @click="analyze">{{ analyzing ? '分析中…' : '作者分析' }}</button>
            <button class="btn" :disabled="scanningId===detail.id" @click="scan(detail)">{{ scanningId===detail.id?'扫描中…':'立即扫描' }}</button>
          </div>
        </div>

        <div class="grid g4 section-gap">
          <div class="stat"><div class="stat-l">粉丝</div><div class="stat-v">{{ num(detail.fans) }}</div></div>
          <div class="stat"><div class="stat-l">监控作品</div><div class="stat-v">{{ detail.works_count }}</div></div>
          <div class="stat"><div class="stat-l">爆款（≥2×中位分）</div><div class="stat-v" style="color:var(--warn)">{{ detail.hot_count }}</div></div>
          <div class="stat"><div class="stat-l">发布间隔</div><div class="stat-v">{{ detail.post_gap_days || '—' }} <small>天</small></div></div>
        </div>

        <div class="card section-gap" v-if="detail.analysis">
          <div class="card-head"><div class="card-title">作者分析 <span class="badge b-vio">{{ JSON.parse(detail.analysis.result_json).engine }}</span></div>
            <span class="mini-note">{{ detail.analysis.created_at.slice(0,16).replace('T',' ') }}</span></div>
          <div class="card-body" v-if="detail.analysis">
            <div class="grid g2">
              <div>
                <div class="ana-row"><div class="ana-label">定位判断</div>{{ JSON.parse(detail.analysis.result_json).reading.positioning }}</div>
                <div class="ana-row"><div class="ana-label">内容公式</div>{{ JSON.parse(detail.analysis.result_json).reading.content_formula }}</div>
                <div class="ana-row"><div class="ana-label" style="color:var(--ok)">可学习</div>{{ JSON.parse(detail.analysis.result_json).reading.learn_from.join(' · ') }}</div>
                <div class="ana-row"><div class="ana-label" style="color:var(--err)">应避免</div>{{ JSON.parse(detail.analysis.result_json).reading.avoid.join(' · ') }}</div>
              </div>
              <div>
                <div class="ana-row"><div class="ana-label">优势</div>{{ JSON.parse(detail.analysis.result_json).reading.strengths.join(' · ') }}</div>
                <div class="ana-row"><div class="ana-label">弱点</div>{{ JSON.parse(detail.analysis.result_json).reading.weaknesses.join(' · ') }}</div>
                <div class="ana-row"><div class="ana-label">关键指标</div>
                  爆款率 {{ JSON.parse(detail.analysis.result_json).metrics.hot_ratio }}% ·
                  平均点赞 {{ JSON.parse(detail.analysis.result_json).metrics.avg_likes }} ·
                  收藏/点赞 {{ JSON.parse(detail.analysis.result_json).metrics.collect_rate }}%</div>
              </div>
            </div>
          </div>
        </div>

        <div class="card">
          <div class="card-head"><div class="card-title">作品监控墙 <span class="card-sub">综合分 = 赞×1 + 评×3 + 藏×4 + 转×5 · 爆款阈值 = 中位分 ×2</span></div></div>
          <div class="card-body" style="padding-top:4px"><table class="tbl">
            <tr><th>作品</th><th>发布</th><th>播放</th><th>点赞</th><th>评论</th><th>收藏</th><th>综合分</th><th></th></tr>
            <tr v-for="w in detail.works" :key="w.id" :style="w.is_hot?'background:var(--warn-soft)':''">
              <td style="max-width:260px"><b>{{ w.title }}</b>
                <span class="badge b-warn" v-if="w.is_hot">爆款</span></td>
              <td class="mono">{{ timeAgo(w.create_time) }}</td>
              <td class="mono">{{ num(w.plays) }}</td>
              <td class="mono">{{ num(w.likes) }}</td>
              <td class="mono">{{ num(w.comments) }}</td>
              <td class="mono">{{ num(w.collects) }}</td>
              <td class="mono"><b>{{ w.score }}</b></td>
              <td><button class="btn btn-sm btn-pri" @click="toExtraction(w)">提取文案</button></td>
            </tr>
          </table></div>
        </div>
      </template>

      <!-- ============ 添加弹层 ============ -->
      <div class="modal" :class="{open:showAdd}" @click.self="showAdd=false">
        <div class="modal-card" style="width:520px">
          <div class="modal-head"><div class="modal-title">添加对标账号</div>
            <button class="modal-x" @click="showAdd=false">✕</button></div>
          <div class="modal-body">
            <div class="field"><label>平台</label>
              <select class="input" v-model="addForm.platform">
                <option value="douyin">抖音</option><option value="xhs">小红书</option>
                <option value="channels">视频号</option><option value="bilibili">B站</option>
              </select></div>
            <div class="field"><label>主页链接 / 分享口令 / ID</label>
              <input class="input" v-model="addForm.link"
                :placeholder="addForm.platform==='xhs'?'粘贴小红书主页分享链接（App→分享→复制链接）':addForm.platform==='channels'?'粘贴视频号 ID（如 sphxxxxxx）':'粘贴抖音主页链接或分享口令'"></div>
            <div class="field"><label>扫描范围</label>
              <select class="input" v-model="addForm.scan_range">
                <option value="30">最近 30 条</option><option value="60">最近 60 条</option>
                <option value="all">全部作品（较耗接口额度）</option>
              </select></div>
            <div class="field"><label>备注（可选）</label><input class="input" v-model="addForm.note"></div>
            <div class="mini-note">添加后立即拉取账号资料与作品并计算爆款；失败会显式报错，不会创建空壳账号。</div>
          </div>
          <div class="modal-foot">
            <button class="btn" @click="showAdd=false">取消</button>
            <button class="btn btn-pri" :disabled="adding" @click="doAdd">{{ adding ? '解析并扫描中…' : '添加并扫描' }}</button>
          </div>
        </div>
      </div>
    </section>`,
  };
})();
