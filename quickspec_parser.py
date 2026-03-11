"""
quickspec_parser.py
Parse server spec PDFs (HPE QuickSpec, Dell Technical Guide) into structured
server + component data.

HPE QuickSpec part number formats:
  - P49614-B21  (letter + 5 digits + dash + letter + 2 digits)
  - 867457-B21  (6 digits + dash + letter + 2 digits)
  - P38431-B21  (general alphanumeric prefix)

Dell ordering code format:
  - 338-CBXZ  (3 digits + dash + 4 uppercase letters)
  - 405-BBMQ, 470-AADY, 634-BYKK, etc.
"""

import os
import re
import logging
from typing import Dict, List, Optional, Tuple

import pdfplumber

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# HPE regex patterns
# ---------------------------------------------------------------------------

HPE_PART_NUMBER_RE = re.compile(
    r'\b([A-Z]\d{5}-[A-Z]\d{2}|'       # P49614-B21
    r'\d{6}-[A-Z]\d{2}|'               # 867457-B21
    r'[A-Z]{1,3}\d{4,6}-[A-Z]\d{2}'   # general alpha-prefix variant
    r')\b'
)

HPE_SERVER_MODEL_RE = re.compile(
    r'HPE\s+(ProLiant|Synergy|Alletra|Edgeline|Apollo)\s+'
    r'([A-Z]{1,3}\d{3,4}[a-z]?(?:\s+Gen\s*\d+[a-z]*)?)',
    re.IGNORECASE
)

# Filename fallback for HPE (e.g. HPE_ProLiant_Compute_DL360_Gen12_QuickSpecs-...)
_HPE_FILENAME_MODEL_RE = re.compile(
    r'(?:HPE_)?(?:ProLiant_)?(?:Compute_)?'
    r'([A-Z]{1,3}\d{3,4}[a-z]?)_?(Gen\d+[a-z]*)',
    re.IGNORECASE
)

# ---------------------------------------------------------------------------
# Dell regex patterns
# ---------------------------------------------------------------------------

# Dell ordering codes: NNN-XXXX or NNN-XXXXX (3 digits, dash, 4-5 uppercase)
DELL_PART_NUMBER_RE = re.compile(
    r'\b(\d{3}-[A-Z]{4,5})\b'
)

# "Dell EMC PowerEdge R760", "Dell PowerEdge R640", "PowerEdge R740xd"
DELL_SERVER_MODEL_RE = re.compile(
    r'(?:Dell\s+(?:EMC\s+)?)?PowerEdge\s+([A-Z]{1,2}\d{3,4}[a-zA-Z0-9]*)',
    re.IGNORECASE
)

# Filename fallback for Dell (e.g. poweredge-r640-technical-guide)
_DELL_FILENAME_MODEL_RE = re.compile(
    r'poweredge[-_]([a-z]{1,2}\d{3,4}[a-z0-9]*)',
    re.IGNORECASE
)

# ---------------------------------------------------------------------------
# Form factor lookup — exact model number (both vendors)
# ---------------------------------------------------------------------------

FORM_FACTOR_BY_MODEL = {
    # HPE — 1U rack
    'DL20':  '1U', 'DL120': '1U', 'DL160': '1U',
    'DL325': '1U', 'DL360': '1U', 'DL365': '1U',
    # HPE — 2U rack
    'DL180': '2U', 'DL345': '2U', 'DL380': '2U', 'DL385': '2U', 'DL388': '2U',
    # HPE — 4U rack
    'DL560': '4U', 'DL580': '4U',
    # HPE — Blade / Tower
    'BL460c': 'Blade', 'BL460': 'Blade', 'BL420c': 'Blade',
    'ML30':  'Tower', 'ML110': 'Tower', 'ML350': 'Tower',
    # HPE — Synergy / Apollo
    'SY480': '2U', 'SY660': '2U', 'SY680': '2U',
    'XL170r': '1U', 'XL190r': '2U', 'XL220n': '1U',

    # Dell PowerEdge — 1U rack
    'R250': '1U', 'R350': '1U', 'R450': '1U', 'R550': '1U',
    'R640': '1U', 'R650': '1U', 'R650xs': '1U',
    'R660': '1U', 'R6615': '1U', 'R6625': '1U',
    'R750xs': '1U',
    # Dell PowerEdge — 2U rack
    'R540': '2U', 'R740': '2U', 'R740xd': '2U', 'R740xd2': '2U',
    'R750': '2U', 'R750xd': '2U', 'R760': '2U', 'R760xd': '2U', 'R760xd2': '2U',
    'R7615': '2U', 'R7625': '2U',
    'R840': '2U', 'R940': '4U', 'R940xa': '4U',
    # Dell PowerEdge — Tower
    'T150': 'Tower', 'T350': 'Tower', 'T550': 'Tower', 'T650': 'Tower',
    # Dell PowerEdge — Blade / Modular
    'MX740c': 'Blade', 'MX750c': 'Blade', 'MX760c': 'Blade',
    'FC640': 'Blade', 'FC830': 'Blade',
}

