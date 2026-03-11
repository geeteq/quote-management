import sqlite3
import json
import os
import logging
from flask import Flask, render_template, request, jsonify, redirect, url_for
from werkzeug.utils import secure_filename
from parser import QuoteParser
from component_registry import ComponentRegistry
from datetime import datetime

# Data directory — outside the app root by default.
# Override with DATA_DIR env var for custom deployments.
_app_dir = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.environ.get('DATA_DIR', os.path.join(_app_dir, '..', 'data'))
DATA_DIR = os.path.abspath(DATA_DIR)
os.makedirs(os.path.join(DATA_DIR, 'uploads'), exist_ok=True)
os.makedirs(os.path.join(DATA_DIR, 'quickspecs'), exist_ok=True)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(os.path.join(DATA_DIR, 'app.log')),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = os.path.join(DATA_DIR, 'uploads')
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max file size
app.config['DATABASE'] = os.path.join(DATA_DIR, 'quotes.db')

_base = os.environ.get('BASE_URL', '/quotes').rstrip('/')
BASE_HREF = _base + '/'


@app.template_filter('is_expired')
def is_expired_filter(expiry_date):
    if not expiry_date:
        return False
    from datetime import date, datetime
    for fmt in ('%B %d, %Y', '%Y-%m-%d', '%m/%d/%Y'):
        try:
            return datetime.strptime(expiry_date, fmt).date() < date.today()
        except ValueError:
            continue
    return False


def _git_branch():
    try:
        import subprocess
        return subprocess.check_output(
            ['git', '-C', _app_dir, 'rev-parse', '--abbrev-ref', 'HEAD'],
            stderr=subprocess.DEVNULL
        ).decode().strip()
    except Exception:
        return 'unknown'

GIT_BRANCH = _git_branch()


@app.context_processor
def inject_base_href():
    return {'base_href': BASE_HREF, 'git_branch': GIT_BRANCH}


ALLOWED_EXTENSIONS = {'pdf'}


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def validate_pdf(filepath, max_size_mb=16):
    """
    Validate PDF file for basic security checks.

    Args:
        filepath: Path to PDF file
        max_size_mb: Maximum allowed file size in MB

    Returns:
        Tuple of (is_valid, error_message)
    """
    try:
        # Check file size
        file_size_mb = os.path.getsize(filepath) / (1024 * 1024)
        if file_size_mb > max_size_mb:
            return False, f"File size ({file_size_mb:.1f}MB) exceeds maximum allowed ({max_size_mb}MB)"

        # Try to open with pdfplumber to validate structure
        import pdfplumber
        with pdfplumber.open(filepath) as pdf:
            # Check if PDF has pages
            if len(pdf.pages) == 0:
                return False, "PDF file contains no pages"

            # Check for excessive pages (potential DoS)
            if len(pdf.pages) > 1000:
                return False, "PDF file has too many pages (max 1000)"

            # Try to extract text from first page to verify it's a real PDF
            first_page = pdf.pages[0]
            text = first_page.extract_text()

            # If completely empty, might be suspicious
            if not text or len(text.strip()) == 0:
                # Check if it's an image-based PDF
                if not first_page.images:
                    return False, "PDF appears to be empty or corrupted"

        return True, None

    except Exception as e:
        return False, f"PDF validation failed: {str(e)}"


@app.template_filter('calculate_memory_total')
def calculate_memory_total(memory_items):
    """Calculate total memory in GB from memory items list.

    Args:
        memory_items: List of memory item dicts with 'specs' containing 'capacity_gb'

    Returns:
        Total memory in GB, or 0 if no valid items
    """
    total_gb = 0
    for item in memory_items:
        try:
            quantity = item.get('quantity', 1)
            capacity = item.get('specs', {}).get('capacity_gb', 0)
            if capacity:
                total_gb += quantity * capacity
        except (TypeError, AttributeError, ValueError):
            # Skip items with malformed data
            continue
    return total_gb


def get_db():
    """Get database connection."""
    db = sqlite3.connect(app.config['DATABASE'])
    db.row_factory = sqlite3.Row
    return db


def init_db():
    """Initialize database schema."""
    db = get_db()
    with open(os.path.join(_app_dir, 'schema_normalized.sql'), 'r') as f:
        db.executescript(f.read())
    db.commit()
    db.close()


