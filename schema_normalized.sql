-- Normalized Component Database Schema
-- This schema separates component catalog from quote line items
-- Each component type has its own detailed specifications table

-- =============================================================================
-- COMPONENT CATALOG - Master registry of all known components
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

-- =============================================================================
-- CPU SPECIFICATIONS - Detailed CPU properties
-- =============================================================================

CREATE TABLE IF NOT EXISTS cpu_specs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    catalog_id INTEGER UNIQUE NOT NULL,

    -- Core Architecture
    cores INTEGER,
    threads INTEGER,
    base_clock_ghz DECIMAL(4, 2),
    max_turbo_clock_ghz DECIMAL(4, 2),

    -- Cache Hierarchy (in KB)
    l1_cache_kb INTEGER,
    l1_instruction_cache_kb INTEGER,
    l1_data_cache_kb INTEGER,
    l2_cache_kb INTEGER,
    l2_instruction_cache_kb INTEGER,
    l2_data_cache_kb INTEGER,
    l3_cache_kb INTEGER,
    l3_shared BOOLEAN DEFAULT 1,

    -- Memory Support
    max_memory_gb INTEGER,
    memory_channels INTEGER,
    memory_types TEXT, -- JSON array: ["DDR5", "DDR4"]
    max_memory_speed_mhz INTEGER,

    -- Power & Thermal
    tdp_watts INTEGER,
    max_temp_celsius INTEGER,

    -- Platform
    socket TEXT,
    lithography_nm INTEGER,
    pcie_lanes INTEGER,
    pcie_version TEXT,

    -- Additional Properties
    instruction_set TEXT,
    instruction_extensions TEXT, -- JSON array
    virtualization_support TEXT, -- JSON array: ["VT-x", "VT-d"]
    launched_date DATE,

    FOREIGN KEY (catalog_id) REFERENCES component_catalog(id) ON DELETE CASCADE
);

-- =============================================================================
-- MEMORY SPECIFICATIONS - Detailed RAM properties
-- =============================================================================

CREATE TABLE IF NOT EXISTS memory_specs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    catalog_id INTEGER UNIQUE NOT NULL,

    -- Capacity
    capacity_gb INTEGER NOT NULL,
    module_type TEXT, -- DIMM, RDIMM, LRDIMM, SODIMM

    -- Speed & Timings
    speed_mhz INTEGER,
    ddr_generation TEXT, -- DDR4, DDR5
    cas_latency INTEGER,
    timings TEXT, -- e.g., "CL40-40-40"

    -- Physical
    form_factor TEXT,
    rank TEXT, -- Single Rank, Dual Rank, Quad Rank
    ecc_support BOOLEAN DEFAULT 0,
    registered BOOLEAN DEFAULT 0,

    -- Voltage
    voltage DECIMAL(3, 2),

    -- Advanced
    xmp_profile TEXT,
    heat_spreader BOOLEAN DEFAULT 0,

    FOREIGN KEY (catalog_id) REFERENCES component_catalog(id) ON DELETE CASCADE
);

-- =============================================================================
-- DISK SPECIFICATIONS - Detailed storage properties
-- =============================================================================

CREATE TABLE IF NOT EXISTS disk_specs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    catalog_id INTEGER UNIQUE NOT NULL,

    -- Capacity
    capacity_gb INTEGER NOT NULL,
    capacity_tb DECIMAL(6, 2),

    -- Type & Interface
    disk_type TEXT, -- SSD, HDD, NVMe, U.2, M.2
    interface TEXT, -- SATA, SAS, NVMe, PCIe
    form_factor TEXT, -- 2.5", 3.5", M.2 2280, U.2

    -- Performance
    read_speed_mbps INTEGER,
    write_speed_mbps INTEGER,
    iops_read INTEGER,
    iops_write INTEGER,
    random_read_iops INTEGER,
    random_write_iops INTEGER,

    -- Endurance (for SSDs)
    tbw INTEGER, -- Terabytes Written
    dwpd DECIMAL(4, 2), -- Drive Writes Per Day
    mtbf_hours INTEGER,

    -- Physical
    rpm INTEGER, -- for HDDs
    cache_mb INTEGER,

    -- Power
    power_consumption_watts DECIMAL(5, 2),
    power_idle_watts DECIMAL(5, 2),

    FOREIGN KEY (catalog_id) REFERENCES component_catalog(id) ON DELETE CASCADE
);

