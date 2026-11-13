import os, re, sqlite3, secrets, json, urllib.parse
from datetime import date, datetime, timedelta
from functools import wraps
from pathlib import Path
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
from werkzeug.security import generate_password_hash, check_password_hash
from translations import LANGUAGES, translate

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = Path(os.environ.get('DATABASE_PATH', BASE_DIR / 'shelfsense_v2.db'))
app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'replace-this-with-a-long-random-secret')
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE='Lax', SESSION_COOKIE_SECURE=os.environ.get('FLASK_ENV') == 'production', PERMANENT_SESSION_LIFETIME=timedelta(days=14))

# ---------- Database ----------
def connect_db():
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys = ON')
    return db

def columns(db, table):
    return {r['name'] for r in db.execute(f'PRAGMA table_info({table})').fetchall()}

def add_column(db, table, name, definition):
    if name not in columns(db, table):
        db.execute(f'ALTER TABLE {table} ADD COLUMN {name} {definition}')

def init_db():
    db = connect_db()
    db.executescript('''
    CREATE TABLE IF NOT EXISTS stores (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
    CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY AUTOINCREMENT, store_id INTEGER NOT NULL, name TEXT NOT NULL, email TEXT NOT NULL UNIQUE, password_hash TEXT NOT NULL, language TEXT DEFAULT 'en', created_at TEXT DEFAULT CURRENT_TIMESTAMP, FOREIGN KEY(store_id) REFERENCES stores(id));
    CREATE TABLE IF NOT EXISTS store_products (
      id INTEGER PRIMARY KEY AUTOINCREMENT, store_id INTEGER NOT NULL, name TEXT NOT NULL, brand TEXT DEFAULT '', category TEXT DEFAULT 'Other', quantity INTEGER NOT NULL DEFAULT 0,
      barcode TEXT DEFAULT '', low_stock_threshold INTEGER DEFAULT 5, cost_price REAL DEFAULT 0, selling_price REAL DEFAULT 0,
      stocked_on TEXT, expiry_date TEXT, notes TEXT DEFAULT '', created_at TEXT DEFAULT CURRENT_TIMESTAMP,
      FOREIGN KEY(store_id) REFERENCES stores(id));
    CREATE TABLE IF NOT EXISTS sales (
      id INTEGER PRIMARY KEY AUTOINCREMENT, store_id INTEGER NOT NULL, product_id INTEGER, product_name TEXT NOT NULL, quantity INTEGER NOT NULL,
      discount_percent REAL DEFAULT 0, revenue REAL DEFAULT 0, unit_cost REAL DEFAULT 0, sold_on TEXT DEFAULT CURRENT_TIMESTAMP,
      FOREIGN KEY(store_id) REFERENCES stores(id));
    CREATE TABLE IF NOT EXISTS supplier_quotes (
      id INTEGER PRIMARY KEY AUTOINCREMENT, store_id INTEGER NOT NULL, product_name TEXT NOT NULL, supplier_name TEXT NOT NULL,
      unit_price REAL NOT NULL, moq INTEGER DEFAULT 1, delivery_charge REAL DEFAULT 0, lead_days INTEGER DEFAULT 0,
      contact TEXT DEFAULT '', location TEXT DEFAULT '', quote_date TEXT NOT NULL, notes TEXT DEFAULT '', created_at TEXT DEFAULT CURRENT_TIMESTAMP);
    CREATE TABLE IF NOT EXISTS competitor_prices (
      id INTEGER PRIMARY KEY AUTOINCREMENT, store_id INTEGER NOT NULL, product_name TEXT NOT NULL, competitor_name TEXT NOT NULL,
      price REAL NOT NULL, observed_on TEXT NOT NULL, location TEXT DEFAULT '', source_note TEXT DEFAULT '', created_at TEXT DEFAULT CURRENT_TIMESTAMP);
    CREATE TABLE IF NOT EXISTS market_notes (
      id INTEGER PRIMARY KEY AUTOINCREMENT, store_id INTEGER NOT NULL, title TEXT NOT NULL, category TEXT DEFAULT 'General',
      details TEXT DEFAULT '', source TEXT DEFAULT 'Shop observation', observed_on TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
    CREATE TABLE IF NOT EXISTS wastage (
      id INTEGER PRIMARY KEY AUTOINCREMENT, store_id INTEGER NOT NULL, product_id INTEGER, product_name TEXT NOT NULL,
      quantity INTEGER NOT NULL, unit_cost REAL DEFAULT 0, reason TEXT NOT NULL, lost_on TEXT NOT NULL, notes TEXT DEFAULT '', created_at TEXT DEFAULT CURRENT_TIMESTAMP);
    CREATE TABLE IF NOT EXISTS restock_dates (
      id INTEGER PRIMARY KEY AUTOINCREMENT, store_id INTEGER NOT NULL, product_id INTEGER NOT NULL, expected_date TEXT NOT NULL,
      quantity INTEGER DEFAULT 0, notes TEXT DEFAULT '', UNIQUE(store_id, product_id));
    CREATE TABLE IF NOT EXISTS festival_events (
      id INTEGER PRIMARY KEY AUTOINCREMENT, store_id INTEGER NOT NULL, title TEXT NOT NULL, event_date TEXT NOT NULL,
      category TEXT DEFAULT 'Festival', expected_demand TEXT DEFAULT 'Medium', notes TEXT DEFAULT '', created_at TEXT DEFAULT CURRENT_TIMESTAMP);
    CREATE INDEX IF NOT EXISTS idx_products_store ON store_products(store_id);
    CREATE INDEX IF NOT EXISTS idx_sales_store_date ON sales(store_id, sold_on);
    CREATE INDEX IF NOT EXISTS idx_sales_product ON sales(store_id, product_id);
    CREATE INDEX IF NOT EXISTS idx_quotes_store ON supplier_quotes(store_id, product_name);
    ''')
    # Safe migrations for databases created by earlier ShelfSense versions.
    for table, name, definition in [
        ('users','language',"TEXT DEFAULT 'en'"), ('store_products','barcode',"TEXT DEFAULT ''"),
        ('store_products','low_stock_threshold','INTEGER DEFAULT 5'), ('store_products','stocked_on','TEXT'),
        ('store_products','notes',"TEXT DEFAULT ''"), ('sales','unit_cost','REAL DEFAULT 0'),
        ('sales','product_id','INTEGER'), ('sales','sold_on','TEXT')]:
        if table in {'users','store_products','sales'} and table_exists(db, table): add_column(db, table, name, definition)
    db.commit(); db.close()

