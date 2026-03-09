"""
Component Registry Service

Manages normalized component catalog with automatic web scraping for missing data.
"""

import sqlite3
import json
import logging
from typing import Dict, Optional, Tuple
from datetime import datetime
from scrapers import get_scraper, IntelARKScraper

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class ComponentRegistry:
    """
    Central registry for component catalog management.

    Features:
    - Lookup components by manufacturer/model/part number
    - Register new components with detailed specs
    - Automatic web scraping for missing specifications
    - Normalized storage by component type
    """

    def __init__(self, db_path: str = 'quotes.db'):
        self.db_path = db_path

    def get_connection(self) -> sqlite3.Connection:
        """Get database connection."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def lookup_component(
        self,
        component_type: str,
        manufacturer: str,
        model: str,
        part_number: Optional[str] = None
    ) -> Optional[int]:
        """
        Look up component in catalog.

        Args:
            component_type: 'CPU', 'Memory', 'Disk', etc.
            manufacturer: Manufacturer name
            model: Model identifier
            part_number: Optional part number

        Returns:
            catalog_id if found, None otherwise
        """
        conn = self.get_connection()
        cursor = conn.cursor()

        try:
            # Try exact match first
            result = cursor.execute('''
                SELECT id FROM component_catalog
                WHERE component_type = ?
                  AND manufacturer = ?
                  AND model = ?
            ''', (component_type, manufacturer, model)).fetchone()

            if result:
                return result['id']

            # Try matching by part number if provided
            if part_number:
                result = cursor.execute('''
                    SELECT id FROM component_catalog
                    WHERE component_type = ?
                      AND (part_number = ? OR vendor_part_numbers LIKE ?)
                ''', (component_type, part_number, f'%{part_number}%')).fetchone()

                if result:
                    return result['id']

            return None

        finally:
            conn.close()

    def register_component(
        self,
        component_type: str,
        manufacturer: str,
        model: str,
        part_number: Optional[str] = None,
        description: Optional[str] = None,
        specs: Optional[Dict] = None,
        data_source: str = 'manual',
        try_scrape: bool = True
    ) -> int:
        """
        Register a new component in the catalog.

        Args:
            component_type: Component category
            manufacturer: Manufacturer name
            model: Model identifier
            part_number: Vendor part number
            description: Text description
            specs: Dictionary of specifications (if available)
            data_source: Source of data ('manual', 'scraped', 'inferred')
            try_scrape: If True and specs are missing, try web scraping

        Returns:
            catalog_id of registered component
        """
        conn = self.get_connection()
        cursor = conn.cursor()

        try:
            # Check if already exists
            existing_id = self.lookup_component(component_type, manufacturer, model, part_number)
            if existing_id:
                logger.info(f"Component already exists: {manufacturer} {model} (ID: {existing_id})")
                return existing_id

            # Try to scrape specs if not provided
            scraped_specs = None
            scraped_source = None

            if try_scrape and (not specs or len(specs) < 3):
                logger.info(f"Attempting to scrape specs for {manufacturer} {model}")
                scraped_specs, scraped_source = self._scrape_component_specs(
                    component_type, manufacturer, model
                )

                if scraped_specs:
                    logger.info(f"Successfully scraped {len(scraped_specs)} specs from {scraped_source}")
                    specs = scraped_specs
                    data_source = 'scraped'

            # Insert into component_catalog
            vendor_part_json = json.dumps([part_number]) if part_number else json.dumps([])

            cursor.execute('''
                INSERT INTO component_catalog
                (component_type, manufacturer, model, part_number, vendor_part_numbers,
                 description, data_source, last_verified)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                component_type,
                manufacturer,
                model,
                part_number,
                vendor_part_json,
                description,
                data_source,
                datetime.now().isoformat()
            ))

            catalog_id = cursor.lastrowid

            # Insert component-specific specs
            if specs:
                self._insert_component_specs(cursor, catalog_id, component_type, specs)

            # Record data source
            if scraped_source:
                cursor.execute('''
                    INSERT INTO component_data_sources
                    (catalog_id, source_type, source_url, data_quality)
                    VALUES (?, ?, ?, ?)
                ''', (
                    catalog_id,
                    scraped_source,
                    specs.get('source_url'),
                    'complete' if len(specs) > 5 else 'partial'
                ))

            conn.commit()
            logger.info(f"Registered component: {manufacturer} {model} (ID: {catalog_id})")
            return catalog_id

        except Exception as e:
            conn.rollback()
            logger.error(f"Error registering component: {e}")
            raise

        finally:
            conn.close()

    def _scrape_component_specs(
        self,
        component_type: str,
        manufacturer: str,
        model: str
    ) -> Tuple[Optional[Dict], Optional[str]]:
        """
        Attempt to scrape component specifications from web.

        Returns:
            Tuple of (specs_dict, source_type) or (None, None)
        """
        scraper = get_scraper(component_type, manufacturer)

        if not scraper:
            logger.warning(f"No scraper available for {manufacturer} {component_type}")
            return None, None

        try:
            if isinstance(scraper, IntelARKScraper):
                specs = scraper.get_cpu_specs(model)
                if specs:
                    return specs, 'intel_ark'

            # Add other scraper types as needed
            return None, None

        except Exception as e:
            logger.error(f"Error scraping specs: {e}")
            return None, None

    def _insert_component_specs(
        self,
        cursor: sqlite3.Cursor,
        catalog_id: int,
        component_type: str,
        specs: Dict
    ):
        """Insert component-specific specifications into appropriate table."""

        if component_type == 'CPU':
            self._insert_cpu_specs(cursor, catalog_id, specs)
        elif component_type == 'Memory':
            self._insert_memory_specs(cursor, catalog_id, specs)
        elif component_type == 'Disk':
            self._insert_disk_specs(cursor, catalog_id, specs)
        elif component_type == 'Network Card':
            self._insert_network_card_specs(cursor, catalog_id, specs)
        elif component_type == 'Power Supply':
            self._insert_power_supply_specs(cursor, catalog_id, specs)
        elif component_type == 'GPU':
            self._insert_gpu_specs(cursor, catalog_id, specs)
        elif component_type == 'Storage Controller':
            self._insert_storage_controller_specs(cursor, catalog_id, specs)

    def _insert_cpu_specs(self, cursor: sqlite3.Cursor, catalog_id: int, specs: Dict):
        """Insert CPU specifications."""
        cursor.execute('''
            INSERT INTO cpu_specs (
                catalog_id, cores, threads, base_clock_ghz, max_turbo_clock_ghz,
                l1_cache_kb, l1_instruction_cache_kb, l1_data_cache_kb,
                l2_cache_kb, l2_instruction_cache_kb, l2_data_cache_kb,
                l3_cache_kb, max_memory_gb, memory_channels, memory_types,
                max_memory_speed_mhz, tdp_watts, max_temp_celsius, socket,
                lithography_nm, pcie_lanes, pcie_version, instruction_set,
                instruction_extensions, virtualization_support, launched_date
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            catalog_id,
            specs.get('cores'),
            specs.get('threads'),
            specs.get('base_clock_ghz'),
            specs.get('max_turbo_clock_ghz'),
            specs.get('l1_cache_kb'),
            specs.get('l1_instruction_cache_kb'),
            specs.get('l1_data_cache_kb'),
            specs.get('l2_cache_kb'),
            specs.get('l2_instruction_cache_kb'),
            specs.get('l2_data_cache_kb'),
            specs.get('l3_cache_kb'),
            specs.get('max_memory_gb'),
            specs.get('memory_channels'),
            json.dumps(specs.get('memory_types')) if isinstance(specs.get('memory_types'), list)
            else specs.get('memory_types'),
            specs.get('max_memory_speed_mhz'),
            specs.get('tdp_watts'),
            specs.get('max_temp_celsius'),
            specs.get('socket'),
            specs.get('lithography_nm'),
            specs.get('pcie_lanes'),
            specs.get('pcie_version'),
            specs.get('instruction_set'),
            json.dumps(specs.get('instruction_extensions')) if specs.get('instruction_extensions') else None,
            json.dumps(specs.get('virtualization_support')) if specs.get('virtualization_support') else None,
            specs.get('launched_date')
        ))

    def _insert_memory_specs(self, cursor: sqlite3.Cursor, catalog_id: int, specs: Dict):
        """Insert memory specifications."""
        cursor.execute('''
            INSERT INTO memory_specs (
                catalog_id, capacity_gb, module_type, speed_mhz, ddr_generation,
                cas_latency, timings, form_factor, rank, ecc_support,
                registered, voltage, xmp_profile, heat_spreader
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            catalog_id,
            specs.get('capacity_gb'),
            specs.get('module_type'),
            specs.get('speed_mhz'),
            specs.get('ddr_generation'),
            specs.get('cas_latency'),
            specs.get('timings'),
            specs.get('form_factor'),
            specs.get('rank'),
            specs.get('ecc_support', 0),
            specs.get('registered', 0),
            specs.get('voltage'),
            specs.get('xmp_profile'),
            specs.get('heat_spreader', 0)
        ))

    def _insert_disk_specs(self, cursor: sqlite3.Cursor, catalog_id: int, specs: Dict):
        """Insert disk specifications."""
        cursor.execute('''
            INSERT INTO disk_specs (
                catalog_id, capacity_gb, capacity_tb, disk_type, interface,
                form_factor, read_speed_mbps, write_speed_mbps, iops_read,
                iops_write, random_read_iops, random_write_iops, tbw, dwpd,
                mtbf_hours, rpm, cache_mb, power_consumption_watts, power_idle_watts
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            catalog_id,
            specs.get('capacity_gb'),
            specs.get('capacity_tb'),
            specs.get('disk_type'),
            specs.get('interface'),
            specs.get('form_factor'),
            specs.get('read_speed_mbps'),
            specs.get('write_speed_mbps'),
            specs.get('iops_read'),
            specs.get('iops_write'),
            specs.get('random_read_iops'),
            specs.get('random_write_iops'),
            specs.get('tbw'),
            specs.get('dwpd'),
            specs.get('mtbf_hours'),
            specs.get('rpm'),
            specs.get('cache_mb'),
            specs.get('power_consumption_watts'),
            specs.get('power_idle_watts')
        ))

    def _insert_network_card_specs(self, cursor: sqlite3.Cursor, catalog_id: int, specs: Dict):
        """Insert network card specifications."""
        cursor.execute('''
            INSERT INTO network_card_specs (
                catalog_id, port_count, port_type, speed_gbps, total_bandwidth_gbps,
                interface, pcie_generation, pcie_lanes, rdma_support, rdma_protocol,
                sr_iov_support, tso_support, rss_support, tcp_offload, ipsec_offload,
                power_consumption_watts
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            catalog_id,
            specs.get('port_count', 1),
            specs.get('port_type'),
            specs.get('speed_gbps'),
            specs.get('total_bandwidth_gbps'),
            specs.get('interface'),
            specs.get('pcie_generation'),
            specs.get('pcie_lanes'),
            specs.get('rdma_support', 0),
            specs.get('rdma_protocol'),
            specs.get('sr_iov_support', 0),
            specs.get('tso_support', 0),
            specs.get('rss_support', 0),
            specs.get('tcp_offload', 0),
            specs.get('ipsec_offload', 0),
            specs.get('power_consumption_watts')
        ))

    def _insert_power_supply_specs(self, cursor: sqlite3.Cursor, catalog_id: int, specs: Dict):
        """Insert power supply specifications."""
        cursor.execute('''
            INSERT INTO power_supply_specs (
                catalog_id, wattage, efficiency_rating, efficiency_percent,
                input_voltage_range, input_frequency_hz, power_factor_correction,
                form_factor, redundant, hot_pluggable, connectors_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            catalog_id,
            specs.get('wattage'),
            specs.get('efficiency_rating'),
            specs.get('efficiency_percent'),
            specs.get('input_voltage_range'),
            specs.get('input_frequency_hz'),
            specs.get('power_factor_correction', 1),
            specs.get('form_factor'),
            specs.get('redundant', 0),
            specs.get('hot_pluggable', 0),
            json.dumps(specs.get('connectors')) if specs.get('connectors') else None
        ))

    def _insert_gpu_specs(self, cursor: sqlite3.Cursor, catalog_id: int, specs: Dict):
        """Insert GPU specifications."""
        cursor.execute('''
            INSERT INTO gpu_specs (
                catalog_id, gpu_architecture, cuda_cores, tensor_cores, rt_cores,
                memory_gb, memory_type, memory_bus_width, memory_bandwidth_gbps,
                base_clock_mhz, boost_clock_mhz, tdp_watts, power_connectors,
                interface, ray_tracing, tensor_processing, multi_gpu_support,
                max_displays, display_outputs, max_resolution
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            catalog_id,
            specs.get('gpu_architecture'),
            specs.get('cuda_cores'),
            specs.get('tensor_cores'),
            specs.get('rt_cores'),
            specs.get('memory_gb'),
            specs.get('memory_type'),
            specs.get('memory_bus_width'),
            specs.get('memory_bandwidth_gbps'),
            specs.get('base_clock_mhz'),
            specs.get('boost_clock_mhz'),
            specs.get('tdp_watts'),
            specs.get('power_connectors'),
            specs.get('interface'),
            specs.get('ray_tracing', 0),
            specs.get('tensor_processing', 0),
            specs.get('multi_gpu_support'),
            specs.get('max_displays'),
            json.dumps(specs.get('display_outputs')) if specs.get('display_outputs') else None,
            specs.get('max_resolution')
        ))

    def _insert_storage_controller_specs(self, cursor: sqlite3.Cursor, catalog_id: int, specs: Dict):
        """Insert storage controller specifications."""
        cursor.execute('''
            INSERT INTO storage_controller_specs (
                catalog_id, controller_type, raid_levels, port_count, port_type,
                max_devices, cache_mb, cache_type, battery_backup, flash_backup
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            catalog_id,
            specs.get('controller_type'),
            json.dumps(specs.get('raid_levels')) if specs.get('raid_levels') else None,
            specs.get('port_count'),
            specs.get('port_type'),
            specs.get('max_devices'),
            specs.get('cache_mb'),
            specs.get('cache_type'),
            specs.get('battery_backup', 0),
            specs.get('flash_backup', 0)
        ))

    def get_component_specs(self, catalog_id: int) -> Optional[Dict]:
        """
        Retrieve complete component specifications.

        Args:
            catalog_id: Component catalog ID

        Returns:
            Dictionary with all specifications or None
        """
        conn = self.get_connection()
        cursor = conn.cursor()

        try:
            # Get catalog entry
            catalog = cursor.execute('''
                SELECT * FROM component_catalog WHERE id = ?
            ''', (catalog_id,)).fetchone()

            if not catalog:
                return None

            result = dict(catalog)
            component_type = catalog['component_type']

            # Get type-specific specs
            if component_type == 'CPU':
                specs = cursor.execute('''
                    SELECT * FROM cpu_specs WHERE catalog_id = ?
                ''', (catalog_id,)).fetchone()
                if specs:
                    result['specs'] = dict(specs)

            elif component_type == 'Memory':
                specs = cursor.execute('''
                    SELECT * FROM memory_specs WHERE catalog_id = ?
                ''', (catalog_id,)).fetchone()
                if specs:
                    result['specs'] = dict(specs)

            # Add other component types as needed

            return result

        finally:
            conn.close()
