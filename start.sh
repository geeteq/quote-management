#!/bin/bash
# Start the Quote Management System

echo "Starting Quote Management System..."
echo ""

# Check if dependencies are installed
if ! python3 -c "import flask" 2>/dev/null; then
    echo "Installing dependencies..."
    pip3 install -r requirements.txt
fi

# Initialize database if it doesn't exist
if [ ! -f "quotes.db" ]; then
    echo "Initializing database..."
    python3 -c "from app import init_db; init_db()"
fi

echo ""
echo "Server starting at http://localhost:5001"
echo "Press Ctrl+C to stop"
echo ""

# Start the Flask application
python3 app.py