def table_exists(db, name):
    return db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None

init_db()

def get_db():
    if 'db' not in g_store:
        g_store['db'] = connect_db()
    return g_store['db']

# Request-local connection without relying on Flask's g in helper imports.
from flask import g
@app.teardown_appcontext
def close_db(_=None):
    db = g.pop('db', None)
    if db is not None: db.close()

g_store = {}
# Use Flask's request context storage for DB access.
def db_conn():
    if 'db' not in g: g.db = connect_db()
    return g.db

# ---------- Language, security and account helpers ----------
def t(key): return translate(key, session.get('language', 'en'))
@app.context_processor
def inject_globals():
    return {'t': t, 'languages': LANGUAGES, 'current_language': session.get('language', 'en'), 'csrf_token': session.get('_csrf_token', '')}

@app.before_request
def protect_posts():
    if request.method == 'POST':
        sent = request.headers.get('X-CSRF-Token') or request.form.get('csrf_token', '')
        expected = session.get('_csrf_token', '')
        if not expected or not secrets.compare_digest(str(sent), str(expected)):
            if request.path.startswith('/api/'):
                return jsonify({'ok': False, 'error': 'Security token expired. Refresh the page.'}), 400
            flash(t('security_error'), 'error')
            return redirect(request.referrer or url_for('home'))

@app.before_request
def ensure_csrf():
    if not session.get('_csrf_token'): session['_csrf_token'] = secrets.token_hex(24)

def login_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not session.get('user_id'): return redirect(url_for('login'))
        return fn(*args, **kwargs)
    return wrapper

def sid(): return session.get('store_id')
def money(v): return round(float(v or 0), 2)
def parse_date(value, fallback=None):
    try: return date.fromisoformat(value)
    except (TypeError, ValueError): return fallback

