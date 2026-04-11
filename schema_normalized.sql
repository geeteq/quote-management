-- Normalized Quote Management System — Full Schema
-- Table order respects FK dependencies: referenced tables precede referencing tables.
-- This file is the source of truth for fresh installs (init_db).
-- Existing databases are upgraded incrementally via migrate_db() in app.py.

-- =============================================================================
-- COMPONENT CATALOG - Master registry of all known components
-- (No external FK dependencies)
-- =============================================================================

CREATE TABLE IF NOT EXISTS component_catalog (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    component_type TEXT NOT NULL CHECK(component_type IN (
        'CPU', 'Memory', 'Disk', 'Storage Controller',
        'Network Card', 'Power Supply', 'GPU', 'Additional Hardware'
    )),
    manufacturer TEXT,
    model TEXT NOT NULL,
    part_number TEXT,
    vendor_part_numbers TEXT, -- JSON array of vendor-specific part numbers
    description TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    data_source TEXT, -- 'manual', 'scraped', 'vendor_api', 'inferred'
    last_verified TIMESTAMP,
    UNIQUE(component_type, manufacturer, model)
);

CREATE INDEX IF NOT EXISTS idx_catalog_type         ON component_catalog(component_type);
CREATE INDEX IF NOT EXISTS idx_catalog_manufacturer ON component_catalog(manufacturer);
CREATE INDEX IF NOT EXISTS idx_catalog_model        ON component_catalog(model);
CREATE INDEX IF NOT EXISTS idx_catalog_part_number  ON component_catalog(part_number);

-- =============================================================================
-- COMPONENT SPEC TABLES (1:1 extension of component_catalog)
-- =============================================================================

