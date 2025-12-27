import ui
import console
import sqlite3
import datetime
import os
import contextlib
import io
import warnings
import glob
import notification  # 将来のリマインド用に残す
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.ticker as ticker
from matplotlib import font_manager

# =============================================================================
# 設定・定数
# =============================================================================

DB_PATH = os.path.expanduser('~/Documents/habits.db')

THEME_COLOR = '#007AFF'
DONE_COLOR = '#007AFF'
TODO_COLOR = '#E0E0E0'
TODAY_HIGHLIGHT = '#E3F2FD'  # 今日の列の背景色

BTN_SIZE = 30
BTN_GAP = 4
RIGHT_MARGIN = 10
LABEL_MIN_W = 80
MAX_DAYS = 30

SQL_HABITS = 'CREATE TABLE IF NOT EXISTS habits(id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE NOT NULL, order_idx INTEGER DEFAULT 0);'
SQL_COMP   = 'CREATE TABLE IF NOT EXISTS completions(id INTEGER PRIMARY KEY AUTOINCREMENT, habit_id INTEGER NOT NULL, date TEXT NOT NULL, UNIQUE(habit_id,date));'
SQL_NOTES  = 'CREATE TABLE IF NOT EXISTS notes(date TEXT PRIMARY KEY, memo TEXT);'

# =============================================================================
# フォント設定
# =============================================================================

jp_prop = None
for fp in glob.glob(os.path.expanduser('~/Documents/NotoSansCJKjp-Regular.*')):
    try:
        font_manager.fontManager.addfont(fp)
        jp_prop = font_manager.FontProperties(fname=fp)
        plt.rcParams['font.family'] = jp_prop.get_name()
        break
    except Exception:
        pass
warnings.filterwarnings('ignore', message='Glyph .* missing from current font')

# =============================================================================
# DB / ロジック
# =============================================================================


@contextlib.contextmanager
def db():
    conn = sqlite3.connect(DB_PATH)
    try:
        yield conn
    finally:
        conn.commit()
        conn.close()


def init_db():
    with db() as c:
        c.execute(SQL_HABITS)
        c.execute(SQL_COMP)
        c.execute(SQL_NOTES)


def list_habits():
    with db() as c:
        return c.execute('SELECT id,name FROM habits ORDER BY order_idx').fetchall()


def add_habit(name):
    with db() as c:
        max_idx = c.execute('SELECT COALESCE(MAX(order_idx),-1)+1 FROM habits').fetchone()[0]
        try:
            c.execute('INSERT INTO habits(name,order_idx) VALUES(?,?)', (name, max_idx))
        except sqlite3.IntegrityError:
            console.hud_alert('重複しています', 'error')


def delete_habit(hid):
    with db() as c:
        c.execute('DELETE FROM completions WHERE habit_id=?', (hid,))
        c.execute('DELETE FROM habits WHERE id=?', (hid,))


def save_order(id_list):
    with db() as c:
        for idx, hid in enumerate(id_list):
            c.execute('UPDATE habits SET order_idx=? WHERE id=?', (idx, hid))


def toggle_completion(hid, date_str):
    with db() as c:
        row = c.execute('SELECT id FROM completions WHERE habit_id=? AND date=?', (hid, date_str)).fetchone()
        if row:
            c.execute('DELETE FROM completions WHERE id=?', (row[0],))
            return False
        else:
            try:
                c.execute('INSERT INTO completions(habit_id,date) VALUES(?,?)', (hid, date_str))
            except sqlite3.IntegrityError:
                # 連打などで競合しても落ちない
                pass
            return True


def get_status_dict(hid, end_date, days):
    """
    end_date: 表示上の右端(最新日)
    days: 最大保持日数
    ※ BETWEEN は両端含むので oldest は (days-1) が正しい
    """
    oldest_date = end_date - datetime.timedelta(days=days-1)
    res = {}
    with db() as c:
        rows = c.execute(
            'SELECT date FROM completions WHERE habit_id=? AND date BETWEEN ? AND ?',
            (hid, oldest_date.isoformat(), end_date.isoformat())
        ).fetchall()
        for r in rows:
            res[r[0]] = True
    return res


