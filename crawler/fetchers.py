# -*- coding: utf-8 -*-
"""
期货公司日研报抓取 —— 7 家公司抓取函数
============================================================
每个函数签名：fetch_xxx(out_dir, date_str, work_dir) -> (company, out_path)
"""
import hashlib
import os
import re
import time
import urllib.parse

from .core import (
    http_get, http_download, soup_of, images_to_pdf, merge_pdfs,
    make_docx, target_path, ensure_dir, UA,
)


# ============================================================
# 1. 金元期货 —— HTTP 解析（详情页内嵌 PDF）
# ============================================================
def fetch_jyqh(out_dir, date_str, work_dir):
    company = '金元期货'
    list_url = 'https://www.jyqh.com.cn/jinyuantouyan/'
    r = http_get(list_url)
    soup = soup_of(r)
    a = None
    for cand in soup.find_all('a', href=re.compile(r'/jinyuantouyan/\d+')):
        if '日报' in cand.get_text():
            a = cand
            break
    if not a:
        raise RuntimeError('未在列表页找到日报条目')
    detail_url = urllib.parse.urljoin('https://www.jyqh.com.cn', a['href'])
    r2 = http_get(detail_url)
    soup2 = soup_of(r2)
    pdf_a = soup2.find('a', href=re.compile(r'\.pdf$', re.I))
    if not pdf_a:
        raise RuntimeError(f'详情页未找到 PDF 附件: {detail_url}')
    pdf_url = urllib.parse.urljoin('https://www.jyqh.com.cn', pdf_a['href'])
    out = target_path(out_dir, company, date_str, 'pdf')
    http_download(pdf_url, out)
    return company, out


# ============================================================
# 2. 中原期货 —— Playwright（站点 WAF 需真实浏览器）
# ============================================================
def fetch_zyfutures(out_dir, date_str, work_dir):
    company = '中原期货'
    base = 'https://www.zyfutures.com'
    from playwright.sync_api import sync_playwright
    pdf_url = None
    body = None
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(user_agent=UA, locale='zh-CN')
        page = ctx.new_page()
        try:
            page.goto(base + '/cb.jhtml', wait_until='domcontentloaded', timeout=60000)
            page.wait_for_selector('a[href*="/cb/"]', timeout=45000)
            hrefs = page.eval_on_selector_all(
                'a', "els => els.map(e => e.href).filter(h => /\\/cb\\/\\d+\\.jhtml/.test(h))")
            if not hrefs:
                raise RuntimeError('未在晨报列表找到条目')
            detail_url = hrefs[0]
            page.goto(detail_url, wait_until='domcontentloaded', timeout=60000)
            page.wait_for_selector('a[href*=".pdf"]', timeout=30000)
            pdf_url = page.locator('a[href*=".pdf"]').first.get_attribute('href')
            if not pdf_url:
                raise RuntimeError(f'晨报详情页未找到 PDF: {detail_url}')
            pdf_url = urllib.parse.urljoin(base, pdf_url)
            # 在同一浏览器会话（已通过 WAF 校验、携带 cookies）中下载 PDF
            resp = ctx.request.get(pdf_url, timeout=180000)
            if not resp.ok:
                raise RuntimeError('PDF 下载失败: ' + str(resp.status))
            body = resp.body()
        finally:
            browser.close()
    out = target_path(out_dir, company, date_str, 'pdf')
    with open(out, 'wb') as f:
        f.write(body)
    return company, out


