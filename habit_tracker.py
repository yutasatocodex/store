import ui
import console
import sqlite3
import datetime
import os
import contextlib
import io
import warnings
import glob
import notification
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.ticker as ticker
from matplotlib import font_manager

# =============================================================================
# 設定と定数
# =============================================================================

DB_PATH = os.path.expanduser('~/Documents/habits.db')
THEME_COLOR = '#007AFF'
DONE_COLOR = '#007AFF'
TODO_COLOR = '#E0E0E0'
DAYS_TO_SHOW = 7  # 1行に表示する日数
BTN_SIZE = 32     # チェックボタンのサイズ
BTN_GAP = 6       # ボタン間の隙間

GRID_DAYS = 30
GRID_CELL_SIZE = 22
GRID_CELL_GAP = 4
GRID_ROW_HEIGHT = 30
GRID_HEADER_HEIGHT = 24
GRID_NAME_COL_WIDTH = 140

# DB初期化用SQL
SQL_HABITS = '''
CREATE TABLE IF NOT EXISTS habits(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT UNIQUE NOT NULL,
  order_idx INTEGER DEFAULT 0
);'''

SQL_COMP = '''
CREATE TABLE IF NOT EXISTS completions(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  habit_id INTEGER NOT NULL,
  date TEXT NOT NULL,
  UNIQUE(habit_id,date)
);'''

SQL_NOTES = '''
CREATE TABLE IF NOT EXISTS notes(
    date TEXT PRIMARY KEY,
    memo TEXT
);'''

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
# DB / ロジック
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

def toggle_completion(hid, date_str):
    with db() as c:
        row = c.execute('SELECT id FROM completions WHERE habit_id=? AND date=?', (hid, date_str)).fetchone()
        if row:
            c.execute('DELETE FROM completions WHERE id=?', (row[0],))
            return False
        else:
            c.execute('INSERT INTO completions(habit_id,date) VALUES(?,?)', (hid, date_str))
            return True

def get_weekly_status(hid, end_date, days=7):
    status_list = []
    dates = [end_date - datetime.timedelta(days=i) for i in reversed(range(days))]
    with db() as c:
        for d in dates:
            done = c.execute('SELECT 1 FROM completions WHERE habit_id=? AND date=?', (hid, d.isoformat())).fetchone() is not None
            status_list.append((d, done))
    return status_list

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
            n = c.execute('SELECT COUNT(*) FROM completions WHERE habit_id=? AND date BETWEEN ? AND ?', (hid, s.isoformat(), e.isoformat())).fetchone()[0]
            data.append(n)
    return data

def completion_map(habit_ids, start_date, end_date):
    if not habit_ids:
        return set()
    placeholders = ','.join(['?'] * len(habit_ids))
    params = list(habit_ids) + [start_date.isoformat(), end_date.isoformat()]
    sql = f'SELECT habit_id, date FROM completions WHERE habit_id IN ({placeholders}) AND date BETWEEN ? AND ?'
    with db() as c:
        rows = c.execute(sql, params).fetchall()
    return {(hid, date_str) for hid, date_str in rows}

# =============================================================================
# UI: カスタムセル & グリッド
# =============================================================================

class HabitGridCell(ui.View):
    def __init__(self, habit_id, habit_name, end_date, parent_delegate):
        # Python 2/3 互換のため明示的に呼び出す
        ui.View.__init__(self)
        self.habit_id = habit_id
        self.end_date = end_date
        self.delegate = parent_delegate
        self.bg_color = 'white'
        
        # 習慣名
        self.lbl_name = ui.Label()
        self.lbl_name.text = habit_name
        self.lbl_name.font = ('HelveticaNeue', 16)
        self.lbl_name.text_color = '#333'
        self.add_subview(self.lbl_name)
        
        # ボタン生成
        self.buttons = []
        self.status_data = get_weekly_status(habit_id, end_date, DAYS_TO_SHOW)
        
        for i, (date_obj, is_done) in enumerate(self.status_data):
            btn = ui.Button()
            btn.name = str(i)
            btn.corner_radius = BTN_SIZE / 2
            btn.bg_color = DONE_COLOR if is_done else TODO_COLOR
            btn.action = self.on_tap_day
            self.add_subview(btn)
            self.buttons.append(btn)

    def layout(self):
        # 自分のサイズが変わったときに呼ばれる
        w, h = self.width, self.height
        grid_w = (BTN_SIZE * DAYS_TO_SHOW) + (BTN_GAP * (DAYS_TO_SHOW - 1))
        
        label_w = w - grid_w - 20
        self.lbl_name.frame = (15, 0, label_w, h)
        
        start_x = w - grid_w - 10
        for i, btn in enumerate(self.buttons):
            bx = start_x + i * (BTN_SIZE + BTN_GAP)
            by = (h - BTN_SIZE) / 2
            btn.frame = (bx, by, BTN_SIZE, BTN_SIZE)

    def on_tap_day(self, sender):
        idx = int(sender.name)
        target_date, current_status = self.status_data[idx]
        new_status = toggle_completion(self.habit_id, target_date.isoformat())
        
        sender.bg_color = DONE_COLOR if new_status else TODO_COLOR
        self.status_data[idx] = (target_date, new_status)
        
        def anim():
            sender.transform = ui.Transform.scale(1.2, 1.2)
        def anim_back():
            sender.transform = ui.Transform.scale(1.0, 1.0)
        ui.animate(anim, duration=0.1, completion=lambda: ui.animate(anim_back, duration=0.1))