-- =============================================================================
-- NETWORK CARD SPECIFICATIONS
-- =============================================================================

CREATE TABLE IF NOT EXISTS network_card_specs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    catalog_id INTEGER UNIQUE NOT NULL,

    -- Ports
    port_count INTEGER DEFAULT 1,
    port_type TEXT, -- RJ45, SFP+, QSFP, SFP28

    -- Speed
    speed_gbps INTEGER, -- per port
    total_bandwidth_gbps INTEGER,

    -- Interface
    interface TEXT, -- PCIe, OCP, LOM
    pcie_generation TEXT,
    pcie_lanes INTEGER,

    -- Features
    rdma_support BOOLEAN DEFAULT 0,
    rdma_protocol TEXT, -- RoCE, iWARP
    sr_iov_support BOOLEAN DEFAULT 0,
    tso_support BOOLEAN DEFAULT 0,
    rss_support BOOLEAN DEFAULT 0,

    -- Offload Engines
    tcp_offload BOOLEAN DEFAULT 0,
    ipsec_offload BOOLEAN DEFAULT 0,

    -- Power
    power_consumption_watts DECIMAL(5, 2),

    FOREIGN KEY (catalog_id) REFERENCES component_catalog(id) ON DELETE CASCADE
);

-- =============================================================================
-- POWER SUPPLY SPECIFICATIONS
-- =============================================================================

CREATE TABLE IF NOT EXISTS power_supply_specs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    catalog_id INTEGER UNIQUE NOT NULL,

    -- Power
    wattage INTEGER NOT NULL,
    efficiency_rating TEXT, -- 80+ Bronze, Gold, Platinum, Titanium
    efficiency_percent DECIMAL(5, 2),

    -- Input
    input_voltage_range TEXT,
    input_frequency_hz TEXT,
    power_factor_correction BOOLEAN DEFAULT 1,

    -- Form Factor
    form_factor TEXT, -- ATX, Redundant, Hot-Plug
    redundant BOOLEAN DEFAULT 0,
    hot_pluggable BOOLEAN DEFAULT 0,

    -- Connectors
    connectors_json TEXT, -- JSON object with connector types and counts

    FOREIGN KEY (catalog_id) REFERENCES component_catalog(id) ON DELETE CASCADE
);

-- =============================================================================
-- GPU SPECIFICATIONS
-- =============================================================================

CREATE TABLE IF NOT EXISTS gpu_specs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    catalog_id INTEGER UNIQUE NOT NULL,

    -- Core
    gpu_architecture TEXT,
    cuda_cores INTEGER,
    tensor_cores INTEGER,
    rt_cores INTEGER,

    -- Memory
    memory_gb INTEGER,
    memory_type TEXT, -- GDDR6, HBM2, HBM3
    memory_bus_width INTEGER,
    memory_bandwidth_gbps INTEGER,

    -- Clock
    base_clock_mhz INTEGER,
    boost_clock_mhz INTEGER,

    -- Power
    tdp_watts INTEGER,
    power_connectors TEXT,

    -- Interface
    interface TEXT, -- PCIe 4.0 x16, PCIe 5.0 x16

    -- Features
    ray_tracing BOOLEAN DEFAULT 0,
    tensor_processing BOOLEAN DEFAULT 0,
    multi_gpu_support TEXT, -- SLI, NVLink, CrossFire

    -- Display
    max_displays INTEGER,
    display_outputs TEXT, -- JSON array
    max_resolution TEXT,

    FOREIGN KEY (catalog_id) REFERENCES component_catalog(id) ON DELETE CASCADE
);

-- =============================================================================
-- STORAGE CONTROLLER SPECIFICATIONS
-- =============================================================================

