/* API 客户端：统一 success/error 结构；Typed Error 透传给错误弹层。 */
(function () {
  async function request(method, url, body, isForm) {
    const opt = { method, headers: {} };
    if (body && !isForm) {
      opt.headers['Content-Type'] = 'application/json';
      opt.body = JSON.stringify(body);
    } else if (body && isForm) {
      opt.body = body;
    }
    let resp;
    try {
      resp = await fetch(url, opt);
    } catch (e) {
      throw { code: 'NETWORK_ERROR', message: '无法连接本地服务：' + e.message, trace_id: '-' };
    }
    let data;
    try { data = await resp.json(); } catch (e) {
      throw { code: 'BAD_RESPONSE', message: '服务返回了非 JSON 内容（HTTP ' + resp.status + '）', trace_id: '-' };
    }
    if (!resp.ok || data.success === false) {
      throw (data.error || { code: 'UNKNOWN', message: '未知错误', trace_id: '-' });
    }
    return data.data !== undefined ? data.data : data;
  }
  window.api = {
    get: (url) => request('GET', url),
    post: (url, body) => request('POST', url, body || {}),
    put: (url, body) => request('PUT', url, body || {}),
    patch: (url, body) => request('PATCH', url, body || {}),
    upload: (url, formData) => request('POST', url, formData, true),
  };
})();
