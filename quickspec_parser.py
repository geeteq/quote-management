"""
quickspec_parser.py
Parse HPE QuickSpec PDFs into structured server + component data.

HPE QuickSpec PDFs follow a well-known layout:
  - Page 1: server model name (e.g. "HPE ProLiant DL360 Gen11")
  - Body: bold section headers (Processors, Memory, Storage, Networking,
          Power Supplies, Graphics, Storage Controllers)
  - Each section: line items with HPE part number + description

Part number formats covered:
  - P49614-B21  (letter + 5 digits + dash + letter + 2 digits) — Gen10/11 kits
  - 867457-B21  (6 digits + dash + letter + 2 digits)           — Gen9-era
  - P38431-B21  (general alphanumeric prefix)                   — misc SKUs
"""

import os
import re
import logging
from typing import Dict, List, Optional, Tuple

import pdfplumber

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Regex patterns
# ---------------------------------------------------------------------------

# Covers the three common HPE SKU formats
HPE_PART_NUMBER_RE = re.compile(
    r'\b([A-Z]\d{5}-[A-Z]\d{2}|'   # P49614-B21
    r'\d{6}-[A-Z]\d{2}|'            # 867457-B21
    r'[A-Z]{1,3}\d{4,6}-[A-Z]\d{2}' # general alpha-prefix variant
    r')\b'
)

# Server model: "HPE ProLiant DL360 Gen11", "HPE Synergy 480 Gen10", etc.
SERVER_MODEL_RE = re.compile(
    r'HPE\s+(ProLiant|Synergy|Alletra|Edgeline|Apollo)\s+'
    r'([A-Z]{1,3}\d{3,4}[a-z]?(?:\s+Gen\s*\d+[a-z]*)?)',
    re.IGNORECASE
)

# ---------------------------------------------------------------------------
# Section header → (component_type, component_role, is_standard) mapping
# Ordered from most-specific to least-specific to avoid mismatch
# ---------------------------------------------------------------------------
SECTION_MAP: List[Tuple[str, str, str, bool]] = [
    # keyword_fragment               component_type         role                  std
    ('storage controller',          'Storage Controller',  'Storage Controller', True),
    ('smart array',                 'Storage Controller',  'RAID Controller',    True),
    ('raid controller',             'Storage Controller',  'RAID Controller',    True),
    ('hba',                         'Storage Controller',  'HBA',                True),
    ('solid state',                 'Disk',                'SSD',                True),
    ('hard drive',                  'Disk',                'HDD',                True),
    ('optical drive',               'Additional Hardware', 'Optical Drive',      False),
    ('nvme',                        'Disk',                'NVMe SSD',           True),
    ('processor',                   'CPU',                 'Processor',          True),
    ('memory',                      'Memory',              'System Memory',      True),
    ('network controller',          'Network Card',        'Network Controller', True),
    ('flexible lom',                'Network Card',        'FlexibleLOM',        True),
    ('ethernet adapter',            'Network Card',        'Ethernet Adapter',   False),
    ('networking',                  'Network Card',        'Network Card',       True),
    ('power suppl',                 'Power Supply',        'Power Supply',       True),
    ('gpu',                         'GPU',                 'GPU',                False),
    ('graphic',                     'GPU',                 'GPU',                False),
    ('accelerator',                 'GPU',                 'GPU',                False),
    ('storage',                     'Disk',                'Storage',            True),
    ('management controller',       'Additional Hardware', 'Management',         True),
    ('rail kit',                    'Additional Hardware', 'Rail Kit',           False),
    ('cable',                       'Additional Hardware', 'Cable',              False),
    ('fan',                         'Additional Hardware', 'Fan',                True),
]

# Form factor inference from model number prefix
FORM_FACTOR_MAP = {
    'DL1': '1U', 'DL2': '2U', 'DL3': '2U', 'DL4': '4U', 'DL5': '4U',
    'DL6': '4U', 'DL8': '8U', 'BL'  : 'Blade', 'ML'  : 'Tower',
    'SY4': '2U', 'SY6': '2U', 'SY8': '2U',
    'XL1': '1U', 'XL2': '2U', 'XL4': '4U',
    'AP2': '2U', 'AP4': '4U', 'AP6': '4U',
}


