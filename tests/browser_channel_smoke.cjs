const { chromium } = require('C:/Users/10336/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const fs = require('fs');

(async () => {
  const base = process.env.JIANZHOU_E2E_URL || 'http://127.0.0.1:8742';
  const readOnly = process.env.JIANZHOU_E2E_READONLY === '1';
  const artifactDir = process.env.JIANZHOU_E2E_ARTIFACTS || 'D:/Temp/UserTemp/jianzhou-channel-e2e-artifacts-20260810-schema30-official-cover';
  fs.mkdirSync(artifactDir, { recursive: true });
  const browser = await chromium.launch({
    headless: true,
    executablePath: 'C:/Program Files (x86)/Google/Chrome/Application/chrome.exe',
  });
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 }, deviceScaleFactor: 1 });
  const errors = [];
  page.on('pageerror', (error) => errors.push('pageerror: ' + error.message));
  page.on('console', (message) => { if (message.type() === 'error') errors.push('console: ' + message.text()); });

  async function visit(hash, selector) {
    await page.goto(base + '/#/' + hash, { waitUntil: 'networkidle' });
    await page.locator(selector).waitFor({ state: 'visible', timeout: 15000 });
    const layout = await page.evaluate(() => ({
      width: document.documentElement.clientWidth,
      scrollWidth: document.documentElement.scrollWidth,
      text: document.querySelector('main')?.innerText?.trim().slice(0, 120) || '',
    }));
    if (!layout.text) throw new Error(hash + ' rendered a blank main area');
    if (layout.scrollWidth > layout.width + 4) throw new Error(hash + ' has horizontal overflow ' + layout.scrollWidth + ' > ' + layout.width);
    return layout;
  }

  const channels = await visit('channels', '.channels-page');
  await page.getByText('建立内容母题', { exact: false }).first().waitFor();
  let momentCount = 0;
  let retroCount = 0;
  let publicPlanTaskId = '';
  let publicPlanTitle = '';
  let visualCoverBound = false;
  if (!readOnly) {
    const title = '浏览器验收母题 ' + Date.now();
    await page.locator('.channel-create-card input').first().fill(title);
    await page.getByRole('button', { name: '建立母题' }).click();
    const campaignRow = page.locator('.campaign-row').filter({ hasText: title }).first();
    await campaignRow.waitFor({ timeout: 15000 });
    const campaignId = await page.evaluate(async (targetTitle) => {
      const payload = await fetch('/api/channels/campaigns?status=all').then((response) => response.json());
      return payload.data.find((item) => item.title === targetTitle).id;
    }, title);
    await campaignRow.getByRole('button', { name: /公域创作/ }).click();
    await page.waitForURL(/#\/creation$/, { timeout: 15000 });
    await page.getByText(title, { exact: true }).first().waitFor({ timeout: 15000 });
    const linked = await page.evaluate(async () => {
      const projectId = new URLSearchParams(location.search).get('project');
      const payload = await fetch('/api/creation/projects/' + projectId).then((response) => response.json());
      return { projectId, campaignId: payload.data.campaign_id,
        linkedTasks: payload.data.linked_tasks || [] };
    });
    if (!linked.projectId || linked.campaignId !== campaignId) throw new Error('Campaign did not link to the public creation project');
    if (linked.linkedTasks.length !== 1 || linked.linkedTasks[0].campaign_id !== campaignId) {
      throw new Error('Public creation did not immediately create one campaign-linked planning task');
    }
    publicPlanTaskId = linked.linkedTasks[0].id;
    publicPlanTitle = title;
    const visual = await page.evaluate(async (projectId) => {
      const headers = { 'Content-Type': 'application/json' };
      const draftPayload = await fetch('/api/creation/projects/' + projectId + '/draft', {
        method: 'POST', headers, body: JSON.stringify({ style_mode: 'structure', platform: '公众号' }),
      }).then((response) => response.json());
      if (!draftPayload.success) throw new Error(draftPayload.error?.message || 'draft generation failed');
      const coverPayload = await fetch('/api/visuals/covers', {
        method: 'POST', headers, body: JSON.stringify({
          creation_project_id: projectId, draft_version_id: draftPayload.data.id,
          content_type: 'cover', ratio: '16:9', generation_mode: 'local_template', quantity: 1,
        }),
      }).then((response) => response.json());
      if (!coverPayload.success) throw new Error(coverPayload.error?.message || 'cover generation failed');
      return { draftId: draftPayload.data.id, coverId: coverPayload.data.id };
    }, linked.projectId);
    if (!visual.coverId) throw new Error('Visual cover was not generated for the campaign');
    await page.goto(base + '/?campaign=' + encodeURIComponent(campaignId) + '#/channels', { waitUntil: 'networkidle' });
    await page.locator('.channels-page').waitFor({ timeout: 15000 });
    await page.getByText('朋友圈', { exact: true }).first().click();
    await page.getByRole('button', { name: '生成朋友圈组' }).click();
    await page.locator('.moment-card').first().waitFor({ timeout: 15000 });
    momentCount = await page.locator('.moment-card').count();
    while (await page.getByRole('button', { name: '登记已发布' }).count()) {
      page.once('dialog', (dialog) => dialog.accept('浏览器端发布登记验收'));
      await Promise.all([
        page.waitForResponse((response) => response.url().includes('/publish-manual') && response.request().method() === 'POST'),
        page.getByRole('button', { name: '登记已发布' }).first().click(),
      ]);
      await page.waitForTimeout(120);
    }
    const metricAnswers = ['120,8,2,3', 'T+1'];
    const metricDialog = (dialog) => dialog.accept(metricAnswers.shift() || '');
    page.on('dialog', metricDialog);
    await page.getByRole('button', { name: '录入真实数据' }).first().click();
    await page.locator('.channel-performance-modal.open').waitFor({ timeout: 15000 });
    page.off('dialog', metricDialog);
    await page.getByRole('button', { name: '生成 / 刷新复盘' }).click();
    await page.locator('.retro-card').first().waitFor({ timeout: 15000 });
    retroCount = await page.locator('.retro-card').count();
    await page.getByRole('button', { name: '×' }).click();

    await page.getByText('公众号', { exact: true }).first().click();
    await page.getByRole('button', { name: '生成公众号初稿' }).click();
    await page.locator('.official-editor').waitFor({ timeout: 15000 });
    const articleText = await page.locator('.official-markdown').inputValue();
    if (articleText.length < 100) throw new Error('Official Account article was not persisted into the editor');
    await page.locator('.official-cover-picker').waitFor({ timeout: 15000 });
    const coverOption = page.locator('.official-cover-option').first();
    await coverOption.waitFor({ timeout: 15000 });
    await coverOption.click();
    await page.locator('.official-cover-option.active').waitFor({ timeout: 15000 });
    visualCoverBound = await page.locator('.official-cover-option.active').getByText('当前文章封面').isVisible();
    await page.locator('.official-cover-picker').screenshot({ path: artifactDir + '/official-cover-picker.png' });
  }
  await page.screenshot({ path: artifactDir + '/channels.png', fullPage: true });

  const assets = await visit('packages', '.asset-tabs');
  await page.getByRole('button', { name: '渠道成品' }).click();
  await page.locator('.asset-card').first().waitFor({ timeout: 15000 });
  const assetCount = await page.locator('.asset-card').count();
  await page.screenshot({ path: artifactDir + '/channel-assets.png', fullPage: true });

  const plan = await visit('plan', '.board');
  const channelCardCount = await page.locator('.kcard').filter({ hasText: '渠道运营' }).count();
  const publicPlanCard = page.locator('.kcard')
    .filter({ hasText: publicPlanTitle }).filter({ hasText: '公域创作' });
  if (publicPlanTaskId) await publicPlanCard.first().waitFor({ timeout: 15000 });
  const publicPlanCardCount = publicPlanTaskId ? await publicPlanCard.count() : 0;
  if (!readOnly && publicPlanCardCount !== 1) {
    throw new Error('Expected one public planning card, found ' + publicPlanCardCount);
  }
  await page.screenshot({ path: artifactDir + '/channel-plan.png', fullPage: true });

  await browser.close();
  if (errors.length) throw new Error(errors.join('\n'));
  console.log(JSON.stringify({ channels, assets, plan, momentCount, retroCount, assetCount,
    channelCardCount, publicPlanTaskId, publicPlanCardCount, visualCoverBound }));
})().catch((error) => { console.error(error.stack || error); process.exit(1); });
