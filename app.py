# -*- coding: utf-8 -*-
"""
期货公司日研报下载器 —— Flask 后端
============================================================
在本地启动一个 Web 服务，前端面板勾选要下载的期货公司、
选择保存目录与研报日期后，后台线程逐家抓取并实时回传进度。

运行：python app.py  （默认 http://127.0.0.1:8899）
"""
import datetime
import os
import shutil
import sys
import threading
import uuid

from flask import Flask, jsonify, render_template, request

from crawler.core import ensure_dir, today_str
from crawler.fetchers import TASKS

# 打包为 exe 时，把 Playwright 浏览器指向 exe 内置的目录
if getattr(sys, '_MEIPASS', None):
    _bundled_browsers = os.path.join(sys._MEIPASS, 'ms-playwright')
    if os.path.isdir(_bundled_browsers):
        os.environ['PLAYWRIGHT_BROWSERS_PATH'] = _bundled_browsers


# 按 id 索引，供前端勾选时引用
TASKS_BY_ID = {t['id']: t for t in TASKS}

app = Flask(__name__)

# 内存中的任务状态：job_id -> {state, steps, current, out_dir, ...}
JOBS = {}
JOBS_LOCK = threading.Lock()

# 当前各公司的抓取线程，用于支持“停止全部”
RUNNING_THREADS = {}
RUNNING_THREADS_LOCK = threading.Lock()


def _public_task(t):
    """只暴露前端需要的字段，去掉函数对象。"""
    return {k: t[k] for k in ('id', 'company', 'ext', 'method', 'desc')}


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/api/companies')
def companies():
    return jsonify([_public_task(t) for t in TASKS])


@app.route('/api/browse', methods=['POST'])
def browse():
    """弹出系统原生“选择文件夹”对话框，返回所选路径。"""
    try:
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
        root.withdraw()
        root.attributes('-topmost', True)
        path = filedialog.askdirectory(title='选择研报保存目录')
        root.destroy()
        if path:
            return jsonify({'path': path})
        return jsonify({'path': None})
    except Exception as e:
        return jsonify({'error': f'无法打开文件夹选择对话框：{e}'}), 500


@app.route('/api/open', methods=['POST'])
def open_dir():
    """在资源管理器中打开指定目录。"""
    data = request.get_json(silent=True) or {}
    path = (data.get('path') or '').strip()
    if not path or not os.path.isdir(path):
        return jsonify({'ok': False, 'error': '目录不存在：' + path}), 400
    try:
        os.startfile(path)  # Windows
        return jsonify({'ok': True})
    except Exception as e:
        return jsonify({'ok': False, 'error': '无法打开目录：' + str(e)}), 500


@app.route('/api/run', methods=['POST'])
def run():
    data = request.get_json(silent=True) or {}
    company_ids = data.get('companies') or []
    date_str = (data.get('date') or '').strip() or today_str()
    out_dir = (data.get('out_dir') or '').strip()

    if not company_ids:
        return jsonify({'error': '请至少勾选一家期货公司'}), 400
    unknown = [c for c in company_ids if c not in TASKS_BY_ID]
    if unknown:
        return jsonify({'error': f'未知的公司：{unknown}'}), 400
    if not out_dir:
        return jsonify({'error': '请选择保存目录'}), 400
    if not os.path.isdir(out_dir):
        try:
            os.makedirs(out_dir, exist_ok=True)
        except Exception as e:
            return jsonify({'error': f'无法创建保存目录：{e}'}), 400

    job_id = uuid.uuid4().hex[:12]
    with JOBS_LOCK:
        JOBS[job_id] = {
            'id': job_id,
            'state': 'running',
            'out_dir': os.path.abspath(out_dir),
            'date': date_str,
            'total': len(company_ids),
            'current_index': 0,
            'current_company': '',
            'steps': [],
            'created': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        }

    def _worker():
        job = JOBS[job_id]
        try:
            root_out = job['out_dir']
            date_str_job = job['date']
            out_dir = ensure_dir(os.path.join(root_out, f'期货日研投{date_str_job}'))
            work_dir = ensure_dir(os.path.join(root_out, '.crawler_tmp'))
            selected = [TASKS_BY_ID[c] for c in company_ids]

            for i, task in enumerate(selected):
                if job['state'] != 'running':
                    break
                job['current_index'] = i
                job['current_company'] = task['company']
                fn = task['fn']
                try:
                    company, path = fn(out_dir, date_str_job, work_dir)
                    size = os.path.getsize(path) if os.path.exists(path) else 0
                    job['steps'].append({
                        'company': company,
                        'status': 'success',
                        'message': os.path.basename(path),
                        'path': path,
                        'size': size,
                    })
                except Exception as e:
                    job['steps'].append({
                        'company': task['company'],
                        'status': 'fail',
                        'message': str(e)[:300],
                        'path': '',
                        'size': 0,
                    })
            if job['state'] == 'running':
                job['state'] = 'done'
            # 清理临时目录（下载的中间图片/单份 PDF）
            shutil.rmtree(work_dir, ignore_errors=True)
        except Exception as e:
            job['state'] = 'error'
            job['steps'].append({'company': '', 'status': 'fail',
                                 'message': '全局异常：' + str(e)[:300], 'path': '', 'size': 0})

    thread = threading.Thread(target=_worker, daemon=True)
    with RUNNING_THREADS_LOCK:
        RUNNING_THREADS[job_id] = thread
    thread.start()
    return jsonify({'job_id': job_id})


@app.route('/api/jobs/<job_id>')
def job_status(job_id):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if not job:
            return jsonify({'error': '任务不存在'}), 404
        # 返回快照
        snap = dict(job)
        snap['steps'] = list(job['steps'])
        return jsonify(snap)


@app.route('/api/stop', methods=['POST'])
def stop():
    data = request.get_json(silent=True) or {}
    job_id = data.get('job_id')
    with JOBS_LOCK:
        if job_id and job_id in JOBS:
            JOBS[job_id]['state'] = 'stopping'
            return jsonify({'ok': True})
    return jsonify({'ok': False, 'error': '任务不存在'}), 404


def _check_playwright():
    """检测当前 python 的 playwright 是否已装浏览器内核，返回提示文本。"""
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            try:
                b = p.chromium.launch(headless=True)
                b.close()
                return None
            except Exception:
                return ('检测到 playwright 已安装，但缺少 chromium 浏览器内核，'
                        '「中原期货」「华联期货」将无法下载。\n'
                        '请执行:  python -m playwright install chromium')
    except Exception:
        return ('未安装 playwright 库，「中原期货」「华联期货」将无法下载。\n'
                '请执行:  pip install playwright  &&  python -m playwright install chromium')


def main():
    port = int(os.environ.get('PORT', '8899'))
    print('=' * 50)
    print('  期货公司日研报下载器已启动')
    print(f'  请在浏览器打开:  http://127.0.0.1:{port}')
    print('=' * 50)
    hint = _check_playwright()
    if hint:
        print('[提示] ' + hint)
        print('-' * 50)
    app.run(host='127.0.0.1', port=port, debug=False, threaded=True)


if __name__ == '__main__':
    main()