class HeaderView(ui.View):
    def __init__(self, end_date):
        ui.View.__init__(self)
        self.bg_color = '#F5F5F5'
        self.end_date = end_date
        self.labels = []
        
        self.lbl_title = ui.Label()
        self.lbl_title.text = "Habit"
        self.lbl_title.font = ('HelveticaNeue-Bold', 12)
        self.lbl_title.text_color = '#999'
        self.add_subview(self.lbl_title)
        
        dates = [end_date - datetime.timedelta(days=i) for i in reversed(range(DAYS_TO_SHOW))]
        for d in dates:
            l = ui.Label()
            l.text = f"{d.month}/{d.day}"
            l.font = ('HelveticaNeue-Bold', 10)
            l.alignment = ui.ALIGN_CENTER
            l.text_color = '#666'
            if d == datetime.date.today():
                l.text_color = THEME_COLOR
            self.add_subview(l)
            self.labels.append(l)

    def layout(self):
        w, h = self.width, self.height
        grid_w = (BTN_SIZE * DAYS_TO_SHOW) + (BTN_GAP * (DAYS_TO_SHOW - 1))
        
        self.lbl_title.frame = (15, 0, 100, h)
        
        start_x = w - grid_w - 10
        for i, l in enumerate(self.labels):
            bx = start_x + i * (BTN_SIZE + BTN_GAP)
            l.frame = (bx, 0, BTN_SIZE, h)

# =============================================================================
# メイン画面クラス
# =============================================================================

class HabitGridDS:
    def __init__(self, tv, parent):
        self.tv = tv
        self.parent = parent
        self.items = []
        self.reload()

    def reload(self):
        self.items = list_habits()
        self.tv.reload()

    def tableview_number_of_rows(self, tv, sec):
        return len(self.items)

    def tableview_cell_for_row(self, tv, sec, row):
        cell = ui.TableViewCell()
        hid, name = self.items[row]
        # セルの中にカスタムViewを配置
        gv = HabitGridCell(hid, name, self.parent.current_date, self)
        gv.frame = cell.content_view.bounds
        gv.flex = 'WH'
        cell.content_view.add_subview(gv)
        cell.selection_style = ui.SELECTION_NONE
        return cell
        
    def tableview_can_delete(self, tv, sec, row):
        return True

    def tableview_delete(self, tv, sec, row):
        hid = self.items[row][0]
        delete_habit(hid)
        del self.items[row]
        tv.delete_rows([row])

class GraphView(ui.View):
    def __init__(self, hid, name):
        ui.View.__init__(self)
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
        for s in ['top','right','left']: ax.spines[s].set_visible(False)
        ax.grid(axis='y', linestyle='--', alpha=0.3)
        buf = io.BytesIO()
        plt.savefig(buf, format='png', bbox_inches='tight')
        plt.close()
        buf.seek(0)
        def update(): iv.image = ui.Image.from_data(buf.getvalue())
        ui.delay(update, 0)