def product_status(p):
    exp = parse_date(p['expiry_date']) if p['expiry_date'] else None
    if exp:
        days = (exp - date.today()).days
        if days < 0: return 'Expired', days
        if days <= 3: return 'Urgent', days
        if days <= 7: return 'Expiring soon', days
        if days <= 30: return 'Watch', days
    else: days = None
    if int(p['quantity'] or 0) <= int(p['low_stock_threshold'] or 5): return 'Low stock', days
    return 'Fresh', days

def current_store():
    return db_conn().execute('SELECT * FROM stores WHERE id=?', (sid(),)).fetchone()

def normalize_identifier(value):
    value = (value or '').strip()
    if '@' in value: return value.lower()
    digits = re.sub(r'\D', '', value)
    if len(digits) == 10: return '+91' + digits
    return '+' + digits if digits else value.lower()

@app.route('/')
def home():
    if session.get('user_id'): return redirect(url_for('dashboard'))
    return render_template('home.html')

@app.route('/register', methods=['GET','POST'])
def register():
    if request.method == 'POST':
        name = request.form.get('name','').strip(); store_name = request.form.get('store_name','').strip()
        identifier = normalize_identifier(request.form.get('identifier') or request.form.get('email',''))
        password = request.form.get('password','')
        if not all([name, store_name, identifier, password]): flash(t('fill_all_fields'),'error'); return redirect(url_for('register'))
        if len(password) < 8: flash(t('password_length'),'error'); return redirect(url_for('register'))
        db = db_conn()
        if db.execute('SELECT id FROM users WHERE email=?',(identifier,)).fetchone(): flash(t('already_registered'),'error'); return redirect(url_for('register'))
        cur = db.execute('INSERT INTO stores(name) VALUES(?)',(store_name,)); new_store_id = cur.lastrowid
        db.execute('INSERT INTO users(store_id,name,email,password_hash,language) VALUES(?,?,?,?,?)',(new_store_id,name,identifier,generate_password_hash(password),session.get('language','en')))
        db.commit(); flash(t('account_created'),'success'); return redirect(url_for('login'))
    return render_template('auth.html', mode='register')

@app.route('/login', methods=['GET','POST'])
def login():
    if request.method == 'POST':
        identifier = normalize_identifier(request.form.get('identifier') or request.form.get('email',''))
        user = db_conn().execute('SELECT * FROM users WHERE email=?',(identifier,)).fetchone()
        if not user or not check_password_hash(user['password_hash'],request.form.get('password','')):
            flash(t('login_failed'),'error'); return redirect(url_for('login'))
        store = db_conn().execute('SELECT name FROM stores WHERE id=?', (user['store_id'],)).fetchone()
        session.clear(); session['_csrf_token'] = secrets.token_hex(24); session['user_id']=user['id']; session['store_id']=user['store_id']; session['user_name']=user['name']; session['store_name']=store['name'] if store else ''; session['language']=user['language'] or 'en'; session.permanent=True
        return redirect(url_for('dashboard'))
    return render_template('auth.html', mode='login')

@app.route('/logout', methods=['POST'])
@login_required
def logout():
    session.clear(); flash(t('signed_out'),'success'); return redirect(url_for('home'))

@app.route('/set-language', methods=['POST'])
def set_language():
    language = request.form.get('language','en')
    if language in LANGUAGES:
        session['language'] = language
        if session.get('user_id'): db_conn().execute('UPDATE users SET language=? WHERE id=?',(language,session['user_id'])); db_conn().commit()
    return redirect(request.referrer or url_for('home'))