# ============================================================
# 3. 华金期货 —— HTTP 解析（研报正文为图片，转 PDF）
# ============================================================
def fetch_huajin(out_dir, date_str, work_dir):
    company = '华金期货'
    list_url = 'https://www.huajinqh.com/Research/EarlyReport/List.html'
    r = http_get(list_url)
    soup = soup_of(r)
    items = soup.select('.industry-list li a')
    if not items:
        raise RuntimeError('未在早报收评列表找到条目')
    detail_url = urllib.parse.urljoin('https://www.huajinqh.com', items[0]['href'])
    r2 = http_get(detail_url)
    soup2 = soup_of(r2)
    imgs = soup2.select('div.edit img[src*="UserFiles/upload/image"]')
    if not imgs:
        imgs = soup2.select('img[src*="UserFiles/upload/image"]')
    if not imgs:
        raise RuntimeError(f'详情页未找到研报图片: {detail_url}')
    tmp_dir = ensure_dir(os.path.join(work_dir, 'tmp_huajin'))
    img_paths = []
    for i, im in enumerate(imgs):
        src = im.get('src')
        img_url = urllib.parse.urljoin('https://www.huajinqh.com', src)
        ext = os.path.splitext(urllib.parse.urlparse(img_url).path)[1] or '.jpg'
        local = os.path.join(tmp_dir, f'report_{i}{ext}')
        http_download(img_url, local)
        img_paths.append(local)
    out = target_path(out_dir, company, date_str, 'pdf')
    images_to_pdf(img_paths, out)
    return company, out


# ============================================================
# 4. 信达期货 —— HTTP API（MD5 签名），合并当日全品种日报 PDF
# ============================================================
_CINDA_BASE = 'https://www.cindaqh.com'
_CINDA_SECRET = 'pQ7mnfkOUNVW85kd'


def _cinda_headers():
    ts = str(int(time.time()))
    sign = hashlib.md5((ts + _CINDA_SECRET).encode()).hexdigest()
    return {'timestamp': ts, 'sign': sign, 'Referer': _CINDA_BASE + '/yjzx/hgzx'}


def _cinda_get(path, params=None):
    r = http_get(_CINDA_BASE + path, params=params, headers=_cinda_headers())
    return r.json()


def fetch_cinda(out_dir, date_str, work_dir):
    company = '信达期货'
    tree = _cinda_get('/api/admin/varietylist')
    if tree.get('code') != 1:
        raise RuntimeError('品种树接口调用失败: ' + str(tree)[:200])
    leaf_ids = []

    def walk(nodes):
        for n in nodes or []:
            children = n.get('children')
            if children:
                walk(children)
            else:
                leaf_ids.append(n.get('id'))

    walk(tree.get('data') or [])
    if not leaf_ids:
        raise RuntimeError('未获取到品种列表')

    candidates = []
    for vid in leaf_ids:
        try:
            d = _cinda_get('/api/admin/getResearchReportList',
                           {'pageNo': 1, 'pageSize': 5, 'type': 1,
                            'variety': vid, 'channel': 1})
            lst = (d.get('data') or {}).get('list') or []
            for it in lst:
                it['_variety'] = vid
                candidates.append(it)
        except Exception:
            continue
    if not candidates:
        raise RuntimeError('信达日报列表为空')

    def item_date(it):
        t = it.get('updateTime') or it.get('publishTime') or ''
        return (t or '')[:10]

    latest_date = max(item_date(c) for c in candidates)
    day_items = [c for c in candidates if item_date(c) == latest_date]
    if not day_items:
        raise RuntimeError('未找到当日日报')

    tmp_dir = ensure_dir(os.path.join(work_dir, 'tmp_cinda'))
    pdf_paths = []
    for it in day_items:
        files = it.get('files')
        if not files:
            continue
        if files.startswith('http'):
            url = files
        else:
            url = _CINDA_BASE + '/uploads/' + urllib.parse.quote(files)
        title = re.sub(r'[\\/:*?"<>|\s]+', '_', it.get('title') or f'report_{it.get("id")}')
        local = os.path.join(tmp_dir, f'{title}_{it.get("id")}.pdf')
        try:
            http_download(url, local, headers=_cinda_headers())
            pdf_paths.append(local)
        except Exception as e:
            print(f'    [信达] 下载 {files} 失败: {e}')
    if not pdf_paths:
        raise RuntimeError('当日日报 PDF 全部下载失败')

    out = target_path(out_dir, company, date_str, 'pdf')
    merge_pdfs(pdf_paths, out)
    return company, out