class HabitGridCanvas(ui.View):
    def __init__(self, dates, habits, completion_set):
        ui.View.__init__(self)
        self.dates = dates
        self.habits = habits
        self.completion_set = completion_set
        self.bg_color = 'white'

    def update_data(self, dates, habits, completion_set):
        self.dates = dates
        self.habits = habits
        self.completion_set = completion_set
        self.set_needs_display()

    def draw(self):
        ui.set_color('white')
        ui.Path.rect(0, 0, self.width, self.height).fill()

        today = datetime.date.today().isoformat()

        ui.set_color('#F5F5F5')
        ui.Path.rect(0, 0, self.width, GRID_HEADER_HEIGHT).fill()
        ui.set_color('#DDDDDD')
        ui.Path.rect(0, GRID_HEADER_HEIGHT, self.width, 1).fill()

        ui.set_color('#999')
        ui.draw_string('Habit', (10, 0, GRID_NAME_COL_WIDTH - 10, GRID_HEADER_HEIGHT), font=('<system-bold>', 11), alignment=ui.ALIGN_LEFT)

        start_x = GRID_NAME_COL_WIDTH
        for idx, date_obj in enumerate(self.dates):
            date_str = date_obj.isoformat()
            x = start_x + idx * (GRID_CELL_SIZE + GRID_CELL_GAP)
            color = THEME_COLOR if date_str == today else '#666'
            ui.set_color(color)
            ui.draw_string(date_obj.strftime('%m/%d'), (x, 0, GRID_CELL_SIZE, GRID_HEADER_HEIGHT), font=('<system-bold>', 9), alignment=ui.ALIGN_CENTER)

        for row, (hid, name) in enumerate(self.habits):
            y = GRID_HEADER_HEIGHT + row * GRID_ROW_HEIGHT
            ui.set_color('#333')
            ui.draw_string(name, (10, y, GRID_NAME_COL_WIDTH - 15, GRID_ROW_HEIGHT), font=('<system>', 12), alignment=ui.ALIGN_LEFT, line_break_mode=ui.LB_TRUNCATE_TAIL)

            for col, date_obj in enumerate(self.dates):
                x = start_x + col * (GRID_CELL_SIZE + GRID_CELL_GAP)
                cell_rect = (x, y + (GRID_ROW_HEIGHT - GRID_CELL_SIZE) / 2, GRID_CELL_SIZE, GRID_CELL_SIZE)
                date_key = date_obj.isoformat()
                if (hid, date_key) in self.completion_set:
                    ui.set_color(DONE_COLOR)
                    ui.Path.rect(*cell_rect).fill()
                else:
                    ui.set_color(TODO_COLOR)
                    path = ui.Path.rect(*cell_rect)
                    path.line_width = 1
                    path.stroke()

    def touch_ended(self, touch):
        x, y = touch.location
        if x < GRID_NAME_COL_WIDTH or y < GRID_HEADER_HEIGHT:
            return
        col = int((x - GRID_NAME_COL_WIDTH) / (GRID_CELL_SIZE + GRID_CELL_GAP))
        row = int((y - GRID_HEADER_HEIGHT) / GRID_ROW_HEIGHT)
        if col < 0 or row < 0:
            return
        if row >= len(self.habits) or col >= len(self.dates):
            return
        hid, _ = self.habits[row]
        date_obj = self.dates[col]
        new_status = toggle_completion(hid, date_obj.isoformat())
        key = (hid, date_obj.isoformat())
        if new_status:
            self.completion_set.add(key)
        else:
            self.completion_set.discard(key)
        self.set_needs_display()


class HabitGrid30View(ui.View):
    def __init__(self):
        ui.View.__init__(self)
        self.bg_color = 'white'
        self.name = 'Grid'
        self.scroll = ui.ScrollView()
        self.scroll.shows_horizontal_scroll_indicator = True
        self.scroll.shows_vertical_scroll_indicator = True
        self.add_subview(self.scroll)

        self.dates = []
        self.habits = []
        self.completion_set = set()

        self.canvas = HabitGridCanvas(self.dates, self.habits, self.completion_set)
        self.scroll.add_subview(self.canvas)

        self.reload_data()

        close_btn = ui.ButtonItem(title='閉じる', action=self.close_action)
        self.left_button_items = [close_btn]

    def close_action(self, sender):
        self.close()

    def reload_data(self):
        self.habits = list_habits()
        end_date = datetime.date.today()
        start_date = end_date - datetime.timedelta(days=GRID_DAYS - 1)
        self.dates = [start_date + datetime.timedelta(days=i) for i in range(GRID_DAYS)]
        habit_ids = [hid for hid, _ in self.habits]
        self.completion_set = completion_map(habit_ids, start_date, end_date)
        self.canvas.update_data(self.dates, self.habits, self.completion_set)
        self.update_layout_sizes()

    def update_layout_sizes(self):
        rows = max(len(self.habits), 1)
        width = GRID_NAME_COL_WIDTH + GRID_DAYS * (GRID_CELL_SIZE + GRID_CELL_GAP)
        height = GRID_HEADER_HEIGHT + rows * GRID_ROW_HEIGHT
        self.canvas.frame = (0, 0, width, height)
        self.scroll.content_size = (width, height)

    def layout(self):
        self.scroll.frame = self.bounds


