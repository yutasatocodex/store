import ui
import sqlite3
import datetime
import os
import contextlib

DB_PATH = os.path.expanduser('~/Documents/habits.db')

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
  value INTEGER NOT NULL DEFAULT 1,
  UNIQUE(habit_id,date)
);'''

SQL_NOTES = 'CREATE TABLE IF NOT EXISTS notes(date TEXT PRIMARY KEY,memo TEXT);'

@contextlib.contextmanager
def db():
    conn = sqlite3.connect(DB_PATH)
    try:
        yield conn
    finally:
        conn.commit(); conn.close()

def init_db():
    with db() as c:
        c.execute(SQL_HABITS)
        c.execute(SQL_COMP)
        c.execute(SQL_NOTES)
        # add value column if missing
        cols = [r[1] for r in c.execute("PRAGMA table_info(completions)")]
        if 'value' not in cols:
            c.execute('ALTER TABLE completions ADD COLUMN value INTEGER NOT NULL DEFAULT 1')

def list_habits():
    with db() as c:
        return c.execute('SELECT id,name FROM habits ORDER BY order_idx').fetchall()

def add_habit(name):
    with db() as c:
        idx=c.execute('SELECT COALESCE(MAX(order_idx),-1)+1 FROM habits').fetchone()[0]
        c.execute('INSERT OR IGNORE INTO habits(name,order_idx) VALUES(?,?)',(name,idx))

def set_value(hid,date,val):
    with db() as c:
        c.execute('INSERT OR REPLACE INTO completions(habit_id,date,value) VALUES(?,?,?)',(hid,date,val))

def get_value(hid,date):
    with db() as c:
        row=c.execute('SELECT value FROM completions WHERE habit_id=? AND date=?',(hid,date)).fetchone()
        return row[0] if row else 0

def cycle_value(hid,date,maxv=5):
    v=get_value(hid,date)
    nv=(v+1)%(maxv+1)
    if nv==0:
        with db() as c:
            c.execute('DELETE FROM completions WHERE habit_id=? AND date=?',(hid,date))
    else:
        set_value(hid,date,nv)

def weekly_avgs(hid,weeks=8,maxv=5):
    today=datetime.date.today(); mon=today-datetime.timedelta(days=today.weekday())
    data=[]
    with db() as c:
        for w in reversed(range(weeks)):
            s=mon-datetime.timedelta(days=7*w); e=s+datetime.timedelta(days=6)
            total=c.execute('SELECT SUM(value) FROM completions WHERE habit_id=? AND date BETWEEN ? AND ?',
                            (hid,s.isoformat(),e.isoformat())).fetchone()[0] or 0
            data.append(total/7.0)
    return data

class HabitDS:
    def __init__(self,tv,parent):
        self.tv=tv; self.parent=parent
        self.reload()
    def reload(self):
        self.items=list_habits(); self.tv.reload()
        if hasattr(self.parent,'ds'): self.parent.position_memo_label()
    def tableview_number_of_rows(self,tv,sec):
        return len(self.items)
    def tableview_cell_for_row(self,tv,sec,row):
        hid,name=self.items[row]
        cell=ui.TableViewCell('subtitle')
        cell.text_label.text=name
        v=get_value(hid,self.parent.current_date.isoformat())
        cell.detail_text_label.text='\u2605'*v if v else ''
        cell.accessory_type='checkmark' if v else 'none'
        return cell
    def tableview_did_select(self,tv,sec,row):
        hid,_=self.items[row]
        cycle_value(hid,self.parent.current_date.isoformat())
        self.reload()

# ... (NotesView and other classes from original script would follow)
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.ticker as ticker
import io

class GraphView(ui.View):
    def __init__(self, hid, name):
        super().__init__(bg_color='white')
        iv = ui.ImageView(frame=self.bounds, flex='WH')
        iv.content_mode = ui.CONTENT_SCALE_ASPECT_FIT
        self.add_subview(iv)
        buf = io.BytesIO()
        self._draw_chart(hid, name, buf)
        iv.image = ui.Image.from_data(buf.getvalue())

    def _draw_chart(self, hid, name, buf):
        WEEKS = 8
        today = datetime.date.today()
        mon   = today - datetime.timedelta(days=today.weekday())
        avgs = weekly_avgs(hid, weeks=WEEKS)
        mondays = [mon - datetime.timedelta(weeks=w) for w in reversed(range(WEEKS))]
        x = np.arange(WEEKS)
        plt.figure(figsize=(4,3), dpi=150)
        ax = plt.gca()
        cmap   = plt.get_cmap('plasma')
        colors = cmap(np.linspace(0,1,WEEKS))
        ax.bar(x, avgs, width=1.0, color=colors, edgecolor='none')
        ax.set_title(name, fontsize=16, weight='bold', pad=12)
        ax.set_xlabel('WEEKS')
        ax.set_ylabel('AVG')
        labels=[f'{d.month}/{d.day}' for d in mondays]
        ax.set_xticks(x)
        ax.set_xticklabels(labels)
        ax.set_ylim(0,5)
        ax.yaxis.set_major_locator(ticker.MaxNLocator(integer=True))
        ax.grid(axis='y', linestyle='-', linewidth=0.5, alpha=0.25)
        plt.tight_layout(pad=0.6)
        plt.savefig(buf, format='png')
        plt.close()
        buf.seek(0)

class StatsList(ui.View):
    def __init__(self):
        super().__init__(bg_color='white'); self.name='Stats'
        tv=ui.TableView(frame=self.bounds,flex='WH'); self.add_subview(tv)
        self.items=list_habits();
        tv.data_source=tv.delegate=self
    def tableview_number_of_rows(self,tv,sec): return len(self.items)
    def tableview_cell_for_row(self,tv,sec,row):
        cell=ui.TableViewCell(); cell.text_label.text=self.items[row][1]; return cell
    def tableview_did_select(self,tv,sec,row):
        GraphView(*self.items[row]).present('sheet')

class NotesView(ui.View):
    def __init__(self,date_str,initial,save_cb):
        super().__init__(bg_color='white')
        self.date_str=date_str; self.save_cb=save_cb
        self.tv=ui.TextView(frame=self.bounds,flex='WH',font=('Helvetica Neue',16),text=initial)
        self.add_subview(self.tv)
        btn=ui.Button(title='Done',frame=(self.width-70,4,60,32))
        btn.action=lambda s:self.dismiss()
        acc=ui.View(frame=(0,0,self.width,40),bg_color='#eee');acc.add_subview(btn)
        self.tv.input_accessory_view=acc
    def dismiss(self):
        self.save_cb(self.date_str,self.tv.text)
        self.close()

class HabitTracker(ui.View):
    MEMO_H=240
    ROW_H=44
    def __init__(self):
        super().__init__(bg_color='white')
        self.current_date=datetime.date.today()
        self.update_title()
        self.tv=ui.TableView(frame=self.bounds,flex='WH')
        self.add_subview(self.tv)
        self.ds=HabitDS(self.tv,self)
        self.tv.data_source=self.tv.delegate=self.ds
        self.tv.allows_selection_during_editing=True
        self.memo_label=ui.Label(frame=(0,0,self.width,self.MEMO_H),flex='W',font=('Helvetica Neue',14),number_of_lines=0)
        self.add_subview(self.memo_label)
        pencil=ui.ButtonItem(image=ui.Image.named('ionicons-ios7-compose-24'),action=self.open_note)
        menu=ui.ButtonItem(title='•••',action=self.show_menu)
        self.right_button_items=[pencil,menu]
        self.left_button_items=[ui.ButtonItem(title='◀︎',action=self.on_prev),ui.ButtonItem(title='▶︎',action=self.on_next)]
        self.load_today_note(); self.position_memo_label()
    def layout(self):
        self.tv.frame=(0,0,self.width,self.height)
        self.position_memo_label()
    def update_title(self):
        d=self.current_date
        w='月火水木金土日'[d.weekday()]
        self.name=f'{d.month}月{d.day}日({w})'
    def load_today_note(self):
        self.memo_label.text=load_note(self.current_date.isoformat()) or ''
        self.memo_label.size_to_fit()
    def save_current_note(self,text):
        save_note(self.current_date.isoformat(),text)
    def position_memo_label(self):
        margin=16
        y0=self.ROW_H*len(self.ds.items)+margin
        self.memo_label.frame=(margin,y0,self.width-margin*2,1)
        self.memo_label.text=load_note(self.current_date.isoformat()) or ''
        self.memo_label.size_to_fit()
        w=self.width-margin*2
        h=self.memo_label.height
        self.memo_label.frame=(margin,y0,w,h)
    def open_note(self,_):
        v=ui.View(frame=(0,0,320,300),bg_color='white');v.name='メモを編集'
        tf=ui.TextView(frame=(10,10,300,230),font=('Helvetica Neue',14))
        tf.text=load_note(self.current_date.isoformat()) or ''
        btn=ui.Button(title='保存',frame=(10,250,300,40))
        def save(sender):
            self.save_current_note(tf.text);v.close();self.load_today_note();self.position_memo_label()
        btn.action=save
        v.add_subview(tf);v.add_subview(btn);v.present('sheet')
    def on_prev(self,_):
        self.current_date-=datetime.timedelta(days=1)
        self.update_title();self.ds.reload();self.load_today_note();self.position_memo_label()
    def on_next(self,_):
        self.current_date+=datetime.timedelta(days=1)
        self.update_title();self.ds.reload();self.load_today_note();self.position_memo_label()
    def show_menu(self,_):
        sheet=ui.TableView()
        opts=[('Stats',lambda _:StatsList().present('sheet')),('Add Habit',self.add_habit),('Edit Order',self.toggle_edit)]
        class MenuDS: pass
        def numrows(s,tv,sec):return len(opts)
        def cell(s,tv,sec,row): c=ui.TableViewCell(); c.text_label.text=opts[row][0]; return c
        def sel(s,tv,sec,row): opts[row][1](None); sheet.close()
        MenuDS.tableview_number_of_rows=numrows
        MenuDS.tableview_cell_for_row=cell
        MenuDS.tableview_did_select=sel
        sheet.data_source=sheet.delegate=MenuDS()
        sheet.width=200; sheet.height=44*len(opts); sheet.name='Menu'
        sheet.present('popover',popover_location=self.bounds.center())
    def toggle_edit(self,_):
        self.tv.editing=not self.tv.editing
    def add_habit(self,_):
        v=ui.View(bg_color='white',frame=(0,0,280,120));v.name='New Habit'
        tf=ui.TextField(frame=(10,10,260,32),placeholder='Habit name')
        def ok(__):
            n=tf.text.strip()
            if n: add_habit(n); self.ds.reload();
            v.close(); self.position_memo_label()
        btn=ui.Button(title='Add',frame=(10,54,260,32)); btn.action=ok
        v.add_subview(tf); v.add_subview(btn); v.present('sheet')

# Note helpers
def load_note(day):
    with db() as c:
        row=c.execute('SELECT memo FROM notes WHERE date=?',(day,)).fetchone(); return row[0] if row else ''

def save_note(day,text):
    with db() as c:
        c.execute('INSERT OR REPLACE INTO notes(date,memo) VALUES(?,?)',(day,text))

if __name__=='__main__':
    init_db()
    HabitTracker().present('sheet',hide_title_bar=False)