# ============================================================
# 5. 东海期货 —— HTTP 解析（列表项直接指向 PDF）
# ============================================================
def fetch_qh168(out_dir, date_str, work_dir):
    company = '东海期货'
    list_url = 'http://www.qh168.com.cn/article?cat_code=IndustryChainDaily&one=y'
    r = http_get(list_url)
    soup = soup_of(r)
    a = soup.find('a', href=re.compile(r'/upload/file/'))
    if not a:
        raise RuntimeError('未在产业链日报列表找到研报')
    pdf_url = urllib.parse.urljoin('http://www.qh168.com.cn', a['href'])
    out = target_path(out_dir, company, date_str, 'pdf')
    http_download(pdf_url, out)
    return company, out


# ============================================================
# 6. 华联期货 —— Playwright（接口全加密，研报为纯文字→docx）
# ============================================================
def fetch_hlqh(out_dir, date_str, work_dir):
    company = '华联期货'
    from playwright.sync_api import sync_playwright
    list_url = 'https://www.hlqh.com/#/hlqh/info/articleCat?p_id=2&c_id=4'
    content = ''
    title = company
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(user_agent=UA, locale='zh-CN')
        page = ctx.new_page()
        try:
            page.goto(list_url, wait_until='domcontentloaded', timeout=60000)
            page.wait_for_timeout(15000)  # 等待加密接口返回与渲染
            item = page.get_by_text(re.compile(r'华联(日评|期货研究所早评)[【（]')).first
            title = item.inner_text().strip()
            item.click(timeout=15000)
            page.wait_for_timeout(12000)
            content = page.evaluate(
                """() => {
                    const marker = [...document.querySelectorAll('*')].find(el =>
                        (el.innerText || '').indexOf('期货交易咨询业务资格') >= 0 && el.children.length === 0);
                    if (!marker) return '';
                    let node = marker;
                    while (node && node.parentElement) {
                        node = node.parentElement;
                        if (node.tagName === 'DIV') {
                            const kids = [...node.children];
                            const pCount = kids.filter(c => c.tagName === 'P').length;
                            if (pCount >= 5) return node.innerText;
                        }
                    }
                    return node ? node.innerText : '';
                }"""
            )
            if not content:
                raise RuntimeError('未提取到研报正文')
        finally:
            browser.close()
    title = re.sub(r'\s+', ' ', title).strip()
    paragraphs = [ln.strip() for ln in content.splitlines() if ln.strip()]
    out = target_path(out_dir, company, date_str, 'docx')
    make_docx(title, paragraphs, out)
    return company, out


# ============================================================
# 7. 安粮期货 —— HTTP 解析（列表项直接指向 PDF）
# ============================================================
def fetch_alqh(out_dir, date_str, work_dir):
    company = '安粮期货'
    list_url = 'https://www.alqh.com/col77/list'
    r = http_get(list_url)
    soup = soup_of(r)
    a = soup.find('a', href=re.compile(r'\.pdf$', re.I))
    if not a:
        raise RuntimeError('未在投资早参列表找到研报')
    pdf_url = a['href']  # 绝对链接（腾讯云 COS）
    out = target_path(out_dir, company, date_str, 'pdf')
    http_download(pdf_url, out)
    return company, out


# ============================================================
# 8. 国泰君安期货 —— HTTP 解析（详情页正文为 base64 图片，合并当日全部日报）
# ============================================================
def _gtja_images_to_pdf(report_images, pdf_path):
    """把一家公司多份报告的图片按顺序合并为一个 PDF（每份报告先转一个小 PDF，再合并，控制内存）。"""
    tmp_parts = []
    for i, imgs in enumerate(report_images):
        part = os.path.join(os.path.dirname(pdf_path), f'_gtja_part_{i}.pdf')
        if imgs:
            images_to_pdf(imgs, part)
            tmp_parts.append(part)
    if tmp_parts:
        merge_pdfs(tmp_parts, pdf_path)
    for p in tmp_parts:
        try:
            os.remove(p)
        except OSError:
            pass


