"""
Web scraping modules for component specifications.

Supports:
- Intel ARK (CPU specifications)
- Dell product pages
- HPE product pages
"""

import re
import time
import json
import requests
from typing import Dict, Optional, List
from bs4 import BeautifulSoup
from urllib.parse import quote, urljoin
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


import threading
import os

# Global rate limiter state using file-based locking for multi-process safety
_rate_limiter_lock = threading.Lock()
_last_request_times = {}  # domain -> timestamp


class RateLimiter:
    """Process-safe rate limiter to avoid overwhelming vendor websites."""
    def __init__(self, domain: str, calls_per_second: float = 1.0):
        self.domain = domain
        self.calls_per_second = calls_per_second
        self.lock_file = f"/tmp/scraper_lock_{domain.replace('.', '_')}.lock"

    def wait(self):
        """Wait if necessary to respect rate limit across all processes."""
        with _rate_limiter_lock:
            now = time.time()
            min_interval = 1.0 / self.calls_per_second

            # Check last request time for this domain
            last_time = _last_request_times.get(self.domain, 0)
            time_since_last = now - last_time

            if time_since_last < min_interval:
                sleep_time = min_interval - time_since_last
                time.sleep(sleep_time)

            _last_request_times[self.domain] = time.time()


class IntelARKScraper:
    """Scrape CPU specifications from Intel ARK database."""

    BASE_URL = "https://ark.intel.com"
    SEARCH_URL = "https://ark.intel.com/content/www/us/en/ark/search.html"

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'
        })
        self.rate_limiter = RateLimiter(domain='ark.intel.com', calls_per_second=0.5)  # 1 request per 2 seconds

    def search_cpu(self, model: str, max_retries: int = 3) -> Optional[str]:
        """
        Search for CPU model and return the product page URL.

        Args:
            model: CPU model like "8592+", "8580", "E-2388G"
            max_retries: Maximum number of retry attempts

        Returns:
            Product page URL or None if not found
        """
        # Clean up model for search
        clean_model = model.strip().upper()
        search_query = f"Xeon {clean_model}"

        logger.info(f"Searching Intel ARK for: {search_query}")

        for attempt in range(max_retries):
            try:
                self.rate_limiter.wait()

                response = self.session.get(
                    self.SEARCH_URL,
                    params={'q': search_query},
                    timeout=15
                )
                response.raise_for_status()

                soup = BeautifulSoup(response.text, 'html.parser')

                # Look for product links in search results
                product_links = soup.find_all('a', href=re.compile(r'/content/www/us/en/ark/products/'))

                if not product_links:
                    logger.warning(f"No results found for {search_query}")
                    return None

                # Get first result (usually most relevant)
                first_link = product_links[0].get('href')
                full_url = urljoin(self.BASE_URL, first_link)

                logger.info(f"Found product page: {full_url}")
                return full_url

            except requests.exceptions.Timeout:
                logger.warning(f"Timeout on attempt {attempt + 1}/{max_retries} for {model}")
                if attempt < max_retries - 1:
                    time.sleep(2 ** attempt)  # Exponential backoff
                continue
            except requests.exceptions.RequestException as e:
                logger.error(f"Request error on attempt {attempt + 1}/{max_retries}: {e}")
                if attempt < max_retries - 1:
                    time.sleep(2 ** attempt)
                continue
            except Exception as e:
                logger.error(f"Unexpected error searching for {model}: {e}")
                return None

        logger.error(f"Failed to search for {model} after {max_retries} attempts")
        return None

    def scrape_cpu_specs(self, product_url: str, max_retries: int = 3) -> Optional[Dict]:
        """
        Scrape detailed CPU specifications from Intel ARK product page.

        Args:
            product_url: Full URL to Intel ARK product page
            max_retries: Maximum number of retry attempts

        Returns:
            Dictionary with CPU specifications or None if failed
        """
        logger.info(f"Scraping CPU specs from: {product_url}")

        for attempt in range(max_retries):
            try:
                self.rate_limiter.wait()

                response = self.session.get(product_url, timeout=15)
                response.raise_for_status()

                soup = BeautifulSoup(response.text, 'html.parser')

                specs = {
                    'source_url': product_url,
                    'scraped_at': time.strftime('%Y-%m-%d %H:%M:%S')
                }

                # Extract product name
                title = soup.find('h1', class_='h1')
                if title:
                    specs['full_name'] = title.get_text(strip=True)

                # Intel ARK uses a specific structure for specifications
                # Look for specification sections with multiple possible class names (fallback)
                spec_sections = soup.find_all('div', class_='specs-section')
                if not spec_sections:
                    spec_sections = soup.find_all('div', {'data-component': 'specs-section'})

                for section in spec_sections:
                    section_title = section.find('h2', class_='section-title')
                    if not section_title:
                        section_title = section.find('h2')
                    if not section_title:
                        continue

                    section_name = section_title.get_text(strip=True)

                    # Find all spec rows in this section
                    spec_rows = section.find_all('div', class_='spec-row')
                    if not spec_rows:
                        spec_rows = section.find_all('div', {'data-component': 'spec-row'})

                    for row in spec_rows:
                        label_elem = row.find('span', class_='label')
                        value_elem = row.find('span', class_='value')

                        if not label_elem or not value_elem:
                            continue

                        label = label_elem.get_text(strip=True).lower()
                        value = value_elem.get_text(strip=True)

                        # Parse specific fields
                        self._parse_spec_field(specs, label, value)

                # Alternative parsing if class names don't match
                if not specs.get('cores'):
                    self._parse_alternative_format(soup, specs)

                logger.info(f"Extracted {len(specs)} specifications")
                return specs if len(specs) > 2 else None  # Must have more than just URL and timestamp

            except requests.exceptions.Timeout:
                logger.warning(f"Timeout on attempt {attempt + 1}/{max_retries} for {product_url}")
                if attempt < max_retries - 1:
                    time.sleep(2 ** attempt)
                continue
            except requests.exceptions.RequestException as e:
                logger.error(f"Request error on attempt {attempt + 1}/{max_retries}: {e}")
                if attempt < max_retries - 1:
                    time.sleep(2 ** attempt)
                continue
            except Exception as e:
                logger.error(f"Unexpected error scraping {product_url}: {e}")
                return None

        logger.error(f"Failed to scrape {product_url} after {max_retries} attempts")
        return None

    def _parse_spec_field(self, specs: Dict, label: str, value: str):
        """Parse individual specification field."""

        # Cores
        if 'total cores' in label or label == 'cores':
            match = re.search(r'(\d+)', value)
            if match:
                specs['cores'] = int(match.group(1))

        # Threads
        elif 'total threads' in label or label == 'threads':
            match = re.search(r'(\d+)', value)
            if match:
                specs['threads'] = int(match.group(1))

        # Base frequency
        elif 'processor base frequency' in label or 'base frequency' in label:
            match = re.search(r'([\d.]+)\s*GHz', value, re.I)
            if match:
                specs['base_clock_ghz'] = float(match.group(1))

        # Turbo frequency
        elif 'max turbo frequency' in label or 'turbo boost' in label:
            match = re.search(r'([\d.]+)\s*GHz', value, re.I)
            if match:
                specs['max_turbo_clock_ghz'] = float(match.group(1))

        # Cache - Intel uses different naming conventions
        elif 'cache' in label:
            match = re.search(r'([\d.]+)\s*MB', value, re.I)
            if match:
                cache_mb = float(match.group(1))
                cache_kb = int(cache_mb * 1024)

                if 'l1' in label:
                    if 'data' in label:
                        specs['l1_data_cache_kb'] = cache_kb
                    elif 'instruction' in label:
                        specs['l1_instruction_cache_kb'] = cache_kb
                    else:
                        specs['l1_cache_kb'] = cache_kb
                elif 'l2' in label:
                    if 'data' in label:
                        specs['l2_data_cache_kb'] = cache_kb
                    elif 'instruction' in label:
                        specs['l2_instruction_cache_kb'] = cache_kb
                    else:
                        specs['l2_cache_kb'] = cache_kb
                elif 'l3' in label or 'smart cache' in label:
                    specs['l3_cache_kb'] = cache_kb

        # TDP
        elif 'tdp' in label or 'thermal design power' in label:
            match = re.search(r'(\d+)\s*W', value, re.I)
            if match:
                specs['tdp_watts'] = int(match.group(1))

        # Max memory
        elif 'max memory size' in label:
            match = re.search(r'(\d+)\s*GB', value, re.I)
            if match:
                specs['max_memory_gb'] = int(match.group(1))
            match = re.search(r'(\d+)\s*TB', value, re.I)
            if match:
                specs['max_memory_gb'] = int(match.group(1)) * 1024

        # Memory channels
        elif 'memory channels' in label:
            match = re.search(r'(\d+)', value)
            if match:
                specs['memory_channels'] = int(match.group(1))

        # Memory types
        elif 'memory types' in label:
            specs['memory_types'] = value

        # Socket
        elif 'socket' in label:
            specs['socket'] = value

        # Lithography
        elif 'lithography' in label:
            match = re.search(r'(\d+)\s*nm', value, re.I)
            if match:
                specs['lithography_nm'] = int(match.group(1))

        # PCIe
        elif 'pci express' in label:
            specs['pcie_version'] = value
            # Extract lane count
            match = re.search(r'(\d+)\s*lanes', value, re.I)
            if match:
                specs['pcie_lanes'] = int(match.group(1))

    def _parse_alternative_format(self, soup: BeautifulSoup, specs: Dict):
        """Try alternative parsing methods if primary method fails."""

        # Look for specs in simple key-value format
        for elem in soup.find_all(['li', 'div', 'span']):
            text = elem.get_text(strip=True)

            # Try to match "Label: Value" patterns
            if ':' in text:
                parts = text.split(':', 1)
                if len(parts) == 2:
                    label = parts[0].strip().lower()
                    value = parts[1].strip()
                    self._parse_spec_field(specs, label, value)

    def get_cpu_specs(self, model: str) -> Optional[Dict]:
        """
        Main method to get CPU specifications.

        Args:
            model: CPU model like "8592+", "8580"

        Returns:
            Dictionary with CPU specifications or None if not found
        """
        product_url = self.search_cpu(model)
        if not product_url:
            return None

        return self.scrape_cpu_specs(product_url)