CREATE TABLE IF NOT EXISTS storage_controller_specs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    catalog_id INTEGER UNIQUE NOT NULL,

    -- Type
    controller_type TEXT, -- RAID, HBA, NVMe
    raid_levels TEXT, -- JSON array: ["0", "1", "5", "6", "10"]

    -- Ports
    port_count INTEGER,
    port_type TEXT, -- SAS, SATA, NVMe

    -- Performance
    max_devices INTEGER,
    cache_mb INTEGER,
    cache_type TEXT, -- DDR4, Flash-backed

    -- Features
    battery_backup BOOLEAN DEFAULT 0,
    flash_backup BOOLEAN DEFAULT 0,

    FOREIGN KEY (catalog_id) REFERENCES component_catalog(id) ON DELETE CASCADE
);

-- =============================================================================
-- COMPONENT DATA SOURCES - Track where data came from
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
-- TENANTS TABLE - Multi-tenancy support
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
-- PROJECTS TABLE - Project hierarchy under tenants
-- =============================================================================

CREATE TABLE IF NOT EXISTS projects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    tenant_id INTEGER NOT NULL,
    description TEXT,
    comments TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    status TEXT DEFAULT 'active' CHECK(status IN ('active', 'inactive', 'archived')),
    FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE,
    UNIQUE(tenant_id, name)
);

CREATE INDEX IF NOT EXISTS idx_projects_tenant ON projects(tenant_id);

-- =============================================================================
-- EXISTING TABLES (Modified)
-- =============================================================================

CREATE TABLE IF NOT EXISTS quotes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    quote_id TEXT UNIQUE NOT NULL,
    vendor TEXT NOT NULL,
    customer_name TEXT,
    quote_date DATE,
    expiry_date DATE,
    total_amount DECIMAL(12, 2),
    currency TEXT DEFAULT 'CAD',
    description TEXT,
    pdf_path TEXT,
    tenant_id INTEGER,
    project_id INTEGER,
    tenant_name TEXT,
    project_name TEXT,
    ica TEXT,
    po_comments TEXT CHECK(length(po_comments) <= 255),
    uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    status TEXT DEFAULT 'active',
    FOREIGN KEY (tenant_id) REFERENCES tenants(id),
    FOREIGN KEY (project_id) REFERENCES projects(id)
);

CREATE TABLE IF NOT EXISTS line_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    quote_id INTEGER NOT NULL,
    line_no TEXT,
    quantity INTEGER,
    product_number TEXT,
    description TEXT,
    category TEXT,
    unit_price DECIMAL(12, 2),
    total_price DECIMAL(12, 2),
    delivery_time TEXT,

    -- NEW: Reference to component catalog
    catalog_component_id INTEGER,

    FOREIGN KEY (quote_id) REFERENCES quotes(id) ON DELETE CASCADE,
    FOREIGN KEY (catalog_component_id) REFERENCES component_catalog(id)
);