def fetch_gtjaqh(out_dir, date_str, work_dir):
    company = '国泰君安期货'
    base = 'https://www.gtjaqh.com'
    target_date = date_str.replace('-', '/')      # 2026/10/09
    report_images = []      # 每份报告的图片列表
    found = 0
    page = 1
    while page <= 12:
        r = http_get(f'{base}/mb/report', params={'catType': '', 'page': page})
        soup = soup_of(r)
        items = soup.select('a[href*="/pc/reportDetail/"]')
        if not items:
            break
        page_dates = []
        for a in items:
            date_el = a.find('span', class_=re.compile('date'))
            type_el = a.find('span', class_=re.compile('type'))
            d = (date_el.get_text(strip=True) if date_el else '')
            t = (type_el.get_text(strip=True) if type_el else '')
            page_dates.append(d)
            if d == target_date and t == '日报':
                href = a.get('href')
                uuid = href.split('/')[-1]
                try:
                    r2 = http_get(f'{base}/pc/reportDetail/{uuid}', timeout=90)
                    b64s = re.findall(
                        r'data:image/[a-zA-Z0-9.]+;base64,([A-Za-z0-9+/=]+)', r2.text)
                except Exception:
                    time.sleep(1.5)
                    continue
                if not b64s:
                    continue
                imgs = []
                for k, b64 in enumerate(b64s):
                    import base64
                    try:
                        raw = base64.b64decode(b64)
                        if not raw:
                            continue
                        local = os.path.join(work_dir, f'gtja_{found}_{k}.jpg')
                        with open(local, 'wb') as f:
                            f.write(raw)
                        imgs.append(local)
                    except Exception:
                        continue
                if imgs:
                    report_images.append(imgs)
                    found += 1
                time.sleep(0.4)   # 放慢节奏，避免被限流
        # 若整页都没有 目标日期 或更晚 的条目（日期已更早），说明已翻过目标日，停止
        older = [d for d in page_dates if d and d < target_date]
        if older:
            break
        page += 1
    if not report_images:
        raise RuntimeError(f'未在列表页找到 {target_date} 的日报条目')
    out = target_path(out_dir, company, date_str, 'pdf')
    _gtja_images_to_pdf(report_images, out)
    return company, out


# ============================================================
# 9. 中辉期货 —— HTTP 解析（每日四大板块日报 PDF 直链，合并）
# ============================================================
def fetch_zhqh(out_dir, date_str, work_dir):
    company = '中辉期货'
    base = 'https://www.zhqh.com.cn'
    target = date_str.replace('-', '')           # 20261009
    identity = {'Accept-Encoding': 'identity'}   # 站点必须 identity，否则乱码
    pdf_urls = []
    r = http_get(base + '/news.asp?id=8', headers=identity)
    soup = soup_of(r)
    for a in soup.find_all('a', href=re.compile(r'/uploadfiles/file/\.*')):
        href = a.get('href')
        text = a.get_text()
        if target in text and href.lower().endswith('.pdf'):
            pdf_urls.append(urllib.parse.urljoin(base, href))
    # 若第一页不完整，再翻分页版 news100.asp?id=8
    if not pdf_urls:
        r = http_get(base + '/news100.asp?id=8', headers=identity)
        soup = soup_of(r)
        for a in soup.find_all('a', href=re.compile(r'/uploadfiles/file/\.*')):
            href = a.get('href')
            text = a.get_text()
            if target in text and href.lower().endswith('.pdf'):
                pdf_urls.append(urllib.parse.urljoin(base, href))
    if not pdf_urls:
        raise RuntimeError(f'未找到 {target} 的板块日报 PDF')
    # 去重（同一日期可能多条）
    pdf_urls = list(dict.fromkeys(pdf_urls))
    tmp_dir = ensure_dir(os.path.join(work_dir, 'tmp_zhqh'))
    pdf_paths = []
    for i, u in enumerate(pdf_urls):
        local = os.path.join(tmp_dir, f'zhqh_{i}.pdf')
        http_download(u, local, headers=identity)
        pdf_paths.append(local)
    out = target_path(out_dir, company, date_str, 'pdf')
    merge_pdfs(pdf_paths, out)
    return company, out


