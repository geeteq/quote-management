import sqlite3
import json
import os
import logging
from flask import Flask, render_template, request, jsonify, redirect, url_for
from werkzeug.utils import secure_filename
from parser import QuoteParser
from component_registry import ComponentRegistry
from datetime import datetime

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('app.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max file size
app.config['DATABASE'] = 'quotes.db'

ALLOWED_EXTENSIONS = {'pdf'}

# Ensure upload folder exists
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)


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
    with open('schema_normalized.sql', 'r') as f:
        db.executescript(f.read())
    db.commit()
    db.close()


def save_quote_to_db(quote_data, line_items, pdf_path, tenant_id=None, project_id=None, tenant_name='', project_name='', ica=''):
    """Save parsed quote data to database."""
    logger.debug(f"Saving quote: {quote_data.get('quote_id')} with {len(line_items)} items")

    db = get_db()
    cursor = db.cursor()

    try:
        # Check for duplicate quote_id before inserting
        existing = cursor.execute(
            'SELECT id FROM quotes WHERE quote_id = ?', (quote_data.get('quote_id'),)
        ).fetchone()
        if existing:
            raise ValueError(f"Quote '{quote_data.get('quote_id')}' already exists (record ID {existing[0]}). Delete it first or upload a different file.")

        # Insert quote (with parameterized queries for SQL injection protection)
        cursor.execute('''
            INSERT INTO quotes (quote_id, vendor, customer_name, quote_date, expiry_date,
                               total_amount, currency, description, pdf_path,
                               tenant_id, project_id, tenant_name, project_name, ica)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            quote_data.get('quote_id'),
            quote_data.get('vendor'),
            quote_data.get('customer_name'),
            quote_data.get('quote_date'),
            quote_data.get('expiry_date'),
            quote_data.get('total_amount'),
            quote_data.get('currency', 'CAD'),
            quote_data.get('description'),
            pdf_path,
            tenant_id,
            project_id,
            tenant_name,
            project_name,
            ica
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

    # Get line items with normalized catalog specs
    line_items = db.execute('''
        SELECT
            li.*,
            c.component_type,
            c.manufacturer,
            c.specs_json,
            GROUP_CONCAT(cl.url) as urls,
            cc.id as catalog_id,
            cc.model as catalog_model,
            cc.manufacturer as catalog_manufacturer
        FROM line_items li
        LEFT JOIN components c ON c.line_item_id = li.id
        LEFT JOIN component_links cl ON cl.component_id = c.id
        LEFT JOIN component_catalog cc ON cc.id = li.catalog_component_id
        WHERE li.quote_id = ?
        GROUP BY li.id
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

        # Try to get normalized specs if available
        if item_dict.get('catalog_id'):
            normalized_specs = get_normalized_specs(item_dict['catalog_id'], category)
            if normalized_specs:
                item_dict['specs'] = normalized_specs
            elif item_dict['specs_json']:
                item_dict['specs'] = json.loads(item_dict['specs_json'])
            else:
                item_dict['specs'] = {}
        elif item_dict['specs_json']:
            item_dict['specs'] = json.loads(item_dict['specs_json'])
        else:
            item_dict['specs'] = {}

        if item_dict['urls']:
            item_dict['urls'] = item_dict['urls'].split(',')
        else:
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


def get_all_quotes():
    """Get all quotes for listing."""
    db = get_db()
    quotes = db.execute('''
        SELECT id, quote_id, vendor, customer_name, quote_date, expiry_date,
               total_amount, currency, description, tenant_name, project_name, ica
        FROM quotes
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
        # Save file
        filename = secure_filename(file.filename)
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

        # Get additional metadata from form (sanitized via parameterized queries)
        tenant_id = request.form.get('tenant_id', '').strip()
        project_id = request.form.get('project_id', '').strip()
        tenant_name = request.form.get('tenant_name', '').strip()
        project_name = request.form.get('project_name', '').strip()
        ica = request.form.get('ica', '').strip()

        # Convert IDs to int or None
        tenant_id = int(tenant_id) if tenant_id else None
        project_id = int(project_id) if project_id else None

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
            ica=ica
        )

        logger.info(f"Quote saved successfully with ID: {quote_db_id}")

        return jsonify({
            'success': True,
            'quote_id': quote_db_id,
            'redirect': url_for('view_quote', quote_id=quote_db_id)
        })

    except ValueError as e:
        logger.warning(f"Upload rejected for {file.filename}: {e}")
        if 'filepath' in locals() and os.path.exists(filepath):
            try:
                os.remove(filepath)
            except Exception:
                pass
        return jsonify({'error': str(e)}), 409

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
    db = get_db()
    quote = db.execute('SELECT pdf_path FROM quotes WHERE id = ?', (quote_id,)).fetchone()
    db.close()

    if not quote or not quote['pdf_path']:
        return "PDF not found", 404

    from flask import send_file
    return send_file(quote['pdf_path'], mimetype='application/pdf')


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
    """List all tenants."""
    db = get_db()
    tenants = db.execute('''
        SELECT id, name, created_at, updated_at, status
        FROM tenants
        ORDER BY name
    ''').fetchall()
    db.close()
    return render_template('admin/tenants_list.html', tenants=[dict(t) for t in tenants])


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
    """API endpoint for tenants list."""
    db = get_db()
    tenants = db.execute('''
        SELECT id, name, created_at, updated_at, status
        FROM tenants
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
            WHERE p.tenant_id = ?
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
               total_amount, currency, description, tenant_name, project_name, ica
        FROM quotes
        WHERE project_id = ?
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
               total_amount, currency, description, tenant_name, project_name, ica
        FROM quotes
        WHERE project_id IS NULL
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

    # Get all tenants
    tenants = db.execute('SELECT id, name, status FROM tenants ORDER BY name').fetchall()

    # Build tenant-project hierarchy
    tenant_projects = []
    for tenant in tenants:
        tenant_dict = dict(tenant)

        # Get projects for this tenant
        projects = db.execute('''
            SELECT p.id, p.name, p.description, p.status, p.created_at,
                   COUNT(q.id) as quote_count
            FROM projects p
            LEFT JOIN quotes q ON q.project_id = p.id
            WHERE p.tenant_id = ?
            GROUP BY p.id
            ORDER BY p.name
        ''', (tenant['id'],)).fetchall()

        tenant_dict['projects'] = [dict(p) for p in projects]
        tenant_dict['project_count'] = len(projects)
        tenant_projects.append(tenant_dict)

    db.close()
    return render_template('admin/projects_list.html', tenant_projects=tenant_projects)


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
        WHERE p.tenant_id = ?
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
                   total_amount, currency, description, ica, uploaded_at
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
               total_amount, currency, description, ica, uploaded_at
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
    tenants = db.execute('SELECT id, name FROM tenants ORDER BY name').fetchall()
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
        return jsonify({'success': True, 'project_id': project_id})
    except sqlite3.IntegrityError:
        return jsonify({'error': 'Project with this name already exists for this tenant'}), 400
    except Exception as e:
        logger.error(f"Error creating project: {e}")
        return jsonify({'error': str(e)}), 500
    finally:
        db.close()


if __name__ == '__main__':
    # Initialize database if it doesn't exist
    if not os.path.exists(app.config['DATABASE']):
        init_db()

    app.run(debug=True, host='0.0.0.0', port=5001)