def migrate_db():
    """Apply incremental schema migrations. Each block is idempotent."""
    db = get_db()
    try:
        db.execute("PRAGMA foreign_keys = OFF")

        tables = lambda: {r[0] for r in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        cols   = lambda t: {r[1] for r in db.execute(f"PRAGMA table_info({t})")}

        # ── M01: projects.status CHECK → include 'archived' ──────────────────
        row = db.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='projects'").fetchone()
        if row and "'archived'" not in row['sql']:
            existing = [c for c in ['id','name','tenant_id','description','comments',
                                    'created_at','updated_at','status'] if c in cols('projects')]
            cs = ', '.join(existing)
            db.execute("""CREATE TABLE projects_migration (
                id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,
                tenant_id INTEGER NOT NULL, description TEXT, comments TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                status TEXT DEFAULT 'active' CHECK(status IN ('active','inactive','archived')),
                FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE,
                UNIQUE(tenant_id, name))""")
            db.execute(f"INSERT INTO projects_migration ({cs}) SELECT {cs} FROM projects")
            db.execute("DROP TABLE projects")
            db.execute("ALTER TABLE projects_migration RENAME TO projects")
            db.execute("CREATE INDEX IF NOT EXISTS idx_projects_tenant ON projects(tenant_id)")
            db.commit()
            logger.info("M01: projects.status updated")

        # ── M02: tenants.status CHECK → include 'archived' ───────────────────
        row = db.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='tenants'").fetchone()
        if row and "'archived'" not in row['sql']:
            existing = [c for c in ['id','name','contact_name','created_at','updated_at','status']
                        if c in cols('tenants')]
            cs = ', '.join(existing)
            db.execute("""CREATE TABLE tenants_migration (
                id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE,
                contact_name TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                status TEXT DEFAULT 'active' CHECK(status IN ('active','inactive','archived')))""")
            db.execute(f"INSERT INTO tenants_migration ({cs}) SELECT {cs} FROM tenants")
            db.execute("DROP TABLE tenants")
            db.execute("ALTER TABLE tenants_migration RENAME TO tenants")
            db.execute("CREATE INDEX IF NOT EXISTS idx_tenant_name ON tenants(name)")
            db.commit()
            logger.info("M02: tenants.status updated")

        # ── M03: quotes.po_comments ───────────────────────────────────────────
        if 'po_comments' not in cols('quotes'):
            db.execute("ALTER TABLE quotes ADD COLUMN po_comments TEXT CHECK(length(po_comments)<=255)")
            db.commit()
            logger.info("M03: quotes.po_comments added")

        # ── M04: manufacturers table ──────────────────────────────────────────
        if 'manufacturers' not in tables():
            db.execute("""CREATE TABLE manufacturers (
                id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE)""")
            db.commit()
            logger.info("M04: manufacturers table created")
        for m in ('Intel','HPE','Dell','Broadcom','Cisco','Samsung',
                  'Seagate','Western Digital','Micron','NVIDIA','AMD'):
            db.execute("INSERT OR IGNORE INTO manufacturers (name) VALUES (?)", (m,))
        db.commit()

        # ── M05: vendors table ────────────────────────────────────────────────
        if 'vendors' not in tables():
            db.execute("""CREATE TABLE vendors (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE, code TEXT NOT NULL UNIQUE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""")
            db.commit()
            logger.info("M05: vendors table created")
        for v in (('HPE','HPE'),('Dell','Dell'),('Cisco','Cisco')):
            db.execute("INSERT OR IGNORE INTO vendors (name,code) VALUES (?,?)", v)
        db.commit()

        # ── M06: quotes.vendor_id FK ──────────────────────────────────────────
        if 'vendor_id' not in cols('quotes'):
            db.execute("ALTER TABLE quotes ADD COLUMN vendor_id INTEGER REFERENCES vendors(id)")
            db.execute("UPDATE quotes SET vendor_id=(SELECT id FROM vendors WHERE code=quotes.vendor)")
            db.commit()
            logger.info("M06: quotes.vendor_id added and populated")

        # ── M07: rename components → learned_components ───────────────────────
        if 'components' in tables() and 'learned_components' not in tables():
            db.execute("ALTER TABLE components RENAME TO learned_components")
            db.commit()
            logger.info("M07: components renamed to learned_components")

        # ── M08: learned_components.manufacturer_id FK (soft) ────────────────
        if 'learned_components' in tables() and 'manufacturer_id' not in cols('learned_components'):
            db.execute("ALTER TABLE learned_components ADD COLUMN manufacturer_id INTEGER REFERENCES manufacturers(id)")
            db.execute("""UPDATE learned_components SET manufacturer_id=
                (SELECT id FROM manufacturers WHERE name=learned_components.manufacturer)""")
            db.commit()
            logger.info("M08: learned_components.manufacturer_id added")

        # ── M09: base_configs table ───────────────────────────────────────────
        if 'base_configs' not in tables():
            db.execute("""CREATE TABLE base_configs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                config_name TEXT NOT NULL, project_id INTEGER NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE)""")
            db.execute("CREATE INDEX IF NOT EXISTS idx_base_configs_project ON base_configs(project_id)")
            db.commit()
            logger.info("M09: base_configs table created")

        # ── M10: defined_components table ─────────────────────────────────────
        if 'defined_components' not in tables():
            db.execute("""CREATE TABLE defined_components (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                component_type TEXT, manufacturer_id INTEGER REFERENCES manufacturers(id),
                part_number TEXT, model TEXT, specs TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""")
            db.execute("CREATE INDEX IF NOT EXISTS idx_defined_comp_type ON defined_components(component_type)")
            db.commit()
            logger.info("M10: defined_components table created")

        # ── M11: base_config_components junction ──────────────────────────────
        if 'base_config_components' not in tables():
            db.execute("""CREATE TABLE base_config_components (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                config_id    INTEGER NOT NULL REFERENCES base_configs(id)    ON DELETE CASCADE,
                component_id INTEGER NOT NULL REFERENCES defined_components(id),
                quantity     INTEGER NOT NULL DEFAULT 1)""")
            db.execute("CREATE INDEX IF NOT EXISTS idx_bcc_config    ON base_config_components(config_id)")
            db.execute("CREATE INDEX IF NOT EXISTS idx_bcc_component ON base_config_components(component_id)")
            db.commit()
            logger.info("M11: base_config_components junction table created")

        # ── M12: drop legacy entered_components ───────────────────────────────
        if 'entered_components' in tables():
            db.execute("DROP TABLE entered_components")
            db.commit()
            logger.info("M12: entered_components dropped (superseded by defined_components)")

        # ── M13a: transaction_types table + seed ──────────────────────────────
        if 'transaction_types' not in tables():
            db.execute("""CREATE TABLE transaction_types (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                code       TEXT NOT NULL UNIQUE,
                label      TEXT NOT NULL,
                description TEXT,
                token_cost INTEGER NOT NULL DEFAULT 1)""")
            db.commit()
            logger.info("M13a: transaction_types table created")

        _TRANSACTION_TYPES = [
            ('add_quote',            'Add Quote',
             'Vendor quote PDF uploaded and parsed into line items',              10),
            ('add_quote_item',       'Add Quote Item',
             'Single line item parsed from a vendor quote (per-item cost)',        1),
            ('add_config',           'Add Base Config',
             'New desired base configuration created for a project',               5),
            ('add_config_component', 'Add Config Component',
             'Component specification added to a base configuration',              1),
            ('archive_quote',        'Archive Quote',
             'Quote hidden from the active view',                                  2),
            ('delete_quote',         'Delete Quote',
             'Quote and all line items permanently deleted',                       2),
            ('compare_quotes',       'Compare Quotes',
             'Side-by-side cost and spec comparison of two vendor quotes',         5),
            ('config_match',         'Config Match',
             'Vendor quote scored against a desired base configuration',          10),
            ('add_tenant',           'Add Tenant',
             'New tenant organisation created',                                    3),
            ('add_project',          'Add Project',
             'New project created under a tenant',                                 3),
            ('archive_tenant',       'Archive Tenant',
             'Tenant and all its projects archived',                               2),
            ('delete_config',        'Delete Config',
             'Base configuration and its component links deleted',                 2),
        ]
        for code, label, desc, cost in _TRANSACTION_TYPES:
            db.execute(
                "INSERT OR IGNORE INTO transaction_types (code,label,description,token_cost) VALUES (?,?,?,?)",
                (code, label, desc, cost)
            )
        db.commit()

        # ── M13b: transactions ledger table ───────────────────────────────────
        if 'transactions' not in tables():
            db.execute("""CREATE TABLE transactions (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                type_id        INTEGER NOT NULL REFERENCES transaction_types(id),
                user_name      TEXT    NOT NULL DEFAULT 'admin',
                quote_id       INTEGER REFERENCES quotes(id)       ON DELETE SET NULL,
                config_id      INTEGER REFERENCES base_configs(id) ON DELETE SET NULL,
                metadata_json  TEXT,
                tokens_charged INTEGER NOT NULL DEFAULT 0,
                created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""")
            db.execute("CREATE INDEX IF NOT EXISTS idx_transactions_type    ON transactions(type_id)")
            db.execute("CREATE INDEX IF NOT EXISTS idx_transactions_user    ON transactions(user_name)")
            db.execute("CREATE INDEX IF NOT EXISTS idx_transactions_quote   ON transactions(quote_id)")
            db.execute("CREATE INDEX IF NOT EXISTS idx_transactions_config  ON transactions(config_id)")
            db.execute("CREATE INDEX IF NOT EXISTS idx_transactions_created ON transactions(created_at)")
            db.commit()
            logger.info("M13b: transactions ledger table created")

        # ── M14a: servers table ───────────────────────────────────────────────
        if 'servers' not in tables():
            db.execute("""CREATE TABLE servers (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                manufacturer_id INTEGER REFERENCES manufacturers(id),
                model_name      TEXT NOT NULL UNIQUE,
                model_number    TEXT,
                form_factor     TEXT,
                generation      TEXT,
                pdf_path        TEXT,
                created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""")
            db.execute("CREATE INDEX IF NOT EXISTS idx_servers_model        ON servers(model_name)")
            db.execute("CREATE INDEX IF NOT EXISTS idx_servers_manufacturer ON servers(manufacturer_id)")
            db.commit()
            logger.info("M14a: servers table created")

        # ── M14b: server_quickspec_components junction ────────────────────────
        if 'server_quickspec_components' not in tables():
            db.execute("""CREATE TABLE server_quickspec_components (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                server_id      INTEGER NOT NULL REFERENCES servers(id)           ON DELETE CASCADE,
                catalog_id     INTEGER NOT NULL REFERENCES component_catalog(id) ON DELETE CASCADE,
                component_role TEXT,
                is_standard    BOOLEAN DEFAULT 1,
                is_optional    BOOLEAN DEFAULT 0,
                created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(server_id, catalog_id))""")
            db.execute("CREATE INDEX IF NOT EXISTS idx_sqc_server  ON server_quickspec_components(server_id)")
            db.execute("CREATE INDEX IF NOT EXISTS idx_sqc_catalog ON server_quickspec_components(catalog_id)")
            db.commit()
            logger.info("M14b: server_quickspec_components junction created")

        # ── M14c: parse_quickspec transaction type ────────────────────────────
        db.execute("""INSERT OR IGNORE INTO transaction_types (code, label, description, token_cost)
            VALUES ('parse_quickspec', 'Parse QuickSpec',
                    'HPE QuickSpec PDF parsed into server catalog with component options', 5)""")
        db.commit()

    finally:
        db.execute("PRAGMA foreign_keys = ON")
        db.close()


def save_quote_to_db(quote_data, line_items, pdf_path, tenant_id=None, project_id=None, tenant_name='', project_name='', ica='', po_comments=''):
    """Save parsed quote data to database."""
    logger.debug(f"Saving quote: {quote_data.get('quote_id')} with {len(line_items)} items")

    db = get_db()
    cursor = db.cursor()

    try:
        # Resolve quote_id — suffix with -2, -3 … if already taken
        base_quote_id = quote_data.get('quote_id') or 'UNKNOWN'
        quote_id = base_quote_id
        counter = 2
        while cursor.execute('SELECT 1 FROM quotes WHERE quote_id = ?', (quote_id,)).fetchone():
            quote_id = f"{base_quote_id}-{counter}"
            counter += 1

        # Insert quote (with parameterized queries for SQL injection protection)
        cursor.execute('''
            INSERT INTO quotes (quote_id, vendor, customer_name, quote_date, expiry_date,
                               total_amount, currency, description, pdf_path,
                               tenant_id, project_id, tenant_name, project_name, ica, po_comments)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            quote_id,
            quote_data.get('vendor'),
            quote_data.get('customer_name'),
            quote_data.get('quote_date'),
            quote_data.get('expiry_date'),
            quote_data.get('total_amount'),
            quote_data.get('currency', 'CAD'),
            quote_data.get('description') or os.path.basename(pdf_path),
            pdf_path,
            tenant_id,
            project_id,
            tenant_name,
            project_name,
            ica,
            po_comments[:255] if po_comments else None
        ))

        quote_db_id = cursor.lastrowid

        # Insert line items (simplified without catalog to avoid locks)
        parser = QuoteParser(pdf_path)
        for item in line_items:
            # Extract component details
            component_details = parser.extract_component_details(item)

            # Insert line item
            cursor.execute('''
                INSERT INTO line_items (quote_id, line_no, quantity, product_number,
                                       description, category, delivery_time)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (
                quote_db_id,
                item.get('line_no'),
                item.get('quantity'),
                item.get('product_number'),
                item.get('description'),
                item.get('category'),
                item.get('delivery_time')
            ))

            line_item_id = cursor.lastrowid

            # Legacy: Also insert into components table for backward compatibility
            cursor.execute('''
                INSERT INTO components (line_item_id, component_type, manufacturer,
                                       part_number, model, specs_json, quantity)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (
                line_item_id,
                component_details['type'],
                component_details['manufacturer'],
                item.get('product_number'),
                component_details['model'],
                json.dumps(component_details['specs']),
                item.get('quantity', 1)
            ))

            component_id = cursor.lastrowid

            # Add manufacturer link if available
            manufacturer_url = get_manufacturer_url(
                component_details['manufacturer'],
                component_details['model']
            )
            if manufacturer_url:
                cursor.execute('''
                    INSERT INTO component_links (component_id, url, url_type)
                    VALUES (?, ?, ?)
                ''', (component_id, manufacturer_url, 'product'))

        db.commit()
        return quote_db_id

    except Exception as e:
        db.rollback()
        raise e
    finally:
        db.close()


def get_manufacturer_url(manufacturer, model):
    """Generate manufacturer product URL."""
    urls = {
        'Intel': f'https://ark.intel.com/content/www/us/en/ark/search.html?q={model}',
        'HPE': f'https://www.hpe.com/us/en/search.html?q={model}',
        'Broadcom': f'https://www.broadcom.com/products/ethernet-connectivity/network-adapters',
    }
    return urls.get(manufacturer)


def get_quote_by_id(quote_db_id):
    """Retrieve quote and all related data with normalized specs."""
    db = get_db()

    # Get quote
    quote = db.execute('SELECT * FROM quotes WHERE id = ?', (quote_db_id,)).fetchone()
    if not quote:
        return None

    # Get line items with catalog info
    line_items = db.execute('''
        SELECT
            li.*,
            cc.id as catalog_id,
            cc.model as catalog_model,
            cc.manufacturer as catalog_manufacturer,
            cc.component_type as catalog_component_type
        FROM line_items li
        LEFT JOIN component_catalog cc ON cc.id = li.catalog_component_id
        WHERE li.quote_id = ?
        ORDER BY li.line_no
    ''', (quote_db_id,)).fetchall()

    db.close()

    # Group by category
    categories = {}
    for item in line_items:
        category = item['category']
        if category not in categories:
            categories[category] = []

        item_dict = dict(item)

        # Try to get normalized specs from catalog if available
        if item_dict.get('catalog_id'):
            normalized_specs = get_normalized_specs(
                item_dict['catalog_id'],
                item_dict.get('catalog_component_type') or category
            )
            item_dict['specs'] = normalized_specs or {}
        else:
            item_dict['specs'] = {}

        item_dict['urls'] = []

        categories[category].append(item_dict)

    return {
        'quote': dict(quote),
        'categories': categories
    }


def get_normalized_specs(catalog_id, component_type):
    """Get detailed specs from normalized tables."""
    db = get_db()
    specs = None

    try:
        if component_type == 'CPU':
            result = db.execute('''
                SELECT * FROM cpu_specs WHERE catalog_id = ?
            ''', (catalog_id,)).fetchone()

            if result:
                specs = {
                    'cores': result['cores'],
                    'threads': result['threads'],
                    'base_clock_ghz': result['base_clock_ghz'],
                    'max_turbo_clock_ghz': result['max_turbo_clock_ghz'],
                    'l1_cache_kb': result['l1_cache_kb'],
                    'l2_cache_kb': result['l2_cache_kb'],
                    'l3_cache_kb': result['l3_cache_kb'],
                    'cache_mb': result['l3_cache_kb'] / 1024 if result['l3_cache_kb'] else None,
                    'tdp_watts': result['tdp_watts'],
                    'max_memory_gb': result['max_memory_gb'],
                    'memory_channels': result['memory_channels'],
                    'socket': result['socket'],
                    'pcie_lanes': result['pcie_lanes']
                }

        elif component_type == 'Memory':
            result = db.execute('''
                SELECT * FROM memory_specs WHERE catalog_id = ?
            ''', (catalog_id,)).fetchone()

            if result:
                specs = {
                    'capacity_gb': result['capacity_gb'],
                    'speed_mhz': result['speed_mhz'],
                    'ddr_generation': result['ddr_generation'],
                    'module_type': result['module_type'],
                    'ecc_support': bool(result['ecc_support']),
                    'registered': bool(result['registered'])
                }

        elif component_type == 'Disk':
            result = db.execute('''
                SELECT * FROM disk_specs WHERE catalog_id = ?
            ''', (catalog_id,)).fetchone()

            if result:
                specs = {
                    'capacity_gb': result['capacity_gb'],
                    'capacity_tb': result['capacity_tb'],
                    'disk_type': result['disk_type'],
                    'interface': result['interface'],
                    'read_speed_mbps': result['read_speed_mbps'],
                    'write_speed_mbps': result['write_speed_mbps'],
                    'iops_read': result['iops_read'],
                    'iops_write': result['iops_write']
                }

        elif component_type == 'Network Card':
            result = db.execute('''
                SELECT * FROM network_card_specs WHERE catalog_id = ?
            ''', (catalog_id,)).fetchone()

            if result:
                specs = {
                    'port_count': result['port_count'],
                    'speed_gbps': result['speed_gbps'],
                    'port_type': result['port_type'],
                    'rdma_support': bool(result['rdma_support'])
                }

    finally:
        db.close()

    return specs


def log_transaction(type_code, user_name='admin', quote_id=None, config_id=None,
                    metadata=None, tokens_override=None):
    """
    Append one row to the transactions ledger.
    Never raises — logging failures must not break the calling API.

    tokens_override: when provided, overrides transaction_types.token_cost.
    Useful for compound actions (e.g. add_quote + N × add_quote_item).
    """
    try:
        db = get_db()
        row = db.execute(
            "SELECT id, token_cost FROM transaction_types WHERE code = ?", (type_code,)
        ).fetchone()
        if not row:
            logger.warning(f"log_transaction: unknown type_code '{type_code}'")
            db.close()
            return
        tokens = tokens_override if tokens_override is not None else row['token_cost']
        db.execute(
            """INSERT INTO transactions
               (type_id, user_name, quote_id, config_id, metadata_json, tokens_charged)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (row['id'], user_name, quote_id, config_id,
             json.dumps(metadata) if metadata else None, tokens)
        )
        db.commit()
        db.close()
    except Exception as e:
        logger.warning(f"log_transaction failed for '{type_code}': {e}")


def get_all_quotes():
    """Get all quotes for listing."""
    db = get_db()
    quotes = db.execute('''
        SELECT id, quote_id, vendor, customer_name, quote_date, expiry_date,
               total_amount, currency, description, tenant_name, project_name, ica, status, po_comments
        FROM quotes
        WHERE status != 'archived'
        ORDER BY uploaded_at DESC
    ''').fetchall()
    db.close()
    return [dict(q) for q in quotes]


@app.route('/')
def index():
    """Main page with tenant/project hierarchy navigation."""
    return render_template('index_new.html')


@app.route('/quote/<int:quote_id>')
def view_quote(quote_id):
    """View single quote card."""
    quote_data = get_quote_by_id(quote_id)
    if not quote_data:
        return "Quote not found", 404

    # Check if split view is requested (default to split)
    use_split_view = request.args.get('view', 'split') == 'split'

    if use_split_view:
        return render_template('quote_card_split.html', data=quote_data)
    else:
        return render_template('quote_card.html', data=quote_data)


@app.route('/upload', methods=['POST'])
def upload_quote():
    """Upload and parse PDF quote."""
    logger.info(f"Upload request received from {request.remote_addr}")

    if 'file' not in request.files:
        logger.warning("Upload rejected: No file in request")
        return jsonify({'error': 'No file provided'}), 400

    file = request.files['file']
    if file.filename == '':
        logger.warning("Upload rejected: Empty filename")
        return jsonify({'error': 'No file selected'}), 400

    if not allowed_file(file.filename):
        logger.warning(f"Upload rejected: Invalid file type - {file.filename}")
        return jsonify({'error': 'Only PDF files allowed'}), 400

    logger.info(f"Processing upload: {file.filename}")

    try:
        # Get metadata first so we can build the filename
        tenant_id = request.form.get('tenant_id', '').strip()
        project_id = request.form.get('project_id', '').strip()
        ica = request.form.get('ica', '').strip()
        po_comments = request.form.get('po_comments', '').strip()

        tenant_id = int(tenant_id) if tenant_id else None
        project_id = int(project_id) if project_id else None

        # Look up tenant/project names for the filename
        db = get_db()
        tenant_name = ''
        project_name = ''
        if tenant_id:
            row = db.execute('SELECT name FROM tenants WHERE id = ?', (tenant_id,)).fetchone()
            if row:
                tenant_name = row['name']
        if project_id:
            row = db.execute('SELECT name FROM projects WHERE id = ?', (project_id,)).fetchone()
            if row:
                project_name = row['name']
        db.close()

        # Build filename: {tenant}-{project}-{ica}-{original}
        parts = [tenant_name, project_name, ica]
        prefix = '-'.join(secure_filename(p) for p in parts if p)
        original_name = secure_filename(file.filename)
        filename = f"{prefix}-{original_name}" if prefix else original_name
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(filepath)

        # Validate PDF security
        logger.info(f"Validating PDF: {filename}")
        is_valid, error_msg = validate_pdf(filepath)
        if not is_valid:
            logger.warning(f"PDF validation failed for {filename}: {error_msg}")
            os.remove(filepath)  # Clean up invalid file
            return jsonify({'error': f'PDF validation failed: {error_msg}'}), 400

        # Parse PDF
        logger.info(f"Parsing PDF: {filename}")
        parser = QuoteParser(filepath)
        result = parser.parse()
        logger.info(f"Parsed {len(result.get('line_items', []))} line items from {filename}")

        # Save to database
        logger.info(f"Saving quote to database: {result['quote'].get('quote_id', 'Unknown')}")
        quote_db_id = save_quote_to_db(
            result['quote'],
            result['line_items'],
            filepath,
            tenant_id=tenant_id,
            project_id=project_id,
            tenant_name=tenant_name,
            project_name=project_name,
            ica=ica,
            po_comments=po_comments
        )

        logger.info(f"Quote saved successfully with ID: {quote_db_id}")

        item_count = len(result.get('line_items', []))
        # Base cost 10 (add_quote) + 1 per line item (add_quote_item)
        log_transaction(
            'add_quote',
            quote_id=quote_db_id,
            metadata={
                'filename': filename,
                'vendor': result['quote'].get('vendor'),
                'item_count': item_count,
                'tenant_id': tenant_id,
                'project_id': project_id,
            },
            tokens_override=10 + item_count
        )

        return jsonify({
            'success': True,
            'quote_id': quote_db_id,
            'redirect': url_for('view_quote', quote_id=quote_db_id)
        })

    except Exception as e:
        logger.error(f"Error processing upload {file.filename}: {e}", exc_info=True)
        # Clean up uploaded file on error
        if 'filepath' in locals() and os.path.exists(filepath):
            try:
                os.remove(filepath)
                logger.info(f"Cleaned up failed upload: {filepath}")
            except Exception as cleanup_error:
                logger.error(f"Failed to clean up file {filepath}: {cleanup_error}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/quotes')
def api_quotes():
    """API endpoint for quotes list."""
    quotes = get_all_quotes()
    return jsonify(quotes)


@app.route('/api/quote/<int:quote_id>')
def api_quote(quote_id):
    """API endpoint for single quote."""
    quote_data = get_quote_by_id(quote_id)
    if not quote_data:
        return jsonify({'error': 'Quote not found'}), 404
    return jsonify(quote_data)


@app.route('/pdf/<int:quote_id>')
def serve_pdf(quote_id):
    """Serve PDF file for a quote."""
    from flask import send_file
    db = get_db()
    quote = db.execute('SELECT pdf_path FROM quotes WHERE id = ?', (quote_id,)).fetchone()
    db.close()
    if not quote or not quote['pdf_path']:
        return "PDF not found", 404
    pdf_path = quote['pdf_path']
    if not os.path.isabs(pdf_path):
        pdf_path = os.path.join(_app_dir, pdf_path)
    if not os.path.exists(pdf_path):
        return "PDF file not found on disk", 404
    return send_file(pdf_path, mimetype='application/pdf')


@app.route('/compare')
def compare_quotes():
    """Compare two quotes side by side."""
    ids_param = request.args.get('ids', '')

    if not ids_param:
        return "No quotes selected for comparison", 400

    try:
        quote_ids = [int(id.strip()) for id in ids_param.split(',')]
    except ValueError:
        return "Invalid quote IDs", 400

    if len(quote_ids) != 2:
        return "Please select exactly 2 quotes to compare", 400

    # Fetch both quotes
    quote1_data = get_quote_by_id(quote_ids[0])
    quote2_data = get_quote_by_id(quote_ids[1])

    if not quote1_data or not quote2_data:
        return "One or both quotes not found", 404

    # Get union of all categories from both quotes, preserving order
    category_order = ['CPU', 'Memory', 'Disk', 'Storage Controller',
                      'Network Card', 'Power Supply', 'GPU', 'Additional Hardware']

    all_categories = [cat for cat in category_order
                      if cat in quote1_data['categories'] or cat in quote2_data['categories']]

    log_transaction(
        'compare_quotes',
        metadata={'quote_ids': quote_ids,
                  'quote_refs': [quote1_data['quote']['quote_id'],
                                 quote2_data['quote']['quote_id']]}
    )

    # Check if split view is requested
    use_split_view = request.args.get('view', 'split') == 'split'

    if use_split_view:
        return render_template('compare_split.html',
                              quote1=quote1_data,
                              quote2=quote2_data,
                              categories=all_categories)
    else:
        return render_template('compare.html',
                              quote1=quote1_data,
                              quote2=quote2_data,
                              categories=all_categories)


# =============================================================================
# ADMIN ROUTES
# =============================================================================

@app.route('/admin')
def admin_dashboard():
    """Admin dashboard with split pane layout."""
    return render_template('admin/dashboard.html')


@app.route('/admin/tenants')
def admin_tenants():
    """List all active tenants."""
    db = get_db()
    tenants = db.execute('''
        SELECT id, name, contact_name, created_at, updated_at, status
        FROM tenants
        WHERE status != 'archived'
        ORDER BY name
    ''').fetchall()
    db.close()
    return render_template('admin/tenants_list.html', tenants=[dict(t) for t in tenants])


@app.route('/admin/tenants/archived')
def admin_tenants_archived():
    """List all archived tenants."""
    db = get_db()
    tenants = db.execute('''
        SELECT id, name, contact_name, created_at, updated_at, status
        FROM tenants
        WHERE status = 'archived'
        ORDER BY name
    ''').fetchall()
    db.close()
    return render_template('admin/archived_tenants_list.html', tenants=[dict(t) for t in tenants])


@app.route('/admin/tenants/<int:tenant_id>/edit')
def admin_tenant_edit(tenant_id):
    """Show edit form for a tenant."""
    db = get_db()
    tenant = db.execute(
        'SELECT id, name, contact_name, status FROM tenants WHERE id = ?', (tenant_id,)
    ).fetchone()
    db.close()
    if not tenant:
        return "Tenant not found", 404
    return render_template('admin/tenant_edit.html', tenant=dict(tenant))


@app.route('/admin/tenants/<int:tenant_id>/update', methods=['POST'])
def admin_tenant_update(tenant_id):
    """Save tenant edits and redirect to admin."""
    tenant_name = request.form.get('tenant_name', '').strip()
    contact_name = request.form.get('contact_name', '').strip()

    if not tenant_name:
        return "Tenant name is required", 400

    db = get_db()
    try:
        db.execute('''
            UPDATE tenants
            SET name = ?, contact_name = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
        ''', (tenant_name, contact_name or None, tenant_id))
        db.commit()
        logger.info(f"Updated tenant {tenant_id}: name={tenant_name}, contact={contact_name}")
    except sqlite3.IntegrityError:
        return "A tenant with that name already exists", 400
    finally:
        db.close()

    return redirect(url_for('admin_dashboard'))


@app.route('/api/admin/tenants/<int:tenant_id>/archive', methods=['POST'])
def api_admin_tenant_archive(tenant_id):
    """Archive a tenant and all its projects."""
    db = get_db()
    try:
        db.execute(
            "UPDATE projects SET status = 'archived', updated_at = CURRENT_TIMESTAMP WHERE tenant_id = ?",
            (tenant_id,)
        )
        db.execute(
            "UPDATE tenants SET status = 'archived', updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (tenant_id,)
        )
        db.commit()
        logger.info(f"Archived tenant {tenant_id} and all its projects")
        log_transaction('archive_tenant', metadata={'tenant_id': tenant_id})
        return jsonify({'success': True})
    except Exception as e:
        db.rollback()
        logger.error(f"Error archiving tenant {tenant_id}: {e}")
        return jsonify({'error': str(e)}), 500
    finally:
        db.close()


@app.route('/admin/tenants/new')
def admin_tenant_new():
    """Show form to create new tenant."""
    return render_template('admin/tenant_form.html')


@app.route('/admin/tenants/create', methods=['POST'])
def admin_tenant_create():
    """Create a new tenant."""
    tenant_name = request.form.get('tenant_name', '').strip()

    if not tenant_name:
        return jsonify({'error': 'Tenant name is required'}), 400

    db = get_db()
    try:
        cursor = db.cursor()
        cursor.execute('''
            INSERT INTO tenants (name, status)
            VALUES (?, 'active')
        ''', (tenant_name,))
        db.commit()
        tenant_id = cursor.lastrowid
        logger.info(f"Created tenant: {tenant_name} (ID: {tenant_id})")
        log_transaction('add_tenant', metadata={'tenant_id': tenant_id, 'name': tenant_name})
        return jsonify({'success': True, 'tenant_id': tenant_id})
    except sqlite3.IntegrityError:
        return jsonify({'error': 'Tenant with this name already exists'}), 400
    except Exception as e:
        logger.error(f"Error creating tenant: {e}")
        return jsonify({'error': str(e)}), 500
    finally:
        db.close()


@app.route('/api/admin/tenants')
def api_admin_tenants():
    """API endpoint for active tenants list."""
    db = get_db()
    tenants = db.execute('''
        SELECT id, name, created_at, updated_at, status
        FROM tenants
        WHERE status != 'archived'
        ORDER BY name
    ''').fetchall()
    db.close()
    return jsonify([dict(t) for t in tenants])


# =============================================================================
# NAVIGATION API ROUTES
# =============================================================================

@app.route('/api/navigation/hierarchy')
def api_navigation_hierarchy():
    """Get complete tenant -> project -> quote hierarchy."""
    db = get_db()

    # Get all tenants with their projects and quote counts
    tenants = db.execute('''
        SELECT
            t.id,
            t.name,
            t.status,
            COUNT(DISTINCT p.id) as project_count
        FROM tenants t
        LEFT JOIN projects p ON p.tenant_id = t.id
        WHERE t.status = 'active'
        GROUP BY t.id
        ORDER BY t.name
    ''').fetchall()

    hierarchy = []
    for tenant in tenants:
        tenant_dict = dict(tenant)

        # Get projects for this tenant with quote counts
        projects = db.execute('''
            SELECT
                p.id,
                p.name,
                p.description,
                p.status,
                COUNT(q.id) as quote_count
            FROM projects p
            LEFT JOIN quotes q ON q.project_id = p.id
            WHERE p.tenant_id = ? AND p.status != 'archived'
            GROUP BY p.id
            ORDER BY p.name
        ''', (tenant['id'],)).fetchall()

        tenant_dict['projects'] = [dict(p) for p in projects]
        hierarchy.append(tenant_dict)

    db.close()
    return jsonify(hierarchy)


@app.route('/api/projects/<int:project_id>/quotes')
def api_project_quotes(project_id):
    """Get all quotes for a specific project."""
    db = get_db()
    quotes = db.execute('''
        SELECT id, quote_id, vendor, customer_name, quote_date, expiry_date,
               total_amount, currency, description, tenant_name, project_name, ica, status, po_comments
        FROM quotes
        WHERE project_id = ? AND status != 'archived'
        ORDER BY quote_date DESC
    ''', (project_id,)).fetchall()
    db.close()
    return jsonify([dict(q) for q in quotes])


@app.route('/api/quotes/unassigned')
def api_unassigned_quotes():
    """Get quotes not assigned to any project."""
    db = get_db()
    quotes = db.execute('''
        SELECT id, quote_id, vendor, customer_name, quote_date, expiry_date,
               total_amount, currency, description, tenant_name, project_name, ica, status, po_comments
        FROM quotes
        WHERE project_id IS NULL AND status != 'archived'
        ORDER BY quote_date DESC
    ''').fetchall()
    db.close()
    return jsonify([dict(q) for q in quotes])


# =============================================================================
# PROJECT MANAGEMENT ROUTES
# =============================================================================

@app.route('/admin/projects')
def admin_projects():
    """List all projects grouped by tenant."""
    db = get_db()

    # Get all active tenants
    tenants = db.execute("SELECT id, name, status FROM tenants WHERE status != 'archived' ORDER BY name").fetchall()

    # Build tenant-project hierarchy
    tenant_projects = []
    for tenant in tenants:
        tenant_dict = dict(tenant)

        # Get projects for this tenant
        projects = db.execute('''
            SELECT p.id, p.name, p.description, p.comments, p.status, p.created_at,
                   COUNT(q.id) as quote_count
            FROM projects p
            LEFT JOIN quotes q ON q.project_id = p.id
            WHERE p.tenant_id = ? AND p.status != 'archived'
            GROUP BY p.id
            ORDER BY p.name
        ''', (tenant['id'],)).fetchall()

        tenant_dict['projects'] = [dict(p) for p in projects]
        tenant_dict['project_count'] = len(projects)
        tenant_projects.append(tenant_dict)

    db.close()
    return render_template('admin/projects_list.html', tenant_projects=tenant_projects)


@app.route('/admin/quotes')
def admin_quotes():
    """List all quotes in the system."""
    db = get_db()
    quotes = db.execute("""
        SELECT id, quote_id, vendor, customer_name, quote_date, expiry_date,
               total_amount, currency, tenant_name, project_name, ica, uploaded_at, status, po_comments
        FROM quotes
        ORDER BY uploaded_at DESC
    """).fetchall()
    db.close()
    return render_template('admin/quotes_list.html', quotes=[dict(q) for q in quotes])


@app.route('/api/admin/quotes/<int:quote_id>/delete', methods=['POST'])
def api_admin_quote_delete(quote_id):
    """Delete a quote and all associated data."""
    db = get_db()
    try:
        db.execute("PRAGMA foreign_keys = ON")
        row = db.execute("SELECT pdf_path FROM quotes WHERE id = ?", (quote_id,)).fetchone()
        if not row:
            return jsonify({'error': 'Quote not found'}), 404
        db.execute("DELETE FROM quotes WHERE id = ?", (quote_id,))
        db.commit()
        log_transaction('delete_quote', metadata={'deleted_quote_id': quote_id})
        # Remove PDF file if it exists
        if row['pdf_path'] and os.path.exists(row['pdf_path']):
            try:
                os.remove(row['pdf_path'])
            except OSError:
                pass
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        db.close()


@app.route('/api/quotes/<int:quote_id>/archive', methods=['POST'])
def api_quote_archive(quote_id):
    """Archive a quote (hides it from the frontend)."""
    db = get_db()
    try:
        row = db.execute("SELECT id FROM quotes WHERE id = ?", (quote_id,)).fetchone()
        if not row:
            return jsonify({'error': 'Quote not found'}), 404
        db.execute("UPDATE quotes SET status = 'archived' WHERE id = ?", (quote_id,))
        db.commit()
        log_transaction('archive_quote', quote_id=quote_id)
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        db.close()


# =============================================================================
# MANUFACTURERS API
# =============================================================================

@app.route('/api/manufacturers')
def api_manufacturers():
    db = get_db()
    rows = db.execute("SELECT id, name FROM manufacturers ORDER BY name").fetchall()
    db.close()
    return jsonify([dict(r) for r in rows])


# =============================================================================
# LEARNED COMPONENTS API (read-only, for dropdown population)
# =============================================================================

@app.route('/api/components/learned')
def api_learned_components():
    db = get_db()
    rows = db.execute("""
        SELECT DISTINCT component_type, manufacturer, part_number, model, specs_json
        FROM learned_components
        WHERE part_number IS NOT NULL AND part_number != ''
        ORDER BY component_type, manufacturer, part_number
    """).fetchall()
    db.close()
    return jsonify([dict(r) for r in rows])


# =============================================================================
# BASE CONFIGS API
# =============================================================================

@app.route('/api/projects/<int:project_id>/configs')
def api_project_configs(project_id):
    db = get_db()
    configs = db.execute("""
        SELECT id, config_name, created_at FROM base_configs
        WHERE project_id = ? ORDER BY created_at DESC
    """, (project_id,)).fetchall()
    result = []
    for c in configs:
        components = db.execute("""
            SELECT dc.id, dc.component_type, dc.part_number, dc.specs, dc.model,
                   m.name AS manufacturer_name, bcc.quantity
            FROM base_config_components bcc
            JOIN defined_components dc ON bcc.component_id = dc.id
            LEFT JOIN manufacturers m  ON dc.manufacturer_id = m.id
            WHERE bcc.config_id = ?
            ORDER BY dc.component_type, dc.part_number
        """, (c['id'],)).fetchall()
        result.append({**dict(c), 'components': [dict(comp) for comp in components]})
    db.close()
    return jsonify(result)


@app.route('/api/projects/<int:project_id>/configs', methods=['POST'])
def api_create_config(project_id):
    data = request.get_json()
    if not data or not data.get('config_name'):
        return jsonify({'error': 'config_name required'}), 400
    db = get_db()
    try:
        cursor = db.cursor()
        cursor.execute(
            "INSERT INTO base_configs (config_name, project_id) VALUES (?, ?)",
            (data['config_name'].strip(), project_id)
        )
        config_id = cursor.lastrowid
        for comp in data.get('components', []):
            # Insert into defined_components, then link via junction
            cursor.execute("""
                INSERT INTO defined_components (component_type, manufacturer_id, part_number, specs, model)
                VALUES (?, ?, ?, ?, ?)
            """, (
                comp.get('component_type'),
                comp.get('manufacturer_id') or None,
                comp.get('part_number'),
                comp.get('specs'),
                comp.get('model'),
            ))
            component_id = cursor.lastrowid
            cursor.execute(
                "INSERT INTO base_config_components (config_id, component_id, quantity) VALUES (?, ?, ?)",
                (config_id, component_id, comp.get('quantity', 1))
            )
        db.commit()
        comp_count = len(data.get('components', []))
        # 5 base (add_config) + 1 per component (add_config_component)
        log_transaction(
            'add_config',
            config_id=config_id,
            metadata={
                'config_name': data['config_name'],
                'project_id': project_id,
                'component_count': comp_count,
            },
            tokens_override=5 + comp_count
        )
        return jsonify({'success': True, 'config_id': config_id})
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        db.close()


@app.route('/api/configs/<int:config_id>', methods=['DELETE'])
def api_delete_config(config_id):
    db = get_db()
    try:
        db.execute("DELETE FROM base_configs WHERE id = ?", (config_id,))
        db.commit()
        log_transaction('delete_config', config_id=config_id)
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        db.close()


# =============================================================================
# CONFIG MATCHING — score vendor quotes against a desired base configuration
# =============================================================================

@app.route('/api/configs/<int:config_id>/match')
def api_config_match(config_id):
    """
    Score every quote in the same project against a base config.

    Matching logic (two levels):
    - Type coverage  : % of config component_types present as quote categories
    - Part coverage  : % of config part_numbers found in quote product_numbers (exact)

    Also logs a config_match transaction.
    """
    db = get_db()
    try:
        # Resolve config → project
        config_row = db.execute(
            "SELECT id, config_name, project_id FROM base_configs WHERE id = ?", (config_id,)
        ).fetchone()
        if not config_row:
            return jsonify({'error': 'Config not found'}), 404

        project_id = config_row['project_id']

        # Config components
        config_comps = db.execute("""
            SELECT dc.component_type, dc.part_number, dc.model,
                   m.name AS manufacturer_name, bcc.quantity
            FROM base_config_components bcc
            JOIN defined_components dc ON bcc.component_id = dc.id
            LEFT JOIN manufacturers m  ON dc.manufacturer_id = m.id
            WHERE bcc.config_id = ?
            ORDER BY dc.component_type
        """, (config_id,)).fetchall()
        config_comps = [dict(c) for c in config_comps]

        if not config_comps:
            return jsonify({
                'config_id': config_id,
                'config_name': config_row['config_name'],
                'quotes': [],
                'message': 'Config has no components defined'
            })

        # All active quotes in this project
        quotes = db.execute("""
            SELECT id, quote_id, vendor, total_amount, currency,
                   quote_date, expiry_date, description
            FROM quotes
            WHERE project_id = ? AND status != 'archived'
            ORDER BY quote_date DESC
        """, (project_id,)).fetchall()

        results = []
        for quote in quotes:
            qid = quote['id']

            # Quote line items
            line_items = db.execute("""
                SELECT category, product_number, description, quantity, unit_price, total_price
                FROM line_items
                WHERE quote_id = ?
            """, (qid,)).fetchall()

            quote_categories  = {li['category'] for li in line_items if li['category']}
            quote_part_numbers = {
                (li['product_number'] or '').strip().upper()
                for li in line_items if li['product_number']
            }

            # Score each config component
            component_results = []
            type_hits = 0
            part_hits = 0
            for comp in config_comps:
                ctype = comp['component_type'] or ''
                cpn   = (comp['part_number'] or '').strip().upper()

                type_match = ctype in quote_categories
                part_match = bool(cpn) and cpn in quote_part_numbers

                if type_match:
                    type_hits += 1
                if part_match:
                    part_hits += 1

                # Find matching line items for context
                matched_items = [
                    dict(li) for li in line_items
                    if li['category'] == ctype or
                       (cpn and (li['product_number'] or '').strip().upper() == cpn)
                ]

                component_results.append({
                    'component_type':  ctype,
                    'part_number':     comp['part_number'],
                    'model':           comp['model'],
                    'manufacturer':    comp['manufacturer_name'],
                    'quantity_required': comp['quantity'],
                    'type_match':      type_match,
                    'part_match':      part_match,
                    'matched_items':   matched_items,
                })

            total = len(config_comps)
            type_coverage_pct = round(type_hits / total * 100, 1) if total else 0
            part_coverage_pct = round(part_hits / total * 100, 1) if total else 0

            results.append({
                'quote_id':         qid,
                'quote_ref':        quote['quote_id'],
                'vendor':           quote['vendor'],
                'total_amount':     quote['total_amount'],
                'currency':         quote['currency'],
                'quote_date':       quote['quote_date'],
                'expiry_date':      quote['expiry_date'],
                'description':      quote['description'],
                'type_coverage_pct': type_coverage_pct,
                'part_coverage_pct': part_coverage_pct,
                'type_hits':        type_hits,
                'part_hits':        part_hits,
                'total_components': total,
                'components':       component_results,
            })

        # Sort by type coverage descending
        results.sort(key=lambda r: r['type_coverage_pct'], reverse=True)

        log_transaction(
            'config_match',
            config_id=config_id,
            metadata={
                'config_name': config_row['config_name'],
                'project_id': project_id,
                'quotes_evaluated': len(results),
            }
        )

        return jsonify({
            'config_id':   config_id,
            'config_name': config_row['config_name'],
            'project_id':  project_id,
            'quotes':      results,
        })
    finally:
        db.close()


# =============================================================================
# ADMIN: TRANSACTIONS LEDGER
# =============================================================================

@app.route('/admin/transactions')
def admin_transactions():
    """Transaction ledger view."""
    db = get_db()
    rows = db.execute("""
        SELECT t.id, t.user_name, t.tokens_charged, t.created_at,
               t.quote_id, t.config_id, t.metadata_json,
               tt.code AS type_code, tt.label AS type_label, tt.token_cost
        FROM transactions t
        JOIN transaction_types tt ON tt.id = t.type_id
        ORDER BY t.created_at DESC
        LIMIT 500
    """).fetchall()

    summary = db.execute("""
        SELECT COUNT(*) AS total_txns, COALESCE(SUM(tokens_charged),0) AS total_tokens
        FROM transactions
    """).fetchone()

    type_summary = db.execute("""
        SELECT tt.label, tt.code, COUNT(*) AS cnt,
               COALESCE(SUM(t.tokens_charged),0) AS total_tokens
        FROM transactions t
        JOIN transaction_types tt ON tt.id = t.type_id
        GROUP BY tt.id
        ORDER BY total_tokens DESC
    """).fetchall()

    db.close()

    transactions = []
    for r in rows:
        d = dict(r)
        try:
            d['metadata'] = json.loads(r['metadata_json']) if r['metadata_json'] else {}
        except Exception:
            d['metadata'] = {}
        transactions.append(d)

    return render_template(
        'admin/transactions_list.html',
        transactions=transactions,
        summary=dict(summary),
        type_summary=[dict(ts) for ts in type_summary],
    )


@app.route('/api/admin/transaction-types')
def api_transaction_types():
    """List all transaction types with their token costs."""
    db = get_db()
    rows = db.execute(
        "SELECT id, code, label, description, token_cost FROM transaction_types ORDER BY token_cost DESC"
    ).fetchall()
    db.close()
    return jsonify([dict(r) for r in rows])


@app.route('/api/admin/transactions/summary')
def api_transactions_summary():
    """Aggregated transaction stats."""
    db = get_db()
    overall = db.execute("""
        SELECT COUNT(*) AS total_txns, COALESCE(SUM(tokens_charged),0) AS total_tokens
        FROM transactions
    """).fetchone()
    by_type = db.execute("""
        SELECT tt.code, tt.label, COUNT(*) AS cnt,
               COALESCE(SUM(t.tokens_charged),0) AS total_tokens
        FROM transactions t
        JOIN transaction_types tt ON tt.id = t.type_id
        GROUP BY tt.id
        ORDER BY total_tokens DESC
    """).fetchall()
    db.close()
    return jsonify({
        'overall': dict(overall),
        'by_type': [dict(r) for r in by_type],
    })


# =============================================================================
# ADMIN: ENTERED COMPONENTS
# =============================================================================

@app.route('/admin/entered-components')
def admin_entered_components():
    db = get_db()
    rows = db.execute("""
        SELECT dc.id, dc.component_type, dc.part_number, dc.specs, dc.model,
               dc.created_at, m.name AS manufacturer_name,
               bc.config_name, p.name AS project_name, t.name AS tenant_name
        FROM defined_components dc
        LEFT JOIN manufacturers       m   ON dc.manufacturer_id = m.id
        LEFT JOIN base_config_components bcc ON bcc.component_id = dc.id
        LEFT JOIN base_configs         bc  ON bcc.config_id = bc.id
        LEFT JOIN projects             p   ON bc.project_id = p.id
        LEFT JOIN tenants              t   ON p.tenant_id = t.id
        ORDER BY dc.created_at DESC
    """).fetchall()
    db.close()
    return render_template('admin/entered_components.html', components=[dict(r) for r in rows])


@app.route('/admin/components')
def admin_components():
    """List all distinct learned components from the components table."""
    import json as _json
    db = get_db()
    rows = db.execute("""
        SELECT c.component_type, c.manufacturer, c.part_number, c.model,
               c.specs_json, COUNT(*) as seen,
               MIN(li.description) as description
        FROM learned_components c
        LEFT JOIN line_items li ON c.line_item_id = li.id
        GROUP BY c.component_type, c.part_number
        ORDER BY c.component_type, c.part_number
    """).fetchall()
    db.close()

    components = []
    for row in rows:
        d = dict(row)
        try:
            d['specs'] = _json.loads(row['specs_json']) if row['specs_json'] else {}
        except Exception:
            d['specs'] = {}
        components.append(d)

    return render_template('admin/components_list.html', components=components)


@app.route('/admin/tenants/<int:tenant_id>/projects')
def admin_tenant_projects(tenant_id):
    """Show projects for a specific tenant with their quotes."""
    db = get_db()

    # Get tenant info
    tenant = db.execute('SELECT * FROM tenants WHERE id = ?', (tenant_id,)).fetchone()
    if not tenant:
        return "Tenant not found", 404

    # Get tenant's projects with quote counts
    projects = db.execute('''
        SELECT p.id, p.name, p.description, p.status, p.created_at,
               COUNT(q.id) as quote_count
        FROM projects p
        LEFT JOIN quotes q ON q.project_id = p.id
        WHERE p.tenant_id = ? AND p.status != 'archived'
        GROUP BY p.id
        ORDER BY p.name
    ''', (tenant_id,)).fetchall()

    # Get quotes for each project
    projects_with_quotes = []
    for project in projects:
        project_dict = dict(project)

        # Get quotes for this project
        quotes = db.execute('''
            SELECT id, quote_id, vendor, customer_name, quote_date, expiry_date,
                   total_amount, currency, description, ica, uploaded_at, po_comments
            FROM quotes
            WHERE project_id = ?
            ORDER BY uploaded_at DESC
        ''', (project['id'],)).fetchall()

        project_dict['quotes'] = [dict(q) for q in quotes]
        projects_with_quotes.append(project_dict)

    db.close()
    return render_template('admin/tenant_projects.html',
                          tenant=dict(tenant),
                          projects=projects_with_quotes)


@app.route('/admin/projects/<int:project_id>/quotes')
def admin_project_quotes(project_id):
    """Show quotes for a specific project."""
    db = get_db()

    # Get project info with tenant
    project = db.execute('''
        SELECT p.*, t.name as tenant_name
        FROM projects p
        LEFT JOIN tenants t ON t.id = p.tenant_id
        WHERE p.id = ?
    ''', (project_id,)).fetchone()

    if not project:
        return "Project not found", 404

    # Get project's quotes
    quotes = db.execute('''
        SELECT id, quote_id, vendor, customer_name, quote_date, expiry_date,
               total_amount, currency, description, ica, uploaded_at, po_comments
        FROM quotes
        WHERE project_id = ?
        ORDER BY quote_date DESC
    ''', (project_id,)).fetchall()

    db.close()
    return render_template('admin/project_quotes.html',
                          project=dict(project),
                          quotes=[dict(q) for q in quotes])


@app.route('/admin/projects/new')
def admin_project_new():
    """Show form to create new project."""
    db = get_db()
    tenants = db.execute("SELECT id, name FROM tenants WHERE status != 'archived' ORDER BY name").fetchall()
    db.close()
    return render_template('admin/project_form.html', tenants=[dict(t) for t in tenants])


@app.route('/admin/projects/create', methods=['POST'])
def admin_project_create():
    """Create a new project."""
    project_name = request.form.get('project_name', '').strip()
    tenant_id = request.form.get('tenant_id', '').strip()
    description = request.form.get('description', '').strip()

    if not project_name or not tenant_id:
        return jsonify({'error': 'Project name and tenant are required'}), 400

    db = get_db()
    try:
        cursor = db.cursor()
        cursor.execute('''
            INSERT INTO projects (name, tenant_id, description, status)
            VALUES (?, ?, ?, 'active')
        ''', (project_name, int(tenant_id), description))
        db.commit()
        project_id = cursor.lastrowid
        logger.info(f"Created project: {project_name} for tenant {tenant_id} (ID: {project_id})")
        log_transaction('add_project', metadata={
            'project_id': project_id, 'name': project_name, 'tenant_id': int(tenant_id)
        })
        return jsonify({'success': True, 'project_id': project_id})
    except sqlite3.IntegrityError:
        return jsonify({'error': 'Project with this name already exists for this tenant'}), 400
    except Exception as e:
        logger.error(f"Error creating project: {e}")
        return jsonify({'error': str(e)}), 500
    finally:
        db.close()


@app.route('/admin/projects/<int:project_id>/edit')
def admin_project_edit(project_id):
    """Show edit form for a project."""
    db = get_db()
    project = db.execute(
        'SELECT id, name, comments, status FROM projects WHERE id = ?', (project_id,)
    ).fetchone()
    db.close()
    if not project:
        return "Project not found", 404
    return render_template('admin/project_edit.html', project=dict(project))


@app.route('/admin/projects/<int:project_id>/update', methods=['POST'])
def admin_project_update(project_id):
    """Save project edits and redirect to admin."""
    project_name = request.form.get('project_name', '').strip()
    comments = request.form.get('project_comments', '').strip()

    if not project_name:
        return "Project name is required", 400

    db = get_db()
    try:
        db.execute('''
            UPDATE projects
            SET name = ?, comments = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
        ''', (project_name, comments or None, project_id))
        db.commit()
        logger.info(f"Updated project {project_id}: name={project_name}")
    except sqlite3.IntegrityError:
        return "A project with that name already exists for this tenant", 400
    finally:
        db.close()

    return redirect(url_for('admin_dashboard'))


# =============================================================================
# QUICKSPEC PARSER — parse HPE QuickSpec PDFs into server + component catalog
# =============================================================================

from quickspec_parser import QuickSpecParser


def _save_quickspec_to_db(server_data: dict, components: list, pdf_path: str) -> dict:
    """
    Upsert server + components into the catalog in a single transaction.

    - servers:                  INSERT OR IGNORE on model_name (UNIQUE)
    - component_catalog:        INSERT OR IGNORE on (component_type, manufacturer, model)
                                Uses part_number as model field per design spec
    - server_quickspec_components: INSERT OR IGNORE on (server_id, catalog_id)

    Returns summary: {server_name, server_id, components_new, components_existing, total}
    """
    db = get_db()
    try:
        db.execute("PRAGMA foreign_keys = ON")

        vendor = server_data.get('manufacturer', 'HPE')
        mfr_row = db.execute(
            "SELECT id FROM manufacturers WHERE name = ?", (vendor,)
        ).fetchone()
        if not mfr_row:
            db.execute("INSERT OR IGNORE INTO manufacturers (name) VALUES (?)", (vendor,))
            mfr_row = db.execute("SELECT id FROM manufacturers WHERE name = ?", (vendor,)).fetchone()
        manufacturer_id = mfr_row['id'] if mfr_row else None

        # Upsert server
        db.execute("""
            INSERT OR IGNORE INTO servers
                (manufacturer_id, model_name, model_number, form_factor, generation, pdf_path)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            manufacturer_id,
            server_data['model_name'],
            server_data.get('model_number'),
            server_data.get('form_factor'),
            server_data.get('generation'),
            pdf_path,
        ))

        server_row = db.execute(
            "SELECT id FROM servers WHERE model_name = ?", (server_data['model_name'],)
        ).fetchone()
        server_id = server_row['id']

        # Replace strategy: clear all existing component links for this server so a
        # re-upload fully replaces the classification (avoids stale Disk entries for NICs etc.)
        db.execute("DELETE FROM server_quickspec_components WHERE server_id = ?", (server_id,))

        TYPE_NORMALIZER = {
            'CPU': 'CPU', 'Memory': 'Memory', 'Disk': 'Disk',
            'Storage Controller': 'Storage Controller', 'Network Card': 'Network Card',
            'Power Supply': 'Power Supply', 'GPU': 'GPU',
            'Additional Hardware': 'Additional Hardware',
        }

        components_new      = 0
        components_existing = 0

        for comp in components:
            ctype = TYPE_NORMALIZER.get(comp['component_type'], 'Additional Hardware')
            pn    = comp['part_number']
            desc  = comp.get('description', '')

            # Upsert catalog entry — part_number used as model per design spec
            db.execute("""
                INSERT OR IGNORE INTO component_catalog
                    (component_type, manufacturer, model, part_number, description, data_source)
                VALUES (?, ?, ?, ?, ?, 'quickspec_pdf')
            """, (ctype, vendor, pn, pn, desc))

            cat_row = db.execute(
                "SELECT id FROM component_catalog "
                "WHERE component_type = ? AND manufacturer = ? AND model = ?",
                (ctype, vendor, pn)
            ).fetchone()
            if not cat_row:
                continue
            catalog_id = cat_row['id']

            db.execute("""
                INSERT OR IGNORE INTO server_quickspec_components
                    (server_id, catalog_id, component_role, is_standard, is_optional)
                VALUES (?, ?, ?, ?, ?)
            """, (
                server_id, catalog_id,
                comp.get('component_role'),
                1 if comp.get('is_standard') else 0,
                1 if comp.get('is_optional') else 0,
            ))

            if already_linked:
                components_existing += 1
            else:
                components_new += 1

        db.commit()
        return {
            'server_name':         server_data['model_name'],
            'server_id':           server_id,
            'model_number':        server_data.get('model_number'),
            'form_factor':         server_data.get('form_factor'),
            'generation':          server_data.get('generation'),
            'components_new':      components_new,
            'components_existing': components_existing,
            'total':               len(components),
        }
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


@app.route('/admin/quickspec/upload')
def admin_quickspec_upload_view():
    """QuickSpec upload form view (injected into admin dashboard)."""
    return render_template('admin/quickspec_upload.html')


@app.route('/api/admin/quickspec/upload', methods=['POST'])
def api_quickspec_upload():
    """
    Upload and parse an HPE QuickSpec PDF.
    Auto-saves on success. Returns JSON summary.
    """
    if 'file' not in request.files:
        return jsonify({'error': 'No file provided'}), 400

    file = request.files['file']
    if not file.filename or not allowed_file(file.filename):
        return jsonify({'error': 'Only PDF files allowed'}), 400

    filename = secure_filename(file.filename)
    filepath = os.path.join(DATA_DIR, 'quickspecs', filename)
    file.save(filepath)

    try:
        is_valid, err = validate_pdf(filepath)
        if not is_valid:
            os.remove(filepath)
            return jsonify({'error': f'PDF validation failed: {err}'}), 400

        parser = QuickSpecParser(filepath)
        result = parser.parse()

        server     = result['server']
        components = result['components']

        if not server.get('model_name'):
            os.remove(filepath)
            return jsonify({'error': 'Could not detect server model from PDF. '
                                     'Supports HPE QuickSpec and Dell Technical Guide PDFs.'}), 422

        summary = _save_quickspec_to_db(server, components, filepath)

        log_transaction(
            'parse_quickspec',
            metadata={
                'filename':        filename,
                'server_model':    server['model_name'],
                'components_new':  summary['components_new'],
                'total':           summary['total'],
                'page_count':      result.get('page_count'),
            },
            tokens_override=5 + summary['total']
        )

        logger.info(
            f"QuickSpec saved: {server['model_name']} — "
            f"{summary['components_new']} new, {summary['components_existing']} existing components"
        )
        return jsonify(summary)

    except Exception as e:
        logger.error(f"QuickSpec parse error for {filename}: {e}", exc_info=True)
        if os.path.exists(filepath):
            try:
                os.remove(filepath)
            except OSError:
                pass
        return jsonify({'error': str(e)}), 500


@app.route('/admin/servers')
def admin_servers():
    """Server catalog list view."""
    db = get_db()
    servers = db.execute("""
        SELECT s.id, s.model_name, s.model_number, s.form_factor, s.generation,
               s.created_at, m.name AS manufacturer_name,
               COUNT(sqc.id) AS component_count
        FROM servers s
        LEFT JOIN manufacturers m ON s.manufacturer_id = m.id
        LEFT JOIN server_quickspec_components sqc ON sqc.server_id = s.id
        GROUP BY s.id
        ORDER BY s.model_name
    """).fetchall()
    db.close()
    return render_template('admin/servers_list.html', servers=[dict(s) for s in servers])


@app.route('/api/catalog/components')
def api_catalog_components():
    """Return components of a given type that appear in at least one server spec.

    For Network Card, applies an additional description filter so that stale
    catalog entries (disks, bezel kits, etc. incorrectly stored as Network Card
    from old parses) don't pollute the NIC picker dropdown.
    """
    ctype = request.args.get('type', '')
    db = get_db()
    rows = db.execute("""
        SELECT DISTINCT cc.part_number, cc.description, cc.manufacturer
        FROM component_catalog cc
        JOIN server_quickspec_components sqc ON sqc.catalog_id = cc.id
        WHERE cc.component_type = ?
        ORDER BY cc.manufacturer, cc.part_number
    """, (ctype,)).fetchall()
    db.close()

    result = [dict(r) for r in rows]

    if ctype == 'Network Card':
        import re
        _nic_re = re.compile(
            r'ethernet|infiniband|adapter|omni-path|\bnic\b|gbe\b|10g|25g|40g|100g|'
            r'sfp|qsfp|osfp|ocp\s*\d|\bfcoe\b|\broce\b',
            re.IGNORECASE
        )
        result = [r for r in result if r['description'] and _nic_re.search(r['description'])]
        # Deduplicate by part_number — prefer named manufacturer over 'Unknown'
        seen = {}
        for r in result:
            pn = r['part_number']
            if pn not in seen or seen[pn]['manufacturer'] in (None, 'Unknown'):
                seen[pn] = r
        result = sorted(seen.values(), key=lambda r: (r['manufacturer'] or '', r['part_number']))

    return jsonify(result)


@app.route('/api/admin/servers')
def api_admin_servers():
    """JSON list of servers with component counts."""
    db = get_db()
    rows = db.execute("""
        SELECT s.id, s.model_name, s.model_number, s.form_factor, s.generation,
               s.created_at, m.name AS manufacturer_name,
               COUNT(sqc.id) AS component_count
        FROM servers s
        LEFT JOIN manufacturers m ON s.manufacturer_id = m.id
        LEFT JOIN server_quickspec_components sqc ON sqc.server_id = s.id
        GROUP BY s.id
        ORDER BY s.model_name
    """).fetchall()
    db.close()
    return jsonify([dict(r) for r in rows])


@app.route('/api/servers/<int:server_id>/components')
def api_server_components_json(server_id):
    """JSON: components for a server grouped by component_type."""
    db = get_db()
    rows = db.execute("""
        SELECT cc.component_type, cc.part_number AS part_number,
               cc.description, cc.manufacturer,
               sqc.component_role, sqc.is_standard, sqc.is_optional
        FROM server_quickspec_components sqc
        JOIN component_catalog cc ON sqc.catalog_id = cc.id
        WHERE sqc.server_id = ?
        ORDER BY cc.component_type,
                 sqc.is_standard DESC,
                 cc.part_number
    """, (server_id,)).fetchall()
    db.close()
    from collections import defaultdict
    grouped = defaultdict(list)
    for r in rows:
        grouped[r['component_type']].append(dict(r))
    return jsonify(grouped)


@app.route('/admin/servers/<int:server_id>/components')
def admin_server_components(server_id):
    """Detail view: all components linked to a server via QuickSpec."""
    db = get_db()
    server = db.execute("""
        SELECT s.*, m.name AS manufacturer_name
        FROM servers s
        LEFT JOIN manufacturers m ON s.manufacturer_id = m.id
        WHERE s.id = ?
    """, (server_id,)).fetchone()
    if not server:
        return 'Server not found', 404

    components = db.execute("""
        SELECT cc.component_type, cc.model AS part_number, cc.description,
               sqc.component_role, sqc.is_standard, sqc.is_optional, sqc.created_at
        FROM server_quickspec_components sqc
        JOIN component_catalog cc ON sqc.catalog_id = cc.id
        WHERE sqc.server_id = ?
        ORDER BY cc.component_type, cc.model
    """, (server_id,)).fetchall()
    db.close()
    return render_template(
        'admin/server_components.html',
        server=dict(server),
        components=[dict(c) for c in components],
    )


if __name__ == '__main__':
    # Initialize database if it doesn't exist
    if not os.path.exists(app.config['DATABASE']):
        init_db()
    else:
        migrate_db()

    logger.info(f"Loaded DB: {app.config['DATABASE']}")
    app.run(debug=True, host='0.0.0.0', port=5001)
