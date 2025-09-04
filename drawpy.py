import json
import math
import tkinter as tk
from tkinter import ttk, filedialog, colorchooser, simpledialog, messagebox

# ---------- small helpers ----------
def tag_shape(sid: int) -> str:
    return f"s:{sid}"


def tag_connector(cid: int) -> str:
    return f"c:{cid}"


def parse_tag(tag: str):
    if not tag or ':' not in tag:
        return None, None
    k, i = tag.split(':', 1)
    try:
        return k, int(i)
    except ValueError:
        return k, None


class DiagramEditor:
    """
    シンプルな draw.io 風エディタ（最小実用版）

    主要機能:
    - 矩形/楕円の追加
    - 選択/移動/リサイズ(ハンドル)
    - 直線コネクタ（矢印）と図形移動に追従
    - ダブルクリックで図形テキスト編集
    - Undo/Redo（スナップショット方式）
    - JSON保存/読み込み、SVGエクスポート
    - グリッド表示とスナップ（任意）
    """

    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("DrawPy - Minimal draw.io-like Editor")

        # モデル
        self.next_id = 1
        self.shapes = {}  # id -> dict
        self.connectors = {}  # id -> dict
        self.selection = set()  # 選択中id（図形とコネクタを区別: 's:id' / 'c:id'）

        # スタイル既定値
        self.default_fill = "#FFFFFF"
        self.default_outline = "#333333"
        self.default_width = 2
        self.default_font = ("Arial", 12)
        self.grid_size = 20
        self.show_grid = True
        self.snap_to_grid = True

        # 履歴（スナップショット）
        self.history = []
        self.history_index = -1

        # UI 構築
        self._build_ui()
        self._bind_keys()
        self._draw_grid()
        self.push_history()

    # ================= UI =================
    def _build_ui(self):
        self.root.geometry("1100x700")
        self.root.minsize(900, 600)

        # メニューバー
        menubar = tk.Menu(self.root)
        self.root.config(menu=menubar)

        file_menu = tk.Menu(menubar, tearoff=0)
        file_menu.add_command(label="新規", command=self.action_new, accelerator="Ctrl+N")
        file_menu.add_command(label="開く...", command=self.action_open, accelerator="Ctrl+O")
        file_menu.add_command(label="保存...", command=self.action_save, accelerator="Ctrl+S")
        file_menu.add_separator()
        file_menu.add_command(label="SVG書き出し...", command=self.action_export_svg)
        file_menu.add_separator()
        file_menu.add_command(label="終了", command=self.root.quit)
        menubar.add_cascade(label="ファイル", menu=file_menu)

        edit_menu = tk.Menu(menubar, tearoff=0)
        edit_menu.add_command(label="元に戻す", command=self.undo, accelerator="Ctrl+Z")
        edit_menu.add_command(label="やり直す", command=self.redo, accelerator="Ctrl+Y")
        edit_menu.add_separator()
        edit_menu.add_command(label="削除", command=self.delete_selection, accelerator="Del")
        menubar.add_cascade(label="編集", menu=edit_menu)

        view_menu = tk.Menu(menubar, tearoff=0)
        # 保持するBooleanVarでUIと状態を同期
        self.var_show_grid = tk.BooleanVar(value=self.show_grid)
        self.var_snap_to_grid = tk.BooleanVar(value=self.snap_to_grid)
        view_menu.add_checkbutton(label="グリッドを表示", onvalue=True, offvalue=False,
                                  variable=self.var_show_grid,
                                  command=self.toggle_grid)
        view_menu.add_checkbutton(label="グリッドにスナップ", onvalue=True, offvalue=False,
                                  variable=self.var_snap_to_grid,
                                  command=self.toggle_snap)
        menubar.add_cascade(label="表示", menu=view_menu)

        style_menu = tk.Menu(menubar, tearoff=0)
        style_menu.add_command(label="塗り色...", command=self.pick_fill)
        style_menu.add_command(label="線色...", command=self.pick_outline)
        style_menu.add_command(label="線の太さ...", command=self.pick_width)
        menubar.add_cascade(label="スタイル", menu=style_menu)

        # 左ツールバー
        left = ttk.Frame(self.root)
        left.pack(side=tk.LEFT, fill=tk.Y)
        self.tool = tk.StringVar(value="select")

        def add_tool(text, value):
            b = ttk.Radiobutton(left, text=text, value=value, variable=self.tool)
            b.pack(fill=tk.X, padx=6, pady=3)
            return b

        add_tool("選択", "select")
        add_tool("矩形", "rect")
        add_tool("楕円", "ellipse")
        add_tool("コネクタ", "connector")
        add_tool("テキスト", "text")

        # 配管記号: シンボルツール + 選択用コンボ
        ttk.Separator(left).pack(fill=tk.X, padx=6, pady=6)
        add_tool("配管", "symbol")
        ttk.Label(left, text="配管記号").pack(fill=tk.X, padx=6)
        self.symbol_kind = tk.StringVar(value="pipe")
        self.symbol_combo = ttk.Combobox(left, textvariable=self.symbol_kind, state="readonly",
                                         values=[
                                             "pipe",      # 直管
                                             "elbow",     # エルボ
                                             "tee",       # チーズ
                                             "valve",     # 弁（簡略）
                                             "reducer",   # レデューサ
                                             "flange",    # フランジ
                                             "instrument" # 計装（丸）
                                         ])
        self.symbol_combo.pack(fill=tk.X, padx=6, pady=3)

        # メイン領域
        main = ttk.Frame(self.root)
        main.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self.canvas = tk.Canvas(main, bg="#f7f7f7")
        self.canvas.pack(fill=tk.BOTH, expand=True)

        # イベント
        self.canvas.bind("<Button-1>", self.on_down)
        self.canvas.bind("<B1-Motion>", self.on_drag)
        self.canvas.bind("<ButtonRelease-1>", self.on_up)
        self.canvas.bind("<Double-Button-1>", self.on_double_click)
        # キャンバスサイズ変更時にグリッドを再描画（1回だけバインド）
        self.canvas.bind("<Configure>", self._on_canvas_configure)

        # ステータスバー
        self.status = tk.StringVar(value="準備完了")
        bar = ttk.Label(self.root, textvariable=self.status, anchor=tk.W)
        bar.pack(side=tk.BOTTOM, fill=tk.X)

        # ドラッグ状態
        self.drag = None  # dict: kind('create','move','resize','connect','marquee'), etc.

    def _bind_keys(self):
        self.root.bind("<Control-n>", lambda e: self.action_new())
        self.root.bind("<Control-o>", lambda e: self.action_open())
        self.root.bind("<Control-s>", lambda e: self.action_save())
        self.root.bind("<Delete>", lambda e: self.delete_selection())
        self.root.bind("<Control-z>", lambda e: self.undo())
        self.root.bind("<Control-y>", lambda e: self.redo())

    # =============== Grid =================
    def _draw_grid(self):
        self.canvas.delete("grid")
        if not self.show_grid:
            return
        w = self.canvas.winfo_width() or 2000
        h = self.canvas.winfo_height() or 1200
        step = self.grid_size
        for x in range(0, w, step):
            self.canvas.create_line(x, 0, x, h, fill="#e5e5e5", tags=("grid",))
        for y in range(0, h, step):
            self.canvas.create_line(0, y, w, y, fill="#e5e5e5", tags=("grid",))
        self.canvas.tag_lower("grid")

    def _on_canvas_configure(self, _event=None):
        self._draw_grid()

    def toggle_grid(self):
        # メニューのチェック状態をソースオブトゥルースに
        self.show_grid = bool(self.var_show_grid.get())
        self._draw_grid()

    def toggle_snap(self):
        self.snap_to_grid = bool(self.var_snap_to_grid.get())

    # ============= Model helpers =============
    def new_id(self):
        i = self.next_id
        self.next_id += 1
        return i

    def shape_center(self, s):
        return (s['x'] + s['w'] / 2.0, s['y'] + s['h'] / 2.0)

    def anchor_point(self, s, toward):
        cx, cy = self.shape_center(s)
        dx = toward[0] - cx
        dy = toward[1] - cy
        if dx == 0 and dy == 0:
            return (cx, cy)
        if s['type'] in ('rect', 'symbol'):
            rx = s['w'] / 2.0
            ry = s['h'] / 2.0
            tx = float('inf') if dx == 0 else rx / abs(dx)
            ty = float('inf') if dy == 0 else ry / abs(dy)
            t = min(tx, ty)
            return (cx + dx * t, cy + dy * t)
        elif s['type'] == 'ellipse':
            a = s['w'] / 2.0
            b = s['h'] / 2.0
            denom = (dx*dx)/(a*a) + (dy*dy)/(b*b)
            if denom <= 0:
                return (cx, cy)
            t = 1.0 / math.sqrt(denom)
            return (cx + dx * t, cy + dy * t)
        else:
            return (cx, cy)

    def snap(self, v):
        if not self.snap_to_grid:
            return v
        g = self.grid_size
        return int(round(v / g)) * g

    # ============ Drawing ============
    def draw_shape(self, s):
        x, y, w, h = s['x'], s['y'], s['w'], s['h']
        if s['type'] == 'rect':
            cid = self.canvas.create_rectangle(
                x, y, x + w, y + h,
                fill=s['fill'], outline=s['outline'], width=s['width'],
                tags=(f"shape", f"s:{s['id']}")
            )
            s['canvas_id'] = cid
            s['item_ids'] = [cid]
        elif s['type'] == 'ellipse':
            cid = self.canvas.create_oval(
                x, y, x + w, y + h,
                fill=s['fill'], outline=s['outline'], width=s['width'],
                tags=(f"shape", f"s:{s['id']}")
            )
            s['canvas_id'] = cid
            s['item_ids'] = [cid]
        elif s['type'] == 'symbol':
            # 配管記号を構成要素として描画
            ids = self._create_symbol_items(s)
            s['item_ids'] = ids
            s['canvas_id'] = ids[0] if ids else None
        else:
            return
        # テキスト
        if s.get('text'):
            cx, cy = self.shape_center(s)
            tid = self.canvas.create_text(cx, cy, text=s['text'], font=self.default_font,
                                          tags=("label", f"s:{s['id']}"))
            s['text_id'] = tid
        else:
            s['text_id'] = None

    def draw_connector(self, c):
        s1 = self.shapes.get(c['src'])
        s2 = self.shapes.get(c['dst'])
        if not s1 or not s2:
            return
        p1 = self.anchor_point(s1, self.shape_center(s2))
        p2 = self.anchor_point(s2, self.shape_center(s1))
        arrow = tk.LAST if c.get('arrow', 'last') in ('last', 'end') else tk.BOTH if c.get('arrow') == 'both' else None
        cid = self.canvas.create_line(
            p1[0], p1[1], p2[0], p2[1],
            arrow=arrow, width=c.get('width', self.default_width), fill=c.get('stroke', self.default_outline),
            tags=("connector", f"c:{c['id']}")
        )
        c['canvas_id'] = cid

    def redraw_all(self):
        self.canvas.delete("shape")
        self.canvas.delete("label")
        self.canvas.delete("connector")
        self.canvas.delete("handle")
        for s in self.shapes.values():
            self.draw_shape(s)
        for c in self.connectors.values():
            self.draw_connector(c)
        self.update_handles()

    # ============= Selection/Handles =============
    def clear_selection(self):
        self.selection.clear()
        self.canvas.delete("handle")
        self.status.set("選択解除")

    def select_item(self, tag_id, add=False):
        if not add:
            self.selection.clear()
        self.selection.add(tag_id)
        self.update_handles()

    def selected_shape_ids(self):
        ids = []
        for t in self.selection:
            if t.startswith('s:'):
                ids.append(int(t.split(':')[1]))
        return ids

    def selected_connector_ids(self):
        ids = []
        for t in self.selection:
            if t.startswith('c:'):
                ids.append(int(t.split(':')[1]))
        return ids

    def update_handles(self):
        self.canvas.delete("handle")
        ids = self.selected_shape_ids()
        if len(ids) != 1:
            return
        sid = ids[0]
        s = self.shapes.get(sid)
        if not s:
            return
        x, y, w, h = s['x'], s['y'], s['w'], s['h']
        points = [
            (x, y, 'nw'), (x + w/2, y, 'n'), (x + w, y, 'ne'),
            (x, y + h/2, 'w'), (x + w, y + h/2, 'e'),
            (x, y + h, 'sw'), (x + w/2, y + h, 's'), (x + w, y + h, 'se')
        ]
        size = 6
        for px, py, pos in points:
            hid = self.canvas.create_rectangle(
                px - size, py - size, px + size, py + size,
                fill="#2d7ff9", outline="white", width=1,
                tags=("handle", f"h:{sid}:{pos}")
            )
            self.canvas.tag_raise(hid)

    # ============= Event handling =============
    def canvas_item_tag(self, item):
        tags = self.canvas.gettags(item)
        for t in tags:
            if t.startswith('s:') or t.startswith('c:') or t.startswith('h:'):
                return t
        return None

    def find_item_at(self, x, y):
        items = self.canvas.find_overlapping(x, y, x, y)
        # 上にあるものほど後
        for it in reversed(items):
            t = self.canvas_item_tag(it)
            if t:
                return t
        return None

    def on_down(self, e):
        tool = self.tool.get()
        x, y = e.x, e.y
        tag = self.find_item_at(x, y)
        add = self._is_shift(e)

        if tool == 'select':
            if tag and tag.startswith('h:'):
                # リサイズ開始
                _, sid, pos = tag.split(':')
                sid = int(sid)
                s = self.shapes.get(sid)
                if s:
                    self.drag = {
                        'kind': 'resize', 'sid': sid, 'pos': pos,
                        'start': (x, y), 'orig': (s['x'], s['y'], s['w'], s['h'])
                    }
                return
            if tag:
                # 選択/移動
                if not add:
                    self.selection.clear()
                self.selection.add(tag)
                self.update_handles()
                if tag.startswith('s:'):
                    sid = int(tag.split(':')[1])
                    s = self.shapes.get(sid)
                    if s:
                        self.drag = {'kind': 'move', 'sids': self.selected_shape_ids(), 'start': (x, y)}
                elif tag.startswith('c:'):
                    # コネクタ選択のみ
                    pass
            else:
                # 余白ドラッグで範囲選択（マーキー）
                self.drag = {
                    'kind': 'marquee',
                    'start': (x, y),
                    'rect': self.canvas.create_rectangle(x, y, x, y, dash=(3, 2),
                                                         outline="#2d7ff9", fill="",
                                                         tags=("marquee",))
                }
            return

        if tool in ('rect', 'ellipse'):
            sid = self.new_id()
            s = {
                'id': sid, 'type': tool,
                'x': self.snap(x), 'y': self.snap(y), 'w': 1, 'h': 1,
                'fill': self.default_fill, 'outline': self.default_outline, 'width': self.default_width,
                'text': ''
            }
            self.shapes[sid] = s
            self.draw_shape(s)
            self.select_item(f's:{sid}')
            self.drag = {'kind': 'create', 'sid': sid, 'start': (x, y)}
            return

        if tool == 'symbol':
            sid = self.new_id()
            s = {
                'id': sid, 'type': 'symbol', 'symbol': self.symbol_kind.get(),
                'x': self.snap(x), 'y': self.snap(y), 'w': 1, 'h': 1,
                'fill': self.default_fill, 'outline': self.default_outline, 'width': self.default_width,
                'text': ''
            }
            self.shapes[sid] = s
            self.draw_shape(s)
            self.select_item(f's:{sid}')
            self.drag = {'kind': 'create', 'sid': sid, 'start': (x, y)}
            return

        if tool == 'connector':
            if tag and tag.startswith('s:'):
                sid = int(tag.split(':')[1])
                self.drag = {'kind': 'connect', 'src': sid, 'line': None}
                # プレビュー線
                sx, sy = self.anchor_point(self.shapes[sid], (x, y))
                self.drag['line'] = self.canvas.create_line(sx, sy, x, y, dash=(4, 2), fill="#2d7ff9")
            return

        if tool == 'text':
            # 図形クリックならその図形テキストを編集、空白なら何もしない（最小版）
            if tag and tag.startswith('s:'):
                sid = int(tag.split(':')[1])
                self.edit_text(sid)
            return

    def on_drag(self, e):
        if not self.drag:
            return
        x, y = e.x, e.y
        kind = self.drag['kind']
        if kind == 'create':
            sid = self.drag['sid']
            s = self.shapes[sid]
            x0, y0 = self.drag['start']
            x1, y1 = x, y
            if self.snap_to_grid:
                x1, y1 = self.snap(x1), self.snap(y1)
            s['x'] = min(x0, x1)
            s['y'] = min(y0, y1)
            s['w'] = abs(x1 - x0)
            s['h'] = abs(y1 - y0)
            self.redraw_shape(s)
            return
        if kind == 'move':
            x0, y0 = self.drag['start']
            dx, dy = x - x0, y - y0
            for sid in self.drag['sids']:
                s = self.shapes.get(sid)
                if not s:
                    continue
                nx = s['x'] + dx
                ny = s['y'] + dy
                if self.snap_to_grid:
                    nx = self.snap(nx)
                    ny = self.snap(ny)
                s['x'], s['y'] = nx, ny
                self.redraw_shape(s)
                self.update_connectors_for(sid)
            self.drag['start'] = (x, y)
            self.update_handles()
            return
        if kind == 'resize':
            sid = self.drag['sid']
            pos = self.drag['pos']
            s = self.shapes.get(sid)
            if not s:
                return
            ox, oy, ow, oh = self.drag['orig']
            x1, y1 = x, y
            if self.snap_to_grid:
                x1, y1 = self.snap(x1), self.snap(y1)
            nx, ny, nw, nh = ox, oy, ow, oh
            # 8方向ハンドル
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
            # 1px未満は0に
            nw = max(1, nw)
            nh = max(1, nh)
            s['x'], s['y'], s['w'], s['h'] = nx, ny, nw, nh
            self.redraw_shape(s)
            self.update_connectors_for(sid)
            self.update_handles()
            return
        if kind == 'connect':
            # プレビュー更新
            src = self.shapes.get(self.drag['src'])
            if not src:
                return
            p1 = self.anchor_point(src, (e.x, e.y))
            self.canvas.coords(self.drag['line'], p1[0], p1[1], e.x, e.y)
            return
        if kind == 'marquee':
            x0, y0 = self.drag['start']
            self.canvas.coords(self.drag['rect'], x0, y0, x, y)
            return

    def on_up(self, e):
        if not self.drag:
            return
        kind = self.drag['kind']
        if kind in ('create', 'move', 'resize'):
            self.push_history()
        if kind == 'connect':
            tag = self.find_item_at(e.x, e.y)
            if tag and tag.startswith('s:'):
                dst = int(tag.split(':')[1])
                if dst != self.drag['src']:
                    cid = self.new_id()
                    c = {'id': cid, 'src': self.drag['src'], 'dst': dst,
                         'arrow': 'last', 'stroke': self.default_outline, 'width': self.default_width}
                    self.connectors[cid] = c
                    if self.drag.get('line'):
                        self.canvas.delete(self.drag['line'])
                    self.draw_connector(c)
                    self.selection = {f'c:{cid}'}
                    self.push_history()
                else:
                    if self.drag.get('line'):
                        self.canvas.delete(self.drag['line'])
            else:
                if self.drag.get('line'):
                    self.canvas.delete(self.drag['line'])
        if kind == 'marquee':
            # 範囲選択を確定
            x0, y0 = self.drag['start']
            x1, y1 = e.x, e.y
            self.canvas.delete(self.drag['rect'])
            self.apply_marquee_selection(x0, y0, x1, y1, add=self._is_shift(e))
        self.drag = None

    def _is_shift(self, e) -> bool:
        """TkイベントのstateビットからShift押下を判定"""
        try:
            return bool(e.state & 0x0001)
        except Exception:
            return False

    def on_double_click(self, e):
        tag = self.find_item_at(e.x, e.y)
        if tag and tag.startswith('s:'):
            sid = int(tag.split(':')[1])
            self.edit_text(sid)

    def redraw_shape(self, s):
        # 図形本体
        if s['type'] in ('rect', 'ellipse'):
            cid = s.get('canvas_id')
            if cid and self.canvas.type(cid):
                if s['type'] == 'rect':
                    self.canvas.coords(cid, s['x'], s['y'], s['x'] + s['w'], s['y'] + s['h'])
                    self.canvas.itemconfig(cid, fill=s['fill'], outline=s['outline'], width=s['width'])
                elif s['type'] == 'ellipse':
                    self.canvas.coords(cid, s['x'], s['y'], s['x'] + s['w'], s['y'] + s['h'])
                    self.canvas.itemconfig(cid, fill=s['fill'], outline=s['outline'], width=s['width'])
            else:
                self.draw_shape(s)
        elif s['type'] == 'symbol':
            # 構成要素の座標/スタイルを更新
            self._update_symbol_items(s)
        else:
            self.draw_shape(s)
        # テキスト
        if s.get('text'):
            if not s.get('text_id') or not self.canvas.type(s['text_id']):
                cx, cy = self.shape_center(s)
                tid = self.canvas.create_text(cx, cy, text=s['text'], font=self.default_font,
                                              tags=("label", f"s:{s['id']}"))
                s['text_id'] = tid
            else:
                cx, cy = self.shape_center(s)
                self.canvas.coords(s['text_id'], cx, cy)
                self.canvas.itemconfig(s['text_id'], text=s['text'])
        else:
            if s.get('text_id') and self.canvas.type(s['text_id']):
                self.canvas.delete(s['text_id'])
                s['text_id'] = None

    def update_connectors_for(self, sid):
        # sid を含むコネクタを更新
        for c in self.connectors.values():
            if c['src'] == sid or c['dst'] == sid:
                s1 = self.shapes.get(c['src'])
                s2 = self.shapes.get(c['dst'])
                if not s1 or not s2 or not c.get('canvas_id'):
                    continue
                p1 = self.anchor_point(s1, self.shape_center(s2))
                p2 = self.anchor_point(s2, self.shape_center(s1))
                self.canvas.coords(c['canvas_id'], p1[0], p1[1], p2[0], p2[1])

    # ============= Actions =============
    def edit_text(self, sid):
        s = self.shapes.get(sid)
        if not s:
            return
        text = simpledialog.askstring("テキスト編集", "図形のテキスト:", initialvalue=s.get('text', ''))
        if text is None:
            return
        s['text'] = text
        self.redraw_shape(s)
        self.push_history()

    def delete_selection(self):
        if not self.selection:
            return
        to_delete_shapes = self.selected_shape_ids()
        to_delete_connectors = set(self.selected_connector_ids())
        # 図形に紐づくコネクタも削除
        if to_delete_shapes:
            for c in list(self.connectors.values()):
                if c['src'] in to_delete_shapes or c['dst'] in to_delete_shapes:
                    to_delete_connectors.add(c['id'])
        for cid in to_delete_connectors:
            c = self.connectors.pop(cid, None)
            if c and c.get('canvas_id'):
                self.canvas.delete(c['canvas_id'])
        for sid in to_delete_shapes:
            s = self.shapes.pop(sid, None)
            if not s:
                continue
            # タグで一括削除（複数要素にも対応）
            self.canvas.delete(f's:{sid}')
        self.selection.clear()
        self.update_handles()
        self.push_history()

    def pick_fill(self):
        color = colorchooser.askcolor(color=self.default_fill)[1]
        if not color:
            return
        self.default_fill = color
        for sid in self.selected_shape_ids():
            s = self.shapes[sid]
            s['fill'] = color
            self.redraw_shape(s)
        self.push_history()

    def pick_outline(self):
        color = colorchooser.askcolor(color=self.default_outline)[1]
        if not color:
            return
        self.default_outline = color
        # 図形とコネクタ
        for sid in self.selected_shape_ids():
            s = self.shapes[sid]
            s['outline'] = color
            self.redraw_shape(s)
        for cid in self.selected_connector_ids():
            c = self.connectors[cid]
            c['stroke'] = color
            if c.get('canvas_id'):
                self.canvas.itemconfig(c['canvas_id'], fill=color)
        self.push_history()

    def pick_width(self):
        try:
            w = simpledialog.askinteger("線の太さ", "1〜10:", minvalue=1, maxvalue=10, initialvalue=self.default_width)
        except Exception:
            w = None
        if not w:
            return
        self.default_width = int(w)
        for sid in self.selected_shape_ids():
            s = self.shapes[sid]
            s['width'] = self.default_width
            self.redraw_shape(s)
        for cid in self.selected_connector_ids():
            c = self.connectors[cid]
            c['width'] = self.default_width
            if c.get('canvas_id'):
                self.canvas.itemconfig(c['canvas_id'], width=self.default_width)
        self.push_history()

    # ============= Persistence =============
    def serialize(self):
        return {
            'shapes': [
                {k: v for k, v in s.items() if k not in ('canvas_id', 'text_id', 'item_ids')}
                for s in self.shapes.values()
            ],
            'connectors': [
                {k: v for k, v in c.items() if k not in ('canvas_id',)}
                for c in self.connectors.values()
            ],
            'next_id': self.next_id,
            'settings': {
                'default_fill': self.default_fill,
                'default_outline': self.default_outline,
                'default_width': self.default_width,
                'grid_size': self.grid_size,
                'show_grid': self.show_grid,
                'snap_to_grid': self.snap_to_grid,
            }
        }

    def deserialize(self, data):
        self.shapes.clear()
        self.connectors.clear()
        for s in data.get('shapes', []):
            s = dict(s)
            s['canvas_id'] = None
            s['text_id'] = None
            s['item_ids'] = []
            self.shapes[s['id']] = s
        for c in data.get('connectors', []):
            c = dict(c)
            c['canvas_id'] = None
            self.connectors[c['id']] = c
        self.next_id = max([1] + [s['id'] for s in self.shapes.values()] + [c['id'] for c in self.connectors.values()]) + 1
        cfg = data.get('settings', {})
        self.default_fill = cfg.get('default_fill', self.default_fill)
        self.default_outline = cfg.get('default_outline', self.default_outline)
        self.default_width = cfg.get('default_width', self.default_width)
        self.grid_size = cfg.get('grid_size', self.grid_size)
        self.show_grid = cfg.get('show_grid', self.show_grid)
        self.snap_to_grid = cfg.get('snap_to_grid', self.snap_to_grid)
        self._draw_grid()
        self.redraw_all()

    def action_new(self):
        if not self.confirm_discard_changes():
            return
        self.shapes.clear()
        self.connectors.clear()
        self.selection.clear()
        self.canvas.delete("shape")
        self.canvas.delete("connector")
        self.canvas.delete("label")
        self.canvas.delete("handle")
        self.next_id = 1
        self.history.clear()
        self.history_index = -1
        self.push_history()

    def action_open(self):
        path = filedialog.askopenfilename(filetypes=[("DrawPy JSON", "*.drawpy.json"), ("JSON", "*.json"), ("All", "*.*")])
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
        path = filedialog.asksaveasfilename(defaultextension=".drawpy.json",
                                            filetypes=[("DrawPy JSON", "*.drawpy.json"), ("JSON", "*.json")])
        if not path:
            return
        try:
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(self.serialize(), f, ensure_ascii=False, indent=2)
            self.status.set(f"保存: {path}")
        except Exception as ex:
            messagebox.showerror("保存失敗", str(ex))

    def action_export_svg(self):
        path = filedialog.asksaveasfilename(defaultextension=".svg",
                                            filetypes=[("SVG", "*.svg")])
        if not path:
            return
        try:
            svg = self.to_svg()
            with open(path, 'w', encoding='utf-8') as f:
                f.write(svg)
            self.status.set(f"SVG出力: {path}")
        except Exception as ex:
            messagebox.showerror("SVG出力失敗", str(ex))

    def confirm_discard_changes(self):
        # 最小実装では常にOK（拡張余地）
        return True

    # ============= History =============
    def push_history(self):
        snap = json.dumps(self.serialize())
        # 現在位置より先の履歴は破棄
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

    # ============= SVG Export =============
    def to_svg(self):
        """キャンバス状態をSVG文字列に変換。コネクタ矢印のマーカーは色ごとに一度だけ定義する。"""
        margin = 20
        xs, ys = [0], [0]
        for s in self.shapes.values():
            xs += [s['x'], s['x'] + s['w']]
            ys += [s['y'], s['y'] + s['h']]
        W = max(xs) + margin
        H = max(ys) + margin

        # 事前に必要なマーカー（色別）を集計
        marker_ids = {}
        def color_id(col: str) -> str:
            return col.replace('#', '').lower()
        for c in self.connectors.values():
            if c.get('arrow', 'last') in ('last', 'end', 'both'):
                stroke = c.get('stroke', self.default_outline)
                marker_ids.setdefault(stroke, f"arrow-{color_id(stroke)}")

        parts = [
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}">'
        ]
        # defs（テキストスタイル + 必要なマーカー）
        parts.append('<defs>')
        parts.append('<style>.label{font-family:Arial; font-size:12px; dominant-baseline:middle; text-anchor:middle;}</style>')
        for stroke, mid in marker_ids.items():
            parts.append(
                f'<marker id="{mid}" markerWidth="10" markerHeight="10" refX="10" refY="3" orient="auto" markerUnits="strokeWidth">'
                f'<path d="M0,0 L10,3 L0,6 Z" fill="{stroke}" />'
                '</marker>'
            )
        parts.append('</defs>')

        # 図形
        for s in self.shapes.values():
            x, y, w, h = s['x'], s['y'], s['w'], s['h']
            fill = s['fill']
            stroke = s['outline']
            sw = s['width']
            if s['type'] == 'rect':
                parts.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" fill="{fill}" stroke="{stroke}" stroke-width="{sw}" />')
            elif s['type'] == 'ellipse':
                parts.append(f'<ellipse cx="{x + w/2}" cy="{y + h/2}" rx="{w/2}" ry="{h/2}" fill="{fill}" stroke="{stroke}" stroke-width="{sw}" />')
            elif s['type'] == 'symbol':
                parts.extend(self._symbol_to_svg(s))
            if s.get('text'):
                cx, cy = self.shape_center(s)
                txt = escape_xml(s['text'])
                parts.append(f'<text class="label" x="{cx}" y="{cy}">{txt}</text>')

        # コネクタ
        for c in self.connectors.values():
            s1 = self.shapes.get(c['src'])
            s2 = self.shapes.get(c['dst'])
            if not s1 or not s2:
                continue
            p1 = self.anchor_point(s1, self.shape_center(s2))
            p2 = self.anchor_point(s2, self.shape_center(s1))
            stroke = c.get('stroke', self.default_outline)
            sw = c.get('width', self.default_width)
            arrow = c.get('arrow', 'last')
            marker = ''
            if arrow in ('last', 'end', 'both'):
                marker = f' marker-end="url(#{marker_ids[stroke]})"'
            parts.append(f'<line x1="{p1[0]}" y1="{p1[1]}" x2="{p2[0]}" y2="{p2[1]}" stroke="{stroke}" stroke-width="{sw}"{marker} />')

        parts.append('</svg>')
        return "\n".join(parts)

    # ============= Symbol rendering =============
    def _create_symbol_items(self, s):
        geoms = self._symbol_geometry(s)
        ids = []
        for g in geoms:
            if g['kind'] == 'line':
                ids.append(self.canvas.create_line(*g['coords'], width=g['width'], fill=g['stroke'],
                                                   capstyle=tk.ROUND, joinstyle=tk.ROUND,
                                                   tags=("shape", f"s:{s['id']}")))
            elif g['kind'] == 'polygon':
                ids.append(self.canvas.create_polygon(*g['coords'], outline=g['stroke'], fill=g['fill'], width=g['width'],
                                                      tags=("shape", f"s:{s['id']}")))
            elif g['kind'] == 'oval':
                ids.append(self.canvas.create_oval(*g['coords'], outline=g['stroke'], fill=g['fill'], width=g['width'],
                                                   tags=("shape", f"s:{s['id']}")))
        return ids

    def _update_symbol_items(self, s):
        geoms = self._symbol_geometry(s)
        # アイテム数が合わなければ作り直し
        if len(s.get('item_ids', [])) != len(geoms):
            # 既存を削除して描き直し
            self.canvas.delete(f"s:{s['id']}")
            s['item_ids'] = self._create_symbol_items(s)
            return
        for item_id, g in zip(s.get('item_ids', []), geoms):
            if not self.canvas.type(item_id):
                continue
            if g['kind'] == 'line':
                self.canvas.coords(item_id, *g['coords'])
                self.canvas.itemconfig(item_id, width=g['width'], fill=g['stroke'])
            elif g['kind'] == 'polygon':
                self.canvas.coords(item_id, *g['coords'])
                self.canvas.itemconfig(item_id, width=g['width'], outline=g['stroke'], fill=g['fill'])
            elif g['kind'] == 'oval':
                self.canvas.coords(item_id, *g['coords'])
                self.canvas.itemconfig(item_id, width=g['width'], outline=g['stroke'], fill=g['fill'])

    def _symbol_geometry(self, s):
        x, y, w, h = s['x'], s['y'], s['w'], s['h']
        cx, cy = x + w/2, y + h/2
        stroke = s.get('outline', self.default_outline)
        fill = s.get('fill', self.default_fill)
        # 太線はサイズに応じて動的に
        thick = max(1, int(min(w, h) * 0.25))
        thin = max(1, int(min(w, h) * 0.08))
        sym = s.get('symbol', 'pipe')
        geoms = []
        if sym == 'pipe':
            geoms.append({'kind': 'line', 'coords': [x, cy, x + w, cy], 'width': thick, 'stroke': stroke})
        elif sym == 'elbow':
            geoms.append({'kind': 'line', 'coords': [x, cy, x, y, cx, y], 'width': thick, 'stroke': stroke})
        elif sym == 'tee':
            geoms.append({'kind': 'line', 'coords': [cx, y + h, cx, y], 'width': thick, 'stroke': stroke})
            geoms.append({'kind': 'line', 'coords': [x, cy, x + w, cy], 'width': thick, 'stroke': stroke})
        elif sym == 'valve':
            geoms.append({'kind': 'line', 'coords': [x, y, x + w, y + h], 'width': thin, 'stroke': stroke})
            geoms.append({'kind': 'line', 'coords': [x + w, y, x, y + h], 'width': thin, 'stroke': stroke})
        elif sym == 'reducer':
            top = y + h * 0.2
            bottom = y + h * 0.8
            small = x + w * 0.75
            geoms.append({'kind': 'polygon',
                          'coords': [x, top, x, bottom, small, cy],
                          'width': thin, 'stroke': stroke, 'fill': fill})
        elif sym == 'flange':
            lf = x + w * 0.25
            rf = x + w * 0.75
            geoms.append({'kind': 'line', 'coords': [x, cy, x + w, cy], 'width': thin, 'stroke': stroke})
            geoms.append({'kind': 'line', 'coords': [lf, y, lf, y + h], 'width': thin, 'stroke': stroke})
            geoms.append({'kind': 'line', 'coords': [rf, y, rf, y + h], 'width': thin, 'stroke': stroke})
        elif sym == 'instrument':
            r = min(w, h) / 2
            geoms.append({'kind': 'oval', 'coords': [cx - r, cy - r, cx + r, cy + r], 'width': thin, 'stroke': stroke, 'fill': fill})
        else:
            # fallback: 直管
            geoms.append({'kind': 'line', 'coords': [x, cy, x + w, cy], 'width': thick, 'stroke': stroke})
        return geoms

    def _symbol_to_svg(self, s):
        x, y, w, h = s['x'], s['y'], s['w'], s['h']
        cx, cy = x + w/2, y + h/2
        stroke = s.get('outline', self.default_outline)
        fill = s.get('fill', self.default_fill)
        thick = max(1, int(min(w, h) * 0.25))
        thin = max(1, int(min(w, h) * 0.08))
        sym = s.get('symbol', 'pipe')
        snips = []
        if sym == 'pipe':
            snips.append(f'<line x1="{x}" y1="{cy}" x2="{x + w}" y2="{cy}" stroke="{stroke}" stroke-width="{thick}" stroke-linecap="round" />')
        elif sym == 'elbow':
            snips.append(f'<polyline points="{x},{cy} {x},{y} {cx},{y}" stroke="{stroke}" stroke-width="{thick}" fill="none" stroke-linejoin="round" stroke-linecap="round" />')
        elif sym == 'tee':
            snips.append(f'<line x1="{cx}" y1="{y + h}" x2="{cx}" y2="{y}" stroke="{stroke}" stroke-width="{thick}" stroke-linecap="round" />')
            snips.append(f'<line x1="{x}" y1="{cy}" x2="{x + w}" y2="{cy}" stroke="{stroke}" stroke-width="{thick}" stroke-linecap="round" />')
        elif sym == 'valve':
            snips.append(f'<line x1="{x}" y1="{y}" x2="{x + w}" y2="{y + h}" stroke="{stroke}" stroke-width="{thin}" />')
            snips.append(f'<line x1="{x + w}" y1="{y}" x2="{x}" y2="{y + h}" stroke="{stroke}" stroke-width="{thin}" />')
        elif sym == 'reducer':
            top = y + h * 0.2
            bottom = y + h * 0.8
            small = x + w * 0.75
            snips.append(f'<polygon points="{x},{top} {x},{bottom} {small},{cy}" stroke="{stroke}" stroke-width="{thin}" fill="{fill}" />')
        elif sym == 'flange':
            lf = x + w * 0.25
            rf = x + w * 0.75
            snips.append(f'<line x1="{x}" y1="{cy}" x2="{x + w}" y2="{cy}" stroke="{stroke}" stroke-width="{thin}" />')
            snips.append(f'<line x1="{lf}" y1="{y}" x2="{lf}" y2="{y + h}" stroke="{stroke}" stroke-width="{thin}" />')
            snips.append(f'<line x1="{rf}" y1="{y}" x2="{rf}" y2="{y + h}" stroke="{stroke}" stroke-width="{thin}" />')
        elif sym == 'instrument':
            r = min(w, h) / 2
            snips.append(f'<ellipse cx="{cx}" cy="{cy}" rx="{r}" ry="{r}" stroke="{stroke}" stroke-width="{thin}" fill="{fill}" />')
        return snips

    # ============= Marquee selection =============
    def apply_marquee_selection(self, x0, y0, x1, y1, add=False):
        if not add:
            self.selection.clear()
        left, right = sorted([x0, x1])
        top, bottom = sorted([y0, y1])
        # 図形
        for s in self.shapes.values():
            sx0, sy0 = s['x'], s['y']
            sx1, sy1 = s['x'] + s['w'], s['y'] + s['h']
            if left <= sx0 and right >= sx1 and top <= sy0 and bottom >= sy1:
                self.selection.add(f"s:{s['id']}")
        # コネクタ（線のBBoxで判定）
        for c in self.connectors.values():
            cid = c.get('canvas_id')
            if not cid:
                continue
            bbox = self.canvas.bbox(cid)
            if not bbox:
                continue
            lx, ty, rx, by = bbox
            if left <= lx and right >= rx and top <= ty and bottom >= by:
                self.selection.add(f"c:{c['id']}")
        self.update_handles()


def escape_xml(s: str) -> str:
    return (s.replace('&', '&amp;')
             .replace('<', '&lt;')
             .replace('>', '&gt;')
             .replace('"', '&quot;')
             .replace("'", '&apos;'))


def main():
    root = tk.Tk()
    DiagramEditor(root)
    root.mainloop()


if __name__ == "__main__":
    main()