def load_note(day_str):
    with db() as c:
        row = c.execute('SELECT memo FROM notes WHERE date=?', (day_str,)).fetchone()
        return row[0] if row else ''


def save_note(day_str, text):
    with db() as c:
        if not text.strip():
            c.execute('DELETE FROM notes WHERE date=?', (day_str,))
        else:
            c.execute('INSERT OR REPLACE INTO notes(date,memo) VALUES(?,?)', (day_str, text))


def weekly_counts(hid, weeks=8):
    today = datetime.date.today()
    mon = today - datetime.timedelta(days=today.weekday())
    data = []
    with db() as c:
        for w in reversed(range(weeks)):
            s = mon - datetime.timedelta(days=7*w)
            e = s + datetime.timedelta(days=6)
            n = c.execute(
                'SELECT COUNT(*) FROM completions WHERE habit_id=? AND date BETWEEN ? AND ?',
                (hid, s.isoformat(), e.isoformat())
            ).fetchone()[0]
            data.append(n)
    return data

# =============================================================================
# UI: グリッドの共通計算(ここを親で一回だけ計算し、全員で共有する)
# =============================================================================


def calc_grid_metrics(width):
    avail_w = width - LABEL_MIN_W - RIGHT_MARGIN
    if avail_w <= 0:
        return 0, [], 0, width

    unit = BTN_SIZE + BTN_GAP
    count = int(avail_w // unit)
    count = min(count, MAX_DAYS)
    count = max(count, 3)

    grid_real_w = count * unit - BTN_GAP
    start_x = width - RIGHT_MARGIN - grid_real_w

    x_positions = [start_x + i * unit for i in range(count)]
    return count, x_positions, start_x, width


def safe_image(name):
    try:
        return ui.Image.named(name)
    except Exception:
        return None

# =============================================================================
# UI: カスタムビュー
# =============================================================================


class HabitGridCell(ui.View):
    def __init__(self, habit_id, habit_name, current_view_date, parent):
        super().__init__()
        self.habit_id = habit_id
        self.current_view_date = current_view_date
        self.parent = parent
        self.bg_color = 'white'

        # 習慣名
        self.lbl_name = ui.Label()
        self.lbl_name.text = habit_name
        self.lbl_name.font = ('HelveticaNeue', 16)
        self.lbl_name.text_color = '#333'
        self.lbl_name.alignment = ui.ALIGN_LEFT
        self.add_subview(self.lbl_name)

        # 列背景(今日ハイライト用)
        self.col_bgs = []
        for _ in range(MAX_DAYS):
            bg = ui.View()
            bg.hidden = True
            bg.bg_color = 'white'
            self.add_subview(bg)  # ボタンより先に add して下に敷く
            self.col_bgs.append(bg)

        # ボタン
        self.buttons = []
        for _ in range(MAX_DAYS):
            btn = ui.Button()
            btn.corner_radius = BTN_SIZE / 2
            btn.bg_color = TODO_COLOR
            btn.action = self.on_tap
            btn.hidden = True
            self.add_subview(btn)
            self.buttons.append(btn)

        self.status_map = get_status_dict(habit_id, current_view_date, MAX_DAYS)

    def layout(self):
        w, h = self.width, self.height
        if w < 50:
            return

        count, x_pos_list, start_x, base_w = self.parent.get_grid_metrics()
        if count <= 0:
            return

        # 親の幅で計算したXをセル内に合わせる(微差吸収)
        dx = (self.width - base_w) / 2.0
        start_x_local = start_x + dx

        # ラベル
        self.lbl_name.frame = (10, 0, start_x_local - 15, h)

        oldest_view_date = self.current_view_date - datetime.timedelta(days=count-1)
        today = datetime.date.today()

        for i, x in enumerate(x_pos_list):
            x_local = x + dx
            d = oldest_view_date + datetime.timedelta(days=i)

            bg = self.col_bgs[i]
            bg.hidden = False
            bg.frame = (x_local, 0, BTN_SIZE, h)
            bg.bg_color = TODAY_HIGHLIGHT if d == today else 'white'

            btn = self.buttons[i]
            btn.hidden = False
            btn.frame = (x_local, (h - BTN_SIZE)/2, BTN_SIZE, BTN_SIZE)
            btn.name = d.isoformat()

            is_done = self.status_map.get(d.isoformat(), False)
            btn.bg_color = DONE_COLOR if is_done else TODO_COLOR

        for k in range(count, MAX_DAYS):
            self.col_bgs[k].hidden = True
            self.buttons[k].hidden = True

    def on_tap(self, sender):
        d_str = sender.name
        new_state = toggle_completion(self.habit_id, d_str)

        if new_state:
            self.status_map[d_str] = True
            sender.bg_color = DONE_COLOR
        else:
            self.status_map.pop(d_str, None)
            sender.bg_color = TODO_COLOR

        def anim():
            sender.transform = ui.Transform.scale(1.2, 1.2)
        def anim_back():
            sender.transform = ui.Transform.scale(1.0, 1.0)
        ui.animate(anim, duration=0.1, completion=lambda: ui.animate(anim_back, duration=0.1))


class HeaderView(ui.View):
    def __init__(self, current_view_date, parent):
        super().__init__()
        self.bg_color = '#FAFAFA'
        self.current_view_date = current_view_date
        self.parent = parent

        self.lbl_title = ui.Label()
        self.lbl_title.text = "HABIT"
        self.lbl_title.font = ('HelveticaNeue-Bold', 12)
        self.lbl_title.text_color = '#999'
        self.add_subview(self.lbl_title)

        self.w_names = ['月','火','水','木','金','土','日']

        # 列背景
        self.col_bgs = []
        for _ in range(MAX_DAYS):
            bg = ui.View()
            bg.hidden = True
            bg.bg_color = 'white'
            self.add_subview(bg)
            self.col_bgs.append(bg)

        # 日付ラベル
        self.labels = []
        for _ in range(MAX_DAYS):
            l = ui.Label()
            l.font = ('HelveticaNeue-Bold', 10)
            l.alignment = ui.ALIGN_CENTER
            l.number_of_lines = 2
            l.hidden = True
            self.add_subview(l)
            self.labels.append(l)

    def set_date(self, new_date):
        self.current_view_date = new_date
        self.set_needs_layout()

    def layout(self):
        w, h = self.width, self.height
        if w < 50:
            return

        count, x_pos_list, start_x, base_w = self.parent.get_grid_metrics()
        if count <= 0:
            return

        dx = (self.width - base_w) / 2.0
        start_x_local = start_x + dx

        self.lbl_title.frame = (10, 0, start_x_local - 15, h)

        oldest_view_date = self.current_view_date - datetime.timedelta(days=count-1)
        today = datetime.date.today()

        for i, x in enumerate(x_pos_list):
            x_local = x + dx
            d = oldest_view_date + datetime.timedelta(days=i)
            w_idx = d.weekday()

            bg = self.col_bgs[i]
            bg.hidden = False
            bg.frame = (x_local, 0, BTN_SIZE, h)
            bg.bg_color = TODAY_HIGHLIGHT if d == today else 'white'

            l = self.labels[i]
            l.hidden = False
            l.frame = (x_local, 0, BTN_SIZE, h)
            l.text = f"{d.month}/{d.day}\n{self.w_names[w_idx]}"

            if d == today:
                l.text_color = THEME_COLOR
                l.font = ('HelveticaNeue-Bold', 11)
            elif w_idx == 5:
                l.text_color = '#007AFF'
                l.font = ('HelveticaNeue-Bold', 10)
            elif w_idx == 6:
                l.text_color = '#FF3B30'
                l.font = ('HelveticaNeue-Bold', 10)
            else:
                l.text_color = '#666'
                l.font = ('HelveticaNeue', 10)

        for k in range(count, MAX_DAYS):
            self.col_bgs[k].hidden = True
            self.labels[k].hidden = True

# =============================================================================
# メイン画面:テーブルDS
# =============================================================================


class HabitGridDS:
    def __init__(self, tv, parent):
        self.tv = tv
        self.parent = parent
        self.items = list_habits()

    def reload_data(self):
        self.items = list_habits()
        self.tv.reload()

    def tableview_number_of_rows(self, tv, sec):
        return len(self.items)

    def tableview_cell_for_row(self, tv, sec, row):
        cell = ui.TableViewCell()

        # 再利用や二重積みを防ぐ
        for sv in list(cell.content_view.subviews):
            sv.remove_from_superview()

        hid, name = self.items[row]
        gv = HabitGridCell(hid, name, self.parent.current_view_date, self.parent)
        gv.flex = 'WH'
        gv.frame = cell.content_view.bounds
        cell.content_view.add_subview(gv)

        cell.selection_style = 0
        return cell

    def tableview_can_delete(self, tv, sec, row):
        return True

    def tableview_delete(self, tv, sec, row):
        hid = self.items[row][0]
        delete_habit(hid)
        del self.items[row]
        tv.delete_rows([row])

    def tableview_can_move(self, tv, sec, row):
        return True

    def tableview_move_row(self, tv, fs, fr, ts, tr):
        item = self.items.pop(fr)
        self.items.insert(tr, item)
        save_order([x[0] for x in self.items])

# =============================================================================
# 統計ビュー
# =============================================================================


class GraphView(ui.View):
    def __init__(self, hid, name):
        super().__init__()
        self.bg_color = 'white'
        iv = ui.ImageView(frame=self.bounds, flex='WH')
        iv.content_mode = ui.CONTENT_SCALE_ASPECT_FIT
        self.add_subview(iv)
        ui.in_background(lambda: self._draw(hid, name, iv))

    def _draw(self, hid, name, iv):
        WEEKS = 8
        counts = weekly_counts(hid, WEEKS)

        plt.figure(figsize=(5, 3), dpi=150)
        ax = plt.gca()

        cmap = plt.get_cmap('plasma')
        colors = cmap(np.linspace(0.2, 0.8, WEEKS))
        ax.bar(range(WEEKS), counts, color=colors)

        font = jp_prop if jp_prop else None
        ax.set_title(name, fontproperties=font)
        ax.set_ylim(0, 7.5)
        ax.yaxis.set_major_locator(ticker.MaxNLocator(integer=True))
        for s in ['top','right','left']:
            ax.spines[s].set_visible(False)
        ax.grid(axis='y', linestyle='--', alpha=0.3)

        buf = io.BytesIO()
        plt.savefig(buf, format='png', bbox_inches='tight')
        plt.close()
        buf.seek(0)

        def update():
            iv.image = ui.Image.from_data(buf.getvalue())
        ui.delay(update, 0)

# =============================================================================
# HabitTrackerGrid
# =============================================================================


class HabitTrackerGrid(ui.View):
    def __init__(self):
        super().__init__()
        self.bg_color = 'white'
        self.current_view_date = datetime.date.today()
        self.header_height = 44

        # グリッド計算結果(横向きズレ対策の核)
        self._grid_count = 0
        self._grid_x = []
        self._grid_start_x = 0
        self._grid_base_w = 0

        self._last_size = (0, 0)
        self._resize_pending = False

        # ヘッダー
        self.header = HeaderView(self.current_view_date, self)
        self.header.frame = (0, 0, self.width, self.header_height)
        self.header.flex = 'W'
        self.add_subview(self.header)

        # テーブル
        self.tv = ui.TableView()
        self.tv.frame = (0, self.header_height, self.width, self.height - self.header_height)
        self.tv.flex = 'WH'
        self.tv.row_height = 50
        self.ds = HabitGridDS(self.tv, self)
        self.tv.data_source = self.ds
        self.tv.delegate = self.ds
        self.add_subview(self.tv)

        # 初回グリッド計算
        self.update_grid_metrics()

        self.update_nav_bar()
        self.update_memo_footer()

    def get_grid_metrics(self):
        # ヘッダー/セルが同じものを参照する
        return self._grid_count, self._grid_x, self._grid_start_x, self._grid_base_w

    def update_grid_metrics(self):
        self._grid_count, self._grid_x, self._grid_start_x, self._grid_base_w = calc_grid_metrics(self.width)

    def layout(self):
        # 回転時などサイズが変わったら、layout内でreloadせず「次のループ」で安全更新
        sz = (int(self.width), int(self.height))
        if sz != self._last_size:
            self._last_size = sz
            if not self._resize_pending:
                self._resize_pending = True
                ui.delay(self._handle_resize, 0)

    def _handle_resize(self):
        self._resize_pending = False
        self.update_grid_metrics()
        self.header.set_needs_layout()
        self.ds.reload_data()
        self.update_memo_footer()

    def update_title(self):
        d = self.current_view_date
        self.name = f"{d.year}/{d.month}/{d.day}"

    def update_nav_bar(self):
        self.update_title()

        # アイコン(無ければタイトル運用にフォールバック)
        img_prev = safe_image('iob-chevron-left-24')
        img_next = safe_image('iob-chevron-right-24')
        img_plus = safe_image('iob-plus-24')
        img_stat = safe_image('iob-stats-bars-24')

        prev_btn = ui.ButtonItem(image=img_prev, title=('◀︎' if img_prev is None else None), action=self.on_prev_day)
        next_btn = ui.ButtonItem(image=img_next, title=('▶︎' if img_next is None else None), action=self.on_next_day)

        back7_btn = ui.ButtonItem(title='◀︎7', action=self.on_prev_week)
        fwd7_btn  = ui.ButtonItem(title='7▶︎', action=self.on_next_week)
        today_btn = ui.ButtonItem(title='今日', action=self.on_today)
        jump_btn  = ui.ButtonItem(title='移動', action=self.show_jump)

        if self.tv.editing:
            done_btn = ui.ButtonItem(title='完了', action=self.toggle_edit)
            self.right_button_items = [done_btn]
            self.left_button_items = []
        else:
            add_btn  = ui.ButtonItem(image=img_plus, title=('+' if img_plus is None else None), action=self.add_habit_action)
            menu_btn = ui.ButtonItem(image=img_stat, title=('統計' if img_stat is None else None), action=self.show_menu)
            edit_btn = ui.ButtonItem(title='編集', action=self.toggle_edit)

            # 「遡り」を左側に集約
            self.left_button_items  = [back7_btn, prev_btn, next_btn, fwd7_btn, today_btn]
            self.right_button_items = [edit_btn, menu_btn, jump_btn, add_btn]

    def on_prev_day(self, sender):
        self.current_view_date -= datetime.timedelta(days=1)
        self.refresh_all_views()

    def on_next_day(self, sender):
        self.current_view_date += datetime.timedelta(days=1)
        self.refresh_all_views()

    def on_prev_week(self, sender):
        self.current_view_date -= datetime.timedelta(days=7)
        self.refresh_all_views()

    def on_next_week(self, sender):
        self.current_view_date += datetime.timedelta(days=7)
        self.refresh_all_views()

    def on_today(self, sender):
        self.current_view_date = datetime.date.today()
        self.refresh_all_views()

    def refresh_all_views(self):
        self.update_title()
        self.header.set_date(self.current_view_date)
        self.ds.reload_data()
        self.update_memo_footer()

    def toggle_edit(self, sender):
        self.tv.set_editing(not self.tv.editing, True)
        self.update_nav_bar()

    def add_habit_action(self, sender):
        def add(sender):
            t = sender.superview['tf'].text.strip()
            if t:
                add_habit(t)
                self.ds.reload_data()
            sender.superview.close()

        v = ui.View(bg_color='white', frame=(0,0,300,100))
        v.name = '新規追加'
        tf = ui.TextField(frame=(10,10,280,40), name='tf', placeholder='習慣名')
        btn = ui.Button(title='追加', frame=(10,60,280,30), bg_color=THEME_COLOR, tint_color='white')
        btn.action = add
        v.add_subview(tf); v.add_subview(btn)
        v.present('sheet')

    def show_menu(self, sender):
        habits = list_habits()
        if not habits:
            return

        def show_stat(sender):
            row = sender.selected_row
            if row >= 0:
                hid, name = habits[row]
                GraphView(hid, name).present('sheet')

        tv = ui.TableView(frame=(0,0,240,300))
        tv.data_source = ui.ListDataSource([h[1] for h in habits])
        tv.delegate = tv.data_source
        tv.data_source.action = show_stat
        tv.name = '統計を表示'
        tv.present('popover')

    def show_jump(self, sender):
        v = ui.View(bg_color='white', frame=(0,0,340,260))
        v.name = '日付へ移動'

        dp = ui.DatePicker(frame=(0,0,340,180))
        dp.mode = ui.DATE_PICKER_MODE_DATE
        dp.date = datetime.datetime.combine(self.current_view_date, datetime.time())
        v.add_subview(dp)

        def go_today(_):
            dp.date = datetime.datetime.combine(datetime.date.today(), datetime.time())

        def go_minus30(_):
            d = dp.date.date() - datetime.timedelta(days=30)
            dp.date = datetime.datetime.combine(d, datetime.time())

        def apply(_):
            self.current_view_date = dp.date.date()
            v.close()
            self.refresh_all_views()

        btn_today = ui.Button(title='今日', frame=(10,190,100,32))
        btn_today.bg_color = THEME_COLOR
        btn_today.tint_color = 'white'
        btn_today.corner_radius = 6
        btn_today.action = go_today
        v.add_subview(btn_today)

        btn_m30 = ui.Button(title='-30日', frame=(120,190,100,32))
        btn_m30.bg_color = '#999'
        btn_m30.tint_color = 'white'
        btn_m30.corner_radius = 6
        btn_m30.action = go_minus30
        v.add_subview(btn_m30)

        btn_apply = ui.Button(title='移動', frame=(230,190,100,32))
        btn_apply.bg_color = THEME_COLOR
        btn_apply.tint_color = 'white'
        btn_apply.corner_radius = 6
        btn_apply.action = apply
        v.add_subview(btn_apply)

        v.present('sheet')

    def update_memo_footer(self):
        d_str = self.current_view_date.isoformat()
        note = load_note(d_str)

        h = 100 if note else 60
        f = ui.View(frame=(0,0,self.width,h), bg_color='#FAFAFA')

        lbl = ui.Label(frame=(15, 5, self.width-30, 20),
                       text=f'{d_str} のメモ:',
                       font=('<system-bold>',12),
                       text_color='#999')
        f.add_subview(lbl)

        txt = ui.Label(frame=(15, 25, self.width-30, h-30))
        txt.text = note if note else "(タップして入力)"
        txt.font = ('<system>', 14)
        txt.number_of_lines = 0
        txt.text_color = '#333' if note else '#CCC'
        f.add_subview(txt)

        btn = ui.Button(frame=f.bounds)
        btn.action = self.edit_note
        f.add_subview(btn)

        self.tv.table_footer_view = f

    def edit_note(self, sender):
        d = self.current_view_date.isoformat()
        cur = load_note(d)

        def save(s):
            save_note(d, s.superview['tv'].text)
            s.superview.close()
            self.update_memo_footer()

        v = ui.View(bg_color='white', frame=(0,0,320,200))
        v.name = 'メモ編集'
        tv = ui.TextView(frame=(10,10,300,140), name='tv', text=cur, font=('<system>',16))
        btn = ui.Button(title='保存', frame=(10,160,300,30), bg_color=THEME_COLOR, tint_color='white')
        btn.action = save
        v.add_subview(tv); v.add_subview(btn)
        v.present('sheet')

# =============================================================================
# 起動
# =============================================================================


if __name__ == '__main__':
    init_db()
    v = HabitTrackerGrid()
    v.present('fullscreen')