# ---------------------------------------------------------------------------
# Section header → (component_type, component_role, is_standard) mapping
# Ordered most-specific first; applies to both HPE and Dell
# ---------------------------------------------------------------------------
SECTION_MAP: List[Tuple[str, str, str, bool]] = [
    # keyword_fragment               component_type         role                  std
    # Most-specific HPE ordering-section headers first
    ('storage controller',          'Storage Controller',  'Storage Controller', True),
    ('smart array',                 'Storage Controller',  'RAID Controller',    True),
    ('raid controller',             'Storage Controller',  'RAID Controller',    True),
    ('hba',                         'Storage Controller',  'HBA',                True),
    # HPE ordering-section reset headers (broad categories in "Core/Additional Options")
    ('cooling option',              'Additional Hardware', 'Cooling',            False),
    ('liquid cooling',              'Additional Hardware', 'Liquid Cooling',     False),
    ('heat sink',                   'Additional Hardware', 'Heatsink',           False),
    ('heatsink',                    'Additional Hardware', 'Heatsink',           False),
    ('riser',                       'Additional Hardware', 'Riser',              False),
    ('backplane',                   'Additional Hardware', 'Backplane',          False),
    ('rail kit',                    'Additional Hardware', 'Rail Kit',           False),
    ('cable',                       'Additional Hardware', 'Cable',              False),
    ('fan',                         'Additional Hardware', 'Fan',                True),
    # Drive types
    ('solid state',                 'Disk',                'SSD',                True),
    ('hard drive',                  'Disk',                'HDD',                True),
    ('optical drive',               'Additional Hardware', 'Optical Drive',      False),
    ('nvme',                        'Disk',                'NVMe SSD',           True),
    # Core types
    ('processor',                   'CPU',                 'Processor',          True),
    ('memory',                      'Memory',              'System Memory',      True),
    ('network controller',          'Network Card',        'Network Controller', True),
    ('flexible lom',                'Network Card',        'FlexibleLOM',        True),
    ('ethernet adapter',            'Network Card',        'Ethernet Adapter',   False),
    ('networking',                  'Network Card',        'Network Card',       True),
    ('network',                     'Network Card',        'Network Card',       True),
    ('power suppl',                 'Power Supply',        'Power Supply',       True),
    ('gpu',                         'GPU',                 'GPU',                False),
    ('graphic',                     'GPU',                 'GPU',                False),
    ('accelerator',                 'GPU',                 'GPU',                False),
    ('storage',                     'Disk',                'Storage',            True),
    ('management controller',       'Additional Hardware', 'Management',         True),
    ('idrac',                       'Additional Hardware', 'iDRAC',              True),
]