# ---------- Core inventory and sales ----------
@app.route('/dashboard')
@login_required
def dashboard():
    db=db_conn(); products=db.execute('SELECT * FROM store_products WHERE store_id=? ORDER BY id DESC',(sid(),)).fetchall()
    enriched=[]
    for p in products:
        d=dict(p); d['status'],d['days_left']=product_status(p); d['suggested_discount']=min(40, max(5, 10 + (7-(d['days_left'] or 7))*4)) if d['days_left'] is not None and d['days_left']>=0 else 0; enriched.append(d)
    sales=db.execute('SELECT COUNT(*) transactions, COALESCE(SUM(revenue),0) revenue FROM sales WHERE store_id=?',(sid(),)).fetchone()
    metrics={'product_count':len(products),'units':sum(int(p['quantity'] or 0) for p in products),'soon':sum(1 for p in enriched if p['days_left'] is not None and 0<=p['days_left']<=7),'expired':sum(1 for p in enriched if p['days_left'] is not None and p['days_left']<0),'stock_cost':sum(int(p['quantity'] or 0)*float(p['cost_price'] or 0) for p in products),'revenue':sales['revenue'],'transactions':sales['transactions'],'recovered':db.execute('SELECT COALESCE(SUM(unit_cost*quantity),0) FROM sales WHERE store_id=?',(sid(),)).fetchone()[0],'expired_loss':sum(int(p['quantity'] or 0)*float(p['cost_price'] or 0) for p in enriched if p['days_left'] is not None and p['days_left']<0)}
    new_arrivals = sorted([dict(p) for p in products if p['stocked_on']], key=lambda x: x['stocked_on'], reverse=True)[:5]
    return render_template('dashboard.html', products=enriched, new_arrivals=new_arrivals, metrics=metrics, store_name=current_store()['name'], today=date.today().isoformat())

@app.route('/products/add', methods=['POST'])
@login_required
def add_product():
    f=request.form; name=f.get('name','').strip(); qty=f.get('quantity','0'); cost=f.get('cost_price','0'); sell=f.get('selling_price','0')
    try: qty=int(qty); cost=float(cost); sell=float(sell); threshold=int(f.get('low_stock_threshold') or 5)
    except ValueError: flash(t('invalid_numbers'),'error'); return redirect(url_for('dashboard'))
    if not name or qty<0 or cost<0 or sell<0 or threshold<0: flash(t('invalid_product'),'error'); return redirect(url_for('dashboard'))
    db=db_conn(); db.execute('INSERT INTO store_products(store_id,name,brand,category,quantity,barcode,low_stock_threshold,cost_price,selling_price,stocked_on,expiry_date,notes) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',(sid(),name,f.get('brand','').strip(),f.get('category','Other'),qty,f.get('barcode','').strip(),threshold,cost,sell,f.get('stocked_on') or date.today().isoformat(),f.get('expiry_date') or None,f.get('notes','').strip())); db.commit(); flash(t('product_added'),'success'); return redirect(url_for('dashboard'))

@app.route('/api/scan', methods=['POST'])
@login_required
def api_scan():
    barcode=(request.json or {}).get('barcode','').strip()
    if not barcode: return jsonify({'ok':False,'message':t('barcode_required')}),400
    db=db_conn(); p=db.execute('SELECT * FROM store_products WHERE store_id=? AND barcode=?',(sid(),barcode)).fetchone()
    if not p: return jsonify({'ok':False,'message':t('barcode_not_found')}),404
    if p['quantity']<=0: return jsonify({'ok':False,'message':t('out_of_stock')}),409
    db.execute('UPDATE store_products SET quantity=quantity-1 WHERE id=? AND store_id=?',(p['id'],sid())); db.commit()
    remaining=p['quantity']-1
    return jsonify({'ok':True,'message':f"{p['name']}: {remaining} {t('units_remaining')}",'remaining':remaining,'product':p['name']})

@app.route('/products/<int:pid>/delete',methods=['POST'])
@login_required
def delete_product(pid):
    db=db_conn(); db.execute('DELETE FROM store_products WHERE id=? AND store_id=?',(pid,sid())); db.commit(); flash(t('product_removed'),'success'); return redirect(url_for('dashboard'))

@app.route('/products/<int:pid>/sell',methods=['POST'])
@login_required
def sell_product(pid):
    db=db_conn(); p=db.execute('SELECT * FROM store_products WHERE id=? AND store_id=?',(pid,sid())).fetchone()
    if not p: flash(t('product_not_found'),'error'); return redirect(url_for('dashboard'))
    try: qty=int(request.form.get('quantity','1')); discount=float(request.form.get('discount','0'))
    except ValueError: flash(t('invalid_numbers'),'error'); return redirect(url_for('dashboard'))
    exp=parse_date(p['expiry_date']) if p['expiry_date'] else None
    if qty<=0 or qty>p['quantity'] or discount<0 or discount>100 or (exp and exp<date.today()): flash(t('sale_not_allowed'),'error'); return redirect(url_for('dashboard'))
    revenue=qty*float(p['selling_price'])*(1-discount/100)
    db.execute('INSERT INTO sales(store_id,product_id,product_name,quantity,discount_percent,revenue,unit_cost,sold_on) VALUES(?,?,?,?,?,?,?,?)',(sid(),pid,p['name'],qty,discount,revenue,p['cost_price'],datetime.now().isoformat(timespec='seconds')))
    db.execute('UPDATE store_products SET quantity=quantity-? WHERE id=? AND store_id=?',(qty,pid,sid())); db.commit(); flash(t('sale_recorded'),'success'); return redirect(url_for('dashboard'))