# ============================================================
# 10. 瑞达期货 —— 晨会纪要(HTML→PDF) + 金融每日全景(PDF)，合并
# ============================================================
def _rdqh_find_item(list_url, keywords):
    """在瑞达列表页找同时命中关键词的条目，返回详情 URL 列表。"""
    r = http_get(list_url)
    soup = soup_of(r)
    out = []
    for a in soup.find_all('a', href=re.compile(r'/content/show/\d+/\d+')):
        text = a.get_text()
        if all(k in text for k in keywords):
            out.append(urllib.parse.urljoin('https://www.rdqh.com', a['href']))
    return out


def _rdqh_render_pdf(url, pdf_path):
    """用 Playwright 把瑞达 HTML 详情页渲染为 PDF。"""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(user_agent=UA, locale='zh-CN')
        page = ctx.new_page()
        try:
            page.goto(url, wait_until='domcontentloaded', timeout=60000)
            page.wait_for_timeout(4000)          # 等图片与正文渲染
            page.pdf(path=pdf_path, format='A4')
        finally:
            browser.close()


def fetch_rdqh(out_dir, date_str, work_dir):
    company = '瑞达期货'
    base = 'https://www.rdqh.com'
    target = date_str.replace('-', '')          # 20261009
    tmp_dir = ensure_dir(os.path.join(work_dir, 'tmp_rdqh'))
    pdf_paths = []

    # 1) 晨会纪要（HTML → PDF）
    morning = _rdqh_find_item('https://www.rdqh.com/content/index/61', [target])
    if morning:
        m_pdf = os.path.join(tmp_dir, 'morning.pdf')
        _rdqh_render_pdf(morning[0], m_pdf)
        pdf_paths.append(m_pdf)

    # 2) 金融每日全景（PDF 附件）
    for detail in _rdqh_find_item('https://www.rdqh.com/content/index/116', [target]):
        r = http_get(detail)
        m = re.search(r'href="(/upload/files/[^"]*\.pdf)"', r.text, re.I)
        if not m:
            continue
        pdf_url = urllib.parse.urljoin(base, m.group(1))
        local = os.path.join(tmp_dir, f'fin_{len(pdf_paths)}.pdf')
        http_download(pdf_url, local)
        pdf_paths.append(local)

    if not pdf_paths:
        raise RuntimeError(f'未找到 {target} 的瑞达研报')
    out = target_path(out_dir, company, date_str, 'pdf')
    merge_pdfs(pdf_paths, out)
    return company, out


# ============================================================
# 11. 广发期货 —— HTTP API（列表接口 + 文章附件 PDF，合并）
# ============================================================
def fetch_gfqh(out_dir, date_str, work_dir):
    company = '广发期货'
    target = date_str.replace('-', '')          # 20261009
    api = 'https://rd.gfqh.cn/RDInformation/article/index/reports/100009'
    hdrs = {'Referer': 'https://rd.gfqh.cn/RDInformation/menu/236'}
    r = http_get(api, headers=hdrs)
    soup = soup_of(r)
    ids = []
    for li in soup.select('li.invest-reporter-news-item'):
        onclick = li.get('onclick') or ''
        m = re.search(r'article/(\d+)', onclick)
        if not m:
            continue
        texts = [s.get_text(strip=True) for s in li.find_all('span')]
        title = next((t for t in texts if t.startswith('广发期货')), '')
        if target in title:
            ids.append(m.group(1))
    # 兜底：若上面按标题没筛到，退回收集当天列表里全部文章 id
    if not ids:
        for li in soup.select('li.invest-reporter-news-item'):
            m = re.search(r'article/(\d+)', li.get('onclick') or '')
            if m:
                ids.append(m.group(1))
    if not ids:
        raise RuntimeError(f'未在广发列表接口找到 {target} 的研报')

    tmp_dir = ensure_dir(os.path.join(work_dir, 'tmp_gfqh'))
    pdf_paths = []
    seen = set()
    for aid in ids:
        ar = http_get(f'https://rd.gfqh.cn/RDInformation/article/{aid}', headers=hdrs)
        for m in re.finditer(r'data-url="([^"]+\.pdf)"', ar.text, re.I):
            url = m.group(1)
            if url in seen:
                continue
            seen.add(url)
            local = os.path.join(tmp_dir, f'gfqh_{len(pdf_paths)}.pdf')
            http_download(url, local, headers=hdrs)
            pdf_paths.append(local)
    if not pdf_paths:
        raise RuntimeError(f'广发 {target} 的文章详情页未找到 PDF 附件')
    out = target_path(out_dir, company, date_str, 'pdf')
    merge_pdfs(pdf_paths, out)
    return company, out