class QuickSpecParser:
    """Parse HPE QuickSpec and Dell Technical Guide PDFs into structured data."""

    def __init__(self, pdf_path: str):
        self.pdf_path = pdf_path
        self.pages_text: List[str] = []
        self.raw_text: str = ''
        self.vendor: Optional[str] = None           # 'HPE' or 'Dell'
        self._part_re: Optional[re.Pattern] = None
        self.server_model_number: Optional[str] = None  # set during _detect_server

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def parse(self) -> Dict:
        """
        Returns:
            {
                'server': {
                    'model_name':   str,
                    'model_number': str,
                    'form_factor':  str,
                    'generation':   str,
                    'manufacturer': str,
                },
                'components': [ { ... }, ... ],
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
            f"QuickSpecParser ({self.vendor}): {server['model_name']} — "
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
        search_text = '\n'.join(self.pages_text[:3])

        # --- Try HPE ---
        m = HPE_SERVER_MODEL_RE.search(search_text)
        if m:
            self.vendor    = 'HPE'
            self._part_re  = HPE_PART_NUMBER_RE
            family         = m.group(1)
            model_tail     = m.group(2).strip()
            full_name      = re.sub(r'\s+', ' ', f'HPE {family} {model_tail}').strip()
            model_number   = (re.search(r'([A-Z]{1,3}\d{3,4}[a-z]?)', model_tail) or [None, model_tail])[1]
            gen_match      = re.search(r'(Gen\s*\d+[a-z]*)', model_tail, re.IGNORECASE)
            generation     = re.sub(r'\s+', '', gen_match.group(1)).title() if gen_match else None
            self.server_model_number = model_number
            return {
                'model_name':   full_name,
                'model_number': model_number,
                'form_factor':  self._infer_form_factor(model_number),
                'generation':   generation,
                'manufacturer': 'HPE',
            }

        # --- Try Dell ---
        m = DELL_SERVER_MODEL_RE.search(search_text)
        if m:
            self.vendor    = 'Dell'
            self._part_re  = DELL_PART_NUMBER_RE
            model_number   = m.group(1).upper()
            full_name      = f'Dell PowerEdge {model_number}'
            self.server_model_number = model_number
            return {
                'model_name':   full_name,
                'model_number': model_number,
                'form_factor':  self._infer_form_factor(model_number),
                'generation':   None,  # Dell doesn't use Gen labels
                'manufacturer': 'Dell',
            }

        # --- Fallback: parse from filename ---
        return self._detect_from_filename()

    def _detect_from_filename(self) -> Dict:
        basename = os.path.splitext(os.path.basename(self.pdf_path))[0]
        logger.warning(f"QuickSpecParser: regex miss — parsing filename '{basename}'")

        # HPE filename pattern
        m = _HPE_FILENAME_MODEL_RE.search(basename)
        if m:
            self.vendor    = 'HPE'
            self._part_re  = HPE_PART_NUMBER_RE
            model_number   = m.group(1).upper()
            generation     = m.group(2).title()
            self.server_model_number = model_number
            return {
                'model_name':   f'HPE ProLiant {model_number} {generation}',
                'model_number': model_number,
                'form_factor':  self._infer_form_factor(model_number),
                'generation':   generation,
                'manufacturer': 'HPE',
            }

        # Dell filename pattern
        m = _DELL_FILENAME_MODEL_RE.search(basename)
        if m:
            self.vendor    = 'Dell'
            self._part_re  = DELL_PART_NUMBER_RE
            model_number   = m.group(1).upper()
            self.server_model_number = model_number
            return {
                'model_name':   f'Dell PowerEdge {model_number}',
                'model_number': model_number,
                'form_factor':  self._infer_form_factor(model_number),
                'generation':   None,
                'manufacturer': 'Dell',
            }

        # Complete fallback
        self.vendor   = 'Unknown'
        self._part_re = HPE_PART_NUMBER_RE  # try HPE patterns as last resort
        return {
            'model_name':   basename,
            'model_number': None,
            'form_factor':  None,
            'generation':   None,
            'manufacturer': 'Unknown',
        }

    @staticmethod
    def _infer_form_factor(model_number: Optional[str]) -> Optional[str]:
        if not model_number:
            return None
        key = model_number.upper()
        if key in FORM_FACTOR_BY_MODEL:
            return FORM_FACTOR_BY_MODEL[key]
        if key.startswith('BL') or key.startswith('MX') or key.startswith('FC'):
            return 'Blade'
        if key.startswith('ML') or re.match(r'^T\d', key):
            return 'Tower'
        return None

    # ------------------------------------------------------------------
    # Component extraction — dispatcher
    # ------------------------------------------------------------------

    def _extract_components(self) -> List[Dict]:
        if self.vendor == 'Dell':
            return self._extract_components_dell_tables()
        return self._extract_components_hpe_lines()

    # ------------------------------------------------------------------
    # HPE line-by-line extraction (part-number driven)
    # ------------------------------------------------------------------

    # HPE QuickSpec notes always start with one of these characters
    _NOTE_LINE_RE = re.compile(r'^[−\-–•·*▪►]')
    # Remove parenthesised content to detect if PN only appears as a cross-reference
    _STRIP_PARENS_RE = re.compile(r'\([^)]*\)')

    # Description-based overrides: (pattern, forced_component_type)
    # Applied after section-based classification; most specific rules first.
    _DESC_RECLASSIFY = [
        (re.compile(r'(?i)\b(ddr[345]?|dimm|sodimm|udimm|rdimm|lrdimm|3ds\s+smart\s+memory|smart\s+memory\s+kit|memory\s+kit)\b'), 'Memory'),
        (re.compile(r'(?i)\b(xeon|processor)\b'),                               'CPU'),
        (re.compile(r'(?i)\bethernet\b.{0,60}(adapter|nic|card|ocp\d?)'),       'Network Card'),
        (re.compile(r'(?i)(adapter|nic|card).{0,60}(ethernet|10g|25g|100g)'),   'Network Card'),
        (re.compile(r'(?i)\bheat\s*sink\b'),                                    'Additional Hardware'),
        (re.compile(r'(?i)\bfan\s+(kit|tray|module)\b'),                        'Additional Hardware'),
        (re.compile(r'(?i)\briser\s+(cage|card|kit|module)\b'),                 'Additional Hardware'),
        (re.compile(r'(?i)\bcable\s+kit\b'),                                    'Additional Hardware'),
        (re.compile(r'(?i)\benablement\s+kit\b'),                               'Additional Hardware'),
        (re.compile(r'(?i)\btrusted\s+supply\b'),                               'Additional Hardware'),
        (re.compile(r'(?i)\bsystem\s+insight\b'),                               'Additional Hardware'),
        (re.compile(r'(?i)\bmedia\s+bay\b'),                                    'Additional Hardware'),
        (re.compile(r'(?i)\bserial\s+port\s+cable\b'),                          'Additional Hardware'),
        (re.compile(r'(?i)\bgpu\s+power\s+cable\b'),                            'Additional Hardware'),
        (re.compile(r'(?i)\b(hdd|hard\s*drive|sff|lff)\s+(spade\s+)?blank\b'), 'Additional Hardware'),
        (re.compile(r'(?i)\bdrive\s+cage\s+kit\b'),                             'Additional Hardware'),
        (re.compile(r'(?i)\bblank\s+kit\b'),                                    'Additional Hardware'),
    ]

    # Descriptions that indicate a garbage/placeholder row — skip entirely
    _DESC_GARBAGE_RE = re.compile(
        r'(?i)^(sku\s+number|system\s+config(uration)?|tbd|n/?a|see\s+note|contact\s+hpe)$'
    )

    # Lines that are cross-reference/requirement notes even if they contain PNs
    _LINE_SKIP_RE = re.compile(
        r'(?i)(requirements?\s+are\s*:|compatible\s+with\s*:|requires?\s+p/n|'
        r'see\s+also\s*:|for\s+use\s+with\s*:)',
    )

    def _extract_components_hpe_lines(self) -> List[Dict]:
        if self._part_re is None:
            self._part_re = HPE_PART_NUMBER_RE

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

            # ── Section header detection ──────────────────────────────────
            section = self._match_section_header(stripped)
            if section:
                current_type, current_role, current_std = section
                continue

            # ── Skip note lines (HPE convention: notes start with − / - / •)
            if self._NOTE_LINE_RE.match(stripped):
                continue

            # ── Skip cross-reference / requirements lines
            if self._LINE_SKIP_RE.search(stripped):
                continue

            # ── Skip continuation prose lines (start with lowercase = mid-sentence)
            if stripped[0].islower():
                continue

            # ── Skip lines where every PN is inside parentheses (cross-refs)
            line_no_parens = self._STRIP_PARENS_RE.sub('', stripped)
            pns = self._part_re.findall(stripped)
            if not pns:
                continue
            primary_pns = self._part_re.findall(line_no_parens)
            if not primary_pns:
                # All PNs were inside ()  — cross-reference only, skip
                continue

            # ── Skip lines with 3+ PNs (comparison tables / reference lists)
            if len(primary_pns) >= 3:
                continue

            for pn in primary_pns:
                if pn in seen_parts:
                    continue
                seen_parts.add(pn)
                desc        = self._extract_description(stripped, pn, lines, i)
                # Skip garbage/placeholder rows
                if desc and self._DESC_GARBAGE_RE.match(desc.strip()):
                    continue
                is_optional = self._is_optional_line(stripped)
                # Override component_type based on description keywords
                ctype = current_type
                for pattern, forced_type in self._DESC_RECLASSIFY:
                    if pattern.search(desc or stripped):
                        ctype = forced_type
                        break
                components.append({
                    'component_type': ctype,
                    'part_number':    pn,
                    'description':    desc,
                    'component_role': current_role,
                    'is_standard':    current_std and not is_optional,
                    'is_optional':    is_optional,
                })
        return components

    # ------------------------------------------------------------------
    # Dell table-based extraction (no ordering codes)
    # ------------------------------------------------------------------

    # Maps table row feature labels → (component_type, role, is_standard)
    _DELL_FEATURE_MAP = [
        ('processor',          'CPU',                 'Processor',          True),
        ('memory',             'Memory',              'System Memory',      True),
        ('storage controller', 'Storage Controller',  'RAID Controller',    True),
        ('raid controller',    'Storage Controller',  'RAID Controller',    True),
        ('hba',                'Storage Controller',  'HBA',                True),
        ('hard drive',         'Disk',                'HDD',                True),
        ('disk drive',         'Disk',                'HDD',                True),
        ('nvme',               'Disk',                'NVMe SSD',           True),
        ('solid state',        'Disk',                'SSD',                True),
        ('pciessd',            'Disk',                'NVMe SSD',           True),
        ('embedded nic',       'Network Card',        'Embedded NIC',       True),
        ('network',            'Network Card',        'Network Card',       True),
        ('power suppl',        'Power Supply',        'Power Supply',       True),
        ('gpu',                'GPU',                 'GPU',                False),
        ('accelerator',        'GPU',                 'GPU/Accelerator',    False),
        ('idrac',              'Additional Hardware', 'iDRAC',              True),
        ('management',         'Additional Hardware', 'Management',         True),
        ('backplane',          'Additional Hardware', 'Backplane',          True),
    ]

    # Feature label headers to skip (not component rows)
    _DELL_SKIP_LABELS = frozenset({
        'feature', 'features', 'technology', 'new technology',
        'detailed description', 'specification', 'technical specification',
        'notes', 'note', 'caution', 'warning',
    })

    def _extract_components_dell_tables(self) -> List[Dict]:
        """
        Extract components from Dell Technical Guide structured tables.
        Each table row: [Feature label, Spec value (possibly multi-line bullet list)].
        Generates a slug-based part number since Dell tech guides have no ordering codes.
        """
        components: List[Dict] = []
        seen_slugs: set = set()
        model_prefix = (self.server_model_number or 'DELL').upper().replace(' ', '-')

        with pdfplumber.open(self.pdf_path) as pdf:
            for page in pdf.pages:
                for table in (page.extract_tables() or []):
                    for row in table:
                        if len(row) < 2:
                            continue
                        feature_cell = (row[0] or '').strip()
                        spec_cell    = (row[1] or '').strip()
                        if not feature_cell or not spec_cell:
                            continue
                        # Skip header/narrative rows
                        if feature_cell.lower() in self._DELL_SKIP_LABELS:
                            continue
                        # Feature label should be short and label-like (not a sentence)
                        if len(feature_cell) > 40 or '\n' in feature_cell:
                            continue

                        mapping = self._dell_map_feature(feature_cell)
                        if not mapping:
                            continue

                        ctype, role, is_standard = mapping

                        # Split bullet-list values into individual components
                        for item in self._dell_split_spec(spec_cell):
                            if not self._dell_is_spec_item(item):
                                continue

                            slug = re.sub(r'[^A-Z0-9]+', '-', item.upper()).strip('-')
                            part_number = f'{model_prefix}-{slug}'[:60]
                            if part_number in seen_slugs:
                                continue
                            seen_slugs.add(part_number)
                            is_optional = self._is_optional_line(item)
                            components.append({
                                'component_type': ctype,
                                'part_number':    part_number,
                                'description':    item[:300],
                                'component_role': role,
                                'is_standard':    is_standard and not is_optional,
                                'is_optional':    is_optional,
                            })
        return components

    @classmethod
    def _dell_map_feature(cls, label: str) -> Optional[Tuple[str, str, bool]]:
        lower = label.lower().replace('\n', ' ')
        for keyword, ctype, role, std in cls._DELL_FEATURE_MAP:
            if keyword in lower:
                return ctype, role, std
        return None

    # Patterns that indicate a genuine spec item (not narrative text)
    _SPEC_SIGNAL_RE = re.compile(
        r'\b('
        r'\d+[GTMKW]B?|'          # storage/memory: 16GB, 750W, 12Gbps
        r'\d+x\s*\d+|'            # counts: 10x2.5, 4x1GbE
        r'\d+\s*(?:GbE|GHz|MT|GT|TB|GB|MB|W|V|Hz|U|x)\b|'
        r'PERC|NVMe|SAS|SATA|DDR4|iDRAC|RDIMM|LRDIMM|PCIe|HBA|RAID|'
        r'Platinum|Titanium|Xeon|Scalable|Gen\d|R\d{3}|H\d{3}|S\d{3}'
        r')',
        re.IGNORECASE
    )
    _NARRATIVE_START_RE = re.compile(
        r'^(The |For |see |and |or |with |including |up to \d|available |contact |'
        r'more information|visit |recommend|note:|caution:|warning:)',
        re.IGNORECASE
    )

    @classmethod
    def _dell_is_spec_item(cls, item: str) -> bool:
        """Return True if item looks like a genuine component spec, not narrative text."""
        if not item or len(item.replace('-', '').strip()) < 3:
            return False
        if len(item) > 120:
            return False
        if re.search(r'https?://|www\.', item, re.I):
            return False
        if cls._NARRATIVE_START_RE.match(item):
            return False
        # Must contain at least one technical signal
        return bool(cls._SPEC_SIGNAL_RE.search(item))

    @staticmethod
    def _dell_split_spec(spec: str) -> List[str]:
        """
        Split a Dell spec cell into individual component descriptions.
        Handles bullet-list format: '● Item A\n● Item B\n● Item C'
        Also handles comma-separated values: 'H330, H730, H740P'
        """
        # Normalize bullet characters
        spec = re.sub(r'[●•·▪▸►\-–]\s*', '', spec)
        items = []
        # Try newline split first
        lines = [l.strip() for l in spec.splitlines() if l.strip()]
        if len(lines) > 1:
            items = lines
        else:
            # Single line — try comma/semicolon split for model lists
            parts = re.split(r'[,;]\s*', spec)
            items = [p.strip() for p in parts if p.strip()]
        # Filter noise
        return [
            it for it in items
            if len(it) >= 3
            and not re.match(r'^(for|see|with|and|or|up to|note|visit|contact|availability)\b', it, re.I)
        ]

    # Section header must be a standalone label, not a note or sentence fragment
    _SECTION_NOTE_RE  = re.compile(r'^[−\-–•·*▪►]')
    _SECTION_NOISE_RE = re.compile(
        r'^(notes?:|if |when |this |see |for |the |and |or |with |up to |'
        r'available |contact |more info|visit |recommend|figure |page \d)',
        re.IGNORECASE
    )

    @classmethod
    def _match_section_header(cls, line: str) -> Optional[Tuple[str, str, bool]]:
        # Must be short, contain no part number, and not be a note/sentence fragment
        if len(line) > 80:
            return None
        if HPE_PART_NUMBER_RE.search(line):
            return None
        if DELL_PART_NUMBER_RE.search(line):
            return None
        if cls._SECTION_NOTE_RE.match(line):
            return None
        if cls._SECTION_NOISE_RE.match(line):
            return None
        lower = line.lower()
        for keyword, ctype, role, std in SECTION_MAP:
            if keyword in lower:
                return ctype, role, std
        return None

    @staticmethod
    def _extract_description(line: str, part_number: str, lines: List[str], idx: int) -> str:
        desc = line.replace(part_number, '').strip()
        desc = re.sub(r'^[\s\-\|:•·]+', '', desc).strip()
        if len(desc) < 5 and idx + 1 < len(lines):
            next_line = lines[idx + 1].strip()
            if next_line and not re.search(r'\b\d{3}-[A-Z]{4}|[A-Z]\d{5}-[A-Z]\d{2}\b', next_line):
                desc = next_line
        return desc[:300] if desc else part_number

    @staticmethod
    def _is_optional_line(line: str) -> bool:
        lower = line.lower()
        return any(kw in lower for kw in ('option', 'upgrade kit', 'add-on', 'spare', 'accessory'))