@app.route('/sales')
@login_required
def sales():
    rows=db_conn().execute('SELECT * FROM sales WHERE store_id=? ORDER BY sold_on DESC LIMIT 500',(sid(),)).fetchall()
    total=sum(float(r['revenue'] or 0) for r in rows); gross=sum(float(r['revenue'] or 0)-float(r['unit_cost'] or 0)*int(r['quantity'] or 0) for r in rows)
    return render_template('sales.html',sales=rows,total_revenue=total,estimated_gross_profit=gross)

@app.route('/guide')
@login_required
def guide(): return render_template('guide.html')

# ---------- Supplier, competitor and market intelligence ----------
@app.route('/suppliers',methods=['GET','POST'])
@login_required
def suppliers():
    db=db_conn()
    if request.method=='POST':
        f=request.form
        try: price=float(f.get('unit_price','')); moq=int(f.get('moq') or 1); delivery=float(f.get('delivery_charge') or 0); lead=int(f.get('lead_days') or 0)
        except ValueError: flash(t('invalid_numbers'),'error'); return redirect(url_for('suppliers'))
        if not f.get('product_name','').strip() or not f.get('supplier_name','').strip() or price<0 or moq<1 or delivery<0 or lead<0: flash(t('invalid_product'),'error'); return redirect(url_for('suppliers'))
        db.execute('INSERT INTO supplier_quotes(store_id,product_name,supplier_name,unit_price,moq,delivery_charge,lead_days,contact,location,quote_date,notes) VALUES(?,?,?,?,?,?,?,?,?,?,?)',(sid(),f['product_name'].strip(),f['supplier_name'].strip(),price,moq,delivery,lead,f.get('contact',''),f.get('location',''),f.get('quote_date') or date.today().isoformat(),f.get('notes',''))); db.commit(); flash(t('quote_saved'),'success'); return redirect(url_for('suppliers'))
    rows=db.execute('SELECT * FROM supplier_quotes WHERE store_id=? ORDER BY product_name, (unit_price*moq+delivery_charge) ASC',(sid(),)).fetchall()
    return render_template('suppliers.html',quotes=rows)

@app.route('/suppliers/<int:item_id>/delete',methods=['POST'])
@login_required
def delete_quote(item_id):
    db_conn().execute('DELETE FROM supplier_quotes WHERE id=? AND store_id=?',(item_id,sid())); db_conn().commit(); return redirect(url_for('suppliers'))

@app.route('/competitors',methods=['GET','POST'])
@login_required
def competitors():
    db=db_conn()
    if request.method=='POST':
        f=request.form
        try: price=float(f.get('price',''))
        except ValueError: flash(t('invalid_numbers'),'error'); return redirect(url_for('competitors'))
        if price<0 or not f.get('product_name','').strip() or not f.get('competitor_name','').strip(): flash(t('invalid_product'),'error'); return redirect(url_for('competitors'))
        db.execute('INSERT INTO competitor_prices(store_id,product_name,competitor_name,price,observed_on,location,source_note) VALUES(?,?,?,?,?,?,?)',(sid(),f['product_name'].strip(),f['competitor_name'].strip(),price,f.get('observed_on') or date.today().isoformat(),f.get('location',''),f.get('source_note',''))); db.commit(); flash(t('price_saved'),'success'); return redirect(url_for('competitors'))
    rows=db.execute('SELECT * FROM competitor_prices WHERE store_id=? ORDER BY observed_on DESC',(sid(),)).fetchall()
    return render_template('competitors.html',prices=rows)

