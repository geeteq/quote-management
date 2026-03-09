# Quote Management System

A web-based system for parsing, storing, and visualizing server hardware quotes from vendors like HPE, Dell, and Cisco.

## Features

- **PDF Parsing**: Automatically extract structured data from vendor PDF quotes
- **Component Categorization**: Intelligent categorization into CPU, Memory, Disk, Network, Power Supply, GPU, etc.
- **Spec Extraction**: Automatically parse technical specifications from component descriptions
- **Card-Based UI**: Clean, modern interface with visual quote cards
- **Database Storage**: PostgreSQL/SQLite backend for persistent storage
- **Manufacturer Links**: Automatic linking to component specification pages

## Quick Start

### 1. Install Dependencies

```bash
pip install -r requirements.txt
```

### 2. Initialize Database

The database will be created automatically on first run, but you can manually initialize it:

```bash
python3 -c "from app import init_db; init_db()"
```

### 3. Start the Server

```bash
python3 app.py
```

The application will be available at: http://localhost:5001

### 4. Upload a Quote

1. Click "Choose File" and select a PDF quote
2. Click "Parse Quote"
3. View the parsed quote card with categorized components

## Project Structure

```
.
├── app.py                  # Flask application and API endpoints
├── parser.py               # PDF parsing and categorization logic
├── schema.sql              # Database schema definition
├── requirements.txt        # Python dependencies
├── templates/
│   ├── index.html         # Main quote listing page
│   └── quote_card.html    # Individual quote detail view
├── static/
│   └── style.css          # Application styling
└── uploads/               # Uploaded PDF files (created automatically)
```

## Component Categories

The system automatically categorizes components into:

- **CPU**: Processors (Intel Xeon, AMD EPYC, etc.)
- **Memory**: RAM modules (DDR4, DDR5, etc.)
- **Disk**: Storage devices (SSD, HDD, NVMe, etc.)
- **Network Card**: Network adapters and NICs
- **Power Supply**: PSUs and power cables
- **GPU**: Graphics cards and accelerators
- **Storage Controller**: RAID controllers and HBAs
- **Additional Hardware**: Cables, kits, licenses, and other components

## Spec Extraction

The parser automatically extracts specifications based on component type:

### CPU
- Series (e.g., Xeon-P)
- Model number
- Core count
- Clock speed

### Memory
- Capacity (GB)
- Type (DDR4/DDR5)
- Speed (MHz)

### Disk
- Capacity (GB/TB)
- Type (SSD/HDD)
- Interface (SATA/SAS/NVMe)

### Network Card
- Speed (Gbps)
- Number of ports
- Connector type (SFP28, RJ45, etc.)

### Power Supply
- Wattage
- Voltage

## API Endpoints

### GET /
Main page showing all uploaded quotes

### GET /quote/<quote_id>
View detailed card for a specific quote

### POST /upload
Upload and parse a new PDF quote

**Request**: multipart/form-data with 'file' field
**Response**: JSON with quote_id and redirect URL

### GET /api/quotes
Get list of all quotes as JSON

### GET /api/quote/<quote_id>
Get detailed quote data as JSON

## Database Schema

### Tables

**quotes**: Quote metadata (vendor, customer, dates, totals)
**line_items**: Individual line items from quotes
**components**: Parsed component details and specifications
**component_links**: URLs to manufacturer specification pages

## Supported Vendors

- HPE (Hewlett Packard Enterprise)
- Dell
- Cisco

The parser can be extended to support additional vendors by adding patterns to the `QuoteParser` class.

## Testing

Test the parser directly:

```bash
python3 test_parser.py
```

This will parse the included sample HPE quote and display:
- Extracted quote metadata
- Categorized line items
- Component specifications

## Customization

### Adding New Categories

Edit `parser.py` and add patterns to `CATEGORY_PATTERNS`:

```python
'Your Category': [
    r'pattern1',
    r'pattern2'
]
```

### Modifying Spec Extraction

Add or modify extraction methods in the `QuoteParser` class:

```python
def _extract_your_component_specs(self, description: str) -> Dict:
    specs = {}
    # Your extraction logic here
    return specs
```

### Styling

Modify `static/style.css` to customize the appearance.

## Production Deployment

For production use:

1. Use PostgreSQL instead of SQLite
2. Configure proper WSGI server (gunicorn, uWSGI)
3. Set up reverse proxy (nginx, Apache)
4. Enable HTTPS
5. Configure file upload limits
6. Implement authentication/authorization
7. Set up backup strategy for database and uploaded PDFs

## License

Proprietary - Internal Use Only