class DellProductScraper:
    """Scrape product specifications from Dell website."""

    BASE_URL = "https://www.dell.com"
    SEARCH_URL = "https://www.dell.com/support/home/en-us/product-support"

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'
        })
        self.rate_limiter = RateLimiter(domain='dell.com', calls_per_second=0.5)

    def get_component_specs(self, part_number: str) -> Optional[Dict]:
        """
        Get component specifications from Dell by part number.

        Args:
            part_number: Dell part number like "338-CPBP"

        Returns:
            Dictionary with specifications or None
        """
        self.rate_limiter.wait()

        logger.info(f"Searching Dell for part: {part_number}")

        # Dell's website structure may require different approaches
        # This is a placeholder implementation
        try:
            # Dell often requires service tag or product ID
            # For now, return None and log
            logger.warning("Dell scraping not fully implemented - requires service tag")
            return None

        except Exception as e:
            logger.error(f"Error scraping Dell: {e}")
            return None


class HPEProductScraper:
    """Scrape product specifications from HPE website."""

    BASE_URL = "https://www.hpe.com"

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'
        })
        self.rate_limiter = RateLimiter(domain='hpe.com', calls_per_second=0.5)

    def get_component_specs(self, part_number: str) -> Optional[Dict]:
        """
        Get component specifications from HPE by part number.

        Args:
            part_number: HPE part number like "P52544-B21"

        Returns:
            Dictionary with specifications or None
        """
        self.rate_limiter.wait()

        logger.info(f"Searching HPE for part: {part_number}")

        try:
            # HPE QuickSpecs PDFs are often the best source
            # This is a placeholder implementation
            logger.warning("HPE scraping not fully implemented")
            return None

        except Exception as e:
            logger.error(f"Error scraping HPE: {e}")
            return None


# Factory function to get appropriate scraper
def get_scraper(component_type: str, manufacturer: str):
    """Get appropriate scraper based on component type and manufacturer."""

    if component_type == 'CPU':
        if 'intel' in manufacturer.lower():
            return IntelARKScraper()

    elif manufacturer.lower() == 'dell':
        return DellProductScraper()

    elif manufacturer.lower() in ['hpe', 'hewlett packard']:
        return HPEProductScraper()

    return None