@app.route('/market',methods=['GET','POST'])
@login_required
def market():
    db=db_conn()
    if request.method=='POST':
        f=request.form
        if not f.get('title','').strip(): flash(t('title_required'),'error'); return redirect(url_for('market'))
        db.execute('INSERT INTO market_notes(store_id,title,category,details,source,observed_on) VALUES(?,?,?,?,?,?)',(sid(),f['title'].strip(),f.get('category','General'),f.get('details',''),f.get('source') or 'Shop observation',f.get('observed_on') or date.today().isoformat())); db.commit(); flash(t('market_note_saved'),'success'); return redirect(url_for('market'))
    notes=db.execute('SELECT * FROM market_notes WHERE store_id=? ORDER BY observed_on DESC',(sid(),)).fetchall()
    # Headlines are fetched only when network access is available. They are not supplier quotes or verified local prices.
    headlines=[]; feed_error=None
    try:
        import requests, xml.etree.ElementTree as ET
        url='https://news.google.com/rss/search?q='+urllib.parse.quote('India retail grocery fruits vegetables food market prices')+'&hl=en-IN&gl=IN&ceid=IN:en'
        response=requests.get(url,timeout=5,headers={'User-Agent':'VencerMirai/1.0'}); response.raise_for_status()
        root=ET.fromstring(response.content)
        for item in root.findall('.//item')[:8]: headlines.append({'title':item.findtext('title',''),'link':item.findtext('link',''),'date':item.findtext('pubDate',''),'source':item.findtext('source','News source')})
    except Exception: feed_error=t('live_feed_unavailable')
    return render_template('market.html',notes=notes,headlines=headlines,feed_error=feed_error)

@app.route('/market/<int:item_id>/delete',methods=['POST'])
@login_required
def delete_market_note(item_id):
    db_conn().execute('DELETE FROM market_notes WHERE id=? AND store_id=?',(item_id,sid())); db_conn().commit(); return redirect(url_for('market'))

# ---------- Availability and stock planner ----------
@app.route('/availability',methods=['GET','POST'])
@login_required
def availability():
    db=db_conn()
    if request.method=='POST':
        try: pid=int(request.form.get('product_id')); qty=int(request.form.get('quantity') or 0)
        except ValueError: flash(t('invalid_numbers'),'error'); return redirect(url_for('availability'))
        p=db.execute('SELECT id FROM store_products WHERE id=? AND store_id=?',(pid,sid())).fetchone()
        if not p: flash(t('product_not_found'),'error'); return redirect(url_for('availability'))
        db.execute('INSERT INTO restock_dates(store_id,product_id,expected_date,quantity,notes) VALUES(?,?,?,?,?) ON CONFLICT(store_id,product_id) DO UPDATE SET expected_date=excluded.expected_date,quantity=excluded.quantity,notes=excluded.notes',(sid(),pid,request.form.get('expected_date') or date.today().isoformat(),qty,request.form.get('notes',''))); db.commit(); flash(t('restock_saved'),'success'); return redirect(url_for('availability'))
    rows=db.execute('SELECT p.*,r.expected_date,r.quantity AS incoming_quantity,r.notes AS restock_notes FROM store_products p LEFT JOIN restock_dates r ON p.id=r.product_id AND r.store_id=p.store_id WHERE p.store_id=? ORDER BY p.category,p.name',(sid(),)).fetchall()
    return render_template('availability.html',products=rows)

@app.route('/planner')
@login_required
def planner():
    db=db_conn(); products=db.execute('SELECT * FROM store_products WHERE store_id=?',(sid(),)).fetchall(); plans=[]
    for p in products:
        sold=db.execute("SELECT COALESCE(SUM(quantity),0) FROM sales WHERE store_id=? AND product_id=? AND date(sold_on)>=date('now','-30 day')",(sid(),p['id'])).fetchone()[0]
        daily=float(sold)/30; target=max(int(p['low_stock_threshold'] or 5)*2, round(daily*14)); suggested=max(0,target-int(p['quantity'] or 0))
        quotes=db.execute('SELECT * FROM supplier_quotes WHERE store_id=? AND lower(product_name)=lower(?) ORDER BY (unit_price*moq+delivery_charge) ASC',(sid(),p['name'])).fetchall()
        plans.append({'product':p,'sold_30':sold,'daily_rate':round(daily,2),'suggested':suggested,'quotes':quotes,'estimated_cost':round(suggested*float(p['cost_price'] or 0),2)})
    return render_template('planner.html',plans=plans)