class QuickSpecParser:
    """Parse HPE QuickSpec PDFs into structured server + component data."""

    def __init__(self, pdf_path: str):
        self.pdf_path = pdf_path
        self.pages_text: List[str] = []
        self.raw_text: str = ''

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def parse(self) -> Dict:
        """
        Returns:
            {
                'server': {
                    'model_name':   str,   # "HPE ProLiant DL360 Gen11"
                    'model_number': str,   # "DL360"
                    'form_factor':  str,   # "1U"
                    'generation':   str,   # "Gen11"
                    'manufacturer': str,   # "HPE"
                },
                'components': [
                    {
                        'component_type': str,   # matches component_catalog CHECK
                        'part_number':    str,
                        'description':    str,
                        'component_role': str,
                        'is_standard':    bool,
                        'is_optional':    bool,
                    }, ...
                ],
                'page_count': int,
            }
        """
        with pdfplumber.open(self.pdf_path) as pdf:
            for page in pdf.pages:
                self.pages_text.append(page.extract_text() or '')
        self.raw_text = '\n'.join(self.pages_text)

        server     = self._detect_server()
        components = self._extract_components()

        logger.info(
            f"QuickSpecParser: {server['model_name']} — "
            f"{len(components)} components across {len(self.pages_text)} pages"
        )
        return {
            'server':     server,
            'components': components,
            'page_count': len(self.pages_text),
        }

    # ------------------------------------------------------------------
    # Server detection
    # ------------------------------------------------------------------

    def _detect_server(self) -> Dict:
        # Search first two pages (model is always on page 1)
        search_text = '\n'.join(self.pages_text[:2])

        match = SERVER_MODEL_RE.search(search_text)
        if match:
            family     = match.group(1)          # ProLiant, Synergy …
            model_tail = match.group(2).strip()  # DL360 Gen11
            full_name  = re.sub(r'\s+', ' ', f'HPE {family} {model_tail}').strip()

            model_number = (re.search(r'([A-Z]{1,3}\d{3,4}[a-z]?)', model_tail)
                            or [None, model_tail])[1]
            gen_match    = re.search(r'(Gen\s*\d+[a-z]*)', model_tail, re.IGNORECASE)
            generation   = re.sub(r'\s+', '', gen_match.group(1)).title() if gen_match else None
            form_factor  = self._infer_form_factor(model_number)

            return {
                'model_name':   full_name,
                'model_number': model_number,
                'form_factor':  form_factor,
                'generation':   generation,
                'manufacturer': 'HPE',
            }

        # Fallback: filename without extension
        basename = os.path.splitext(os.path.basename(self.pdf_path))[0]
        logger.warning(f"QuickSpecParser: could not detect server model — using filename '{basename}'")
        return {
            'model_name':   basename,
            'model_number': None,
            'form_factor':  None,
            'generation':   None,
            'manufacturer': 'HPE',
        }

    @staticmethod
    def _infer_form_factor(model_number: Optional[str]) -> Optional[str]:
        if not model_number:
            return None
        prefix = model_number.upper()
        for key, ff in FORM_FACTOR_MAP.items():
            if prefix.startswith(key):
                return ff
        return 'Rack' if prefix.startswith('DL') else None

    # ------------------------------------------------------------------
    # Component extraction
    # ------------------------------------------------------------------

    def _extract_components(self) -> List[Dict]:
        components: List[Dict] = []
        seen_parts: set = set()

        current_type = 'Additional Hardware'
        current_role = 'Miscellaneous'
        current_std  = False

        lines = self.raw_text.splitlines()

        for i, line in enumerate(lines):
            stripped = line.strip()
            if not stripped:
                continue

            # Try section header detection first
            section = self._match_section_header(stripped)
            if section:
                current_type, current_role, current_std = section
                continue

            # Extract all part numbers on this line
            for pn in HPE_PART_NUMBER_RE.findall(stripped):
                if pn in seen_parts:
                    continue
                seen_parts.add(pn)

                desc       = self._extract_description(stripped, pn, lines, i)
                is_optional = self._is_optional_line(stripped)

                components.append({
                    'component_type': current_type,
                    'part_number':    pn,
                    'description':    desc,
                    'component_role': current_role,
                    'is_standard':    current_std and not is_optional,
                    'is_optional':    is_optional,
                })

        return components

    @staticmethod
    def _match_section_header(line: str) -> Optional[Tuple[str, str, bool]]:
        """
        Return (component_type, component_role, is_standard) if the line
        looks like a QuickSpec section header, else None.

        Heuristic: line is under 80 chars AND has no part number AND
        contains a known section keyword.
        """
        if len(line) > 80:
            return None
        if HPE_PART_NUMBER_RE.search(line):
            return None

        lower = line.lower()
        for keyword, ctype, role, std in SECTION_MAP:
            if keyword in lower:
                return ctype, role, std
        return None

    @staticmethod
    def _extract_description(line: str, part_number: str, lines: List[str], idx: int) -> str:
        """
        Strip the part number from the line and return what remains as
        the description. If the remainder is too short, look at the next line.
        """
        desc = line.replace(part_number, '').strip()
        desc = re.sub(r'^[\s\-\|:•·]+', '', desc).strip()

        if len(desc) < 5 and idx + 1 < len(lines):
            next_line = lines[idx + 1].strip()
            if next_line and not HPE_PART_NUMBER_RE.search(next_line):
                desc = next_line

        return desc[:300] if desc else part_number

    @staticmethod
    def _is_optional_line(line: str) -> bool:
        lower = line.lower()
        return any(kw in lower for kw in ('option', 'upgrade kit', 'add-on', 'spare', 'accessory'))
