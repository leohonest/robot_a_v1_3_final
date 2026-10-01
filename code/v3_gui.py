"""
v3_gui.py - 灵龙 ASR 实时打印窗口 (Tkinter)
=============================================
轮询 asr_watch_local.log，把 [ASR] / [LLM] / [TTS] 高亮显示，
类似 Leo 之前看到的"打印窗口"。

启动：python v3_gui.py  （或双击）
依赖：Python 3.10+ tkinter（标准库）
"""
import tkinter as tk
from tkinter import scrolledtext
import time
import os
import re

LOG = r'C:\Users\DFET\.openclaw\workspace\asr_watch_local.log'

COLOR_ASR  = '#00bcd4'   # 青
COLOR_LLM  = '#43a047'   # 绿
COLOR_TTS  = '#1e88e5'   # 蓝
COLOR_VAD  = '#9e9e9e'   # 灰
COLOR_WAKE = '#fbc02d'   # 黄
COLOR_ERR  = '#e53935'   # 红
COLOR_DIAL = '#8e24aa'   # 紫

# ASR / DIALOG / WAKE / LLM / TTS / VAD / ERR
PATTERNS = [
    (re.compile(r'\[ASR\]'),         COLOR_ASR),
    (re.compile(r'\[LLM\] reply'),  COLOR_LLM),
    (re.compile(r'\[TTS\]'),        COLOR_TTS),
    (re.compile(r'\[VAD\]'),        COLOR_VAD),
    (re.compile(r'\[WAKE\]'),       COLOR_WAKE),
    (re.compile(r'\[NO MATCH\]|Error|Traceback|too short'), COLOR_ERR),
    (re.compile(r'\[DIALOG\]'),     COLOR_DIAL),
]

class TailApp:
    def __init__(self, root):
        self.root = root
        root.title('灵龙 v3 ASR 实时打印 (v3_gui.py)')
        root.geometry('900x520')

        # 顶部状态栏
        self.status = tk.Label(root, text='启动中...', anchor='w', bg='#263238', fg='white', font=('Consolas', 10))
        self.status.pack(fill='x')

        # 文本区
        self.text = scrolledtext.ScrolledText(root, wrap=tk.NONE, font=('Consolas', 11), bg='#0e1116', fg='#e0e0e0')
        self.text.pack(fill='both', expand=True)

        # tag 样式（彩色）
        for pat, color in PATTERNS:
            self.text.tag_configure(color, foreground=color)
        self.text.tag_configure('plain', foreground='#e0e0e0')

        # 按钮
        btn_frame = tk.Frame(root)
        btn_frame.pack(fill='x')
        tk.Button(btn_frame, text='清屏', command=self.clear).pack(side='left', padx=4, pady=4)
        tk.Button(btn_frame, text='复制最新一行', command=self.copy_last).pack(side='left', padx=4, pady=4)

        # 文件读取状态
        self._size = 0
        self._buf = ''

        self.root.after(200, self.poll)

    def clear(self):
        self.text.delete('1.0', tk.END)

    def copy_last(self):
        last = self.text.get('end-2c linestart', 'end-1c')
        if last.strip():
            self.root.clipboard_clear()
            self.root.clipboard_append(last.strip())
            self.status.config(text=f'已复制: {last.strip()[:80]}')

    def poll(self):
        try:
            if os.path.exists(LOG):
                size = os.path.getsize(LOG)
                if size < self._size:
                    # 文件被截断（重启 tail 时）
                    self._size = 0
                if size > self._size:
                    with open(LOG, 'r', encoding='utf-8', errors='replace') as f:
                        f.seek(self._size)
                        chunk = f.read(size - self._size)
                        self._size = size
                        self._buf += chunk
                        if len(self._buf) > 200000:
                            self._buf = self._buf[-100000:]
                        # 切行输出
                        while '\n' in self._buf:
                            line, self._buf = self._buf.split('\n', 1)
                            self._append_line(line)
                    self.status.config(text=f'监听中: {LOG}  大小: {size:,} bytes')
            else:
                self.status.config(text=f'等待文件: {LOG}')
        except Exception as e:
            self._append_line(f'[GUI-ERR] {e}', color=COLOR_ERR)
        self.root.after(300, self.poll)

    def _append_line(self, line, color=None):
        if not line:
            return
        # 找匹配的最高优先级 tag
        applied = color
        if applied is None:
            for pat, c in PATTERNS:
                if pat.search(line):
                    applied = c
                    break
        tag = applied if applied else 'plain'
        self.text.insert(tk.END, line + '\n', tag)
        # 自动滚动到底
        self.text.see(tk.END)
        # 限制最大行数
        line_count = int(self.text.index('end-1c').split('.')[0])
        if line_count > 1500:
            self.text.delete('1.0', '200.0')


if __name__ == '__main__':
    root = tk.Tk()
    app = TailApp(root)
    root.mainloop()