# ---------- Demand, wastage, events, reporting, advisor ----------
@app.route('/wastage',methods=['GET','POST'])
@login_required
def wastage():
    db=db_conn()
    if request.method=='POST':
        f=request.form
        try: qty=int(f.get('quantity','')); cost=float(f.get('unit_cost','0')); pid=int(f.get('product_id') or 0)
        except ValueError: flash(t('invalid_numbers'),'error'); return redirect(url_for('wastage'))
        if qty<=0 or cost<0 or not f.get('product_name','').strip(): flash(t('invalid_product'),'error'); return redirect(url_for('wastage'))
        if pid:
            p=db.execute('SELECT * FROM store_products WHERE id=? AND store_id=?',(pid,sid())).fetchone()
            if p and qty<=p['quantity']:
                db.execute('UPDATE store_products SET quantity=quantity-? WHERE id=? AND store_id=?',(qty,pid,sid())); cost=float(p['cost_price'] or cost)
        db.execute('INSERT INTO wastage(store_id,product_id,product_name,quantity,unit_cost,reason,lost_on,notes) VALUES(?,?,?,?,?,?,?,?)',(sid(),pid or None,f['product_name'].strip(),qty,cost,f.get('reason','Other'),f.get('lost_on') or date.today().isoformat(),f.get('notes',''))); db.commit(); flash(t('wastage_saved'),'success'); return redirect(url_for('wastage'))
    rows=db.execute('SELECT * FROM wastage WHERE store_id=? ORDER BY lost_on DESC',(sid(),)).fetchall(); loss=sum(int(r['quantity'])*float(r['unit_cost']) for r in rows)
    products=db.execute('SELECT * FROM store_products WHERE store_id=? ORDER BY name',(sid(),)).fetchall()
    return render_template('wastage.html',records=rows,total_loss=loss,products=products)

@app.route('/events',methods=['GET','POST'])
@login_required
def events():
    db=db_conn()
    if request.method=='POST':
        f=request.form
        if not f.get('title','').strip() or not parse_date(f.get('event_date')): flash(t('event_invalid'),'error'); return redirect(url_for('events'))
        db.execute('INSERT INTO festival_events(store_id,title,event_date,category,expected_demand,notes) VALUES(?,?,?,?,?,?)',(sid(),f['title'].strip(),f['event_date'],f.get('category','Festival'),f.get('expected_demand','Medium'),f.get('notes',''))); db.commit(); flash(t('event_saved'),'success'); return redirect(url_for('events'))
    rows=db.execute('SELECT * FROM festival_events WHERE store_id=? ORDER BY event_date',(sid(),)).fetchall()
    return render_template('events.html',events=rows)

@app.route('/events/<int:item_id>/delete',methods=['POST'])
@login_required
def delete_event(item_id):
    db_conn().execute('DELETE FROM festival_events WHERE id=? AND store_id=?',(item_id,sid())); db_conn().commit(); return redirect(url_for('events'))

@app.route('/strategies')
@login_required
def strategies():
    db = db_conn()
    products = db.execute('SELECT * FROM store_products WHERE store_id=? ORDER BY name', (sid(),)).fetchall()
    results = []
    for p in products:
        cost = float(p['cost_price'] or 0); price = float(p['selling_price'] or 0)
        margin = price - cost
        margin_pct = (margin / price * 100) if price > 0 else 0
        sold = db.execute("SELECT COALESCE(SUM(quantity),0) FROM sales WHERE store_id=? AND product_id=? AND date(sold_on)>=date('now','-30 day')", (sid(), p['id'])).fetchone()[0]
        advice = 'Review selling price and costs; current price does not cover recorded unit cost.' if margin < 0 else ('Margin is relatively thin; compare supplier quotes before discounting.' if margin_pct < 15 else 'Consider a small promotion only if stock is slow-moving or expiry is approaching.')
        if sold == 0: advice += ' No sales were recorded in the last 30 days; avoid automatic reordering.'
        results.append({'name':p['name'],'cost':cost,'price':price,'margin':margin,'margin_pct':margin_pct,'sold':sold,'advice':advice})
    results.sort(key=lambda x: x['margin_pct'])
    return render_template('strategies.html', strategies=results)