# ============================================================
# 12. 国信期货 —— 页面为 JS 触发、需登录/会员，暂无法自动下载
# ============================================================
def fetch_guosenqh(out_dir, date_str, work_dir):
    company = '国信期货'
    raise RuntimeError(
        '国信期货研报入口（vcc.guosenqh.com.cn）为前端 JS 触发且需登录/会员，'
        '点击条目无响应，当前版本无法自动下载，请手动访问其官网获取。')


# ============================================================
# 任务清单（供前端勾选与后端调度）
# ============================================================
TASKS = [
    {
        'id': 'jyqh',
        'company': '金元期货',
        'fn': fetch_jyqh,
        'ext': 'pdf',
        'method': 'HTTP 解析 · PDF',
        'desc': '详情页内嵌 PDF，直接下载',
    },
    {
        'id': 'zyfutures',
        'company': '中原期货',
        'fn': fetch_zyfutures,
        'ext': 'pdf',
        'method': 'Playwright · PDF',
        'desc': '站点带 WAF，需真实浏览器下载晨报 PDF',
    },
    {
        'id': 'huajin',
        'company': '华金期货',
        'fn': fetch_huajin,
        'ext': 'pdf',
        'method': 'HTTP 解析 · 图片转PDF',
        'desc': '研报正文为图片，下载后合并转 PDF',
    },
    {
        'id': 'cinda',
        'company': '信达期货',
        'fn': fetch_cinda,
        'ext': 'pdf',
        'method': 'HTTP API · 合并PDF',
        'desc': 'MD5 签名接口，合并当日全品种日报',
    },
    {
        'id': 'qh168',
        'company': '东海期货',
        'fn': fetch_qh168,
        'ext': 'pdf',
        'method': 'HTTP 解析 · PDF',
        'desc': '产业链日报，列表项直接指向 PDF',
    },
    {
        'id': 'hlqh',
        'company': '华联期货',
        'fn': fetch_hlqh,
        'ext': 'docx',
        'method': 'Playwright · 纯文字转docx',
        'desc': '接口全加密，正文纯文字，生成公文格式 docx',
    },
    {
        'id': 'alqh',
        'company': '安粮期货',
        'fn': fetch_alqh,
        'ext': 'pdf',
        'method': 'HTTP 解析 · PDF',
        'desc': '投资早参，列表项直接指向 PDF',
    },
    {
        'id': 'gtjaqh',
        'company': '国泰君安期货',
        'fn': fetch_gtjaqh,
        'ext': 'pdf',
        'method': 'HTTP 解析 · 图片转PDF',
        'desc': '详情页正文为图片，合并当日全部日报',
    },
    {
        'id': 'zhqh',
        'company': '中辉期货',
        'fn': fetch_zhqh,
        'ext': 'pdf',
        'method': 'HTTP 解析 · 合并PDF',
        'desc': '每日四大板块日报 PDF 直链，合并',
    },
    {
        'id': 'rdqh',
        'company': '瑞达期货',
        'fn': fetch_rdqh,
        'ext': 'pdf',
        'method': 'HTML转PDF + 附件PDF',
        'desc': '晨会纪要(渲染PDF)+金融每日全景(PDF) 合并',
    },
    {
        'id': 'gfqh',
        'company': '广发期货',
        'fn': fetch_gfqh,
        'ext': 'pdf',
        'method': 'HTTP API · 合并PDF',
        'desc': '期现日报汇总+日评 附件 PDF，合并',
    },
    {
        'id': 'guosenqh',
        'company': '国信期货',
        'fn': fetch_guosenqh,
        'ext': 'pdf',
        'method': '需登录（暂不支持）',
        'desc': '页面为前端JS且需会员登录，暂无法自动下载',
    },
]
