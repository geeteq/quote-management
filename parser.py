import pdfplumber
import re
from typing import Dict, List, Tuple
from datetime import datetime

class QuoteParser:
    """Parse vendor PDF quotes and extract structured data."""

    # Known CPU specifications lookup (Intel Xeon Scalable)
    CPU_SPECS_LOOKUP = {
        '8592+': {'cores': 64, 'threads': 128, 'base_clock_ghz': 1.9, 'cache_mb': 320, 'tdp_watts': 350},
        '8580': {'cores': 60, 'threads': 120, 'base_clock_ghz': 2.0, 'cache_mb': 300, 'tdp_watts': 350},
        '8570': {'cores': 56, 'threads': 112, 'base_clock_ghz': 2.1, 'cache_mb': 300, 'tdp_watts': 350},
        '8558': {'cores': 48, 'threads': 96, 'base_clock_ghz': 2.1, 'cache_mb': 260, 'tdp_watts': 330},
        '8468': {'cores': 48, 'threads': 96, 'base_clock_ghz': 2.1, 'cache_mb': 105, 'tdp_watts': 350},
        '6558Q': {'cores': 32, 'threads': 64, 'base_clock_ghz': 2.15, 'cache_mb': 60, 'tdp_watts': 265},
        '6538Y+': {'cores': 32, 'threads': 64, 'base_clock_ghz': 2.1, 'cache_mb': 60, 'tdp_watts': 225},
        '6530': {'cores': 32, 'threads': 64, 'base_clock_ghz': 2.1, 'cache_mb': 60, 'tdp_watts': 270},
        '6442Y': {'cores': 24, 'threads': 48, 'base_clock_ghz': 2.6, 'cache_mb': 60, 'tdp_watts': 225},
        '5520+': {'cores': 28, 'threads': 56, 'base_clock_ghz': 2.2, 'cache_mb': 52.5, 'tdp_watts': 225},
        '5512U': {'cores': 24, 'threads': 48, 'base_clock_ghz': 2.1, 'cache_mb': 45, 'tdp_watts': 185},
    }

    # Known CPU specifications lookup (AMD EPYC Genoa 9004 series)
    EPYC_SPECS_LOOKUP = {
        '9654':  {'cores': 96, 'threads': 192, 'base_clock_ghz': 2.4,  'cache_mb': 384, 'tdp_watts': 360},
        '9654P': {'cores': 96, 'threads': 192, 'base_clock_ghz': 2.4,  'cache_mb': 384, 'tdp_watts': 360},
        '9554':  {'cores': 64, 'threads': 128, 'base_clock_ghz': 3.1,  'cache_mb': 256, 'tdp_watts': 360},
        '9554P': {'cores': 64, 'threads': 128, 'base_clock_ghz': 3.1,  'cache_mb': 256, 'tdp_watts': 360},
        '9534':  {'cores': 64, 'threads': 128, 'base_clock_ghz': 2.45, 'cache_mb': 256, 'tdp_watts': 225},
        '9474F': {'cores': 48, 'threads': 96,  'base_clock_ghz': 3.6,  'cache_mb': 256, 'tdp_watts': 360},
        '9454':  {'cores': 48, 'threads': 96,  'base_clock_ghz': 2.75, 'cache_mb': 256, 'tdp_watts': 290},
        '9454P': {'cores': 48, 'threads': 96,  'base_clock_ghz': 2.75, 'cache_mb': 256, 'tdp_watts': 290},
        '9374F': {'cores': 32, 'threads': 64,  'base_clock_ghz': 3.85, 'cache_mb': 256, 'tdp_watts': 320},
        '9354':  {'cores': 32, 'threads': 64,  'base_clock_ghz': 3.25, 'cache_mb': 256, 'tdp_watts': 280},
        '9354P': {'cores': 32, 'threads': 64,  'base_clock_ghz': 3.25, 'cache_mb': 256, 'tdp_watts': 280},
        '9274F': {'cores': 24, 'threads': 48,  'base_clock_ghz': 4.05, 'cache_mb': 256, 'tdp_watts': 320},
        '9254':  {'cores': 24, 'threads': 48,  'base_clock_ghz': 2.9,  'cache_mb': 128, 'tdp_watts': 200},
        '9174F': {'cores': 16, 'threads': 32,  'base_clock_ghz': 4.1,  'cache_mb': 256, 'tdp_watts': 320},
        '9124':  {'cores': 16, 'threads': 32,  'base_clock_ghz': 3.0,  'cache_mb': 64,  'tdp_watts': 200},
    }

    # Category mapping patterns
    CATEGORY_PATTERNS = {
        'CPU': [
            r'xeon', r'cpu', r'processor', r'intel.*\d{4}[a-z+]*',
            r'amd.*epyc', r'ryzen', r'heatsink.*cpu'
        ],
        'Memory': [
            r'memory', r'ram', r'dimm', r'\d+gb.*pc5', r'\d+gb.*ddr',
            r'smart kit.*gb', r'\d+gb\s+rdimm', r'rdimm.*\d+gb'
        ],
        'Disk': [
            r'ssd', r'hdd', r'disk', r'drive', r'storage.*\d+gb',
            r'nvme', r'sata', r'\d+tb', r'm\.2.*\d+gb', r'boss.*\d+gb'
        ],
        'Network Card': [
            r'network', r'nic', r'ethernet', r'gbe', r'sfp', r'rj45',
            r'adapter.*gbe', r'\d+gbe', r'base-t', r'e810', r'broadcom.*\d+.*port',
            r'lom', r'ocp nic'
        ],
        'Power Supply': [
            r'power supply', r'psu', r'power.*\d+w', r'\d+w.*power',
            r'pwr spl', r'flexslot.*pwr', r'hot-plug.*power', r'\d+w.*-?\d*vdc'
        ],
        'GPU': [
            r'gpu', r'graphics', r'nvidia', r'amd.*radeon', r'tesla',
            r'quadro', r'geforce'
        ],
        'Storage Controller': [
            r'storage.*controller', r'storage.*cntlr', r'raid', r'hba',
            r'mr\d+', r'smart array', r'boss.*controller', r'boss-n\d+'
        ],
        'Additional Hardware': []  # Catch-all
    }

    def __init__(self, pdf_path: str):
        self.pdf_path = pdf_path
        self.raw_text = ""
        self.quote_data = {}
        self.line_items = []
        self.vendor_format = None  # Will be 'HPE' or 'Dell'

    def parse(self) -> Dict:
        """Main parsing method."""
        with pdfplumber.open(self.pdf_path) as pdf:
            # Extract text from all pages
            full_text = ""
            for page in pdf.pages:
                full_text += page.extract_text() + "\n"

            self.raw_text = full_text

            # Detect vendor format
            self._detect_vendor_format()

            # Parse different sections based on vendor
            self._parse_header()
            self._parse_line_items()

        return {
            'quote': self.quote_data,
            'line_items': self.line_items
        }

    def _detect_vendor_format(self):
        """Detect which vendor format this quote uses."""
        text = self.raw_text

        # Dell quotes have specific markers
        if 'MOBIA' in text and 'PowerEdge' in text:
            self.vendor_format = 'Dell'
            self.quote_data['vendor'] = 'Dell'
        # HPE quotes have different markers
        elif 'Hewlett Packard' in text or ('HPE' in text and 'Quote ID:' in text):
            self.vendor_format = 'HPE'
            self.quote_data['vendor'] = 'HPE'
        elif 'Cisco' in text:
            self.vendor_format = 'Cisco'
            self.quote_data['vendor'] = 'Cisco'
        else:
            self.vendor_format = 'Unknown'
            self.quote_data['vendor'] = 'Unknown'

    def _parse_header(self):
        """Extract quote header information."""
        if self.vendor_format == 'Dell':
            self._parse_dell_header()
        else:
            self._parse_hpe_header()

    def _parse_hpe_header(self):
        """Extract HPE quote header information."""
        text = self.raw_text

        # Quote ID
        quote_id_match = re.search(r'Quote ID:\s*([A-Z0-9-]+)', text)
        if quote_id_match:
            self.quote_data['quote_id'] = quote_id_match.group(1)

        # Customer
        customer_match = re.search(r'Sold To Address:\s*([^\n]+)', text)
        if customer_match:
            self.quote_data['customer_name'] = customer_match.group(1).strip()

        # Dates
        date_match = re.search(r'Date:\s*([A-Za-z]+\s+\d+,\s+\d{4})', text)
        if date_match:
            self.quote_data['quote_date'] = date_match.group(1)

        expires_match = re.search(r'Expires On:\s*([A-Za-z]+\s+\d+,\s+\d{4})', text)
        if expires_match:
            self.quote_data['expiry_date'] = expires_match.group(1)

        # Total
        total_match = re.search(r'Grand Total:\s*CAD\s*([\d,]+\.\d{2})', text)
        if total_match:
            self.quote_data['total_amount'] = float(total_match.group(1).replace(',', ''))
            self.quote_data['currency'] = 'CAD'

        # Description
        desc_match = re.search(r'In reply to your request:\s*([^\n]+)', text)
        if desc_match:
            desc = desc_match.group(1).strip()
            # Ignore if captured text is just the expiry date line
            if not re.match(r'Expires On:', desc, re.I):
                self.quote_data['description'] = desc

    def _parse_dell_header(self):
        """Extract Dell quote header information."""
        text = self.raw_text
        lines = text.split('\n')

        # Quote ID - Format: "53455 Page 1 of 5 07/24/2025"
        quote_id_match = re.search(r'QUOTE NO\.\s+PAGE NO\.\s+QUOTE DATE\s+(\d+)\s+Page', text)
        if quote_id_match:
            self.quote_data['quote_id'] = quote_id_match.group(1)

        # Quote Date - Format: "07/24/2025"
        date_match = re.search(r'(\d{2}/\d{2}/\d{4})', text)
        if date_match:
            self.quote_data['quote_date'] = date_match.group(1)

        # Valid Until
        valid_match = re.search(r'VALID UNTIL\s+(\d{1,2}/\d{1,2}/\d{4})', text)
        if valid_match:
            self.quote_data['expiry_date'] = valid_match.group(1)

        # Customer - Look for company name after "Quotation provided to:"
        customer_found = False
        for i, line in enumerate(lines):
            if 'Quotation provided to:' in line and not customer_found:
                # Customer name is usually in the next 2-3 lines
                for j in range(i + 1, min(i + 6, len(lines))):
                    customer_line = lines[j].strip()
                    # Skip lines that are part of the form template or address details
                    skip_patterns = [
                        'Please use', 'Estimated Delivery', 'Succursale', 'PO BOX',
                        'Montreal', 'CANADA', 'Att:', 'communications', 'quote',
                        'QC H3C', 'Centre-Ville'
                    ]
                    if customer_line and not any(skip in customer_line for skip in skip_patterns):
                        # Check if it looks like a company name (not a number, has some length)
                        if len(customer_line) > 5 and not re.match(r'^\d', customer_line):
                            self.quote_data['customer_name'] = customer_line
                            customer_found = True
                            break
                if customer_found:
                    break

        # Purchase Order description - appears after VALID UNTIL line
        # Format: "8/22/2025 Low Usage R660 Server Jeff Burbidge Net60Days"
        desc_match = re.search(r'\d{1,2}/\d{1,2}/\d{4}\s+([A-Za-z0-9\s]+?)\s+[A-Za-z]+\s+[A-Za-z]+\s+[A-Za-z0-9]+', text)
        if desc_match:
            desc = desc_match.group(1).strip()
            # Clean up the description - remove trailing contact names
            desc_words = desc.split()
            # Keep words until we hit what looks like a name (capitalized word after the desc)
            clean_desc = []
            for word in desc_words:
                if word in ['Jeff', 'John', 'Mike', 'Bob', 'Net60Days', 'Net30Days']:
                    break
                clean_desc.append(word)
            if clean_desc:
                self.quote_data['description'] = ' '.join(clean_desc)

        # Total Amount - Format: "TOTAL AMOUNT QUOTED $ 34,868.75"
        total_match = re.search(r'TOTAL AMOUNT QUOTED\s+\$\s+([\d,]+\.\d{2})', text)
        if total_match:
            self.quote_data['total_amount'] = float(total_match.group(1).replace(',', ''))
            self.quote_data['currency'] = 'CAD'

    def _parse_line_items(self):
        """Extract line items from quote details section."""
        if self.vendor_format == 'Dell':
            self._parse_dell_line_items()
        else:
            self._parse_hpe_line_items()

    def _parse_hpe_line_items(self):
        """Extract HPE line items."""
        lines = self.raw_text.split('\n')

        in_items_section = False
        current_item = None

        for line in lines:
            line = line.strip()

            # Detect start of items section
            if 'Quote details' in line or re.match(r'^No\.\s+Qty\s+Product', line):
                in_items_section = True
                continue

            if not in_items_section:
                continue

            # Stop at totals section (only after we've started parsing items)
            if 'Sub-Total:' in line or 'Grand Total:' in line:
                break

            # Skip empty lines and page headers
            if not line or 'Hewlett Packard' in line or 'Page' in line:
                continue

            # Parse line item pattern: line_no, qty, product_number, description, delivery
            # Example: 0104 2 P67089-B21 INT Xeon-P 8592+ CPU for HPE 66 days
            # More flexible pattern to handle variations
            item_match = re.match(
                r'^(\d{4})\s+(\d+)\s+([A-Z0-9#-]+)\s+(.+)$',
                line
            )

            if item_match:
                line_no, qty, product, rest = item_match.groups()

                # Skip if this looks like a sub-item (has #0D1 or #ABA suffix)
                if '#' in product:
                    continue

                # Extract delivery time from the end
                delivery_match = re.search(r'(\d+\s+days|Support product)\s*$', rest)
                if delivery_match:
                    delivery = delivery_match.group(1)
                    description = rest[:delivery_match.start()].strip()
                else:
                    delivery = 'N/A'
                    description = rest.strip()

                # Only add items with valid product numbers (not config lines)
                if product and not product.startswith('CNFG'):
                    current_item = {
                        'line_no': line_no,
                        'quantity': int(qty),
                        'product_number': product,
                        'description': description,
                        'delivery_time': delivery,
                        'category': self._categorize(description)
                    }
                    self.line_items.append(current_item)

        # Remove duplicates (some items appear multiple times with factory integrated)
        seen = set()
        unique_items = []
        for item in self.line_items:
            key = (item['line_no'], item['product_number'])
            if key not in seen:
                seen.add(key)
                unique_items.append(item)

        self.line_items = unique_items

    def _parse_dell_line_items(self):
        """Extract Dell line items."""
        lines = self.raw_text.split('\n')

        in_items_section = False
        line_counter = 0
        skip_next = False  # For handling multi-line descriptions

        # Items to filter out (configuration, not actual hardware)
        FILTER_PATTERNS = [
            r'^No\s+',  # "No Hard Drive", "No Controller"
            r'Disabled$',
            r'NOT Installed$',
            r'UEFI BIOS',
            r'Performance Optimized$',
            r'Performance BIOS',
            r'Performance Heatsink',  # Keep heatsinks but in CPU category
            r'No\s+Energy\s+Star',
            r'No\s+Quick\s+Sync',
            r'No\s+Media',
            r'No\s+Operating\s+System',
            r'No\s+Systems\s+Documentation',
            r'No\s+Cables',
            r'No\s+Power\s+Cord',
            r'Additional Processor Selected',  # This is implied by having 2 CPUs
            r'No\s+HBM',
            r'Motherboard\s+MLK',
            r'Riser\s+Config',
            r'Diskless Configuration',
            r'No\s+Controller',
            r'No\s+Hard\s+Drive',
            r'No\s+HD,\s+No\s+Backplane',
            r'Trusted\s+Platform\s+Module',  # TPM
            r'.*RDIMMs$',  # The generic "5600MT/s RDIMMs" line (not actual DIMMs)
            r'Marking',  # CE/CCC Marking
            r'Shipping\s+Material',
            r'iDRAC.*Password',
            r'iDRAC.*Module.*NOT',
            r'iDRAC.*Manager.*Disabled',
            r'Connectivity\s+Client',
            r'Connectivity\s+Module',
        ]

        for i, line in enumerate(lines):
            if skip_next:
                skip_next = False
                continue

            line_stripped = line.strip()

            # Detect start of items section
            if 'QUOTE ITEM NO.' in line_stripped and 'DESCRIPTION' in line_stripped:
                in_items_section = True
                continue

            if not in_items_section:
                continue

            # Stop at totals/summary section
            if 'SUBTOTAL' in line_stripped or 'TOTAL AMOUNT QUOTED' in line_stripped:
                break

            # Skip empty lines, page headers, and BOM marker
            if not line_stripped or 'QUOTATION' in line_stripped or 'QUOTE NO.' in line_stripped or line_stripped == 'BOM Consists of:':
                continue

            # Skip MOBIA header lines
            if 'MOBIA' in line_stripped or 'Dartmouth NS' in line_stripped or 'Eileen Stubbs' in line_stripped:
                continue

            # Parse Dell line item - Format: PART_NO Description Qty $ Price $Total
            # Pattern: ###-#### or ###-##### (part number)
            item_match = re.match(
                r'^(\d{3}-[A-Z]{4,5})\s+(.+?)\s+(\d+\.\d{2})\s+\$\s+([\d,]+\.\d{2})\s+\$([\d,]+\.\d{2})$',
                line_stripped
            )

            if item_match:
                part_no, description, qty, unit_price, total = item_match.groups()

                # Check if this item should be filtered out
                should_filter = False
                for pattern in FILTER_PATTERNS:
                    if re.search(pattern, description, re.I):
                        should_filter = True
                        break

                if should_filter:
                    continue

                # Skip the main server line (it's a container, not an actual component)
                if 'PowerEdge' in description and 'Server' in description:
                    continue

                line_counter += 1

                # Check next line for continuation of description
                full_description = description.strip()
                if i + 1 < len(lines):
                    next_line = lines[i + 1].strip()
                    # If next line doesn't start with a part number and isn't a header, it's a continuation
                    if next_line and not re.match(r'^\d{3}-[A-Z]{4,5}', next_line) and 'QUOTATION' not in next_line:
                        # Check if it looks like a description continuation (not price info)
                        if not re.search(r'\$\s*[\d,]+\.\d{2}', next_line) and not re.match(r'^[A-Z\s]+$', next_line):
                            full_description += ' ' + next_line
                            skip_next = True

                current_item = {
                    'line_no': f"D{line_counter:03d}",  # Dell line numbers: D001, D002, etc.
                    'quantity': int(float(qty)),
                    'product_number': part_no,
                    'description': full_description,
                    'delivery_time': 'N/A',
                    'category': self._categorize(full_description)
                }

                self.line_items.append(current_item)

        # Remove duplicates by part number
        seen = set()
        unique_items = []
        for item in self.line_items:
            key = item['product_number']
            if key not in seen:
                seen.add(key)
                unique_items.append(item)

        self.line_items = unique_items

    def _categorize(self, description: str) -> str:
        """Categorize component based on description."""
        description_lower = description.lower()

        # Check each category's patterns
        for category, patterns in self.CATEGORY_PATTERNS.items():
            if category == 'Additional Hardware':
                continue

            for pattern in patterns:
                if re.search(pattern, description_lower):
                    return category

        # Default to Additional Hardware
        return 'Additional Hardware'

    def extract_component_details(self, line_item: Dict) -> Dict:
        """Extract detailed component specifications."""
        description = line_item['description']
        category = line_item['category']

        details = {
            'type': category,
            'manufacturer': self._extract_manufacturer(description),
            'model': line_item['product_number'],
            'specs': {}
        }

        # Category-specific extraction
        if category == 'CPU':
            details['specs'] = self._extract_cpu_specs(description)
            if details['specs'].get('model'):
                details['model'] = details['specs']['model']
        elif category == 'Memory':
            details['specs'] = self._extract_memory_specs(description)
        elif category == 'Disk':
            details['specs'] = self._extract_disk_specs(description)
        elif category == 'Network Card':
            details['specs'] = self._extract_network_specs(description)
        elif category == 'Power Supply':
            details['specs'] = self._extract_power_specs(description)

        return details

    def _extract_manufacturer(self, description: str) -> str:
        """Extract manufacturer from description."""
        desc_lower = description.lower()

        if 'intel' in desc_lower or 'int ' in desc_lower:
            return 'Intel'
        elif 'amd' in desc_lower:
            return 'AMD'
        elif 'nvidia' in desc_lower:
            return 'NVIDIA'
        elif 'broadcom' in desc_lower or 'bcm' in desc_lower:
            return 'Broadcom'
        elif 'hpe' in desc_lower:
            return 'HPE'
        elif 'dell' in desc_lower:
            return 'Dell'
        elif 'cisco' in desc_lower:
            return 'Cisco'

        return 'Unknown'

    def _extract_cpu_specs(self, description: str) -> Dict:
        """Extract CPU specifications."""
        specs = {}

        # Model extraction - handles both HPE and Dell formats
        # Dell: "Intel Xeon Platinum 8592+ 1.9G, 64C/128T"
        # HPE: "INT Xeon-P 8592+ CPU"
        model_match = re.search(r'xeon[- ]?(?:platinum|gold|silver)?[- ]?([a-z]+\s)?(\d{4}[a-z+]*)', description, re.I)
        if model_match:
            tier = model_match.group(1)
            model = model_match.group(2)
            if tier:
                specs['series'] = f"Xeon-{tier.strip().upper()}"
            else:
                # Try to find series indicator
                series_match = re.search(r'xeon[- ]?([a-z])\s+\d{4}', description, re.I)
                if series_match:
                    specs['series'] = f"Xeon-{series_match.group(1).upper()}"
            specs['model'] = model

            # Try to get specs from lookup table if model is known
            if model in self.CPU_SPECS_LOOKUP:
                lookup_specs = self.CPU_SPECS_LOOKUP[model]
                # Only use lookup if we don't have the spec from description
                for key, value in lookup_specs.items():
                    if key not in specs:
                        specs[key] = value

        # AMD EPYC model extraction
        # Matches: "AMD EPYC 9534", "EPYC 9654P", "EPYC 9374F"
        epyc_match = re.search(r'epyc\s+(\d{4}[a-z+F]*)', description, re.I)
        if epyc_match:
            model = epyc_match.group(1).upper()
            specs['model'] = model
            specs['series'] = 'EPYC'
            if model in self.EPYC_SPECS_LOOKUP:
                for key, value in self.EPYC_SPECS_LOOKUP[model].items():
                    if key not in specs:
                        specs[key] = value

        # Cores and threads - Dell format "64C/128T"
        cores_match = re.search(r'(\d+)C/(\d+)T', description)
        if cores_match:
            specs['cores'] = int(cores_match.group(1))
            specs['threads'] = int(cores_match.group(2))

        # Clock speed - Dell format "1.9G"
        clock_match = re.search(r'(\d+\.?\d*)G(?:Hz)?', description, re.I)
        if clock_match:
            specs['base_clock_ghz'] = float(clock_match.group(1))

        # Cache - Dell format "320M Cache"
        cache_match = re.search(r'(\d+)M\s+Cache', description, re.I)
        if cache_match:
            specs['cache_mb'] = int(cache_match.group(1))

        # TDP - Dell format "(350W)"
        tdp_match = re.search(r'\((\d+)W\)', description)
        if tdp_match:
            specs['tdp_watts'] = int(tdp_match.group(1))

        return specs

    def _extract_memory_specs(self, description: str) -> Dict:
        """Extract memory specifications."""
        specs = {}

        # Capacity
        capacity_match = re.search(r'(\d+)gb', description, re.I)
        if capacity_match:
            specs['capacity_gb'] = int(capacity_match.group(1))

        # Type
        if 'ddr5' in description.lower() or 'pc5' in description.lower():
            specs['type'] = 'DDR5'
        elif 'ddr4' in description.lower():
            specs['type'] = 'DDR4'

        # Speed - handles both formats
        # HPE: "PC5-44800" or "5600MT/s"
        # Dell: "5600MT/s" or "5600MT/s RDIMM"
        speed_match = re.search(r'(\d{4,5})MT/s', description, re.I)
        if speed_match:
            specs['speed_mhz'] = int(speed_match.group(1))
        else:
            speed_match = re.search(r'pc5-(\d+)', description, re.I)
            if speed_match:
                specs['speed_mhz'] = int(speed_match.group(1))

        # Rank - Dell format "Single Rank" or "Dual Rank"
        if 'single rank' in description.lower():
            specs['rank'] = 'Single'
        elif 'dual rank' in description.lower():
            specs['rank'] = 'Dual'

        return specs

    def _extract_disk_specs(self, description: str) -> Dict:
        """Extract disk specifications."""
        specs = {}

        # Capacity
        capacity_match = re.search(r'(\d+)(gb|tb)', description, re.I)
        if capacity_match:
            value = int(capacity_match.group(1))
            unit = capacity_match.group(2).upper()
            specs['capacity'] = f"{value}{unit}"
            specs['capacity_gb'] = value if unit == 'GB' else value * 1024

        # Type
        if 'ssd' in description.lower():
            specs['type'] = 'SSD'
        elif 'hdd' in description.lower():
            specs['type'] = 'HDD'

        # Interface
        if 'nvme' in description.lower():
            specs['interface'] = 'NVMe'
        elif 'sata' in description.lower():
            specs['interface'] = 'SATA'
        elif 'sas' in description.lower():
            specs['interface'] = 'SAS'

        return specs

    def _extract_network_specs(self, description: str) -> Dict:
        """Extract network card specifications."""
        specs = {}

        # Speed - handles multiple formats
        # Dell: "10/25GbE" or "1GbE"
        # HPE: "25gbe" or "10gbe"
        speed_match = re.search(r'(\d+)/(\d+)GbE', description, re.I)
        if speed_match:
            # Dell format shows min/max speed
            specs['speed_gbps'] = int(speed_match.group(2))  # Use max speed
            specs['speed_range'] = f"{speed_match.group(1)}/{speed_match.group(2)}GbE"
        else:
            speed_match = re.search(r'(\d+)gbe', description, re.I)
            if speed_match:
                specs['speed_gbps'] = int(speed_match.group(1))

        # Ports - handles multiple formats
        # Dell: "Quad Port" = 4, "Dual Port" = 2
        # HPE: "4p " = 4 ports
        if 'quad port' in description.lower():
            specs['ports'] = 4
        elif 'dual port' in description.lower():
            specs['ports'] = 2
        else:
            port_match = re.search(r'(\d+)[\s-]?port', description, re.I)
            if port_match:
                specs['ports'] = int(port_match.group(1))
            else:
                port_match = re.search(r'(\d+)p\s', description, re.I)
                if port_match:
                    specs['ports'] = int(port_match.group(1))

        # Type
        if 'sfp28' in description.lower():
            specs['connector'] = 'SFP28'
        elif 'sfp+' in description.lower() or 'sfp' in description.lower():
            specs['connector'] = 'SFP+'
        elif 'base-t' in description.lower() or 'rj45' in description.lower():
            specs['connector'] = 'RJ45'

        # LOM vs Adapter
        if 'lom' in description.lower():
            specs['form_factor'] = 'LOM'
        elif 'ocp' in description.lower():
            specs['form_factor'] = 'OCP'
        elif 'pcie' in description.lower():
            specs['form_factor'] = 'PCIe'

        return specs

    def _extract_power_specs(self, description: str) -> Dict:
        """Extract power supply specifications."""
        specs = {}

        # Wattage
        watt_match = re.search(r'(\d+)w', description, re.I)
        if watt_match:
            specs['wattage'] = int(watt_match.group(1))

        # Voltage
        if '-48vdc' in description.lower():
            specs['voltage'] = '-48VDC'
        elif '48vdc' in description.lower():
            specs['voltage'] = '48VDC'

        return specs