@app.route('/reports')
@login_required
def reports():
    db=db_conn(); today=date.today(); start=(today-timedelta(days=29)).isoformat()
    sales=db.execute('SELECT * FROM sales WHERE store_id=? AND date(sold_on)>=date(?) ORDER BY sold_on DESC',(sid(),start)).fetchall()
    daily={}
    for r in sales:
        day=str(r['sold_on'])[:10]; daily.setdefault(day,{'revenue':0,'profit':0,'units':0}); daily[day]['revenue']+=float(r['revenue'] or 0); daily[day]['profit']+=float(r['revenue'] or 0)-float(r['unit_cost'] or 0)*int(r['quantity'] or 0); daily[day]['units']+=int(r['quantity'] or 0)
    by_product={}
    for r in sales:
        key=r['product_name']; by_product.setdefault(key,{'units':0,'revenue':0,'profit':0}); by_product[key]['units']+=int(r['quantity']); by_product[key]['revenue']+=float(r['revenue'] or 0); by_product[key]['profit']+=float(r['revenue'] or 0)-float(r['unit_cost'] or 0)*int(r['quantity'])
    best=sorted(by_product.items(),key=lambda x:x[1]['units'],reverse=True)[:5]
    products=db.execute('SELECT * FROM store_products WHERE store_id=?',(sid(),)).fetchall()
    waste=db.execute('SELECT COALESCE(SUM(quantity*unit_cost),0) FROM wastage WHERE store_id=? AND date(lost_on)>=date(?)',(sid(),start)).fetchone()[0]
    return render_template('reports.html',daily=sorted(daily.items()),best=best,revenue=sum(float(x['revenue'] or 0) for x in sales),profit=sum(float(x['revenue'] or 0)-float(x['unit_cost'] or 0)*int(x['quantity']) for x in sales),units=sum(int(x['quantity']) for x in sales),waste=waste,products=products,period_start=start)

@app.route('/advisor')
@login_required
def advisor():
    db=db_conn(); products=db.execute('SELECT * FROM store_products WHERE store_id=?',(sid(),)).fetchall(); insights=[]
    for p in products:
        status,days=product_status(p)
        sold=db.execute("SELECT COALESCE(SUM(quantity),0) FROM sales WHERE store_id=? AND product_id=? AND date(sold_on)>=date('now','-30 day')",(sid(),p['id'])).fetchone()[0]
        if status=='Expired': insights.append(( 'high',f"{p['name']}: expired stock is still recorded. Review it and log any disposal as wastage."))
        elif days is not None and 0<=days<=7: insights.append(('high',f"{p['name']}: expiry is within {days} day(s). Consider a lawful, clearly labelled promotion or smaller future orders."))
        if int(p['quantity'] or 0)<=int(p['low_stock_threshold'] or 5): insights.append(('medium',f"{p['name']}: stock is at or below its minimum threshold. Check the purchase planner and supplier quotes."))
        if sold==0: insights.append(('low',f"{p['name']}: no sales recorded in the last 30 days. Check whether the item is slow-moving before reordering."))
    sales30=db.execute("SELECT COALESCE(SUM(revenue),0) revenue, COALESCE(SUM(quantity),0) units FROM sales WHERE store_id=? AND date(sold_on)>=date('now','-30 day')",(sid(),)).fetchone()
    waste30=db.execute("SELECT COALESCE(SUM(quantity*unit_cost),0) FROM wastage WHERE store_id=? AND date(lost_on)>=date('now','-30 day')",(sid(),)).fetchone()[0]
    if not sales30['units']: insights.append(('low','There is not enough recent sales history to produce a reliable demand forecast. Record sales consistently to improve recommendations.'))
    insights.insert(0,('info',f"Last 30 days: {sales30['units']} units sold, ₹{sales30['revenue']:.2f} revenue and ₹{waste30:.2f} recorded wastage cost."))
    return render_template('advisor.html',insights=insights)

@app.errorhandler(404)
def not_found(_): return render_template('error.html',message='Page not found'),404
@app.errorhandler(500)
def server_error(_): return render_template('error.html',message='Something went wrong. Check the application logs.'),500

if __name__ == '__main__': app.run(host='0.0.0.0',port=int(os.environ.get('PORT',5000)),debug=False)