class HabitTrackerGrid(ui.View):
    def __init__(self):
        ui.View.__init__(self)
        self.bg_color = 'white'
        self.current_date = datetime.date.today()
        
        self.header_height = 30
        
        # 1. ヘッダー
        self.header = HeaderView(self.current_date)
        self.add_subview(self.header)
        
        # 2. テーブル
        self.tv = ui.TableView()
        self.tv.row_height = 50
        self.ds = HabitGridDS(self.tv, self)
        self.tv.data_source = self.ds
        self.tv.delegate = self.ds
        self.add_subview(self.tv)
        
        # UI設定
        self.update_title()
        self.right_button_items = [
            ui.ButtonItem(image=ui.Image.named('iob-plus-24'), action=self.add_habit_action),
            ui.ButtonItem(image=ui.Image.named('iob-stats-bars-24'), action=self.show_menu)
        ]
        
        self.update_memo_footer()

    def update_title(self):
        d = self.current_date
        self.name = f"習慣トラッカー ({d.month}/{d.day})"
        
    def layout(self):
        # layoutが呼ばれたら、サブビューのフレームだけ調整する
        # ビューの削除・再生成は行わない
        if hasattr(self, 'header'):
            self.header.frame = (0, 0, self.width, self.header_height)
        
        if hasattr(self, 'tv'):
            self.tv.frame = (0, self.header_height, self.width, self.height - self.header_height)

    # --- アクション ---
    def add_habit_action(self, sender):
        def add(sender):
            t = sender.superview['tf'].text.strip()
            if t: add_habit(t); self.ds.reload()
            sender.superview.close()
        v = ui.View(bg_color='white', frame=(0,0,300,100))
        v.name = '新規追加'
        tf = ui.TextField(frame=(10,10,280,40), name='tf', placeholder='習慣名')
        btn = ui.Button(title='追加', frame=(10,60,280,30), bg_color=THEME_COLOR, tint_color='white')
        btn.action = add
        v.add_subview(tf); v.add_subview(btn); v.present('sheet')

    def show_menu(self, sender):
        options = ['Grid', 'Stats']
        tv = ui.TableView(frame=(0, 0, 200, 120))
        tv.data_source = ui.ListDataSource(options)
        tv.delegate = tv.data_source

        def handle_selection(sender):
            row = sender.selected_row
            if row < 0:
                return
            if options[row] == 'Grid':
                grid_view = HabitGrid30View()
                grid_view.present('fullscreen')
            else:
                self.show_stats_menu()
        tv.data_source.action = handle_selection
        tv.name = 'メニュー'
        tv.present('popover')

    def show_stats_menu(self):
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

    # --- メモ機能 ---
    def update_memo_footer(self):
        note = load_note(self.current_date.isoformat())
        h = 100 if note else 60
        f = ui.View(frame=(0,0,self.width, h), bg_color='#FAFAFA')
        
        lbl = ui.Label(frame=(15, 5, 200, 20), text='今日のメモ:', font=('<system-bold>',12), text_color='#999')
        f.add_subview(lbl)
        
        txt = ui.Label(frame=(15, 25, self.width-30, h-30))
        txt.text = note if note else "(タップして入力)"
        txt.font = ('<system>', 14)
        txt.number_of_lines = 0
        txt.text_color = '#333' if note else '#CCC'
        f.add_subview(txt)
        
        btn = ui.Button(frame=f.bounds)
        btn.action = self.edit_note
        f.add_subview(btn)
        
        self.tv.table_footer_view = f

    def edit_note(self, sender):
        d = self.current_date.isoformat()
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

if __name__ == '__main__':
    init_db()
    v = HabitTrackerGrid()
    v.present('fullscreen')
