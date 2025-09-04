import json
import math
import tkinter as tk
from tkinter import ttk, filedialog, colorchooser, simpledialog, messagebox

import matplotlib
matplotlib.use("TkAgg")
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
from matplotlib.figure import Figure
from matplotlib.patches import Rectangle, Ellipse
from matplotlib.lines import Line2D
from matplotlib.patches import FancyArrowPatch


class MplDrawioEditor:
    """
    Matplotlib を用いた最小版の draw.io ライクな図形エディタ

    - 図形: 矩形・楕円・配管記号（pipe/elbow/tee/valve/reducer/flange/instrument）
    - 編集: 選択/移動/リサイズ(8ハンドル)/範囲選択
    - コネクタ: 図形間の直線矢印（追従更新）
    - テキスト: 図形ダブルクリックで編集
    - 履歴: Undo/Redo（スナップショット）
    - 保存/読み込み: JSON、SVG書き出し（matplotlibの savefig）
    - グリッド/スナップ
    """

    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("DrawPy (Matplotlib) - Minimal draw.io-like Editor")

        # モデル
        self.next_id = 1
        self.shapes = {}       # id -> dict
        self.connectors = {}   # id -> dict
        self.selection = set() # 's:id' / 'c:id'
        self.drag = None

        # 既定スタイル
        self.default_fill = "#ffffff"
        self.default_outline = "#333333"
        self.default_width = 2
        self.grid_size = 20
        self.snap_to_grid = True
        self.show_grid = True

        # 履歴
        self.history = []
        self.history_index = -1

        # UI
        self._build_ui()
        self._bind_keys()
        self._config_axes()
        self.push_history()

    # -------------- UI --------------
    def _build_ui(self):
        self.root.geometry("1200x780")

        # 左ツールバー
        left = ttk.Frame(self.root)
        left.pack(side=tk.LEFT, fill=tk.Y)
        self.tool = tk.StringVar(value="select")

        def add_tool(text, value):
            b = ttk.Radiobutton(left, text=text, value=value, variable=self.tool)
            b.pack(fill=tk.X, padx=8, pady=4)
            return b

        add_tool("選択", "select")
        add_tool("矩形", "rect")
        add_tool("楕円", "ellipse")
        add_tool("配管", "symbol")
        ttk.Label(left, text="配管記号").pack(fill=tk.X, padx=8)
        self.symbol_kind = tk.StringVar(value="pipe")
        ttk.Combobox(left, textvariable=self.symbol_kind, state="readonly",
                     values=["pipe","elbow","tee","valve","reducer","flange","instrument"]).pack(fill=tk.X, padx=8, pady=2)
        add_tool("コネクタ", "connector")
        add_tool("テキスト", "text")

        ttk.Separator(left).pack(fill=tk.X, padx=8, pady=8)
        ttk.Button(left, text="塗り色...", command=self.pick_fill).pack(fill=tk.X, padx=8, pady=2)
        ttk.Button(left, text="線色...", command=self.pick_outline).pack(fill=tk.X, padx=8, pady=2)
        ttk.Button(left, text="線幅...", command=self.pick_width).pack(fill=tk.X, padx=8, pady=2)
        self.status = tk.StringVar(value="準備完了")
        ttk.Label(left, textvariable=self.status, anchor=tk.W, wraplength=180).pack(side=tk.BOTTOM, fill=tk.X, padx=8, pady=8)

        # 右: Matplotlib Figure
        right = ttk.Frame(self.root)
        right.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.fig = Figure(figsize=(9, 6), dpi=100)
        self.ax = self.fig.add_subplot(111)
        self.ax.set_aspect('equal', adjustable='box')
        self.ax.set_xlim(0, 1600)
        self.ax.set_ylim(0, 1000)
        self.ax.invert_yaxis()  # 画面座標風（上が0）
        self.ax.set_facecolor('#f7f7f7')
        self.canvas = FigureCanvasTkAgg(self.fig, master=right)
        self.canvas.draw()
        self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)
        self.toolbar = NavigationToolbar2Tk(self.canvas, right)
        self.toolbar.update()

        # MPL イベント
        self.cid_down = self.canvas.mpl_connect('button_press_event', self.on_down)
        self.cid_move = self.canvas.mpl_connect('motion_notify_event', self.on_drag)
        self.cid_up = self.canvas.mpl_connect('button_release_event', self.on_up)
        self.cid_dbl = self.canvas.mpl_connect('button_press_event', self.on_double_click_detector)

        # 補助レイヤ
        self.marquee = None
        self.handles = []

        # メニュー
        menubar = tk.Menu(self.root)
        self.root.config(menu=menubar)
        filem = tk.Menu(menubar, tearoff=0)
        filem.add_command(label="新規", command=self.action_new, accelerator="Ctrl+N")
        filem.add_command(label="開く...", command=self.action_open, accelerator="Ctrl+O")
        filem.add_command(label="保存...", command=self.action_save, accelerator="Ctrl+S")
        filem.add_separator()
        filem.add_command(label="SVG書き出し...", command=self.action_export_svg)
        filem.add_separator()
        filem.add_command(label="終了", command=self.root.quit)
        menubar.add_cascade(label="ファイル", menu=filem)

        editm = tk.Menu(menubar, tearoff=0)
        editm.add_command(label="元に戻す", command=self.undo, accelerator="Ctrl+Z")
        editm.add_command(label="やり直す", command=self.redo, accelerator="Ctrl+Y")
        editm.add_separator()
        editm.add_command(label="削除", command=self.delete_selection, accelerator="Del")
        menubar.add_cascade(label="編集", menu=editm)

        viewm = tk.Menu(menubar, tearoff=0)
        viewm.add_checkbutton(label="グリッド表示", command=self.toggle_grid)
        viewm.add_checkbutton(label="グリッドにスナップ", command=self.toggle_snap)
        menubar.add_cascade(label="表示", menu=viewm)

    def _bind_keys(self):
        self.root.bind("<Control-n>", lambda e: self.action_new())
        self.root.bind("<Control-o>", lambda e: self.action_open())
        self.root.bind("<Control-s>", lambda e: self.action_save())
        self.root.bind("<Delete>", lambda e: self.delete_selection())
        self.root.bind("<Control-z>", lambda e: self.undo())
        self.root.bind("<Control-y>", lambda e: self.redo())

    def _config_axes(self):
        self.ax.set_xticks(range(0, 2001, self.grid_size))
        self.ax.set_yticks(range(0, 2001, self.grid_size))
        self.ax.grid(self.show_grid, which='both', color='#e5e5e5', linewidth=0.8)
        self.canvas.draw_idle()

    # -------------- helpers --------------
    def new_id(self):
        i = self.next_id
        self.next_id += 1
        return i

    def snap(self, v):
        return int(round(v / self.grid_size)) * self.grid_size if self.snap_to_grid else v

    def shape_center(self, s):
        return (s['x'] + s['w']/2.0, s['y'] + s['h']/2.0)

    def anchor_point(self, s, toward):
        cx, cy = self.shape_center(s)
        dx, dy = toward[0]-cx, toward[1]-cy
        if dx == 0 and dy == 0:
            return (cx, cy)
        if s['type'] in ('rect', 'symbol'):
            rx, ry = s['w']/2.0, s['h']/2.0
            tx = float('inf') if dx == 0 else rx/abs(dx)
            ty = float('inf') if dy == 0 else ry/abs(dy)
            t = min(tx, ty)
            return (cx + dx*t, cy + dy*t)
        elif s['type'] == 'ellipse':
            a, b = s['w']/2.0, s['h']/2.0
            denom = (dx*dx)/(a*a) + (dy*dy)/(b*b)
            if denom <= 0:
                return (cx, cy)
            t = 1.0/math.sqrt(denom)
            return (cx + dx*t, cy + dy*t)
        else:
            return (cx, cy)

    # -------------- draw --------------
    def draw_all(self):
        self.ax.patches.clear()
        self.ax.lines.clear()
        self.ax.texts.clear()
        # connectors first
        for c in self.connectors.values():
            self._draw_connector(c)
        # shapes
        for s in self.shapes.values():
            self._draw_shape(s)
        # handles
        self._draw_handles()
        self.canvas.draw_idle()

    def _draw_shape(self, s):
        x, y, w, h = s['x'], s['y'], s['w'], s['h']
        s['artists'] = []
        if s['type'] == 'rect':
            patch = Rectangle((x, y), w, h, facecolor=s['fill'], edgecolor=s['outline'], linewidth=s['width'], zorder=2)
            self.ax.add_patch(patch)
            s['artists'].append(patch)
        elif s['type'] == 'ellipse':
            patch = Ellipse((x + w/2, y + h/2), w, h, facecolor=s['fill'], edgecolor=s['outline'], linewidth=s['width'], zorder=2)
            self.ax.add_patch(patch)
            s['artists'].append(patch)
        elif s['type'] == 'symbol':
            for art in self._symbol_artists(s):
                if isinstance(art, Line2D):
                    self.ax.add_line(art)
                else:
                    # パッチ（Ellipse など）
                    self.ax.add_patch(art)
                s['artists'].append(art)
        # label
        if s.get('text'):
            cx, cy = self.shape_center(s)
            txt = self.ax.text(cx, cy, s['text'], ha='center', va='center', fontsize=12, zorder=3)
            s['text_artist'] = txt
        else:
            s['text_artist'] = None

    def _draw_connector(self, c):
        s1 = self.shapes.get(c['src'])
        s2 = self.shapes.get(c['dst'])
        if not s1 or not s2:
            return
        p1 = self.anchor_point(s1, self.shape_center(s2))
        p2 = self.anchor_point(s2, self.shape_center(s1))
        arrow = FancyArrowPatch(p1, p2, arrowstyle='-|>', mutation_scale=10 + 2*c.get('width', self.default_width),
                                linewidth=c.get('width', self.default_width), color=c.get('stroke', self.default_outline), zorder=1)
        c['artist'] = arrow
        self.ax.add_patch(arrow)

    def _symbol_artists(self, s):
        x, y, w, h = s['x'], s['y'], s['w'], s['h']
        cx, cy = x + w/2, y + h/2
        stroke = s.get('outline', self.default_outline)
        fill = s.get('fill', self.default_fill)
        thick = max(1, int(min(w, h) * 0.25))
        thin = max(1, int(min(w, h) * 0.08))
        sym = s.get('symbol', 'pipe')
        arts = []
        def line(x1,y1,x2,y2,lw):
            ln = Line2D([x1,x2],[y1,y2], color=stroke, linewidth=lw, zorder=2)
            arts.append(ln)
        if sym == 'pipe':
            line(x, cy, x + w, cy, thick)
        elif sym == 'elbow':
            arts.append(Line2D([x, x],[cy, y], color=stroke, linewidth=thick, zorder=2))
            arts.append(Line2D([x, cx],[y, y], color=stroke, linewidth=thick, zorder=2))
        elif sym == 'tee':
            line(cx, y + h, cx, y, thick)
            line(x, cy, x + w, cy, thick)
        elif sym == 'valve':
            line(x, y, x + w, y + h, thin)
            line(x + w, y, x, y + h, thin)
        elif sym == 'reducer':
            top = y + h * 0.2
            bottom = y + h * 0.8
            small = x + w * 0.75
            arts.append(Line2D([x, x],[top, bottom], color=stroke, linewidth=thin, zorder=2))
            arts.append(Line2D([x, small],[top, cy], color=stroke, linewidth=thin, zorder=2))
            arts.append(Line2D([x, small],[bottom, cy], color=stroke, linewidth=thin, zorder=2))
        elif sym == 'flange':
            line(x, cy, x + w, cy, thin)
            lf = x + w * 0.25
            rf = x + w * 0.75
            line(lf, y, lf, y + h, thin)
            line(rf, y, rf, y + h, thin)
        elif sym == 'instrument':
            arts.append(Ellipse((cx, cy), min(w, h), min(w, h), facecolor=fill, edgecolor=stroke, linewidth=thin, zorder=2))
        else:
            line(x, cy, x + w, cy, thick)
        return arts

    def _draw_handles(self):
        # 既存ハンドル削除
        for h in self.handles:
            try:
                h.remove()
            except Exception:
                pass
        self.handles.clear()
        ids = self.selected_shape_ids()
        if len(ids) != 1:
            return
        s = self.shapes.get(ids[0])
        if not s:
            return
        x, y, w, h = s['x'], s['y'], s['w'], s['h']
        pts = [
            (x, y, 'nw'), (x + w/2, y, 'n'), (x + w, y, 'ne'),
            (x, y + h/2, 'w'), (x + w, y + h/2, 'e'),
            (x, y + h, 'sw'), (x + w/2, y + h, 's'), (x + w, y + h, 'se'),
        ]
        size = 8
        for px, py, pos in pts:
            r = Rectangle((px - size/2, py - size/2), size, size, facecolor="#2d7ff9", edgecolor='white', linewidth=1, zorder=4)
            r._handle = (s['id'], pos)
            self.ax.add_patch(r)
            self.handles.append(r)

    # -------------- selection helpers --------------
    def clear_selection(self):
        self.selection.clear()
        self._draw_handles()
        self.status.set("選択解除")
        self.canvas.draw_idle()

    def select_item(self, tag, add=False):
        if not add:
            self.selection.clear()
        self.selection.add(tag)
        self._draw_handles()
        self.canvas.draw_idle()

    def selected_shape_ids(self):
        return [int(t.split(':')[1]) for t in self.selection if t.startswith('s:')]

    def selected_connector_ids(self):
        return [int(t.split(':')[1]) for t in self.selection if t.startswith('c:')]

    # -------------- event handling --------------
    def in_axes(self, event):
        return event.inaxes == self.ax and event.xdata is not None and event.ydata is not None

    def on_double_click_detector(self, event):
        if event.dblclick and self.in_axes(event):
            tag = self.find_item_at(event.xdata, event.ydata)
            if tag and tag.startswith('s:') and self.tool.get() in ('select', 'text'):
                sid = int(tag.split(':')[1])
                self.edit_text(sid)

    def on_down(self, event):
        if not self.in_axes(event):
            return
        x, y = event.xdata, event.ydata
        tool = self.tool.get()
        add = (event.key == 'shift') or (event.guiEvent.state & 0x0001 if hasattr(event, 'guiEvent') and event.guiEvent else 0)

        # ハンドル命中？
        for h in self.handles:
            if h.contains_point((event.x, event.y)):
                sid, pos = h._handle
                s = self.shapes.get(sid)
                if s:
                    self.drag = {'kind':'resize','sid':sid,'pos':pos,'start':(x,y),'orig':(s['x'],s['y'],s['w'],s['h'])}
                    return

        if tool == 'select':
            tag = self.find_item_at(x, y)
            if tag:
                self.select_item(tag, add=add)
                if tag.startswith('s:'):
                    self.drag = {'kind':'move','sids': self.selected_shape_ids(), 'start':(x,y)}
            else:
                # マーキー開始
                self.drag = {'kind':'marquee','start':(x,y)}
                self.marquee = Rectangle((x, y), 0, 0, fill=True, facecolor=(0.18,0.5,0.97,0.1), edgecolor="#2d7ff9", linestyle="--", zorder=5)
                self.ax.add_patch(self.marquee)
                self.canvas.draw_idle()
            return

        if tool in ('rect','ellipse','symbol'):
            sid = self.new_id()
            s = {'id':sid, 'type': 'symbol' if tool=='symbol' else tool, 'symbol': self.symbol_kind.get(),
                 'x': self.snap(x), 'y': self.snap(y), 'w': 1, 'h': 1,
                 'fill': self.default_fill, 'outline': self.default_outline, 'width': self.default_width,
                 'text': ''}
            self.shapes[sid] = s
            self.select_item(f's:{sid}')
            self.drag = {'kind':'create','sid':sid,'start':(x,y)}
            self.draw_all()
            return

        if tool == 'connector':
            tag = self.find_item_at(x, y)
            if tag and tag.startswith('s:'):
                sid = int(tag.split(':')[1])
                self.drag = {'kind':'connect','src':sid}
            return

        if tool == 'text':
            tag = self.find_item_at(x, y)
            if tag and tag.startswith('s:'):
                sid = int(tag.split(':')[1])
                self.edit_text(sid)

    def on_drag(self, event):
        if not self.drag or not self.in_axes(event):
            return
        x, y = event.xdata, event.ydata
        kind = self.drag['kind']
        if kind == 'create':
            s = self.shapes[self.drag['sid']]
            x0, y0 = self.drag['start']
            x1, y1 = (self.snap(x), self.snap(y)) if self.snap_to_grid else (x, y)
            s['x'] = min(x0, x1)
            s['y'] = min(y0, y1)
            s['w'] = max(1, abs(x1 - x0))
            s['h'] = max(1, abs(y1 - y0))
            self.draw_all()
            return
        if kind == 'move':
            x0, y0 = self.drag['start']
            dx, dy = x - x0, y - y0
            for sid in self.drag['sids']:
                s = self.shapes.get(sid)
                if not s:
                    continue
                nx = self.snap(s['x'] + dx) if self.snap_to_grid else s['x'] + dx
                ny = self.snap(s['y'] + dy) if self.snap_to_grid else s['y'] + dy
                s['x'], s['y'] = nx, ny
            self.drag['start'] = (x, y)
            self.draw_all()
            return
        if kind == 'resize':
            sid = self.drag['sid']
            pos = self.drag['pos']
            s = self.shapes.get(sid)
            if not s:
                return
            ox, oy, ow, oh = self.drag['orig']
            x1, y1 = (self.snap(x), self.snap(y)) if self.snap_to_grid else (x, y)
            nx, ny, nw, nh = ox, oy, ow, oh
            if 'w' in pos:
                nx = min(x1, ox + ow)
                nw = abs(ox + ow - x1)
            if 'e' in pos:
                nx = min(ox, x1)
                nw = abs(x1 - ox)
            if 'n' in pos:
                ny = min(y1, oy + oh)
                nh = abs(oy + oh - y1)
            if 's' in pos:
                ny = min(oy, y1)
                nh = abs(y1 - oy)
            s['x'], s['y'], s['w'], s['h'] = nx, ny, max(1, nw), max(1, nh)
            self.draw_all()
            return
        if kind == 'connect':
            # 単にプレビューなし、移動中は無視
            return
        if kind == 'marquee':
            x0, y0 = self.drag['start']
            self.marquee.set_x(min(x0, x))
            self.marquee.set_y(min(y0, y))
            self.marquee.set_width(abs(x - x0))
            self.marquee.set_height(abs(y - y0))
            self.canvas.draw_idle()

    def on_up(self, event):
        if not self.drag:
            return
        kind = self.drag['kind']
        if not self.in_axes(event) and kind != 'marquee':
            self.drag = None
            return

        if kind in ('create','move','resize'):
            self.push_history()
        if kind == 'connect' and self.in_axes(event):
            tag = self.find_item_at(event.xdata, event.ydata)
            if tag and tag.startswith('s:'):
                dst = int(tag.split(':')[1])
                if dst != self.drag['src']:
                    cid = self.new_id()
                    c = {'id':cid,'src':self.drag['src'],'dst':dst,'arrow':'last',
                         'stroke': self.default_outline,'width': self.default_width}
                    self.connectors[cid] = c
                    self.push_history()
            self.draw_all()
        if kind == 'marquee':
            x0, y0 = self.drag['start']
            x1, y1 = (event.xdata, event.ydata) if self.in_axes(event) else (x0, y0)
            add = (event.key == 'shift') if hasattr(event, 'key') else False
            self.apply_marquee_selection(x0, y0, x1, y1, add=add)
            if self.marquee:
                try:
                    self.marquee.remove()
                except Exception:
                    pass
                self.marquee = None
            self.canvas.draw_idle()
        self.drag = None

    # -------------- hit tests & marquee --------------
    def find_item_at(self, xd, yd):
        # 形優先で判定（最前面から）
        for sid, s in reversed(list(self.shapes.items())):
            if self.point_in_shape(xd, yd, s):
                return f's:{sid}'
        # コネクタ（近接距離）
        tol = self.pixel_to_data_tol(6)
        for cid, c in reversed(list(self.connectors.items())):
            s1 = self.shapes.get(c['src']); s2 = self.shapes.get(c['dst'])
            if not s1 or not s2:
                continue
            p1 = self.anchor_point(s1, self.shape_center(s2))
            p2 = self.anchor_point(s2, self.shape_center(s1))
            if self.dist_point_to_segment((xd, yd), p1, p2) <= tol:
                return f'c:{cid}'
        return None

    def pixel_to_data_tol(self, px):
        # x方向の1pxがデータ単位でどれくらいか
        inv = self.ax.transData.inverted()
        d0 = inv.transform((0, 0))
        d1 = inv.transform((px, 0))
        return abs(d1[0] - d0[0])

    def point_in_shape(self, x, y, s):
        if s['type'] in ('rect', 'symbol'):
            return (s['x'] <= x <= s['x'] + s['w']) and (s['y'] <= y <= s['y'] + s['h'])
        if s['type'] == 'ellipse':
            cx, cy = self.shape_center(s)
            rx, ry = s['w']/2.0, s['h']/2.0
            if rx <= 0 or ry <= 0:
                return False
            return ((x - cx)**2)/(rx*rx) + ((y - cy)**2)/(ry*ry) <= 1.0
        return False

    def dist_point_to_segment(self, p, a, b):
        # p:(x,y), a:(x,y), b:(x,y) in data coords
        (x, y), (x1, y1), (x2, y2) = p, a, b
        dx, dy = x2 - x1, y2 - y1
        if dx == 0 and dy == 0:
            return math.hypot(x - x1, y - y1)
        t = ((x - x1) * dx + (y - y1) * dy) / (dx*dx + dy*dy)
        t = max(0, min(1, t))
        proj = (x1 + t*dx, y1 + t*dy)
        return math.hypot(x - proj[0], y - proj[1])

    def apply_marquee_selection(self, x0, y0, x1, y1, add=False):
        if not add:
            self.selection.clear()
        left, right = sorted([x0, x1])
        top, bottom = sorted([y0, y1])
        for sid, s in self.shapes.items():
            sx0, sy0, sx1, sy1 = s['x'], s['y'], s['x'] + s['w'], s['y'] + s['h']
            if left <= sx0 and right >= sx1 and top <= sy0 and bottom >= sy1:
                self.selection.add(f's:{sid}')
        for cid, c in self.connectors.items():
            s1 = self.shapes.get(c['src']); s2 = self.shapes.get(c['dst'])
            if not s1 or not s2:
                continue
            p1 = self.anchor_point(s1, self.shape_center(s2))
            p2 = self.anchor_point(s2, self.shape_center(s1))
            lx, rx = sorted([p1[0], p2[0]])
            ty, by = sorted([p1[1], p2[1]])
            if left <= lx and right >= rx and top <= ty and bottom >= by:
                self.selection.add(f'c:{cid}')
        self._draw_handles()

    # -------------- actions / edit --------------
    def update_connectors_for(self, sid):
        for c in self.connectors.values():
            if c['src'] == sid or c['dst'] == sid:
                # 描き直しは draw_all で全体更新
                pass

    def edit_text(self, sid):
        s = self.shapes.get(sid)
        if not s:
            return
        txt = simpledialog.askstring("テキスト編集", "図形のテキスト:", initialvalue=s.get('text',''))
        if txt is None:
            return
        s['text'] = txt
        self.draw_all()
        self.push_history()

    def delete_selection(self):
        if not self.selection:
            return
        sids = self.selected_shape_ids()
        cids = set(self.selected_connector_ids())
        if sids:
            for cid, c in list(self.connectors.items()):
                if c['src'] in sids or c['dst'] in sids:
                    cids.add(cid)
        for cid in cids:
            self.connectors.pop(cid, None)
        for sid in sids:
            self.shapes.pop(sid, None)
        self.selection.clear()
        self.draw_all()
        self.push_history()

    def pick_fill(self):
        color = colorchooser.askcolor(color=self.default_fill)[1]
        if not color:
            return
        self.default_fill = color
        for sid in self.selected_shape_ids():
            s = self.shapes[sid]
            s['fill'] = color
        self.draw_all()
        self.push_history()

    def pick_outline(self):
        color = colorchooser.askcolor(color=self.default_outline)[1]
        if not color:
            return
        self.default_outline = color
        for sid in self.selected_shape_ids():
            s = self.shapes[sid]
            s['outline'] = color
        for cid in self.selected_connector_ids():
            c = self.connectors[cid]
            c['stroke'] = color
        self.draw_all()
        self.push_history()

    def pick_width(self):
        w = simpledialog.askinteger("線の太さ", "1〜10:", minvalue=1, maxvalue=10, initialvalue=self.default_width)
        if not w:
            return
        self.default_width = int(w)
        for sid in self.selected_shape_ids():
            self.shapes[sid]['width'] = self.default_width
        for cid in self.selected_connector_ids():
            self.connectors[cid]['width'] = self.default_width
        self.draw_all()
        self.push_history()

    def toggle_grid(self):
        self.show_grid = not self.show_grid
        self.ax.grid(self.show_grid)
        self.canvas.draw_idle()

    def toggle_snap(self):
        self.snap_to_grid = not self.snap_to_grid

    # -------------- persistence --------------
    def serialize(self):
        return {
            'shapes': [
                {k: v for k, v in s.items() if k not in ('artists','text_artist')}
                for s in self.shapes.values()
            ],
            'connectors': [
                {k: v for k, v in c.items() if k not in ('artist',)}
                for c in self.connectors.values()
            ],
            'next_id': self.next_id,
            'settings': {
                'default_fill': self.default_fill,
                'default_outline': self.default_outline,
                'default_width': self.default_width,
                'grid_size': self.grid_size,
                'snap_to_grid': self.snap_to_grid,
                'show_grid': self.show_grid,
            }
        }

    def deserialize(self, data):
        self.shapes.clear()
        self.connectors.clear()
        for s in data.get('shapes', []):
            s = dict(s)
            s['artists'] = []
            s['text_artist'] = None
            self.shapes[s['id']] = s
        for c in data.get('connectors', []):
            c = dict(c)
            c['artist'] = None
            self.connectors[c['id']] = c
        self.next_id = data.get('next_id', max([0]+[s['id'] for s in self.shapes.values()]+[c['id'] for c in self.connectors.values()]) + 1)
        cfg = data.get('settings', {})
        self.default_fill = cfg.get('default_fill', self.default_fill)
        self.default_outline = cfg.get('default_outline', self.default_outline)
        self.default_width = cfg.get('default_width', self.default_width)
        self.grid_size = cfg.get('grid_size', self.grid_size)
        self.snap_to_grid = cfg.get('snap_to_grid', self.snap_to_grid)
        self.show_grid = cfg.get('show_grid', self.show_grid)
        self._config_axes()
        self.draw_all()

    def action_new(self):
        if not self.confirm_discard_changes():
            return
        self.shapes.clear()
        self.connectors.clear()
        self.selection.clear()
        self.next_id = 1
        self.history.clear()
        self.history_index = -1
        self.draw_all()
        self.push_history()

    def action_open(self):
        path = filedialog.askopenfilename(filetypes=[("DrawPy JSON","*.drawpy.json"),("JSON","*.json"),("All","*.*")])
        if not path:
            return
        try:
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            self.deserialize(data)
            self.push_history()
            self.status.set(f"読み込み: {path}")
        except Exception as ex:
            messagebox.showerror("読み込み失敗", str(ex))

    def action_save(self):
        path = filedialog.asksaveasfilename(defaultextension=".drawpy.json", filetypes=[("DrawPy JSON","*.drawpy.json"),("JSON","*.json")])
        if not path:
            return
        try:
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(self.serialize(), f, ensure_ascii=False, indent=2)
            self.status.set(f"保存: {path}")
        except Exception as ex:
            messagebox.showerror("保存失敗", str(ex))

    def action_export_svg(self):
        path = filedialog.asksaveasfilename(defaultextension=".svg", filetypes=[("SVG","*.svg")])
        if not path:
            return
        try:
            self.fig.savefig(path, format='svg', dpi=120)
            self.status.set(f"SVG出力: {path}")
        except Exception as ex:
            messagebox.showerror("SVG出力失敗", str(ex))

    def confirm_discard_changes(self):
        # 簡易: 常にOK（必要なら変更フラグで確認ダイアログ）
        return True

    # -------------- history --------------
    def push_history(self):
        snap = json.dumps(self.serialize())
        if self.history_index < len(self.history) - 1:
            self.history = self.history[:self.history_index + 1]
        self.history.append(snap)
        self.history_index = len(self.history) - 1
        self.status.set("履歴追加")

    def undo(self):
        if self.history_index <= 0:
            return
        self.history_index -= 1
        data = json.loads(self.history[self.history_index])
        self.deserialize(data)
        self.status.set("元に戻す")

    def redo(self):
        if self.history_index >= len(self.history) - 1:
            return
        self.history_index += 1
        data = json.loads(self.history[self.history_index])
        self.deserialize(data)
        self.status.set("やり直す")


def main():
    root = tk.Tk()
    app = MplDrawioEditor(root)
    root.mainloop()


if __name__ == '__main__':
    main()