-- Legacy components table (keep for backward compatibility during migration)
CREATE TABLE IF NOT EXISTS components (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    line_item_id INTEGER NOT NULL,
    component_type TEXT NOT NULL,
    manufacturer TEXT,
    part_number TEXT,
    model TEXT,
    specs_json TEXT,
    quantity INTEGER DEFAULT 1,
    FOREIGN KEY (line_item_id) REFERENCES line_items(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS component_links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    component_id INTEGER NOT NULL,
    url TEXT NOT NULL,
    url_type TEXT DEFAULT 'product',
    last_verified TIMESTAMP,
    FOREIGN KEY (component_id) REFERENCES components(id) ON DELETE CASCADE
);

-- =============================================================================
-- INDEXES
-- =============================================================================

CREATE INDEX IF NOT EXISTS idx_quote_id ON quotes(quote_id);
CREATE INDEX IF NOT EXISTS idx_quotes_tenant ON quotes(tenant_id);
CREATE INDEX IF NOT EXISTS idx_quotes_project ON quotes(project_id);
CREATE INDEX IF NOT EXISTS idx_line_items_quote ON line_items(quote_id);
CREATE INDEX IF NOT EXISTS idx_components_line_item ON components(line_item_id);
CREATE INDEX IF NOT EXISTS idx_category ON line_items(category);

CREATE INDEX IF NOT EXISTS idx_catalog_type ON component_catalog(component_type);
CREATE INDEX IF NOT EXISTS idx_catalog_manufacturer ON component_catalog(manufacturer);
CREATE INDEX IF NOT EXISTS idx_catalog_model ON component_catalog(model);
CREATE INDEX IF NOT EXISTS idx_catalog_part_number ON component_catalog(part_number);
CREATE INDEX IF NOT EXISTS idx_line_items_catalog ON line_items(catalog_component_id);

-- =============================================================================
-- VENDORS (companies that issue quotes: HPE, Dell, Cisco)
-- =============================================================================
CREATE TABLE IF NOT EXISTS vendors (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL UNIQUE,
    code       TEXT NOT NULL UNIQUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- =============================================================================
-- MANUFACTURERS (hardware component makers: Intel, HPE, Dell, Broadcom …)
-- =============================================================================
CREATE TABLE IF NOT EXISTS manufacturers (
    id   INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE
);

-- =============================================================================
-- BASE CONFIGURATIONS (named component sets per project)
-- =============================================================================
CREATE TABLE IF NOT EXISTS base_configs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    config_name TEXT    NOT NULL,
    project_id  INTEGER NOT NULL,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
);

-- =============================================================================
-- DEFINED COMPONENTS (manually defined, standalone, never auto-modified)
-- =============================================================================
CREATE TABLE IF NOT EXISTS defined_components (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    component_type  TEXT,
    manufacturer_id INTEGER REFERENCES manufacturers(id),
    part_number     TEXT,
    model           TEXT,
    specs           TEXT,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- =============================================================================
-- BASE CONFIG COMPONENTS (M:N junction: configs ↔ defined_components)
-- =============================================================================
CREATE TABLE IF NOT EXISTS base_config_components (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    config_id    INTEGER NOT NULL REFERENCES base_configs(id)    ON DELETE CASCADE,
    component_id INTEGER NOT NULL REFERENCES defined_components(id),
    quantity     INTEGER NOT NULL DEFAULT 1
);

CREATE INDEX IF NOT EXISTS idx_base_configs_project ON base_configs(project_id);
CREATE INDEX IF NOT EXISTS idx_defined_comp_type    ON defined_components(component_type);
CREATE INDEX IF NOT EXISTS idx_bcc_config           ON base_config_components(config_id);
CREATE INDEX IF NOT EXISTS idx_bcc_component        ON base_config_components(component_id);

-- =============================================================================
-- TRANSACTION TYPES — catalogue of billable API actions with dummy token costs
-- =============================================================================
CREATE TABLE IF NOT EXISTS transaction_types (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    code        TEXT NOT NULL UNIQUE,
    label       TEXT NOT NULL,
    description TEXT,
    token_cost  INTEGER NOT NULL DEFAULT 1
);

-- =============================================================================
-- TRANSACTIONS — immutable audit ledger of every user action
-- =============================================================================
CREATE TABLE IF NOT EXISTS transactions (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    type_id        INTEGER NOT NULL REFERENCES transaction_types(id),
    user_name      TEXT    NOT NULL DEFAULT 'admin',
    quote_id       INTEGER REFERENCES quotes(id)       ON DELETE SET NULL,
    config_id      INTEGER REFERENCES base_configs(id) ON DELETE SET NULL,
    metadata_json  TEXT,                -- arbitrary JSON context per action
    tokens_charged INTEGER NOT NULL DEFAULT 0,
    created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_transactions_type    ON transactions(type_id);
CREATE INDEX IF NOT EXISTS idx_transactions_user    ON transactions(user_name);
CREATE INDEX IF NOT EXISTS idx_transactions_quote   ON transactions(quote_id);
CREATE INDEX IF NOT EXISTS idx_transactions_config  ON transactions(config_id);
CREATE INDEX IF NOT EXISTS idx_transactions_created ON transactions(created_at);