CREATE TABLE IF NOT EXISTS cpu_specs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    catalog_id INTEGER UNIQUE NOT NULL,
    cores INTEGER,
    threads INTEGER,
    base_clock_ghz DECIMAL(4, 2),
    max_turbo_clock_ghz DECIMAL(4, 2),
    l1_cache_kb INTEGER,
    l1_instruction_cache_kb INTEGER,
    l1_data_cache_kb INTEGER,
    l2_cache_kb INTEGER,
    l2_instruction_cache_kb INTEGER,
    l2_data_cache_kb INTEGER,
    l3_cache_kb INTEGER,
    l3_shared BOOLEAN DEFAULT 1,
    max_memory_gb INTEGER,
    memory_channels INTEGER,
    memory_types TEXT,           -- JSON array: ["DDR5", "DDR4"]
    max_memory_speed_mhz INTEGER,
    tdp_watts INTEGER,
    max_temp_celsius INTEGER,
    socket TEXT,
    lithography_nm INTEGER,
    pcie_lanes INTEGER,
    pcie_version TEXT,
    instruction_set TEXT,
    instruction_extensions TEXT, -- JSON array
    virtualization_support TEXT, -- JSON array: ["VT-x", "VT-d"]
    launched_date DATE,
    FOREIGN KEY (catalog_id) REFERENCES component_catalog(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS memory_specs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    catalog_id INTEGER UNIQUE NOT NULL,
    capacity_gb INTEGER NOT NULL,
    module_type TEXT,
    speed_mhz INTEGER,
    ddr_generation TEXT,
    cas_latency INTEGER,
    timings TEXT,
    form_factor TEXT,
    rank TEXT,
    ecc_support BOOLEAN DEFAULT 0,
    registered BOOLEAN DEFAULT 0,
    voltage DECIMAL(3, 2),
    xmp_profile TEXT,
    heat_spreader BOOLEAN DEFAULT 0,
    FOREIGN KEY (catalog_id) REFERENCES component_catalog(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS disk_specs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    catalog_id INTEGER UNIQUE NOT NULL,
    capacity_gb INTEGER NOT NULL,
    capacity_tb DECIMAL(6, 2),
    disk_type TEXT,
    interface TEXT,
    form_factor TEXT,
    read_speed_mbps INTEGER,
    write_speed_mbps INTEGER,
    iops_read INTEGER,
    iops_write INTEGER,
    random_read_iops INTEGER,
    random_write_iops INTEGER,
    tbw INTEGER,
    dwpd DECIMAL(4, 2),
    mtbf_hours INTEGER,
    rpm INTEGER,
    cache_mb INTEGER,
    power_consumption_watts DECIMAL(5, 2),
    power_idle_watts DECIMAL(5, 2),
    FOREIGN KEY (catalog_id) REFERENCES component_catalog(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS network_card_specs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    catalog_id INTEGER UNIQUE NOT NULL,
    port_count INTEGER DEFAULT 1,
    port_type TEXT,
    speed_gbps INTEGER,
    total_bandwidth_gbps INTEGER,
    interface TEXT,
    pcie_generation TEXT,
    pcie_lanes INTEGER,
    rdma_support BOOLEAN DEFAULT 0,
    rdma_protocol TEXT,
    sr_iov_support BOOLEAN DEFAULT 0,
    tso_support BOOLEAN DEFAULT 0,
    rss_support BOOLEAN DEFAULT 0,
    tcp_offload BOOLEAN DEFAULT 0,
    ipsec_offload BOOLEAN DEFAULT 0,
    power_consumption_watts DECIMAL(5, 2),
    FOREIGN KEY (catalog_id) REFERENCES component_catalog(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS power_supply_specs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    catalog_id INTEGER UNIQUE NOT NULL,
    wattage INTEGER NOT NULL,
    efficiency_rating TEXT,
    efficiency_percent DECIMAL(5, 2),
    input_voltage_range TEXT,
    input_frequency_hz TEXT,
    power_factor_correction BOOLEAN DEFAULT 1,
    form_factor TEXT,
    redundant BOOLEAN DEFAULT 0,
    hot_pluggable BOOLEAN DEFAULT 0,
    connectors_json TEXT,
    FOREIGN KEY (catalog_id) REFERENCES component_catalog(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS gpu_specs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    catalog_id INTEGER UNIQUE NOT NULL,
    gpu_architecture TEXT,
    cuda_cores INTEGER,
    tensor_cores INTEGER,
    rt_cores INTEGER,
    memory_gb INTEGER,
    memory_type TEXT,
    memory_bus_width INTEGER,
    memory_bandwidth_gbps INTEGER,
    base_clock_mhz INTEGER,
    boost_clock_mhz INTEGER,
    tdp_watts INTEGER,
    power_connectors TEXT,
    interface TEXT,
    ray_tracing BOOLEAN DEFAULT 0,
    tensor_processing BOOLEAN DEFAULT 0,
    multi_gpu_support TEXT,
    max_displays INTEGER,
    display_outputs TEXT,
    max_resolution TEXT,
    FOREIGN KEY (catalog_id) REFERENCES component_catalog(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS storage_controller_specs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    catalog_id INTEGER UNIQUE NOT NULL,
    controller_type TEXT,
    raid_levels TEXT, -- JSON array: ["0", "1", "5", "6", "10"]
    port_count INTEGER,
    port_type TEXT,
    max_devices INTEGER,
    cache_mb INTEGER,
    cache_type TEXT,
    battery_backup BOOLEAN DEFAULT 0,
    flash_backup BOOLEAN DEFAULT 0,
    FOREIGN KEY (catalog_id) REFERENCES component_catalog(id) ON DELETE CASCADE
);

-- =============================================================================
-- DEFINED COMPONENTS - Admin-curated approved component list
-- Each row pins a catalog entry as org-approved. Multiple rows per catalog_id
-- allowed to represent the same component in different contexts or labels.
-- updated_at is maintained by the trigger below.
-- =============================================================================

CREATE TABLE IF NOT EXISTS defined_components (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    catalog_id   INTEGER NOT NULL,
    label        TEXT,                        -- optional friendly display name / context
    notes        TEXT,                        -- org-specific notes or constraints
    is_preferred BOOLEAN NOT NULL DEFAULT 0,  -- marks the org's preferred option
    created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (catalog_id) REFERENCES component_catalog(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_defined_comp_catalog   ON defined_components(catalog_id);
CREATE INDEX IF NOT EXISTS idx_defined_comp_preferred ON defined_components(is_preferred);

CREATE TRIGGER IF NOT EXISTS trg_defined_components_updated_at
AFTER UPDATE ON defined_components
FOR EACH ROW
BEGIN
    UPDATE defined_components SET updated_at = CURRENT_TIMESTAMP WHERE id = OLD.id;
END;

-- =============================================================================
-- COMPONENT DATA SOURCES - Track where catalog data came from
-- =============================================================================

CREATE TABLE IF NOT EXISTS component_data_sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    catalog_id INTEGER NOT NULL,
    source_type TEXT NOT NULL, -- 'intel_ark', 'dell_web', 'hpe_web', 'manual', 'pdf'
    source_url TEXT,
    scraped_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    data_quality TEXT, -- 'complete', 'partial', 'inferred'
    notes TEXT,
    FOREIGN KEY (catalog_id) REFERENCES component_catalog(id) ON DELETE CASCADE
);

-- =============================================================================
-- TENANTS - Multi-tenancy support
-- (No external FK dependencies)
-- =============================================================================

CREATE TABLE IF NOT EXISTS tenants (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    contact_name TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    status TEXT DEFAULT 'active' CHECK(status IN ('active', 'inactive', 'archived'))
);

CREATE INDEX IF NOT EXISTS idx_tenant_name ON tenants(name);

-- =============================================================================
-- PROJECTS - Project hierarchy under tenants
-- =============================================================================

CREATE TABLE IF NOT EXISTS projects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    tenant_id INTEGER NOT NULL,
    description TEXT,
    comments TEXT,
    delivery_deadline DATE,
    budget REAL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    status TEXT DEFAULT 'active' CHECK(status IN ('active', 'inactive', 'archived')),
    FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE,
    UNIQUE(tenant_id, name)
);

CREATE INDEX IF NOT EXISTS idx_projects_tenant ON projects(tenant_id);

-- =============================================================================
-- VENDORS - Companies that issue quotes (HPE, Dell, Cisco …)
-- (No external FK dependencies)
-- =============================================================================

CREATE TABLE IF NOT EXISTS vendors (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL UNIQUE,
    code       TEXT NOT NULL UNIQUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- =============================================================================
-- MANUFACTURERS - Hardware component makers (Intel, HPE, Dell, Broadcom …)
-- (No external FK dependencies)
-- =============================================================================

CREATE TABLE IF NOT EXISTS manufacturers (
    id   INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE
);

-- =============================================================================
-- BASE CONFIGURATIONS - Named component sets per project
-- =============================================================================

CREATE TABLE IF NOT EXISTS base_configs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    config_name TEXT    NOT NULL,
    project_id  INTEGER NOT NULL,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_base_configs_project ON base_configs(project_id);

-- =============================================================================
-- BASE CONFIG COMPONENTS - M:N junction: configs ↔ component_catalog
-- =============================================================================

CREATE TABLE IF NOT EXISTS base_config_components (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    config_id    INTEGER NOT NULL REFERENCES base_configs(id)      ON DELETE CASCADE,
    component_id INTEGER NOT NULL REFERENCES component_catalog(id),
    quantity     INTEGER NOT NULL DEFAULT 1
);

CREATE INDEX IF NOT EXISTS idx_bcc_config     ON base_config_components(config_id);
CREATE INDEX IF NOT EXISTS idx_bcc_component  ON base_config_components(component_id);

-- =============================================================================
-- TRANSACTION TYPES - Catalogue of billable actions
-- (No external FK dependencies)
-- =============================================================================

CREATE TABLE IF NOT EXISTS transaction_types (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    code        TEXT NOT NULL UNIQUE,
    label       TEXT NOT NULL,
    description TEXT,
    token_cost  INTEGER NOT NULL DEFAULT 1
);

-- =============================================================================
-- USERS - Authentication accounts
-- (No external FK dependencies)
-- Passwords are stored as werkzeug pbkdf2:sha256 hashes — never plaintext.
-- user_status: 'enabled' | 'disabled' — no rows are ever deleted.
-- =============================================================================

CREATE TABLE IF NOT EXISTS users (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_name       TEXT NOT NULL UNIQUE,
    user_email      TEXT,
    user_gecos      TEXT,           -- full display name (GECOS field convention)
    password_hash   TEXT NOT NULL,
    user_last_login TIMESTAMP,
    user_status     TEXT NOT NULL DEFAULT 'enabled'
                        CHECK(user_status IN ('enabled', 'disabled')),
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- =============================================================================
-- QUOTES - One row per vendor quote document
-- file_path stores the original uploaded file (PDF or Excel).
-- vendor is stored as a denormalized text string (the authoritative value)
--   because quote documents name vendors in free text. vendor_id is a soft FK
--   populated where a seeded vendor matches; queries must not rely on it alone.
-- tenant_name / project_name are intentionally denormalized: they capture the
--   name at upload time so historical quotes remain accurate if entities rename.
-- quote_items: number of identical units in this quote (drives unit cost calc).
-- =============================================================================

CREATE TABLE IF NOT EXISTS quotes (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    quote_id     TEXT UNIQUE NOT NULL,
    vendor       TEXT NOT NULL,
    vendor_id    INTEGER,                          -- soft FK, may be NULL
    customer_name TEXT,
    quote_date   DATE,
    expiry_date  DATE,
    total_amount DECIMAL(12, 2),
    currency     TEXT DEFAULT 'CAD',
    description  TEXT,
    file_path    TEXT,                             -- original uploaded file (PDF or Excel)
    tenant_id    INTEGER,
    project_id   INTEGER,
    tenant_name  TEXT,                             -- denormalized snapshot at upload time
    project_name TEXT,                             -- denormalized snapshot at upload time
    ica          TEXT,
    po_comments  TEXT CHECK(length(po_comments) <= 255),
    config_id    INTEGER,
    quote_items  INTEGER NOT NULL DEFAULT 1 CHECK(quote_items > 0),
    uploaded_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    status       TEXT DEFAULT 'active',
    FOREIGN KEY (tenant_id)  REFERENCES tenants(id),
    FOREIGN KEY (project_id) REFERENCES projects(id),
    FOREIGN KEY (vendor_id)  REFERENCES vendors(id),
    FOREIGN KEY (config_id)  REFERENCES base_configs(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_quote_id       ON quotes(quote_id);
CREATE INDEX IF NOT EXISTS idx_quotes_tenant  ON quotes(tenant_id);
CREATE INDEX IF NOT EXISTS idx_quotes_project ON quotes(project_id);
CREATE INDEX IF NOT EXISTS idx_quotes_config  ON quotes(config_id);

-- =============================================================================
-- LINE ITEMS - Individual components/SKUs within a quote
-- catalog_component_id is populated when a line item can be matched to the
-- component catalog; NULL means unmatched (matching is not automatic on insert).
-- =============================================================================

CREATE TABLE IF NOT EXISTS line_items (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    quote_id             INTEGER NOT NULL,
    line_no              TEXT,
    quantity             INTEGER,
    product_number       TEXT,
    description          TEXT,
    category             TEXT,
    unit_price           DECIMAL(12, 2),
    total_price          DECIMAL(12, 2),
    delivery_time        TEXT,
    catalog_component_id INTEGER,                  -- NULL when unmatched
    FOREIGN KEY (quote_id)             REFERENCES quotes(id)           ON DELETE CASCADE,
    FOREIGN KEY (catalog_component_id) REFERENCES component_catalog(id)
);

CREATE INDEX IF NOT EXISTS idx_line_items_quote   ON line_items(quote_id);
CREATE INDEX IF NOT EXISTS idx_line_items_catalog ON line_items(catalog_component_id);
CREATE INDEX IF NOT EXISTS idx_category           ON line_items(category);

-- =============================================================================
-- COMPONENTS / COMPONENT_LINKS - Legacy tables kept for backward compatibility
-- =============================================================================

CREATE TABLE IF NOT EXISTS components (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    line_item_id   INTEGER NOT NULL,
    component_type TEXT NOT NULL,
    manufacturer   TEXT,
    part_number    TEXT,
    model          TEXT,
    specs_json     TEXT,
    quantity       INTEGER DEFAULT 1,
    FOREIGN KEY (line_item_id) REFERENCES line_items(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_components_line_item ON components(line_item_id);

CREATE TABLE IF NOT EXISTS component_links (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    component_id  INTEGER NOT NULL,
    url           TEXT NOT NULL,
    url_type      TEXT DEFAULT 'product',
    last_verified TIMESTAMP,
    FOREIGN KEY (component_id) REFERENCES components(id) ON DELETE CASCADE
);

-- =============================================================================
-- TRANSACTIONS - Immutable audit ledger of every user action
-- =============================================================================

CREATE TABLE IF NOT EXISTS transactions (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    type_id        INTEGER NOT NULL REFERENCES transaction_types(id),
    user_name      TEXT    NOT NULL DEFAULT 'system',
    quote_id       INTEGER REFERENCES quotes(id)       ON DELETE SET NULL,
    config_id      INTEGER REFERENCES base_configs(id) ON DELETE SET NULL,
    metadata_json  TEXT,
    tokens_charged INTEGER NOT NULL DEFAULT 0,
    created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_transactions_type    ON transactions(type_id);
CREATE INDEX IF NOT EXISTS idx_transactions_user    ON transactions(user_name);
CREATE INDEX IF NOT EXISTS idx_transactions_quote   ON transactions(quote_id);
CREATE INDEX IF NOT EXISTS idx_transactions_config  ON transactions(config_id);
CREATE INDEX IF NOT EXISTS idx_transactions_created ON transactions(created_at);

-- =============================================================================
-- SERVERS - Catalog of server models parsed from QuickSpec PDFs
-- =============================================================================

CREATE TABLE IF NOT EXISTS servers (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    manufacturer_id INTEGER REFERENCES manufacturers(id),
    model_name      TEXT NOT NULL UNIQUE,
    model_number    TEXT,
    form_factor     TEXT,
    generation      TEXT,
    pdf_path        TEXT,   -- QuickSpec PDFs only; always .pdf
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_servers_model        ON servers(model_name);
CREATE INDEX IF NOT EXISTS idx_servers_manufacturer ON servers(manufacturer_id);

-- =============================================================================
-- SERVER_QUICKSPEC_COMPONENTS - Junction: server ↔ component_catalog
-- =============================================================================

CREATE TABLE IF NOT EXISTS server_quickspec_components (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    server_id      INTEGER NOT NULL REFERENCES servers(id)           ON DELETE CASCADE,
    catalog_id     INTEGER NOT NULL REFERENCES component_catalog(id) ON DELETE CASCADE,
    component_role TEXT,
    is_standard    BOOLEAN DEFAULT 1,
    is_optional    BOOLEAN DEFAULT 0,
    created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(server_id, catalog_id)
);

CREATE INDEX IF NOT EXISTS idx_sqc_server  ON server_quickspec_components(server_id);
CREATE INDEX IF NOT EXISTS idx_sqc_catalog ON server_quickspec_components(catalog_id);
